"""
Diagnose a Paystack connectivity/auth problem in isolation, with zero
dependency on anything else in this repo working first.

Usage:
    export PAYSTACK_SECRET_KEY=sk_test_...
    python -m connectors.diagnose
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    secret_key = os.environ.get("PAYSTACK_SECRET_KEY")
    if not secret_key:
        print("error: set PAYSTACK_SECRET_KEY first", file=sys.stderr)
        return 2

    url = "https://api.paystack.co/transaction?perPage=1"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {secret_key}")
    req.add_header("Content-Type", "application/json")

    print(f"GET {url}")
    print(f"Authorization: Bearer {secret_key[:12]}...{secret_key[-4:]}")
    print()

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"HTTP {resp.status}")
            print(f"Content-Type: {resp.headers.get('Content-Type')}")
            body = resp.read()
            print(f"Body ({len(body)} bytes):")
            try:
                print(json.dumps(json.loads(body), indent=2)[:2000])
            except json.JSONDecodeError:
                print(repr(body[:1000]))
            print("\n=> Reached Paystack, got a valid response. Auth and network are fine.")
            return 0
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        print(f"HTTP {exc.code} (error response)")
        print(f"Content-Type: {exc.headers.get('Content-Type') if exc.headers else '(none)'}")
        print(f"Body ({len(raw)} bytes):")
        try:
            print(json.dumps(json.loads(raw), indent=2)[:2000])
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(repr(raw[:1000]) if raw else "(completely empty body)")
        print()
        if exc.code == 401:
            print("=> 401: the secret key is wrong, revoked, or a live/test mismatch.")
        elif not raw:
            print(
                "=> Got an HTTP error with a totally empty body — very likely something "
                "*between* you and Paystack (a proxy, VPN, antivirus TLS inspection, or "
                "ISP-level interception) rather than Paystack itself. Try again on a "
                "different network (e.g. phone hotspot) to confirm."
            )
        return 1
    except urllib.error.URLError as exc:
        print(f"Never reached the server: {exc.reason}")
        print("=> DNS failure, no route, or connection blocked before any HTTP exchange happened.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
