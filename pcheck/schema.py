"""
The Postcondition receipt schema.

A receipt is a JSON object recording what an authoritative system
confirmed after an agent action — not what the agent claimed. See
SPEC.md at the repo root for the full field-by-field rationale.
"""

from __future__ import annotations

import json
from typing import Any


# The seven bounded outcomes a receipt may declare. UNKNOWN_OUTCOME is
# not a failure mode to hide — it is a first-class, honest result for
# "we could not establish what happened" (e.g. a timeout after submit,
# before the post-state read could confirm or deny the write).
RECEIPT_STATES = frozenset(
    {
        "ALLOWED_NOT_EXECUTED",
        "EXECUTED_AND_VERIFIED",
        "EXECUTED_BUT_POSTCONDITION_FAILED",
        "REJECTED_BY_POLICY",
        "CONFLICT_DETECTED",
        "COMPENSATION_REQUIRED",
        "UNKNOWN_OUTCOME",
    }
)

# Fields that must be present for a receipt to be schema-valid.
# "signature" and "signer_key_id" are validated separately by crypto.py
# since they are outside the signed payload by definition.
REQUIRED_FIELDS = (
    "receipt_version",
    "receipt_id",
    "tenant_id",
    "connector_id",
    "action_type",
    "canonical_action_digest",
    "policy_id",
    "invariant_result",
    "postcondition_result",
    "issued_at",
    "scope_of_claim",
    "status",
)

# Fields excluded from the signed payload — added after signing, or
# meaningful only to the verifier, never to the signature itself.
UNSIGNED_FIELDS = ("signature", "signer_key_id")


def canonicalize(receipt: dict[str, Any]) -> bytes:
    """
    Produce the exact byte sequence that was (or should be) signed.

    Canonicalization rule: drop unsigned fields, sort keys
    lexicographically, use compact separators, UTF-8 encode. This is a
    simplified relative of RFC 8785 (JCS) — sufficient here because
    receipt payloads are flat-ish JSON with no floats that need
    canonical number formatting. A receipt signed by one
    implementation must verify under any other that follows this same
    rule; that interoperability is the entire point of publishing it.
    """
    payload = {k: v for k, v in receipt.items() if k not in UNSIGNED_FIELDS}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def validate_schema(receipt: dict[str, Any]) -> list[str]:
    """
    Return a list of schema errors (empty list = schema-valid).

    This checks shape, not truth: a schema-valid receipt can still
    have a bad signature, be stale, or assert something false. Those
    are separate checks — see crypto.py and cli.py.
    """
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in receipt:
            errors.append(f"missing required field: {field}")

    if "status" in receipt and receipt["status"] not in RECEIPT_STATES:
        errors.append(
            f"status {receipt['status']!r} is not one of the seven "
            f"declared states: {sorted(RECEIPT_STATES)}"
        )

    scope = receipt.get("scope_of_claim")
    if scope is not None:
        if not isinstance(scope, dict):
            errors.append("scope_of_claim must be an object")
        else:
            for key in ("asserts", "does_not_assert"):
                if key not in scope:
                    errors.append(f"scope_of_claim missing '{key}'")
                elif not isinstance(scope[key], list):
                    errors.append(f"scope_of_claim.{key} must be a list")

    for field in UNSIGNED_FIELDS:
        if field not in receipt:
            errors.append(f"missing required field: {field}")

    return errors
