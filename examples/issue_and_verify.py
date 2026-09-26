"""
Minimal end-to-end example: build a draft receipt, seal it, verify it,
then show a tampered copy correctly failing.

Run from the repo root:  python examples/issue_and_verify.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcheck.crypto import generate_keypair, seal_receipt, verify_receipt

private_key, public_key = generate_keypair()

draft = {
    "receipt_version": "1.0",
    "receipt_id": "pcr_example_0001",
    "tenant_id": "example-tenant",
    "connector_id": "example-connector",
    "action_type": "refund",
    "canonical_action_digest": "sha256:" + "1" * 64,
    "policy_id": "example-policy",
    "invariant_result": "PASS",
    "postcondition_result": "PASS",
    "issued_at": "2026-09-26T00:00:00Z",
    "scope_of_claim": {
        "asserts": ["this is a worked example, not a real transaction"],
        "does_not_assert": ["anything about a real system"],
    },
    "status": "EXECUTED_AND_VERIFIED",
}

sealed = seal_receipt(draft, private_key, signer_key_id="example-key-1")
print("Sealed receipt:")
print(sealed)

result = verify_receipt(sealed, public_key)
print(f"\nVerification of sealed receipt: valid={result.valid}")

tampered = dict(sealed)
tampered["postcondition_result"] = "FAIL"
tampered_result = verify_receipt(tampered, public_key)
print(f"Verification of tampered copy: valid={tampered_result.valid} reasons={tampered_result.reasons}")
