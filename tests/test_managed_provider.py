import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from service.provider_registry import managed_ollama_provider


class ManagedProvider(unittest.TestCase):
    def test_catalog_added_after_setup_has_exact_managed_provider(self):
        spec = {
            "id": "gemma4-12b",
            "ollama": {
                "tag": "gemma4:12b",
                "digest": "a" * 64,
                "engine_revision": "0.40.18",
                "think": False,
            },
        }
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = directory / "settings.json"
        settings.write_text('{"ollama_port":18769}')
        with patch("service.provider_registry.load_providers", return_value={}):
            provider = managed_ollama_provider(spec, settings)
        self.assertEqual(provider.identity["model_revision"], spec["ollama"]["digest"])
        self.assertEqual(provider.profile.tag, "gemma4:12b")
        self.assertEqual(provider.url, "http://127.0.0.1:18769")
        self.assertTrue(provider.managed)
        self.assertFalse(provider.default_enabled)
        with patch(
            "service.provider_registry.load_providers",
            return_value={spec["id"]: provider},
        ):
            self.assertIs(managed_ollama_provider(spec, settings), provider)
