"""
Tests for the hosted service, run against SQLite and a fake Paystack client.
They prove the service's own behavior (auth, receipt chaining, webhook
signature checking). They do not prove Postgres works or that Paystack's
real webhook payload matches what the handler expects; only a deploy can.
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcheck.crypto import generate_keypair, public_from_private, verify_receipt
from service.app import create_app
from service.store import SQLiteStore

TOKEN = "test-token"
PAYSTACK_SECRET = "sk_test_fake_for_hmac"


class FakePaystack:
    """refund_status is shared across instances so a test can 'settle' the refund between calls."""

    refund_status = "pending"

    def __init__(self, secret):
        pass

    def verify_transaction(self, reference):
        return {"data": {"status": "success", "reference": reference}}

    def list_refunds_for_transaction(self, reference):
        return [{"id": 777, "status": FakePaystack.refund_status}] if FakePaystack.refund_status != "none" else []

    def create_refund(self, reference, amount_kobo=None):
        FakePaystack.refund_status = "pending"
        return {"data": {"id": 777, "status": "pending"}}

    def fetch_refund(self, refund_id):
        return {"data": {"id": refund_id, "status": FakePaystack.refund_status}}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        FakePaystack.refund_status = "none"
        self.priv, self.pub = generate_keypair()
        self.db = os.path.join(tempfile.mkdtemp(), "t.db")
        self.store = SQLiteStore(self.db)
        self.app = create_app(
            store=self.store,
            config={
                "signing_key": self.priv,
                "signing_key_id": "test-key",
                "api_token": TOKEN,
                "paystack_secret": PAYSTACK_SECRET,
                "poll_seconds": 0,
            },
            paystack_client_factory=FakePaystack,
        )
        self.c = self.app.test_client()
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def _refund(self, ref="T1"):
        return self.c.post("/v1/paystack/refunds", json={"reference": ref}, headers=self.auth)

    # ---- public ----

    def test_landing_page_and_health(self):
        self.assertEqual(self.c.get("/healthz").get_json(), {"ok": True})
        page = self.c.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Did the agent", page.data)

    def test_keys_endpoint_publishes_public_key_only(self):
        body = self.c.get("/v1/keys").get_json()
        self.assertEqual(body["keys"][0]["public_key"], public_from_private(self.priv))
        self.assertNotIn(self.priv, json.dumps(body))

    def test_landing_page_sample_verifies_and_tamper_fails(self):
        page = self.c.get("/").data.decode()
        start = page.index('id="sample" type="application/json">') + len('id="sample" type="application/json">')
        sample = json.loads(page[start : page.index("</script>", start)].replace("<\\/", "</"))
        ok = self.c.post("/v1/verify", json=sample).get_json()
        self.assertTrue(ok["valid"], ok)
        sample["receipt"]["postcondition_result"] = "FAIL"
        bad = self.c.post("/v1/verify", json=sample).get_json()
        self.assertFalse(bad["valid"])

    def test_verify_uses_published_key_when_none_supplied(self):
        r = self._refund().get_json()
        res = self.c.post("/v1/verify", json={"receipt": r}).get_json()
        self.assertTrue(res["valid"])
        self.assertEqual(res["key_source"], "published")

    def test_verify_rejects_unknown_signer_without_key(self):
        r = self._refund().get_json()
        r["signer_key_id"] = "someone-else"
        res = self.c.post("/v1/verify", json={"receipt": r}).get_json()
        self.assertFalse(res["valid"])

    def test_verify_bad_body(self):
        self.assertEqual(self.c.post("/v1/verify", json={"nope": 1}).status_code, 400)

    # ---- auth ----

    def test_authenticated_endpoints_reject_missing_and_wrong_token(self):
        self.assertEqual(self.c.post("/v1/paystack/refunds", json={"reference": "T1"}).status_code, 401)
        self.assertEqual(
            self.c.post("/v1/paystack/refunds", json={"reference": "T1"}, headers={"Authorization": "Bearer nope"}).status_code,
            401,
        )
        self.assertEqual(self.c.get("/v1/receipts").status_code, 401)

    def test_fails_closed_when_token_not_configured(self):
        app = create_app(store=self.store, config={"api_token": None, "signing_key": self.priv})
        self.assertEqual(app.test_client().get("/v1/receipts", headers=self.auth).status_code, 503)

    # ---- receipts ----

    def test_refund_receipt_is_signed_stored_and_verifiable(self):
        resp = self._refund("T1")
        self.assertEqual(resp.status_code, 201)
        r = resp.get_json()
        self.assertEqual(r["status"], "UNKNOWN_OUTCOME")  # refund still pending, poll_seconds=0
        self.assertTrue(verify_receipt(r, public_from_private(self.priv)).valid)
        fetched = self.c.get(f"/v1/receipts/{r['receipt_id']}", headers=self.auth).get_json()
        self.assertEqual(fetched["receipt_id"], r["receipt_id"])

    def test_recheck_issues_new_receipt_that_supersedes_the_old(self):
        first = self._refund("T2").get_json()
        FakePaystack.refund_status = "processed"
        resp = self.c.post(f"/v1/receipts/{first['receipt_id']}/recheck", headers=self.auth)
        self.assertEqual(resp.status_code, 201)
        second = resp.get_json()
        self.assertEqual(second["status"], "EXECUTED_AND_VERIFIED")
        self.assertEqual(second["supersedes"], first["receipt_id"])
        # supersedes is inside the signed payload, so it cannot be rewritten afterwards
        self.assertTrue(verify_receipt(second, public_from_private(self.priv)).valid)
        second["supersedes"] = "pcr_forged"
        self.assertFalse(verify_receipt(second, public_from_private(self.priv)).valid)
        # the original is kept, not overwritten
        self.assertEqual(
            self.c.get(f"/v1/receipts/{first['receipt_id']}", headers=self.auth).get_json()["status"], "UNKNOWN_OUTCOME"
        )

    def test_cannot_recheck_an_already_superseded_receipt(self):
        first = self._refund("T3").get_json()
        self.c.post(f"/v1/receipts/{first['receipt_id']}/recheck", headers=self.auth)
        again = self.c.post(f"/v1/receipts/{first['receipt_id']}/recheck", headers=self.auth)
        self.assertEqual(again.status_code, 409)

    def test_missing_reference_is_a_400(self):
        self.assertEqual(self.c.post("/v1/paystack/refunds", json={}, headers=self.auth).status_code, 400)

    # ---- webhook ----

    def _webhook(self, payload, secret=PAYSTACK_SECRET, sign=True):
        raw = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if sign:
            headers["x-paystack-signature"] = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
        return self.c.post("/webhooks/paystack", data=raw, headers=headers)

    def test_webhook_rejects_bad_or_missing_signature_but_records_it(self):
        self.assertEqual(self._webhook({"event": "refund.processed"}, sign=False).status_code, 401)
        self.assertEqual(self._webhook({"event": "refund.processed"}, secret="wrong").status_code, 401)
        n = self.store._conn.execute("SELECT COUNT(*) FROM events WHERE signature_valid=0").fetchone()[0]
        self.assertEqual(n, 2)

    def test_webhook_refund_event_rechecks_open_receipt(self):
        first = self._refund("T4").get_json()
        FakePaystack.refund_status = "processed"
        resp = self._webhook({"event": "refund.processed", "data": {"transaction_reference": "T4"}})
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["action"], "rechecked")
        self.assertEqual(body["status"], "EXECUTED_AND_VERIFIED")
        latest = self.store.latest_for_reference("T4")
        self.assertEqual(latest["supersedes"], first["receipt_id"])

    def test_duplicate_webhook_does_not_issue_another_receipt(self):
        self._refund("T5")
        FakePaystack.refund_status = "processed"
        payload = {"event": "refund.processed", "data": {"transaction_reference": "T5"}}
        self._webhook(payload)
        second = self._webhook(payload).get_json()
        self.assertEqual(second["action"], "no open receipt for this transaction")
        count = self.store._conn.execute("SELECT COUNT(*) FROM receipts WHERE reference='T5'").fetchone()[0]
        self.assertEqual(count, 2)

    def test_webhook_ignores_unrelated_events_and_unknown_transactions(self):
        self.assertEqual(self._webhook({"event": "charge.success", "data": {}}).get_json()["action"], "ignored")
        self.assertEqual(
            self._webhook({"event": "refund.processed", "data": {"transaction_reference": "NOPE"}}).get_json()["action"],
            "no open receipt for this transaction",
        )


if __name__ == "__main__":
    unittest.main()
