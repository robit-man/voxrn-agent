import asyncio
import time
import unittest
from unittest.mock import patch

from service.tunnel_access import session_scope
from service.wire import Authorization


class ScopedSessionTests(unittest.TestCase):
    def test_legacy_admin_and_expiring_inference_keys(self):
        self.assertEqual(session_scope({}), "admin")
        self.assertEqual(
            session_scope({"scope": "inference", "expires_at": time.time() + 120}),
            "inference",
        )
        self.assertIsNone(
            session_scope({"scope": "inference", "expires_at": time.time() - 1})
        )
        self.assertIsNone(session_scope({"scope": "admin", "expires_at": False}))
        self.assertIsNone(session_scope({"scope": "unrecognized"}))

    def test_inference_scope_only_removes_management_authority(self):
        with patch.dict(
            "os.environ", {"VOXRN_DEV_TOKEN": "local-test-token-with-enough-bytes"}
        ):
            auth = Authorization("test")
            state = {"tunnel_subject": "a" * 32, "tunnel_scope": "inference"}
            principal = asyncio.run(auth.authenticate({}, scope={"state": state}))
            self.assertTrue(principal.scopes)
            self.assertFalse(
                any(value.endswith(":manage") for value in principal.scopes)
            )
            self.assertEqual(
                principal.scopes,
                {value for value in auth.scopes if not value.endswith(":manage")},
            )
            state["tunnel_scope"] = "admin"
            admin = asyncio.run(auth.authenticate({}, scope={"state": state}))
            self.assertEqual(admin.scopes, auth.scopes)
            self.assertEqual(admin.owner, principal.owner)
