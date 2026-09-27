"""
Print the raw GET /refund response so we can see exactly how Paystack
structures the 'transaction' field on a refund object — needed because
list_refunds_for_transaction() isn't finding refunds that definitely
exist, which means the assumed response shape was wrong.

Usage:
    export PAYSTACK_SECRET_KEY=sk_test_...
    python -m connectors.inspect_refunds
"""

import json
import os
import sys

from connectors.paystack_client import PaystackClient, PaystackError


def main() -> int:
    secret_key = os.environ.get("PAYSTACK_SECRET_KEY")
    if not secret_key:
        print("error: set PAYSTACK_SECRET_KEY first", file=sys.stderr)
        return 2

    client = PaystackClient(secret_key)
    try:
        raw = client._request("GET", "/refund")
    except PaystackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(raw, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
