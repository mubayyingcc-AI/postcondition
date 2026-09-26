# Postcondition Receipt — Specification v1.0

## Canonicalization rule

To sign or verify a receipt, first remove `signature` and
`signer_key_id`, then serialize the remaining object with:

- keys sorted lexicographically (byte order on the UTF-8 key strings)
- compact separators: `,` and `:`, no whitespace
- UTF-8 encoding, `ensure_ascii=False`

This is a simplified relative of RFC 8785 (JSON Canonicalization
Scheme) — sufficient for flat, string/int/bool/null payloads with no
floats requiring canonical number formatting. Reference implementation:
`pcheck/schema.py::canonicalize()`.

**Interoperability requirement:** a receipt signed by any conforming
implementation must verify under any other conforming implementation.
If you build a second-language port (TypeScript, Go, Rust), the
canonicalization test in `tests/test_verify.py`
(`test_reordered_keys_still_verify`) is the contract to hold — key
order in the source JSON must never affect the signature.

## Fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `receipt_version` | string | yes | Schema version, currently `"1.0"` |
| `receipt_id` | string | yes | Unique ID for this receipt |
| `tenant_id` | string | yes | Customer/tenant this receipt belongs to |
| `connector_id` | string | yes | Which connector produced this (e.g. `stripe-refunds-test`) |
| `connector_version` | string | no | Connector implementation version |
| `actor_identity` | string | no | Human or system that authorized the action |
| `agent_identity` | string | no | The AI agent that initiated the action |
| `action_type` | string | yes | e.g. `refund`, `claim_adjustment` |
| `canonical_action_digest` | string | yes | `sha256:...` hash of the canonicalized action request |
| `policy_id` | string | yes | Which policy was evaluated |
| `policy_version` | string | no | Policy version at evaluation time |
| `invariant_result` | `PASS`\|`FAIL`\|`UNKNOWN` | yes | Result of pre-execution invariant checks |
| `pre_state_commitment` | string | no | `sha256:...` of the authoritative pre-state read |
| `pre_state_version` | string | no | Source system's own version/ETag for that read |
| `idempotency_key` | string | no | The key used to make execution safe to retry |
| `provider_operation_id` | string\|null | no | The source system's own ID for this operation, if it committed |
| `post_state_commitment` | string\|null | no | `sha256:...` of the authoritative post-state read |
| `post_state_version` | string\|null | no | Source system's version/ETag for the post-state read |
| `postcondition_result` | `PASS`\|`FAIL`\|`UNKNOWN` | yes | Did the post-state match what was expected |
| `freshness_bound_seconds` | number | no | Max allowed staleness between pre- and post-state reads |
| `issued_at` | ISO 8601 string | yes | When this receipt was signed |
| `scope_of_claim` | object | yes | `{"asserts": [...], "does_not_assert": [...]}` — see below |
| `status` | enum | yes | One of the seven Receipt States |
| `signer_key_id` | string | yes* | Identifies the signing key (not part of the signed payload) |
| `signature` | string (base64) | yes* | Ed25519 signature over the canonicalized payload |

\* Required on a *sealed* receipt; a draft receipt being built up before
signing won't have these yet.

## Receipt States

Exactly seven. A receipt's `status` must be one of:

- **`ALLOWED_NOT_EXECUTED`** — policy allowed the action, but it was
  never submitted (e.g. the agent chose not to proceed)
- **`EXECUTED_AND_VERIFIED`** — submitted, and the post-state read
  confirms the expected postcondition
- **`EXECUTED_BUT_POSTCONDITION_FAILED`** — submitted, but the
  post-state read shows the expected result did not hold
- **`REJECTED_BY_POLICY`** — never submitted; policy/invariant check
  failed first
- **`CONFLICT_DETECTED`** — a concurrent writer changed the target
  state between pre- and post-state reads
- **`COMPENSATION_REQUIRED`** — a partial multi-step operation needs a
  compensating action
- **`UNKNOWN_OUTCOME`** — submission occurred but the outcome could
  not be established (e.g. timeout before the post-state read) — this
  is a first-class honest result, not an error to suppress

## `scope_of_claim`

Every receipt must state, in its own words, exactly what it does and
does not prove. This is not boilerplate — it is the thing that keeps a
receipt from being over-trusted by a reader who skims it.

```json
"scope_of_claim": {
  "asserts": [
    "Stripe confirmed refund re_... applied to charge ch_...",
    "policy X evaluated PASS before execution"
  ],
  "does_not_assert": [
    "that the customer was notified",
    "that the policy itself was correctly configured"
  ]
}
```

A verifier that finds a receipt schema-valid and correctly signed has
confirmed the receipt is genuine and unaltered — **not** that
everything a careless reader might assume from it is true. Read
`does_not_assert` every time.

## Non-goals (do not encode these as receipt claims, ever)

- That the AI's reasoning was sound
- That the policy it followed was itself well-designed
- That no side effect occurred outside what this connector observes
- That a model is broadly safe or trustworthy
- That the customer is legally compliant merely because a receipt
  exists

See `fixtures/` for four worked, actually-signed examples: a valid
receipt, a tampered one (signature correctly fails), a stale one, and
an honest `UNKNOWN_OUTCOME`.
