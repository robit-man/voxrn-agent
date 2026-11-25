"""The model installer must fail capacity preflight before allocating assets."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from model_install import CapacityError, require_capacity  # noqa: E402


class CapacityTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.prefix = Path(folder)
        self.spec = {"disk_mib": 200, "memory_mib": 500}

    def test_install_and_cached_enable_have_different_disk_requirements(self):
        with (
            patch(
                "shutil.disk_usage", return_value=SimpleNamespace(free=150 * 1024**2)
            ),
            patch("model_install.available_memory", return_value=1000),
        ):
            with self.assertRaises(CapacityError) as raised:
                require_capacity(self.prefix, self.spec)
            self.assertEqual(raised.exception.code, "insufficient_model_disk")
            require_capacity(self.prefix, self.spec, installed=True)

    def test_low_memory_blocks_an_existing_model_before_activation(self):
        with patch("model_install.available_memory", return_value=499):
            with self.assertRaises(CapacityError) as raised:
                require_capacity(self.prefix, self.spec, installed=True)
            self.assertEqual(raised.exception.code, "insufficient_model_memory")

    def test_cuda_needs_an_explicit_scope(self):
        cuda = {**self.spec, "hardware": "cuda", "vram_mib": 600}
        with (
            patch("model_install.available_memory", return_value=2000),
            patch.dict("os.environ", {"CUDA_VISIBLE_DEVICES": "-1"}),
        ):
            with self.assertRaises(CapacityError) as raised:
                require_capacity(self.prefix, cuda, installed=True)
            self.assertEqual(raised.exception.code, "cuda_scope_required")
