"""
Regression test for a real bug caught on the first live run: Paystack's
refund objects carry `transaction` as a numeric transaction ID, not the
reference string — the actual reference lives in a separate
`transaction_reference` field. Pins the fix against the real response
shape captured 2026-09-27 (see connectors/README.md history / commit
message), not a re-guess.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.paystack_client import PaystackClient

# Real response shape, captured live via `python -m connectors.inspect_refunds`
REAL_REFUND_LIST_RESPONSE = {
    "status": True,
    "message": "Refunds retrieved",
    "data": [
        {
            "transaction": 6599191638,  # numeric transaction ID — NOT the reference
            "id": 18443530,
            "status": "pending",
            "transaction_reference": "T229827652905019",  # the actual reference string
        }
    ],
    "meta": {"total": 1},
}


class TestPaystackClientRefundFiltering(unittest.TestCase):
    def test_finds_refund_by_transaction_reference_field(self):
        client = PaystackClient("sk_test_fake")
        with patch.object(client, "_request", return_value=REAL_REFUND_LIST_RESPONSE):
            matches = client.list_refunds_for_transaction("T229827652905019")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], 18443530)

    def test_does_not_match_on_the_numeric_transaction_id(self):
        """Guards against reintroducing the original bug: filtering on the
        `transaction` field (a numeric ID) instead of `transaction_reference`
        (the string we're actually given) would silently find nothing."""
        client = PaystackClient("sk_test_fake")
        with patch.object(client, "_request", return_value=REAL_REFUND_LIST_RESPONSE):
            matches = client.list_refunds_for_transaction("6599191638")
        self.assertEqual(matches, [])

    def test_no_match_for_unrelated_reference(self):
        client = PaystackClient("sk_test_fake")
        with patch.object(client, "_request", return_value=REAL_REFUND_LIST_RESPONSE):
            matches = client.list_refunds_for_transaction("SOME_OTHER_REFERENCE")
        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
