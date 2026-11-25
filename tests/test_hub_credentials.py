"""Verify protected credential storage and its download-only handoff."""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from service.hub_credentials import TOKEN_FILENAME, read_hub_token, save_hub_token
from service.server import create_app
from service.wire import OPENAPI, Principal
from support import HEADERS, TOKEN

ADMIN_PATH = "/v1/control/huggingface"
FIRST = "hf_" + "a" * 34
SECOND = "hf_" + "b" * 34
KIND = OPENAPI["info"]["title"].split()[-1].lower()
ROOT = Path(__file__).resolve().parents[1]


class HuggingFaceCredentials(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch.dict(os.environ, {"VOXRN_DEV_TOKEN": TOKEN}, clear=True)
        )
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.key_file = self.directory / TOKEN_FILENAME
        options = {"providers": {}} if KIND == "agent" else {"models": {}}
        self.app = create_app(directory=self.directory, **options)
        self.client = self.enterContext(
            TestClient(self.app, base_url="http://localhost:8000")
        )

    def test_admin_save_replace_remove_without_disclosing_token(self):
        seen = [
            (self.client.get(ADMIN_PATH, headers=HEADERS), False),
            (self.client.put(ADMIN_PATH, headers=HEADERS, json={"token": FIRST}), True),
        ]
        self.assertEqual(stat.S_IMODE(self.key_file.stat().st_mode), 0o600)
        self.assertEqual(read_hub_token(self.key_file), FIRST)
        seen.append((self.client.get(ADMIN_PATH, headers=HEADERS), True))
        seen.append(
            (self.client.put(ADMIN_PATH, headers=HEADERS, json={"token": SECOND}), True)
        )
        self.assertEqual(read_hub_token(self.key_file), SECOND)
        seen.append((self.client.delete(ADMIN_PATH, headers=HEADERS), False))
        seen.append((self.client.delete(ADMIN_PATH, headers=HEADERS), False))
        self.assertFalse(self.key_file.exists())
        for response, configured in seen:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"configured": configured})
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_denied_credentials_and_invalid_requests_do_not_change_saved_key(self):
        save_hub_token(self.key_file, FIRST)
        reader = Principal("issuer", "tenant", "reader", frozenset({f"{KIND}:read"}))
        for method in ("GET", "PUT", "DELETE"):
            kwargs = {"json": {"token": SECOND}} if method == "PUT" else {}
            anonymous = self.client.request(method, ADMIN_PATH, **kwargs)
            self.assertEqual(anonymous.status_code, 401)
            with patch.object(self.app.state.auth, "authenticate", return_value=reader):
                unprivileged = self.client.request(method, ADMIN_PATH, **kwargs)
            self.assertEqual(unprivileged.status_code, 403)
        for token in (SECOND + "!", SECOND + "\n", "hf_" + "z" * 258, None):
            rejected = self.client.put(
                ADMIN_PATH, headers=HEADERS, json={"token": token}
            )
            self.assertEqual(rejected.status_code, 400)
            self.assertNotIn(SECOND, rejected.text)
        self.assertEqual(read_hub_token(self.key_file), FIRST)

    def test_read_fails_closed_for_symlinks_and_exposed_permissions(self):
        other_file = self.directory / "other"
        other_file.write_text(FIRST)
        other_file.chmod(0o600)
        self.key_file.symlink_to(other_file)
        self.assertEqual(self.client.get(ADMIN_PATH, headers=HEADERS).status_code, 503)
        self.assertEqual(
            self.client.delete(ADMIN_PATH, headers=HEADERS).status_code, 200
        )
        self.assertEqual(other_file.read_text(), FIRST)
        save_hub_token(self.key_file, SECOND)
        self.key_file.chmod(0o644)
        self.assertEqual(self.client.get(ADMIN_PATH, headers=HEADERS).status_code, 503)

    def test_downloader_uses_the_saved_key_only_for_the_official_hub(self):
        # Exercise the actual downloader subprocess program without calling the network.
        with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
            from provision_models import HF_PROGRAM

        hub = ModuleType("huggingface_hub")
        hub.snapshot_download = Mock(return_value=str(self.directory))
        revision = "b" * 40
        args = [
            "-c",
            "owner/checkpoint",
            revision,
            "/assets",
            str(self.directory),
            "*.bin",
        ]
        expected = {
            "repo_id": "owner/checkpoint",
            "revision": revision,
            "cache_dir": "/assets",
            "allow_patterns": ["*.bin"],
        }
        with (
            patch.dict(sys.modules, {"huggingface_hub": hub}),
            patch.dict(
                os.environ,
                {"HF_ENDPOINT": "https://example.invalid", "HF_TOKEN": SECOND},
            ),
            patch.object(sys, "argv", args),
            patch("builtins.print"),
        ):
            exec(HF_PROGRAM, {})
            hub.snapshot_download.assert_called_once_with(**expected)
            hub.snapshot_download.reset_mock()
            save_hub_token(self.key_file, FIRST)
            exec(HF_PROGRAM, {})
            hub.snapshot_download.assert_called_once_with(
                **expected, token=FIRST, endpoint="https://huggingface.co"
            )
        self.assertNotIn(FIRST, args)
