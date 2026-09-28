# Hosted service

Flask API + landing page + Paystack webhook receiver. See the docstring at the top of
`service/app.py` for every endpoint and environment variable.

## Run locally

```bash
pip install -r requirements.txt
python -m pcheck.cli keygen                     # copy the private_key into POSTCONDITION_SIGNING_KEY
export POSTCONDITION_SIGNING_KEY=...            # (Git Bash on Windows: same export syntax)
export POSTCONDITION_API_TOKEN=some-long-random-string
export PAYSTACK_SECRET_KEY=sk_test_...
python -c "from service.app import create_app; create_app().run(port=8000)"
```

Open http://localhost:8000 for the page. Create a receipt:

```bash
curl -X POST http://localhost:8000/v1/paystack/refunds \
  -H "Authorization: Bearer $POSTCONDITION_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"reference":"T229827652905019"}'
```

## Deploy on Railway

1. New project, deploy from the GitHub repo `mubayyingcc-AI/postcondition`.
2. Add a Postgres database to the project. Railway provides `DATABASE_URL`; reference it in the web service's variables.
3. Set the variables from `.env.example` on the web service (signing key, key id, API token, Paystack key).
4. Generate a public domain for the web service. Health check path is `/healthz`.
5. In the Paystack dashboard, set the test webhook URL to `https://<your-domain>/webhooks/paystack`.

## What is and is not verified

- Tested here (`tests/test_service.py`, SQLite, fake Paystack): auth, fail-closed config, receipt signing and
  storage, recheck chaining (the `supersedes` link is inside the signed payload), webhook signature checking,
  duplicate-webhook handling, the landing page's sample receipt verifying.
- NOT tested anywhere yet: the Postgres store, the Railway start command, and the shape of a real Paystack
  webhook. Every webhook is stored in the `events` table, valid or not, so the first real delivery can be
  inspected and the handler corrected, the same way the refund-list bug was found.
- Not built yet: rate limiting on `/v1/verify`, key rotation, per-customer tenants and API keys. One shared
  API token means this is a single-operator service for now.
