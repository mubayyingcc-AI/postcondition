"""
pcheck — the Postcondition verifier.

Core library for signing and verifying Postcondition receipts:
the signed record of what an authoritative system actually confirmed
after an AI agent action, as distinct from what the agent claimed.

Pipeline stage this package implements: CHECK + SEAL (verification and
signed-receipt issuance). INTAKE, RULING, SNAPSHOT, BIND, COMMIT and
CONFIRM — the parts that require a live connector to a real system of
record — are not implemented here yet; see README.md.
"""

from .schema import (
    RECEIPT_STATES,
    REQUIRED_FIELDS,
    canonicalize,
    validate_schema,
)
from .crypto import generate_keypair, seal_receipt, verify_receipt

__all__ = [
    "RECEIPT_STATES",
    "REQUIRED_FIELDS",
    "canonicalize",
    "validate_schema",
    "generate_keypair",
    "seal_receipt",
    "verify_receipt",
]

__version__ = "0.1.0"
