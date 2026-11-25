import asyncio
import tempfile
import unittest
from pathlib import Path

from service.model_control import ModelControl
from service.wire import Problem


class Adapter:
    identity = {
        "model": "profile",
        "model_revision": "weights",
        "engine": "test",
        "engine_revision": "runtime",
    }


async def wait_for(control, state):
    async with asyncio.timeout(3):
        while control.list()["models"][0]["state"] != state:
            await asyncio.sleep(0.005)


class ModelControlConformance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.path = (
            Path(self.enterContext(tempfile.TemporaryDirectory())) / "models.json"
        )
        self.calls = []

    async def load(self, adapter):
        self.calls.append(("load", adapter))

    async def unload(self, adapter):
        self.calls.append(("unload", adapter))

    def build(self, model, **kwargs):
        return ModelControl(
            {"profile": model},
            self.path,
            load=self.load,
            unload=self.unload,
            **kwargs,
        )

    async def test_disable_fences_new_work_and_drains_then_persists(self):
        model = Adapter()
        control = self.build(model)
        await control.start()
        self.addAsyncCleanup(control.stop)
        self.assertEqual(self.calls, [("load", model)])
        async with control.acquire("profile") as leased:
            self.assertIs(leased, model)
            row = control.set_enabled("profile", False)
            self.assertEqual((row["state"], row["active_requests"]), ("draining", 1))
            self.assertEqual(control.available(), {})
            with self.assertRaises(Problem) as blocked:
                async with control.acquire("profile"):
                    self.fail("Disabled profile admitted another request")
            self.assertEqual(blocked.exception.code, "model_unavailable")
            await asyncio.sleep(0)
            self.assertEqual(self.calls, [("load", model)])
        await wait_for(control, "disabled")
        self.assertEqual(self.calls[-1], ("unload", model))
        await control.stop()
        reopened = self.build(model)
        await reopened.start()
        self.addAsyncCleanup(reopened.stop)
        self.assertFalse(reopened.list()["models"][0]["enabled"])
        self.assertEqual(reopened.available(), {})
        reopened.set_enabled("profile", True)
        await wait_for(reopened, "ready")
        self.assertIs(reopened.available()["profile"], model)

    async def test_exclusive_registry_ownership(self):
        original = self.build(Adapter(), default_enabled=set())
        await original.start()
        self.addAsyncCleanup(original.stop)
        second = self.build(Adapter())
        with self.assertRaisesRegex(RuntimeError, "Another service process"):
            await second.start()
        self.assertEqual(original.available(), {})

    async def test_failed_load_cleans_partial_adapter_and_can_retry(self):
        model = Adapter()

        async def sometimes_fails(adapter):
            self.calls.append(("load", adapter))
            if len(self.calls) == 1:
                raise Problem(502, "worker_failed", "Synthetic boot failure.")

        control = ModelControl(
            {"profile": model},
            self.path,
            load=sometimes_fails,
            unload=self.unload,
        )
        await control.start()
        self.addAsyncCleanup(control.stop)
        self.assertEqual(control.list()["models"][0]["error_code"], "worker_failed")
        self.assertEqual(self.calls, [("load", model), ("unload", model)])
        self.assertEqual(control.available(), {})
        control.set_enabled("profile", True)
        await wait_for(control, "ready")
