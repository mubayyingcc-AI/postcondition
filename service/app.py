"""
Postcondition hosted service.

Public (no auth):
    GET  /                      landing page + live verifier
    POST /v1/verify             verify a receipt (given key, or a key this service publishes)
    GET  /v1/keys               public keys this service signs with
    GET  /healthz

Authenticated (Authorization: Bearer $POSTCONDITION_API_TOKEN):
    POST /v1/paystack/refunds           run the Paystack refund connector, store + return a sealed receipt
    GET  /v1/receipts                   list recent receipts
    GET  /v1/receipts/<id>              fetch one
    POST /v1/receipts/<id>/recheck      re-read the provider, issue a NEW receipt that supersedes this one

Signed by Paystack (x-paystack-signature, HMAC-SHA512 of the raw body):
    POST /webhooks/paystack     refund events; rechecks the latest UNKNOWN_OUTCOME receipt for that transaction

Configuration (environment):
    POSTCONDITION_SIGNING_KEY     base64 raw Ed25519 private key (python -m pcheck.cli keygen)
    POSTCONDITION_SIGNING_KEY_ID  label for that key, e.g. pc-2026-09
    POSTCONDITION_API_TOKEN       bearer token for the authenticated endpoints
    PAYSTACK_SECRET_KEY           sk_test_... (or sk_live_... later)
    DATABASE_URL                  postgres://... on Railway; omit locally for SQLite
    POSTCONDITION_POLL_SECONDS    how long a refund request waits for the provider (default 25)

Fail-closed: endpoints that need a setting that is missing return 503
instead of quietly using a default.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Flask, jsonify, request, send_from_directory

from connectors.paystack_client import PaystackClient, PaystackError
from connectors.paystack_refund import run as run_refund
from pcheck.crypto import public_from_private, verify_receipt
from service.store import make_store

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    store=None,
    config: Optional[dict[str, Any]] = None,
    paystack_client_factory: Optional[Callable[[str], Any]] = None,
) -> Flask:
    cfg = {
        "signing_key": os.environ.get("POSTCONDITION_SIGNING_KEY"),
        "signing_key_id": os.environ.get("POSTCONDITION_SIGNING_KEY_ID", "pc-key-1"),
        "api_token": os.environ.get("POSTCONDITION_API_TOKEN"),
        "paystack_secret": os.environ.get("PAYSTACK_SECRET_KEY"),
        "poll_seconds": int(os.environ.get("POSTCONDITION_POLL_SECONDS", "25")),
    }
    cfg.update(config or {})
    store = store or make_store(os.environ.get("DATABASE_URL"))
    make_client = paystack_client_factory or PaystackClient

    app = Flask(__name__)

    # ---- helpers ----

    def published_keys() -> dict[str, str]:
        if not cfg["signing_key"]:
            return {}
        return {cfg["signing_key_id"]: public_from_private(cfg["signing_key"])}

    def unavailable(what: str):
        return jsonify({"error": f"{what} is not configured on this server"}), 503

    def authorized() -> Optional[tuple]:
        """Return an error response if the caller is not allowed, else None."""
        if not cfg["api_token"]:
            return unavailable("POSTCONDITION_API_TOKEN")
        header = request.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied.encode(), cfg["api_token"].encode()):
            return jsonify({"error": "missing or invalid bearer token"}), 401
        return None

    def issue(reference: str, amount_kobo: Optional[int], supersedes: Optional[str], poll_seconds: int):
        client = make_client(cfg["paystack_secret"])
        receipt = run_refund(
            reference,
            amount_kobo,
            client,
            cfg["signing_key"],
            cfg["signing_key_id"],
            supersedes=supersedes,
            poll_seconds=poll_seconds,
        )
        store.save_receipt(receipt, reference)
        return receipt

    def need_signing_and_paystack():
        if not cfg["signing_key"]:
            return unavailable("POSTCONDITION_SIGNING_KEY")
        if not cfg["paystack_secret"]:
            return unavailable("PAYSTACK_SECRET_KEY")
        return None

    # ---- public ----

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/<path:filename>")
    def static_asset(filename: str):
        # Narrow on purpose: only the specific logo files the page actually
        # references, not an open directory listing of service/static/.
        allowed = {"logo.png", "logo-mark.png", "logo-icon.png", "logo-lockup.png", "logo-full.png"}
        if filename not in allowed:
            return jsonify({"error": "not found"}), 404
        return send_from_directory(STATIC_DIR, filename)

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.get("/v1/keys")
    def keys():
        return jsonify(
            {"keys": [{"key_id": k, "algorithm": "Ed25519", "public_key": v} for k, v in published_keys().items()]}
        )

    @app.post("/v1/verify")
    def verify():
        body = request.get_json(silent=True) or {}
        receipt = body.get("receipt")
        if not isinstance(receipt, dict):
            return jsonify({"error": "body must be JSON with a 'receipt' object"}), 400

        public_key = body.get("public_key")
        key_source = "supplied"
        if not public_key:
            public_key = published_keys().get(receipt.get("signer_key_id"))
            key_source = "published"
            if not public_key:
                return (
                    jsonify(
                        {
                            "valid": False,
                            "reasons": ["no public_key supplied and signer_key_id is not a key this service publishes"],
                        }
                    ),
                    200,
                )

        result = verify_receipt(receipt, public_key)
        return jsonify(
            {
                "valid": result.valid,
                "reasons": result.reasons,
                "key_source": key_source,
                "status": receipt.get("status"),
                "note": "A valid signature means the receipt is unaltered and signed by that key. "
                "Read scope_of_claim.does_not_assert for what it does not prove.",
            }
        )

    # ---- authenticated ----

    @app.post("/v1/paystack/refunds")
    def create_refund_receipt():
        denied = authorized() or need_signing_and_paystack()
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        reference = body.get("reference")
        if not isinstance(reference, str) or not reference:
            return jsonify({"error": "'reference' (Paystack transaction reference) is required"}), 400
        amount = body.get("amount_kobo")
        if amount is not None and not isinstance(amount, int):
            return jsonify({"error": "'amount_kobo' must be an integer"}), 400
        try:
            receipt = issue(reference, amount, None, cfg["poll_seconds"])
        except PaystackError as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify(receipt), 201

    @app.get("/v1/receipts")
    def list_receipts():
        denied = authorized()
        if denied:
            return denied
        return jsonify({"receipts": store.list_receipts()})

    @app.get("/v1/receipts/<receipt_id>")
    def get_receipt(receipt_id: str):
        denied = authorized()
        if denied:
            return denied
        row = store.get_receipt(receipt_id)
        if not row:
            return jsonify({"error": "no such receipt"}), 404
        return jsonify(row["receipt"])

    @app.post("/v1/receipts/<receipt_id>/recheck")
    def recheck(receipt_id: str):
        denied = authorized() or need_signing_and_paystack()
        if denied:
            return denied
        row = store.get_receipt(receipt_id)
        if not row:
            return jsonify({"error": "no such receipt"}), 404
        latest = store.latest_for_reference(row["reference"])
        if latest and latest["receipt_id"] != receipt_id:
            return (
                jsonify({"error": "this receipt has already been superseded", "latest_receipt_id": latest["receipt_id"]}),
                409,
            )
        try:
            receipt = issue(row["reference"], None, receipt_id, cfg["poll_seconds"])
        except PaystackError as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify(receipt), 201

    # ---- Paystack webhook ----

    def paystack_signature_ok(raw: bytes) -> bool:
        secret = cfg["paystack_secret"]
        supplied = request.headers.get("x-paystack-signature", "")
        if not secret or not supplied:
            return False
        expected = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, supplied)

    def reference_from_event(event: dict[str, Any]) -> Optional[str]:
        # Shape is taken from Paystack's API responses and docs, NOT confirmed against a
        # real delivered webhook yet. Every event is stored so the first real one can be
        # inspected and this function corrected, as happened with the refund list API.
        data = event.get("data") or {}
        ref = data.get("transaction_reference")
        if not ref and isinstance(data.get("transaction"), dict):
            ref = data["transaction"].get("reference")
        return ref if isinstance(ref, str) else None

    @app.post("/webhooks/paystack")
    def paystack_webhook():
        raw = request.get_data()
        valid = paystack_signature_ok(raw)
        store.save_event(raw.decode("utf-8", errors="replace"), valid)
        if not valid:
            return jsonify({"error": "invalid signature"}), 401

        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return jsonify({"ok": True, "action": "ignored: not JSON"}), 200

        name = str(event.get("event", ""))
        reference = reference_from_event(event)
        if not name.startswith("refund.") or not reference:
            return jsonify({"ok": True, "action": "ignored"}), 200

        latest = store.latest_for_reference(reference)
        if not latest or latest["status"] != "UNKNOWN_OUTCOME":
            return jsonify({"ok": True, "action": "no open receipt for this transaction"}), 200
        if not cfg["signing_key"]:
            return jsonify({"ok": True, "action": "signing key not configured"}), 200

        try:
            # The event says something changed, so one read is enough; do not hold Paystack's request open.
            receipt = issue(reference, None, latest["receipt_id"], 0)
        except PaystackError as exc:
            return jsonify({"ok": True, "action": f"recheck failed: {exc}"}), 200
        return jsonify({"ok": True, "action": "rechecked", "receipt_id": receipt["receipt_id"], "status": receipt["status"]}), 200

    return app
