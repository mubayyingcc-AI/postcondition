"""
Create a real Paystack TEST-mode transaction and get its reference, so
paystack_refund.py has a genuine transaction to refund.

Why this needs a browser step, and isn't fully automated: Paystack (like
Stripe) requires the card entry to happen on their own hosted checkout
page — that's a real security property, not a limitation of this
script — so completing a transaction means opening a URL and typing in
a documented test card. This is the one manual step in the whole
pipeline; everything after it (refund submission, verification,
conflict/idempotency handling, receipt sealing) is fully automated.

Usage:
    export PAYSTACK_SECRET_KEY=sk_test_...
    python -m connectors.create_test_transaction you@example.com 5000

Then open the printed authorization_url, pay with Paystack's documented
reusable success card:
    Card:   4084 0840 8408 4081
    Expiry: any future date (e.g. 12/27)
    CVV:    408
(Per paystack.com/docs/payments/test-payments — "No validation
(reusable)" success card, no PIN/OTP prompt expected.)

Once paid, the reference printed by this script (also visible in your
Paystack Test Mode dashboard under Transactions) is what you pass to
paystack_refund.py.
"""

from __future__ import annotations

import os
import sys

from connectors.paystack_client import PaystackClient, PaystackError


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python -m connectors.create_test_transaction <email> <amount_kobo>", file=sys.stderr)
        print("example: python -m connectors.create_test_transaction you@example.com 5000", file=sys.stderr)
        return 2

    email, amount_str = sys.argv[1], sys.argv[2]
    amount_kobo = int(amount_str)

    secret_key = os.environ.get("PAYSTACK_SECRET_KEY")
    if not secret_key:
        print("error: set PAYSTACK_SECRET_KEY first (your sk_test_... key)", file=sys.stderr)
        return 2

    client = PaystackClient(secret_key)
    try:
        result = client.initialize_transaction(email, amount_kobo)
    except PaystackError as exc:
        print(f"Paystack rejected the request: {exc}", file=sys.stderr)
        return 1

    data = result.get("data", {})
    print(f"reference: {data.get('reference')}")
    print(f"authorization_url: {data.get('authorization_url')}")
    print(
        "\nOpen the authorization_url above and pay with one of Paystack's test cards "
        "(see paystack.com/docs/payments/test-payments):\n"
        "  success:          4084 0840 8408 4081  exp any future  CVV 408\n"
        "  refund->needs attention: 4084 0800 0067 1902  exp 09/27  CVV 190\n"
        "  refund->failed:   4084 0800 0067 1803  exp 09/27  CVV 180\n"
        "Then run:\n"
        f"  python -m connectors.paystack_refund {data.get('reference')} --out receipt.json"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
