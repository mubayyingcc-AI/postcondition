"""
A minimal MCP server exposing Postcondition's CHECK stage as a tool an
agent can call before trusting another agent's (or its own prior
step's) success claim.

Hand-rolled JSON-RPC over stdio, no MCP SDK dependency — this container
has no network access to install one, and a ~150-line reference server
is easier to audit anyway for a security-adjacent tool. Swap this for
the official `mcp` Python package before shipping; the tool contract
below (name, schema, behavior) is what should stay stable.

Run:  python -m mcp_server.server
"""

from __future__ import annotations

import json
import sys
from typing import Any

sys.path.insert(0, "..")
from pcheck.crypto import verify_receipt  # noqa: E402

TOOL_NAME = "verify_postcondition_receipt"

TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Verify a Postcondition receipt: checks the Ed25519 signature and "
        "schema of a signed record of what an authoritative system "
        "confirmed after an agent action. Returns valid=false with reasons "
        "for a tampered, malformed, or wrong-key receipt. Does not confirm "
        "the claim is still true right now — only that the record is "
        "genuine and unaltered since it was issued."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "receipt": {
                "type": "object",
                "description": "The receipt JSON object to verify.",
            },
            "public_key": {
                "type": "string",
                "description": "Base64-encoded Ed25519 public key of the expected signer.",
            },
        },
        "required": ["receipt", "public_key"],
    },
}


def handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    args = params.get("arguments", {})
    receipt = args.get("receipt")
    public_key = args.get("public_key")

    if not isinstance(receipt, dict) or not isinstance(public_key, str):
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"valid": False, "reasons": ["receipt (object) and public_key (string) are required"]}
                    ),
                }
            ],
            "isError": True,
        }

    result = verify_receipt(receipt, public_key)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"valid": result.valid, "reasons": result.reasons}),
            }
        ],
        "isError": not result.valid,
    }


def handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "postcondition-mcp", "version": "0.1.0"},
        }
    elif method == "tools/list":
        result = {"tools": [TOOL_SCHEMA]}
    elif method == "tools/call":
        params = req.get("params", {})
        if params.get("name") != TOOL_NAME:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"unknown tool: {params.get('name')}"},
            }
        result = handle_tools_call(params)
    elif method == "notifications/initialized":
        return None  # no response required for notifications
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"unknown method: {method}"},
        }

    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(req)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
