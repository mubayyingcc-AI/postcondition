# Connectors

The first real authoritative connector: **Paystack test-mode refunds**.

This is the piece the original v0.1 explicitly did not have — everything
here reads and writes a real (sandboxed) system of record, not a
hand-written fixture. Two scripts, zero extra dependencies beyond
what's already in this repo (`cryptography` for signing; the Paystack
HTTP calls use only Python's stdlib `urllib`).

**Honesty note:** this code was written from Paystack's public API
documentation, not run against a live account by whoever wrote it —
there's no network access in that environment. The decision logic
(`connectors/paystack_refund.py`'s SNAPSHOT/COMMIT/CONFIRM/CHECK
branching) is covered by `tests/test_paystack_connector.py` against a
fake client, which proves the *logic* is correct for every documented
response shape. It does not prove Paystack's real API actually returns
those shapes. Only a real run does that — which is exactly why this
folder exists as a two-step manual process rather than a "trust me"
claim.

## Step 1 — create a real test transaction

```bash
export PAYSTACK_SECRET_KEY=sk_test_...   # your Paystack TEST secret key
python -m connectors.create_test_transaction you@example.com 5000
```

This prints an `authorization_url`. Open it, pay with Paystack's
documented reusable success card:

| Field | Value |
|---|---|
| Card | `4084 0840 8408 4081` |
| Expiry | any future date |
| CVV | `408` |

(No PIN/OTP prompt expected — this is Paystack's "no validation,
reusable" success card per their test-payments docs.)

## Step 2 — run the actual connector

```bash
python -m connectors.paystack_refund <reference> --out receipt.json
```

This performs, against your real transaction:
1. **SNAPSHOT** — reads the transaction's real current state
2. an idempotency check — lists existing refunds so a retry doesn't
   double-refund (Paystack doesn't give us an `Idempotency-Key` header
   the way Stripe does, so this connector builds its own)
3. **COMMIT** — submits the refund
4. **CONFIRM** — polls the real refund + transaction state (refunds
   are asynchronous: `pending → processing → processed`)
5. **CHECK** — maps the real outcome onto one of the seven receipt
   states
6. **SEAL** — signs the receipt with `pcheck`'s existing Ed25519 code

## Step 3 — verify what you got

```bash
python -m pcheck.cli inspect receipt.json
```

The script also prints the public key to stderr — use it with
`pcheck verify` to confirm the signature independently.

## What to check for and report back

Since this hasn't been run live yet, the things most likely to need a
fix on first real contact with Paystack's API:
- Does `GET /refund?transaction=<ref>` actually filter server-side, or
  does it need the client-side filtering this code already does
  defensively?
- Does a refund on the success test card (`4084 0840 8408 4081`)
  resolve to `processed` quickly, or sit in `pending`/`processing`
  past the 30-second bound (in which case you'd correctly get
  `UNKNOWN_OUTCOME` — that's the connector being honest, not broken)?
- Try it once more with the documented "Needs attention" refund test
  card (`4084 0800 0067 1902`) to confirm `COMPENSATION_REQUIRED`
  actually fires.
