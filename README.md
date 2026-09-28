# Postcondition

**We don't sign what an agent claims. We check it against what an authoritative system actually confirms.**

Postcondition is a small, auditable verifier for signed records of AI agent
actions — the layer that answers *"did this actually happen, or did the
agent just say it did?"* — plus, eventually, the authoritative connectors
that make that check possible against real systems of record.

## Why this repo exists, and why now

By late 2026 several teams already ship Ed25519-signed "agent action
receipt" formats — the agent signs a claim, you verify the signature.
That part of the problem is solved and getting crowded. What none of
them do is confirm the claim against the actual system the agent
claims to have changed: reading the real post-state, detecting
conflicts, handling timeouts honestly, and reconciling partial
failures. That's the harder, more valuable 20%, and it's what this
repo builds toward.

**What's in v0.1 (this commit):** the `CHECK` and `SEAL` stages —
schema validation, Ed25519 signing, and offline verification of a
receipt. This is deliberately the self-serve, zero-relationship part:
clone it, `pip install`, verify a receipt in your terminal, done.

**What's not here yet:** the `INTAKE`/`RULING`/`SNAPSHOT`/`BIND`/
`COMMIT`/`CONFIRM` stages — the live connector to a real system of
record (starting with Stripe test-mode refunds). That needs an actual
integration and isn't something to fake with placeholder numbers; see
[Roadmap](#roadmap).

## The pipeline

Every receipt corresponds to a pass through eight stages. This repo
implements the last two.

| Stage | What it does | In this repo? |
|---|---|---|
| **INTAKE** | Identity + capability check on the calling agent | not yet |
| **RULING** | Policy/invariant decision before anything executes | not yet |
| **SNAPSHOT** | Fresh, authoritative pre-state read | not yet |
| **BIND** | Canonicalize the action and bind it to the policy decision | not yet |
| **COMMIT** | Idempotent/conditional execution against the real system | not yet |
| **CONFIRM** | Authoritative post-state read | not yet |
| **CHECK** | Postcondition + conflict verification | ✅ `pcheck verify`, ✅ `connectors/paystack_refund.py` |
| **SEAL** | Sign the receipt | ✅ `pcheck seal` |

The Paystack connector (`connectors/`) implements INTAKE through CHECK
end-to-end for one action type (refunds) against one system. See
`connectors/README.md` for how to run it against a real test account —
it needs a live Paystack key, which this repo obviously doesn't ship
with.

## Quickstart

```bash
pip install cryptography   # only dependency
cd postcondition

# generate a keypair
python -m pcheck.cli keygen

# verify the bundled example receipt
python -m pcheck.cli verify fixtures/valid_receipt.json --pubkey "$(cat fixtures/demo_public_key.txt)"

# see it correctly reject a tampered one
python -m pcheck.cli verify fixtures/tampered_receipt.json --pubkey "$(cat fixtures/demo_public_key.txt)"

# read a receipt without needing a key at all
python -m pcheck.cli inspect fixtures/unknown_outcome_receipt.json
```

Run the test suite (zero external dependencies beyond `cryptography`):

```bash
python -m unittest tests.test_verify -v
```

### As an MCP tool

`mcp_server/server.py` exposes `verify_postcondition_receipt` over
stdio so an agent can check a receipt — its own prior step's, or
another agent's — before trusting the claim behind it. It's a
~150-line hand-rolled JSON-RPC server (no `mcp` package available to
install offline while this was built); swap in the official SDK
before shipping. The tool contract is what should stay stable:

```json
{
  "name": "verify_postcondition_receipt",
  "arguments": {
    "receipt": { "...": "..." },
    "public_key": "base64-ed25519-public-key"
  }
}
```

## The receipt

Seven bounded outcomes, no more, no less — see `SPEC.md` for the full
field reference:

`ALLOWED_NOT_EXECUTED` · `EXECUTED_AND_VERIFIED` ·
`EXECUTED_BUT_POSTCONDITION_FAILED` · `REJECTED_BY_POLICY` ·
`CONFLICT_DETECTED` · `COMPENSATION_REQUIRED` · `UNKNOWN_OUTCOME`

`UNKNOWN_OUTCOME` is not a bug to hide — see
`fixtures/unknown_outcome_receipt.json`. A timeout after submission,
before the post-state read can confirm or deny the write, must not be
silently reported as success or failure.

Every receipt carries a `scope_of_claim` block with explicit
`asserts` / `does_not_assert` lists. A receipt should never let a
reader infer more than it actually proves — see the fixtures for
worked examples.

## What this deliberately doesn't claim

- That the AI reasoned correctly
- That the policy it followed was itself appropriate
- That no unobserved side effect occurred
- That a signature check right now means the underlying fact is
  *still* true (see `--freshness` handling in `pcheck inspect`)

## Hosted service

`service/` is a small Flask app: a landing page with a live verifier, a public `POST /v1/verify`,
published signing keys at `/v1/keys`, authenticated endpoints that run the Paystack connector and
re-check `UNKNOWN_OUTCOME` receipts (issuing a new receipt that `supersedes` the old one), and a
Paystack webhook receiver. See `service/README.md` for running it, deploying it on Railway, and an
exact list of what is and is not verified yet.

## Roadmap

1. **Paystack test-mode refund connector** — `connectors/`. First
   connector, chosen over Stripe because Stripe does not support
   opening a merchant account from Nigeria (Paystack is Stripe's own
   African payments infrastructure, acquired 2020, run as a separate
   product). Logic is built and unit-tested against a fake client
   (`tests/test_paystack_connector.py`); the live run against a real
   Paystack test account is the next step — see `connectors/README.md`.
2. **Cross-format ingestion** — accept and verify receipts from other
   published formats (ActionProof, AGA) alongside our own, so a buyer
   isn't locked into one signer's schema to get independent
   confirmation.
3. **Audit/GRC export** — the packaging a compliance or insurer
   reviewer actually wants, which is not the same shape as a dev-tool
   JSON blob.
4. **Second connector**: insurance claim adjustment (clear
   postcondition + human-approval threshold, per the original wedge
   sequencing).

## Kill criteria (carried over from the original plan, unchanged)

Stop or narrow this project if: a buyer says native transactions +
policy-as-code + observability already solve their problem; a major
cloud/observability vendor reproduces the value without new
authoritative connectors; a connector can't establish a trustworthy
commit/post-state boundary; receipts are accepted only as logs, not
as usable audit evidence; or no target buyer will pay for a pilot
after seeing real adversarial test results.

## License

Apache-2.0 — see `LICENSE`.
