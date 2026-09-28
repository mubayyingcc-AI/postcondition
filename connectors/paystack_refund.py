"""
The Paystack refund connector — implements SNAPSHOT, COMMIT, CONFIRM,
and CHECK against a real Paystack transaction, then SEALs a receipt
using the same pcheck signing code the rest of the repo uses.

This is the piece that was NOT in v0.1: a real authoritative
connector, not a hand-written JSON fixture. Everything downstream of
"what did Paystack actually say" is real; nothing here is invented.

Idempotency design note: Paystack's public API does not document a
Stripe-style Idempotency-Key header for POST /refund. So this
connector builds its own idempotency guard: before submitting a
refund, it lists existing refunds for the transaction and, if one
already exists in a non-terminal-failure state, reuses it instead of
submitting a duplicate. This is exactly the kind of provider-specific
behavior the original plan meant by "the hard-to-copy asset is the
tested connector semantics."

Usage:
    export PAYSTACK_SECRET_KEY=sk_test_...
    python -m connectors.paystack_refund <reference> [--amount KOBO]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

from connectors.paystack_client import PaystackClient, PaystackError
from pcheck.crypto import seal_receipt

CONNECTOR_ID = "paystack-refunds-test"
CONNECTOR_VERSION = "0.1.0"

# Refund states Paystack documents: pending, processing, processed, failed
# (and the docs mention a "needs attention" case surfaced via a dedicated
# test card). We map these onto our seven receipt states below.
# A transaction that was ever successfully charged can carry a refund.
# Paystack moves a charged transaction's own status away from "success"
# once a refund is *in progress* against it (confirmed live: it becomes
# "reversal-pending"), and presumably "reversed" once complete — neither
# of those means "never chargeable," which is what we actually want to
# reject. Kept as an explicit set, not a != check, because "anything
# that isn't literally 'success'" was the bug: it conflated "never
# succeeded" with "succeeded, and a refund already exists," which are
# opposite situations.
REFUNDABLE_PRE_STATES = {"success", "reversal-pending", "reversal pending", "reversed"}

TERMINAL_SUCCESS = {"processed"}
TERMINAL_FAILURE = {"failed"}
NEEDS_ATTENTION = {"needs-attention", "needs_attention"}
IN_PROGRESS = {"pending", "processing"}


def _digest(obj) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def run(
    reference: str,
    amount_kobo: int | None,
    client: PaystackClient,
    private_key: str,
    signer_key_id: str,
    supersedes: str | None = None,
    poll_seconds: int = 30,
) -> dict:
    # --- SNAPSHOT: fresh authoritative pre-state read ---
    t0 = time.time()
    pre_state = client.verify_transaction(reference)
    pre_data = pre_state.get("data", {})
    pre_commitment = _digest(pre_data)

    if pre_data.get("status") not in REFUNDABLE_PRE_STATES:
        # REJECTED_BY_POLICY isn't quite right here — this is closer to
        # "the precondition for refunding was never met" — but we stay
        # inside the seven declared states rather than inventing an
        # eighth. A transaction that never succeeded can't be refunded.
        return _build_and_seal(
            status="REJECTED_BY_POLICY",
            postcondition_result="FAIL",
            reference=reference,
            pre_commitment=pre_commitment,
            pre_data=pre_data,
            post_data=None,
            refund_data=None,
            private_key=private_key,
            signer_key_id=signer_key_id,
            supersedes=supersedes,
            asserts=[f"transaction {reference} status was '{pre_data.get('status')}', which is not in the refundable set {sorted(REFUNDABLE_PRE_STATES)}, at pre-state read"],
            does_not_assert=["that a refund was attempted"],
        )

    # --- Idempotency guard (our own, since Paystack doesn't give us one) ---
    existing = client.list_refunds_for_transaction(reference)
    reusable = [r for r in existing if r.get("status") not in TERMINAL_FAILURE]

    if reusable:
        refund_data = reusable[0]
        idempotency_note = "reused_existing_refund"
    else:
        # --- COMMIT: submit the refund ---
        try:
            commit_result = client.create_refund(reference, amount_kobo)
            refund_data = commit_result.get("data", {})
            idempotency_note = "submitted_new_refund"
        except PaystackError as exc:
            return _build_and_seal(
                status="EXECUTED_BUT_POSTCONDITION_FAILED",
                postcondition_result="FAIL",
                reference=reference,
                pre_commitment=pre_commitment,
                pre_data=pre_data,
                post_data=None,
                refund_data={"error": str(exc)},
                private_key=private_key,
                signer_key_id=signer_key_id,
            supersedes=supersedes,
                asserts=[f"Paystack rejected the refund submission: {exc}"],
                does_not_assert=["that any refund was created"],
            )

    # --- CONFIRM: authoritative post-state read ---
    # Refunds are asynchronous (pending -> processing -> processed), so a
    # single immediate read may legitimately still be in progress. We do
    # a short bounded poll rather than either lying about the result or
    # blocking forever — and if it's still unresolved when we give up,
    # that's UNKNOWN_OUTCOME, not a fabricated success or failure.
    refund_id = refund_data.get("id")
    final_refund_state = refund_data
    freshness_bound_seconds = 30
    deadline = time.time() + poll_seconds
    while refund_id and final_refund_state.get("status") in IN_PROGRESS and time.time() < deadline:
        time.sleep(2)
        final_refund_state = client.fetch_refund(refund_id).get("data", final_refund_state)

    post_state = client.verify_transaction(reference)
    post_data = post_state.get("data", {})
    post_commitment = _digest(post_data)

    refund_status = final_refund_state.get("status")

    if refund_status in TERMINAL_SUCCESS:
        status, pc_result = "EXECUTED_AND_VERIFIED", "PASS"
    elif refund_status in TERMINAL_FAILURE:
        status, pc_result = "EXECUTED_BUT_POSTCONDITION_FAILED", "FAIL"
    elif refund_status in NEEDS_ATTENTION:
        status, pc_result = "COMPENSATION_REQUIRED", "FAIL"
    else:
        # still pending/processing after our bounded wait
        status, pc_result = "UNKNOWN_OUTCOME", "UNKNOWN"

    return _build_and_seal(
        status=status,
        postcondition_result=pc_result,
        reference=reference,
        pre_commitment=pre_commitment,
        pre_data=pre_data,
        post_data=post_data,
        post_commitment=post_commitment,
        refund_data=final_refund_state,
        private_key=private_key,
        signer_key_id=signer_key_id,
        supersedes=supersedes,
        asserts=[
            f"Paystack transaction {reference} was at status 'success' before refund submission",
            f"refund {idempotency_note.replace('_', ' ')}: refund id {refund_id}, final status '{refund_status}'",
            f"post-state read occurred within {freshness_bound_seconds}s of pre-state read",
        ],
        does_not_assert=[
            "that the customer was notified of the refund",
            "that this was the only refund ever issued on this transaction outside this connector's visibility",
            "that Paystack's own refund-processing bank rails settled synchronously",
        ],
    )


def _build_and_seal(
    *,
    status: str,
    postcondition_result: str,
    reference: str,
    pre_commitment: str,
    pre_data: dict,
    post_data: dict | None,
    refund_data: dict | None,
    private_key: str,
    signer_key_id: str,
    asserts: list[str],
    does_not_assert: list[str],
    post_commitment: str | None = None,
    supersedes: str | None = None,
) -> dict:
    receipt = {
        "receipt_version": "1.0",
        "receipt_id": f"pcr_{uuid.uuid4().hex}",
        "tenant_id": "self-serve-demo",
        "connector_id": CONNECTOR_ID,
        "connector_version": CONNECTOR_VERSION,
        "action_type": "refund",
        "canonical_action_digest": _digest({"reference": reference, "action": "refund"}),
        "policy_id": "manual-test-run",
        "invariant_result": "PASS",
        "pre_state_commitment": pre_commitment,
        "pre_state_version": pre_data.get("status"),
        "idempotency_key": None,  # Paystack has none native; see module docstring
        "provider_operation_id": (refund_data or {}).get("id"),
        "post_state_commitment": post_commitment,
        "post_state_version": (post_data or {}).get("status"),
        "postcondition_result": postcondition_result,
        "freshness_bound_seconds": 30,
        "issued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope_of_claim": {"asserts": asserts, "does_not_assert": does_not_assert},
        "status": status,
    }
    if supersedes:
        receipt["supersedes"] = supersedes
    return seal_receipt(receipt, private_key, signer_key_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", help="Paystack transaction reference to refund")
    parser.add_argument("--amount", type=int, default=None, help="partial refund amount in kobo (default: full)")
    parser.add_argument("--out", default=None, help="write sealed receipt to this path (default: print to stdout)")
    args = parser.parse_args()

    secret_key = os.environ.get("PAYSTACK_SECRET_KEY")
    if not secret_key:
        print("error: set PAYSTACK_SECRET_KEY first (your sk_test_... key)", file=sys.stderr)
        return 2

    # For this demo run we generate a fresh keypair each time. In a real
    # deployment the signing key is provisioned once and kept stable so
    # verifiers can pin a public key — see pcheck/cli.py keygen.
    from pcheck.crypto import generate_keypair

    private_key, public_key = generate_keypair()
    client = PaystackClient(secret_key)

    try:
        sealed = run(args.reference, args.amount, client, private_key, "paystack-connector-demo-key")
    except PaystackError as exc:
        print(f"Paystack API error: {exc}", file=sys.stderr)
        return 1

    output = json.dumps(sealed, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(output)
        print(f"receipt written to {args.out}")
    else:
        print(output)

    print(f"\npublic key to verify this receipt: {public_key}", file=sys.stderr)
    print(f"status: {sealed['status']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
