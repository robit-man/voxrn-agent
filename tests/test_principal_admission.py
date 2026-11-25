import unittest
from unittest.mock import patch

from service.wire import Admission, Principal, Problem


class PrincipalAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_owner_cannot_consume_another_owners_slot(self):
        with patch.dict("os.environ", {"VOXRN_PER_PRINCIPAL_SLOTS": "1"}):
            control = Admission(2)
        principal = Principal("test", "tenant", "one", frozenset())
        other = Principal("test", "tenant", "two", frozenset())
        async with control.use(principal=principal):
            with self.assertRaises(Problem) as denied:
                async with control.use(principal=principal):
                    self.fail("The first principal exceeded its quota.")
            self.assertEqual(denied.exception.code, "principal_capacity_exhausted")
            async with control.use(principal=other):
                self.assertEqual(control.active, 2)
        self.assertEqual(control.active, 0)
        self.assertEqual(control.owners, {})
