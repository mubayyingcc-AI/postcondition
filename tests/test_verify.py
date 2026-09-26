"""
Adversarial test suite — the honesty check the original plan promised.

Uses stdlib unittest (not pytest) deliberately: this repo should run
its own test suite with zero external dependencies beyond
`cryptography`, so a contributor — or a skeptical prospective customer
— can clone it and run the tests without a package manager reaching
the internet.

Run:  python -m unittest tests.test_verify -v
(pytest also discovers and runs these fine, if you have it installed)
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcheck.crypto import generate_keypair, seal_receipt, verify_receipt
from pcheck.schema import validate_schema, RECEIPT_STATES


def _base_draft():
    return {
        "receipt_version": "1.0",
        "receipt_id": "pcr_test_0001",
        "tenant_id": "test-tenant",
        "connector_id": "test-connector",
        "action_type": "refund",
        "canonical_action_digest": "sha256:" + "0" * 64,
        "policy_id": "test-policy",
        "invariant_result": "PASS",
        "postcondition_result": "PASS",
        "issued_at": "2026-09-26T00:00:00Z",
        "scope_of_claim": {"asserts": ["a"], "does_not_assert": ["b"]},
        "status": "EXECUTED_AND_VERIFIED",
    }


class TestPostconditionVerifier(unittest.TestCase):
    def test_valid_receipt_verifies(self):
        priv, pub = generate_keypair()
        sealed = seal_receipt(_base_draft(), priv, "key-1")
        result = verify_receipt(sealed, pub)
        self.assertTrue(result.valid, result.reasons)

    def test_tampered_field_fails(self):
        priv, pub = generate_keypair()
        sealed = seal_receipt(_base_draft(), priv, "key-1")
        sealed["postcondition_result"] = "FAIL"
        result = verify_receipt(sealed, pub)
        self.assertFalse(result.valid)
        self.assertIn("signature", result.reasons[0])

    def test_wrong_key_fails(self):
        priv, _ = generate_keypair()
        _, other_pub = generate_keypair()
        sealed = seal_receipt(_base_draft(), priv, "key-1")
        result = verify_receipt(sealed, other_pub)
        self.assertFalse(result.valid)

    def test_reordered_keys_still_verify(self):
        """Canonicalization must be order-independent — a receipt
        re-serialized with different key order must still verify. This
        is what makes the schema interoperable across implementations."""
        priv, pub = generate_keypair()
        sealed = seal_receipt(_base_draft(), priv, "key-1")
        reordered = json.loads(json.dumps(sealed))
        reversed_dict = dict(reversed(list(reordered.items())))
        result = verify_receipt(reversed_dict, pub)
        self.assertTrue(result.valid)

    def test_missing_required_field_rejected(self):
        draft = _base_draft()
        del draft["policy_id"]
        errors = validate_schema({**draft, "signature": "", "signer_key_id": "k"})
        self.assertTrue(any("policy_id" in e for e in errors))

    def test_invalid_status_rejected(self):
        draft = _base_draft()
        draft["status"] = "TOTALLY_FINE_TRUST_ME"
        errors = validate_schema({**draft, "signature": "", "signer_key_id": "k"})
        self.assertTrue(any("not one of the seven" in e for e in errors))

    def test_unknown_outcome_is_a_valid_state(self):
        self.assertIn("UNKNOWN_OUTCOME", RECEIPT_STATES)

    def test_cannot_seal_schema_invalid_receipt(self):
        draft = _base_draft()
        del draft["receipt_id"]
        priv, _ = generate_keypair()
        with self.assertRaises(ValueError):
            seal_receipt(draft, priv, "key-1")

    def test_signer_key_id_swap_does_not_bypass_pubkey_check(self):
        """signer_key_id is metadata about who claims to have signed, not
        part of the signed payload — a caller must always supply the
        public key it trusts, never trust the receipt to name its own
        verifier key. This test documents that verify_receipt() only
        ever checks against the key the caller explicitly passed in."""
        priv, pub = generate_keypair()
        sealed = seal_receipt(_base_draft(), priv, "original-key-id")
        sealed["signer_key_id"] = "a-different-key-id-claimed-by-attacker"
        result = verify_receipt(sealed, pub)
        self.assertTrue(result.valid)  # valid because `pub` is still correct


if __name__ == "__main__":
    unittest.main()
