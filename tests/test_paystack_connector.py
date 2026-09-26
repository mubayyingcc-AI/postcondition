"""
Tests for the connector's decision logic — SNAPSHOT/COMMIT/CONFIRM/CHECK
branching — against a fake Paystack client. This does NOT prove the
real Paystack API behaves as documented (only a live run can prove
that); it proves the connector correctly maps every documented
response shape onto the right receipt state, which is the part that's
under our control and worth guarding with a regression test.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.paystack_refund import run
from pcheck.crypto import generate_keypair, verify_receipt


class FakeClient:
    """Stands in for PaystackClient. Configure .transactions and .refunds
    dicts to script what each call returns."""

    def __init__(self, transaction_data, refund_after_create=None, existing_refunds=None, refund_after_fetch=None):
        self._transaction_data = transaction_data
        self._refund_after_create = refund_after_create
        self._existing_refunds = existing_refunds or []
        self._refund_after_fetch = refund_after_fetch or refund_after_create
        self.create_refund_calls = 0

    def verify_transaction(self, reference):
        return {"data": self._transaction_data}

    def list_refunds_for_transaction(self, reference):
        return self._existing_refunds

    def create_refund(self, reference, amount_kobo=None):
        self.create_refund_calls += 1
        return {"data": self._refund_after_create}

    def fetch_refund(self, refund_id):
        return {"data": self._refund_after_fetch}


def _keys():
    return generate_keypair()


class TestPaystackConnector(unittest.TestCase):
    def test_transaction_not_successful_rejects_before_refund_attempt(self):
        priv, pub = _keys()
        client = FakeClient(transaction_data={"status": "abandoned"})
        receipt = run("ref_1", None, client, priv, "test-key")
        self.assertEqual(receipt["status"], "REJECTED_BY_POLICY")
        self.assertEqual(client.create_refund_calls, 0)
        self.assertTrue(verify_receipt(receipt, pub).valid)

    def test_successful_refund_maps_to_executed_and_verified(self):
        priv, pub = _keys()
        client = FakeClient(
            transaction_data={"status": "success"},
            refund_after_create={"id": "rfd_123", "status": "processed"},
        )
        receipt = run("ref_2", None, client, priv, "test-key")
        self.assertEqual(receipt["status"], "EXECUTED_AND_VERIFIED")
        self.assertEqual(receipt["postcondition_result"], "PASS")
        self.assertEqual(receipt["provider_operation_id"], "rfd_123")
        self.assertEqual(client.create_refund_calls, 1)
        self.assertTrue(verify_receipt(receipt, pub).valid)

    def test_failed_refund_maps_to_postcondition_failed(self):
        priv, pub = _keys()
        client = FakeClient(
            transaction_data={"status": "success"},
            refund_after_create={"id": "rfd_124", "status": "failed"},
        )
        receipt = run("ref_3", None, client, priv, "test-key")
        self.assertEqual(receipt["status"], "EXECUTED_BUT_POSTCONDITION_FAILED")
        self.assertEqual(receipt["postcondition_result"], "FAIL")

    def test_needs_attention_maps_to_compensation_required(self):
        priv, pub = _keys()
        client = FakeClient(
            transaction_data={"status": "success"},
            refund_after_create={"id": "rfd_125", "status": "needs-attention"},
        )
        receipt = run("ref_4", None, client, priv, "test-key")
        self.assertEqual(receipt["status"], "COMPENSATION_REQUIRED")

    def test_existing_refund_is_reused_not_duplicated(self):
        """The idempotency guard: if a refund already exists for this
        transaction, we must not submit a second one."""
        priv, pub = _keys()
        client = FakeClient(
            transaction_data={"status": "success"},
            existing_refunds=[{"id": "rfd_already_here", "status": "processed"}],
        )
        receipt = run("ref_5", None, client, priv, "test-key")
        self.assertEqual(client.create_refund_calls, 0, "must not submit a duplicate refund")
        self.assertEqual(receipt["provider_operation_id"], "rfd_already_here")
        self.assertEqual(receipt["status"], "EXECUTED_AND_VERIFIED")

    def test_receipt_scope_of_claim_never_overclaims_notification(self):
        priv, pub = _keys()
        client = FakeClient(
            transaction_data={"status": "success"},
            refund_after_create={"id": "rfd_126", "status": "processed"},
        )
        receipt = run("ref_6", None, client, priv, "test-key")
        does_not_assert = " ".join(receipt["scope_of_claim"]["does_not_assert"])
        self.assertIn("notified", does_not_assert)


if __name__ == "__main__":
    unittest.main()
