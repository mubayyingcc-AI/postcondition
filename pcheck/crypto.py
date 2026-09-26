"""
Ed25519 signing and verification for Postcondition receipts.

Deliberately thin: SEAL (signing) and CHECK (verification) should be
auditable in a few minutes by someone who doesn't trust us. No custom
crypto, no key management service required to verify — a receipt and
a public key are sufficient to check a receipt entirely offline.
"""

from __future__ import annotations

import base64
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

from .schema import canonicalize, validate_schema


def generate_keypair() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64), both raw 32-byte Ed25519 keys."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(priv_bytes).decode("ascii"),
        base64.b64encode(pub_bytes).decode("ascii"),
    )


def seal_receipt(
    receipt: dict[str, Any], private_key_b64: str, signer_key_id: str
) -> dict[str, Any]:
    """
    SEAL stage: sign a receipt payload and attach signature + signer_key_id.

    Raises ValueError if the receipt is not schema-valid before signing
    — a malformed receipt should never be sealed, since a valid
    signature over a broken payload is worse than no receipt at all.
    """
    draft = dict(receipt)
    draft.setdefault("signature", "")
    draft.setdefault("signer_key_id", signer_key_id)
    errors = validate_schema(draft)
    if errors:
        raise ValueError(f"cannot seal an invalid receipt: {'; '.join(errors)}")

    priv_bytes = base64.b64decode(private_key_b64)
    private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)

    payload_bytes = canonicalize(receipt)
    signature = private_key.sign(payload_bytes)

    sealed = dict(receipt)
    sealed["signer_key_id"] = signer_key_id
    sealed["signature"] = base64.b64encode(signature).decode("ascii")
    return sealed


class VerificationResult:
    def __init__(self, valid: bool, reasons: list[str]):
        self.valid = valid
        self.reasons = reasons

    def __bool__(self) -> bool:
        return self.valid

    def __repr__(self) -> str:
        return f"VerificationResult(valid={self.valid}, reasons={self.reasons})"


def verify_receipt(
    receipt: dict[str, Any], public_key_b64: str
) -> VerificationResult:
    """
    CHECK stage: verify schema validity and signature.

    Does NOT check freshness, connector reachability, or whether the
    claimed postcondition is still true right now — a signature only
    proves the receipt is unaltered and was signed by the holder of
    this key at issuance time. See cli.py's `inspect` for freshness
    and scope-of-claim reporting.
    """
    reasons: list[str] = []

    schema_errors = validate_schema(receipt)
    if schema_errors:
        return VerificationResult(False, schema_errors)

    try:
        pub_bytes = base64.b64decode(public_key_b64)
        public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        payload_bytes = canonicalize(receipt)
        signature = base64.b64decode(receipt["signature"])
        public_key.verify(signature, payload_bytes)
    except InvalidSignature:
        return VerificationResult(False, ["signature does not match payload"])
    except Exception as exc:  # malformed base64, wrong key length, etc.
        return VerificationResult(False, [f"could not verify signature: {exc}"])

    return VerificationResult(True, reasons)
