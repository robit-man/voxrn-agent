"""Transactional runs, idempotent commands, event replay and fenced worker recovery."""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager

import rfc8785

from .wire import Problem, canonical_json, new_id, validate

TERMINAL = frozenset({"completed", "failed", "cancelled"})
LEASE_SECONDS = 15
IDEMPOTENCY_TTL = 86_400
RUN_IDLE_TTL = 604_800
MAX_EVENTS_BATCH = 64


class RunStore:
    def __init__(self, path):
        self.path = path
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, workflow TEXT NOT NULL,
                    status TEXT NOT NULL, rev INTEGER NOT NULL, seq INTEGER NOT NULL,
                    state TEXT NOT NULL, history TEXT NOT NULL, touched REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT, seq INTEGER, value TEXT,
                    PRIMARY KEY (run_id, seq)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    run_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, input TEXT NOT NULL,
                    lease TEXT, lease_expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS keys (
                    owner TEXT, scope TEXT, key TEXT, hash TEXT, response TEXT, expires REAL,
                    PRIMARY KEY (owner, scope, key)
                );
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL, value TEXT NOT NULL,
                    UNIQUE (run_id, turn_id, operation_id)
                );
                CREATE TABLE IF NOT EXISTS expired (
                    run_id TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL
                );
            """)

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def snapshot(row):
        return {
            "id": row["id"],
            "workflow": json.loads(row["workflow"]),
            "status": row["status"],
            "revision": row["rev"],
            "last_event_seq": row["seq"],
            "state": json.loads(row["state"]),
        }

    def owned(self, db, owner, run_id):
        row = db.execute(
            "SELECT * FROM runs WHERE owner = ? AND id = ?", (owner, run_id)
        ).fetchone()
        if not row:
            if db.execute(
                "SELECT 1 FROM expired WHERE owner = ? AND run_id = ? AND expires > ?",
                (owner, run_id, time.time()),
            ).fetchone():
                raise Problem(
                    410, "run_expired", "The run exceeded its retention interval."
                )
            raise Problem(404, "run_not_found", "The run was not found.")
        return row

    def get(self, owner, run_id):
        with self.connection() as db:
            return self.snapshot(self.owned(db, owner, run_id))

    @staticmethod
    def append(db, run_id, kind, **fields):
        row = db.execute("SELECT seq FROM runs WHERE id = ?", (run_id,)).fetchone()
        seq = row["seq"] + 1
        event = validate({"type": kind, "seq": seq, **fields}, "RunEvent", status=500)
        db.execute(
            "UPDATE runs SET seq = ?, touched = ? WHERE id = ?",
            (seq, time.time(), run_id),
        )
        db.execute(
            "INSERT INTO events(run_id, seq, value) VALUES (?, ?, ?)",
            (run_id, seq, canonical_json(event)),
        )
        return seq

    def command(self, owner, scope, key, body, apply):
        try:
            body_hash = hashlib.sha256(rfc8785.dumps(body)).hexdigest()
        except (ValueError, TypeError) as exc:
            raise Problem(
                400,
                "noncanonical_request",
                "The body cannot be canonically represented.",
            ) from exc
        now = time.time()
        with self.connection(write=True) as db:
            db.execute("DELETE FROM keys WHERE expires <= ?", (now,))
            prior = db.execute(
                "SELECT hash, response FROM keys WHERE owner=? AND scope=? AND key=?",
                (owner, scope, key),
            ).fetchone()
            if prior:
                if prior["hash"] != body_hash:
                    raise Problem(
                        409,
                        "idempotency_conflict",
                        "This key already identifies a different request.",
                    )
                return json.loads(prior["response"])
            result = apply(db)
            db.execute(
                "INSERT INTO keys VALUES (?, ?, ?, ?, ?, ?)",
                (
                    owner,
                    scope,
                    key,
                    body_hash,
                    canonical_json(result),
                    now + IDEMPOTENCY_TTL,
                ),
            )
            return result

    @staticmethod
    def require_model(workflow, available_models):
        if available_models is not None:
            identity = workflow["identity"]
            if available_models.get(identity["model"]) != identity:
                raise Problem(
                    503,
                    "pinned_model_not_ready",
                    "The exact model profile pinned by this workflow is unavailable.",
                    retryable=True,
                )

    def create(self, owner, key, body, workflows, *, available_models=None):
        def initialize(db):
            workflow = workflows.get((body["workflow_id"], body["workflow_revision"]))
            if workflow is None:
                raise Problem(
                    404, "workflow_not_found", "The workflow revision is not installed."
                )
            self.require_model(workflow.identity, available_models)
            count = db.execute(
                "SELECT COUNT(*) FROM runs WHERE owner=?", (owner,)
            ).fetchone()[0]
            if count >= 1000:
                raise Problem(
                    429,
                    "run_quota_exceeded",
                    "The run retention quota has been reached.",
                    retryable=True,
                )
            run_id = new_id()
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, 'ready', 0, 0, ?, '[]', ?)",
                (
                    run_id,
                    owner,
                    canonical_json(workflow.identity),
                    canonical_json(workflow.manifest["initial_state"]),
                    time.time(),
                ),
            )
            self.append(db, run_id, "run.started", revision=0)
            return self.snapshot(self.owned(db, owner, run_id))

        return self.command(owner, "create", key, body, initialize)

    def submit(self, owner, run_id, key, body, *, available_models=None):
        def apply(db):
            row = self.owned(db, owner, run_id)
            if row["status"] != "ready" or row["rev"] != body["expected_revision"]:
                raise Problem(
                    409,
                    "run_revision_conflict",
                    "Read the latest ready run before submitting a turn.",
                )
            self.require_model(json.loads(row["workflow"]), available_models)
            turn_id = new_id()
            revision = row["rev"] + 1
            db.execute(
                "UPDATE runs SET status='running', rev=? WHERE id=?", (revision, run_id)
            )
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, NULL, 0)",
                (run_id, turn_id, body["input"]),
            )
            seq = self.append(
                db, run_id, "turn.started", revision=revision, turn_id=turn_id
            )
            return {
                "run_id": run_id,
                "turn_id": turn_id,
                "revision": revision,
                "last_event_seq": seq,
            }

        return self.command(owner, f"turn:{run_id}", key, body, apply)

    def request_cancel(self, owner, run_id, key, body):
        def apply(db):
            row = self.owned(db, owner, run_id)
            if (
                row["status"] not in ("ready", "running")
                or row["rev"] != body["expected_revision"]
            ):
                raise Problem(
                    409,
                    "run_revision_conflict",
                    "The run cannot be cancelled at this revision.",
                )
            revision = row["rev"] + 1
            db.execute(
                "UPDATE runs SET status='cancelling', rev=? WHERE id=?",
                (revision, run_id),
            )
            self.append(db, run_id, "run.cancelling", revision=revision)
            return self.snapshot(self.owned(db, owner, run_id))

        return self.command(owner, f"cancel:{run_id}", key, body, apply)

    def poll(self, owner, run_id, cursor):
        with self.connection() as db:
            state = self.owned(db, owner, run_id)
            if cursor > state["seq"]:
                raise Problem(
                    409, "future_cursor", "The cursor exceeds the last durable event."
                )
            rows = db.execute(
                "SELECT value FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                (run_id, cursor, MAX_EVENTS_BATCH),
            ).fetchall()
            return (
                state["status"],
                state["seq"],
                [json.loads(row["value"]) for row in rows],
            )

    def claim(self, unavailable_models=()):
        now = time.time()
        with self.connection(write=True) as db:
            cancelled = db.execute(
                "SELECT id, rev FROM runs WHERE status='cancelling'"
            ).fetchone()
            if cancelled:
                run_id, revision = cancelled["id"], cancelled["rev"] + 1
                job = db.execute(
                    "SELECT turn_id FROM jobs WHERE run_id=?", (run_id,)
                ).fetchone()
                if job:
                    exc = Problem(409, "operator_cancelled", "The turn was cancelled.")
                    self.append(
                        db,
                        run_id,
                        "turn.failed",
                        turn_id=job["turn_id"],
                        revision=revision,
                        problem=exc.body(),
                    )
                db.execute("DELETE FROM jobs WHERE run_id=?", (run_id,))
                db.execute(
                    "UPDATE runs SET status='cancelled', rev=? WHERE id=?",
                    (revision, run_id),
                )
                self.append(db, run_id, "run.cancelled", revision=revision)
            job = db.execute(
                """SELECT j.*, r.workflow, r.state, r.history FROM jobs j
                              JOIN runs r ON j.run_id = r.id
                              WHERE r.status='running' AND j.lease_expires <= ?
                                AND NOT EXISTS (
                                    SELECT 1 FROM json_each(?)
                                    WHERE value = json_extract(r.workflow, '$.identity.model')
                                )
                              ORDER BY r.touched LIMIT 1""",
                (now, json.dumps(sorted(unavailable_models))),
            ).fetchone()
            if not job:
                return None
            marker = new_id()
            db.execute(
                "UPDATE jobs SET lease=?, lease_expires=? WHERE run_id=?",
                (marker, now + LEASE_SECONDS, job["run_id"]),
            )
            return {**dict(job), "lease": marker}

    def release(self, job):
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE jobs SET lease=NULL, lease_expires=0 WHERE run_id=? AND lease=?",
                (job["run_id"], job["lease"]),
            )

    def renew(self, job):
        with self.connection(write=True) as db:
            now = time.time()
            return (
                db.execute(
                    """UPDATE jobs SET lease_expires=? WHERE run_id=? AND lease=? AND lease_expires > ?
                               AND EXISTS (SELECT 1 FROM runs WHERE id=? AND status='running')""",
                    (
                        now + LEASE_SECONDS,
                        job["run_id"],
                        job["lease"],
                        now,
                        job["run_id"],
                    ),
                ).rowcount
                == 1
            )

    def finish(self, job, workflow, proposal=None, error=None):
        run_id, turn_id = job["run_id"], job["turn_id"]
        with self.connection(write=True) as db:
            owned = db.execute(
                """SELECT r.* FROM runs r JOIN jobs j ON r.id=j.run_id
                                WHERE r.id=? AND r.status='running' AND j.lease=? AND j.lease_expires>?""",
                (run_id, job["lease"], time.time()),
            ).fetchone()
            if not owned:
                return False
            revision = owned["rev"]
            if error:
                revision += 1
                self.append(
                    db,
                    run_id,
                    "turn.failed",
                    turn_id=turn_id,
                    problem=error.body(),
                    revision=revision,
                )
                revision += 1
                self.append(
                    db, run_id, "run.failed", problem=error.body(), revision=revision
                )
                status = "failed"
            else:
                history = json.loads(owned["history"])
                if proposal:
                    operation = workflow.manifest["operations"][0]
                    receipt = new_id()
                    validate(
                        {"record_id": receipt}, operation["output_schema"], status=502
                    )
                    db.execute(
                        "INSERT INTO records VALUES (?, ?, ?, ?, ?)",
                        (
                            receipt,
                            run_id,
                            turn_id,
                            operation["id"],
                            canonical_json(proposal),
                        ),
                    )
                    self.append(
                        db,
                        run_id,
                        "action.receipt",
                        turn_id=turn_id,
                        receipt_id=receipt,
                        operation_id=operation["id"],
                        status="recorded",
                    )
                    revision += 1
                    db.execute(
                        "UPDATE runs SET state=? WHERE id=?",
                        (canonical_json(proposal), run_id),
                    )
                    self.append(
                        db,
                        run_id,
                        "state.committed",
                        turn_id=turn_id,
                        state=proposal,
                        revision=revision,
                    )
                    reply = f"I recorded the request for {proposal['day']}."
                else:
                    reply = "Which day of the week should I record for this request?"
                history.extend(
                    [
                        {"role": "user", "content": job["input"]},
                        {"role": "assistant", "content": reply},
                    ]
                )
                db.execute(
                    "UPDATE runs SET history=? WHERE id=?",
                    (canonical_json(history), run_id),
                )
                revision += 1
                self.append(
                    db,
                    run_id,
                    "turn.completed",
                    turn_id=turn_id,
                    reply=reply,
                    revision=revision,
                )
                status = "completed" if proposal else "ready"
                if proposal:
                    revision += 1
                    self.append(db, run_id, "run.completed", revision=revision)
            db.execute(
                "UPDATE runs SET status=?, rev=? WHERE id=?", (status, revision, run_id)
            )
            db.execute("DELETE FROM jobs WHERE run_id=?", (run_id,))
            return True

    def purge(self):
        now = time.time()
        with self.connection(write=True) as db:
            rows = db.execute(
                """SELECT id, owner FROM runs WHERE touched<=?
                               AND status NOT IN ('running', 'cancelling')""",
                (now - RUN_IDLE_TTL,),
            ).fetchall()
            for row in rows:
                db.execute(
                    "INSERT INTO expired VALUES (?, ?, ?)",
                    (row["id"], row["owner"], now + RUN_IDLE_TTL),
                )
                db.execute("DELETE FROM events WHERE run_id=?", (row["id"],))
                db.execute("DELETE FROM records WHERE run_id=?", (row["id"],))
                db.execute("DELETE FROM runs WHERE id=?", (row["id"],))
            db.execute("DELETE FROM expired WHERE expires <= ?", (now,))
            db.execute("DELETE FROM keys WHERE expires <= ?", (now,))
