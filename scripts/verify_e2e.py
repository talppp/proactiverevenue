"""
End-to-end live demo: feed ONE P0 SMS from the dummy test user
(Tester Buyer-Smith) and print the full L1->L6 trace.

Usage:
    # local: assumes uvicorn app.main:app is already running on :8000
    python scripts/verify_e2e.py

    # against a deployed service
    python scripts/verify_e2e.py --base-url https://apteker-router.onrender.com
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent

# Pick a P0 row that should route to Nathalie.
DEMO_PAYLOAD = {
    "msg_id":       "DEMO-P0-001",
    "sender_phone": "+14045550199",   # Tester Buyer-Smith
    "body":         ("EMERGENCY - water is pouring from the ceiling at "
                     "456 Oak Ave, Sandy Springs!! Please help, it is "
                     "flooding the bathroom and the hallway."),
    "received_at":  None,
}


def _row(label: str, value: str, width: int = 14) -> str:
    return f"  [{label.ljust(width)}] {value}"


async def run(base_url: str) -> int:
    url = f"{base_url}/webhook/sms"
    print("=" * 70)
    print(" Apteker AI Issue Router  ·  end-to-end demo")
    print("=" * 70)
    print(_row("FROM",    f"Tester Buyer-Smith ({DEMO_PAYLOAD['sender_phone']})"))
    print(_row("CHANNEL", "sms"))
    print(_row("MESSAGE", DEMO_PAYLOAD["body"]))
    print("-" * 70)

    async with aiohttp.ClientSession() as s:
        # 1. Health
        try:
            async with s.get(f"{base_url}/healthz", timeout=aiohttp.ClientTimeout(total=5)) as r:
                health = await r.json()
        except Exception as e:
            print(f"\n  Service not reachable at {base_url}.  Start it with:")
            print("      uvicorn app.main:app --reload --port 8000")
            print(f"  Error: {e}")
            return 1

        print(_row("HEALTH", json.dumps(health)))

        # 2. POST webhook
        async with s.post(url, json=DEMO_PAYLOAD,
                          timeout=aiohttp.ClientTimeout(total=30)) as r:
            body = await r.json()
            ok = r.status == 200

    print("-" * 70)
    print(" PIPELINE TRACE")
    print("-" * 70)

    if not ok or body.get("status") != "ok":
        print(_row("STATUS", f"FAIL ({r.status}) - {body}"))
        return 2

    print(_row("L1 inbound",   f"persisted message_id={body['message_id']}"))
    print(_row("L2 normalize", "redacted, dedupe_hash computed"))
    print(_row("L3 urgency",   body["urgency"]))
    print(_row("L3 topic",     body["topic"]))
    print(_row("L4 + L5",      f"owner={body['owner']}  unsure={body['is_unsure']}  archive={body['is_archive']}"))
    deliveries = body.get("deliveries", [])
    kinds = ", ".join(f"{d['kind']}:{d['status']}" for d in deliveries)
    print(_row("L6 deliver",   kinds))
    print(_row("WALL TIME",    f"{body['wall_ms']} ms"))
    print(_row("LLM COST",     f"${body['cost_usd']:.5f}"))
    print("-" * 70)

    expected_owner = "Nathalie"
    expected_urgency = "P0"
    if body["owner"] == expected_owner and body["urgency"] == expected_urgency:
        print("\n  RESULT: PASS - delivered to Nathalie's mobile (open /inbox/nathalie)\n")
        return 0
    else:
        print(f"\n  RESULT: UNEXPECTED - expected owner={expected_owner} urgency={expected_urgency}")
        print(f"          got     owner={body['owner']}     urgency={body['urgency']}\n")
        return 3


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", default="http://localhost:8000")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.base_url)))


if __name__ == "__main__":
    main()
