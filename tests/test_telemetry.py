import time
import unittest
from types import SimpleNamespace

from service.telemetry import Telemetry, fit_health


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.host = {
            "memory_bytes": 16 * 1024**3,
            "memory_used_bytes": 8 * 1024**3,
            "memory_available_bytes": 8 * 1024**3,
            "filesystem_free_bytes": 16 * 1024**3,
        }
        self.plan = {
            "memory_mib": 2048,
            "disk_mib": 8192,
            "installed": False,
            "hardware": "cpu",
        }
        self.entry = SimpleNamespace(
            adapter=SimpleNamespace(installation=lambda: self.plan),
            state="disabled",
            active_requests=0,
            enabled=False,
            loaded=False,
        )

    def test_health_distinguishes_resource_limits_and_observed_audio(self):
        estimate = fit_health(self.entry, self.host, 0, None, None)
        self.assertEqual(
            (estimate["label"], estimate["basis"]), ("Excellent", "Estimated")
        )
        observed = fit_health(self.entry, self.host, 0, 851200000, 1.6)
        self.assertEqual(observed["audio_rtf"], 1.6)
        self.assertLess(observed["score"], estimate["score"])
        self.assertEqual(observed["basis"], "Measured + estimate")
        limited = fit_health(
            self.entry, {**self.host, "memory_available_bytes": 1024}, 0, None, None
        )
        self.assertEqual((limited["score"], limited["label"]), (0, "Unusable"))

    def test_utilization_filters_use_canonical_routes_and_model(self):
        metrics = Telemetry(
            {
                "/v1/media/{media_id}": {"get": {"responses": {}}},
                "/v1/capabilities": {"get": {"responses": {}}},
            },
            [],
            SimpleNamespace(entries={"voice": self.entry}),
        )
        name = metrics.endpoint(
            {"type": "http", "method": "GET", "path": "/v1/media/object-private"}
        )
        self.assertEqual(name, "GET /v1/media/{media_id}")
        now = time.time()
        metrics.requests.extend(
            ((now, name, "voice", True, 21), (now - 700, name, "voice", False, 8))
        )
        metrics.calls.append((now, "voice", False, 12))
        metrics.event("voice", "loading")
        metrics.event("voice", "ready")
        report = metrics.snapshot(60, model="voice", endpoint=name)
        self.assertEqual(
            report["endpoints"],
            [{"name": name, "calls": 1, "errors": 1, "mean_ms": 21}],
        )
        self.assertEqual(report["models"][0]["calls"], 1)
        self.assertEqual(
            [row["state"] for row in report["lifecycle"]], ["ready", "loading"]
        )
        self.assertNotIn("object-private", str(report))
