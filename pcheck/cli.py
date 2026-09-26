"""
pcheck — command-line verifier for Postcondition receipts.

    pcheck keygen                              print a new Ed25519 keypair
    pcheck seal receipt.json --key priv.key    sign a draft receipt
    pcheck verify receipt.json --pubkey KEY    verify signature + schema
    pcheck inspect receipt.json                human-readable summary,
                                                including scope-of-claim
                                                and freshness, no key needed

Exit codes: 0 = valid / success, 1 = invalid, 2 = usage error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from .crypto import generate_keypair, seal_receipt, verify_receipt
from .schema import validate_schema


def _load(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        print(f"error: no such file: {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)


def cmd_keygen(_: argparse.Namespace) -> int:
    priv, pub = generate_keypair()
    print(json.dumps({"private_key": priv, "public_key": pub}, indent=2))
    print(
        "\nStore the private key like any other signing secret. The public "
        "key is what you hand to anyone who needs to verify your receipts — "
        "publishing it (e.g. as a JWKS-style document) is expected.",
        file=sys.stderr,
    )
    return 0


def cmd_seal(args: argparse.Namespace) -> int:
    draft = _load(args.receipt)
    private_key = Path(args.key).read_text().strip()
    try:
        sealed = seal_receipt(draft, private_key, args.signer_key_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(sealed, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    receipt = _load(args.receipt)
    public_key = Path(args.pubkey).read_text().strip() if Path(args.pubkey).exists() else args.pubkey
    result = verify_receipt(receipt, public_key)

    output = {"valid": result.valid, "reasons": result.reasons}
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        if result.valid:
            print(f"VALID   {args.receipt}")
        else:
            print(f"INVALID {args.receipt}")
            for reason in result.reasons:
                print(f"        - {reason}")
    return 0 if result.valid else 1


def cmd_inspect(args: argparse.Namespace) -> int:
    receipt = _load(args.receipt)
    errors = validate_schema(receipt)

    print(f"receipt_id:            {receipt.get('receipt_id', '<missing>')}")
    print(f"status:                 {receipt.get('status', '<missing>')}")
    print(f"action_type:            {receipt.get('action_type', '<missing>')}")
    print(f"connector_id:           {receipt.get('connector_id', '<missing>')}")
    print(f"postcondition_result:   {receipt.get('postcondition_result', '<missing>')}")
    print(f"invariant_result:       {receipt.get('invariant_result', '<missing>')}")

    scope = receipt.get("scope_of_claim", {})
    print("asserts:")
    for item in scope.get("asserts", []):
        print(f"  + {item}")
    print("does_not_assert:")
    for item in scope.get("does_not_assert", []):
        print(f"  - {item}")

    issued_at = receipt.get("issued_at")
    if issued_at:
        try:
            issued = dt.datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
            age = dt.datetime.now(dt.timezone.utc) - issued
            print(f"issued_at:              {issued_at}  (age: {age})")
        except ValueError:
            print(f"issued_at:              {issued_at}  (unparseable timestamp)")

    if errors:
        print("\nSCHEMA ERRORS:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\nschema: valid (signature not checked — use `pcheck verify`)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcheck", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_keygen = sub.add_parser("keygen", help="generate a new Ed25519 keypair")
    p_keygen.set_defaults(func=cmd_keygen)

    p_seal = sub.add_parser("seal", help="sign a draft receipt")
    p_seal.add_argument("receipt", help="path to draft receipt JSON")
    p_seal.add_argument("--key", required=True, help="path to private key file")
    p_seal.add_argument("--signer-key-id", required=True, dest="signer_key_id")
    p_seal.set_defaults(func=cmd_seal)

    p_verify = sub.add_parser("verify", help="verify signature + schema")
    p_verify.add_argument("receipt", help="path to receipt JSON")
    p_verify.add_argument(
        "--pubkey", required=True, help="public key (base64) or path to a key file"
    )
    p_verify.add_argument("--json", action="store_true", help="machine-readable output")
    p_verify.set_defaults(func=cmd_verify)

    p_inspect = sub.add_parser("inspect", help="human-readable summary, no key needed")
    p_inspect.add_argument("receipt", help="path to receipt JSON")
    p_inspect.set_defaults(func=cmd_inspect)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
