import unittest

from service.trace_context import CURRENT_TRACE, provider_headers, start_span


class TraceContextTests(unittest.TestCase):
    def test_valid_trace_keeps_trace_id_and_allocates_fresh_span(self):
        parent = b"00-0637fd13875a4292bccfc55d904e069d-fbe947114cf540ed-01"
        child = start_span([(b"traceparent", parent)])
        self.assertEqual(child.split("-")[1], parent.split(b"-")[1].decode())
        self.assertNotEqual(child.split("-")[2], parent.split(b"-")[2].decode())
        self.assertTrue(child.endswith("-01"))
        token = CURRENT_TRACE.set(child)
        try:
            self.assertEqual(provider_headers(), {"traceparent": child})
        finally:
            CURRENT_TRACE.reset(token)

    def test_invalid_and_duplicate_values_start_distinct_traces(self):
        parent = b"00-0637fd13875a4292bccfc55d904e069d-fbe947114cf540ed-01"
        duplicate = [(b"traceparent", parent)] * 2
        for attributes in (
            duplicate,
            [(b"traceparent", b"00-" + b"0" * 32 + b"-" + b"a" * 16 + b"-01")],
        ):
            value = start_span(attributes)
            self.assertNotEqual(value.split("-")[1], parent.split(b"-")[1].decode())
            self.assertTrue(value.endswith("-00"))
