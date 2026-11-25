import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    import setup_host


class AgentInstallerConformance(unittest.TestCase):
    def test_retry_uses_same_model_and_stops_after_three_failures(self):
        env = {"OLLAMA_HOST": "127.0.0.1:18130"}
        command = ("ollama", "pull", setup_host.QWEN_TAG)
        unavailable = subprocess.CalledProcessError(1, command)
        with patch.object(setup_host, "run", side_effect=unavailable) as run:
            with patch.object(setup_host.time, "sleep") as sleep:
                with self.assertRaises(subprocess.CalledProcessError):
                    setup_host.pull_pinned_model("ollama", env)
        self.assertEqual(run.call_args_list, [call(*command, env=env, timeout=600)] * 3)
        self.assertEqual(sleep.call_args_list, [call(3), call(6)])

    def test_seed_verifies_blob_before_admitting_the_model_manifest(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cache = root / "source"
        relative = Path("manifests/registry.ollama.ai/library/qwen3/0.6b")
        (cache / relative).parent.mkdir(parents=True)
        payload = b"synthetic weight fixture"
        digest = hashlib.sha256(payload).hexdigest()
        manifest = json.dumps({"config": {"digest": f"sha256:{digest}"}, "layers": []})
        (cache / relative).write_text(manifest)
        blob = cache / "blobs" / f"sha256-{digest}"
        blob.parent.mkdir()
        blob.write_bytes(payload)
        with patch.object(setup_host, "QWEN_DIGEST", digest):
            setup_host.seed_model_cache(cache, root / "verified")
            self.assertEqual((root / "verified" / relative).read_text(), manifest)
            blob.write_bytes(b"corrupt")
            with self.assertRaisesRegex(RuntimeError, "failed its hash check"):
                setup_host.seed_model_cache(cache, root / "rejected")
        self.assertFalse((root / "rejected" / relative).exists())
