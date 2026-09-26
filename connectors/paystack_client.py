"""
Minimal Paystack API client — stdlib only (urllib.request), zero extra
dependencies, so this runs on a bare Python install with no pip
installs beyond what the rest of the repo already needs.

Endpoints used, confirmed against Paystack's public docs as of this
build (paystack.com/docs/api/transaction, paystack.com/docs/api/refund):

    POST /transaction/initialize     -- create a checkout session
    GET  /transaction/verify/:ref    -- read authoritative transaction state
    POST /refund                     -- submit a refund
    GET  /refund                     -- list refunds (used for our own
                                         idempotency check, since Paystack
                                         does not document a native
                                         Idempotency-Key header the way
                                         Stripe does)
    GET  /refund/:id                 -- fetch a single refund's state

IMPORTANT: this client has not been run against a live Paystack account
by the assistant that wrote it — there is no network access in that
environment. It was built from Paystack's public documentation. Treat
the first real run as the actual test, not this code's existence.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    def __init__(self, status_code: int, body: dict[str, Any]):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Paystack API error {status_code}: {body.get('message', body)}")


class PaystackClient:
    def __init__(self, secret_key: str):
        if not secret_key.startswith("sk_"):
            raise ValueError(
                "This looks like a public key (pk_...), not a secret key. "
                "The connector needs the secret key (sk_test_... or sk_live_...) "
                "for server-side calls."
            )
        self.secret_key = secret_key

    def _request(
        self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None
    ) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if query:
                url = f"{url}?{query}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.secret_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = json.loads(exc.read().decode("utf-8"))
            raise PaystackError(exc.code, error_body) from exc

    # ---- Transactions ----

    def initialize_transaction(self, email: str, amount_kobo: int) -> dict[str, Any]:
        """Create a checkout session. amount_kobo is in the smallest currency
        unit (kobo for NGN: ₦100 = 10000)."""
        return self._request(
            "POST", "/transaction/initialize", body={"email": email, "amount": amount_kobo}
        )

    def verify_transaction(self, reference: str) -> dict[str, Any]:
        """Authoritative read of a transaction's current state. This is our
        SNAPSHOT and (called again later) CONFIRM step."""
        return self._request("GET", f"/transaction/verify/{reference}")

    # ---- Refunds ----

    def list_refunds_for_transaction(self, reference: str) -> list[dict[str, Any]]:
        """Our own idempotency guard: list refunds and filter client-side by
        transaction reference, since the query-param filter for this isn't
        fully confirmed from documentation alone. Defensive by design —
        worth double-checking against the live API response shape on first
        real run."""
        result = self._request("GET", "/refund", params={"transaction": reference})
        refunds = result.get("data", [])
        matching = []
        for r in refunds:
            txn = r.get("transaction")
            txn_ref = txn.get("reference") if isinstance(txn, dict) else txn
            if txn_ref == reference:
                matching.append(r)
        return matching

    def create_refund(self, reference: str, amount_kobo: Optional[int] = None) -> dict[str, Any]:
        body: dict[str, Any] = {"transaction": reference}
        if amount_kobo is not None:
            body["amount"] = amount_kobo
        return self._request("POST", "/refund", body=body)

    def fetch_refund(self, refund_id: str) -> dict[str, Any]:
        return self._request("GET", f"/refund/{refund_id}")
