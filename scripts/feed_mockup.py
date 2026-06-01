"""
Replay the 3 000-row mockup corpus into a running service.

Reads the CSV files under 07_Mockup_Data/{sms,whatsapp,email}/ and posts
each row to POST /webhook/<channel>.  By default it shuffles all 3K rows
together so the three team-member dashboards animate concurrently.

Usage:
    # full replay against local service
    python scripts/feed_mockup.py

    # 100-row smoke test
    python scripts/feed_mockup.py --limit 100

    # against a deployed service
    python scripts/feed_mockup.py --base-url https://apteker-router.onrender.com

    # adjust rate (default 10 requests/sec)
    python scripts/feed_mockup.py --rate 25
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import random
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent
MOCKUP    = REPO_ROOT / "07_Mockup_Data"

def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))

def _all_rows() -> list[dict[str, Any]]:
    """Return a list of (channel, payload) dicts ready to post."""
    rows: list[dict[str, Any]] = []
    for r in _read_csv(MOCKUP / "sms" / "sms_mock_1k.csv"):
        rows.append({
            "channel": "sms",
            "payload": {
                "msg_id":       r["msg_id"],
                "sender_phone": r["sender_phone"],
                "body":         r["body"],
                "received_at":  r["received_at"],
            },
            "expected_owner": r.get("expected_owner", ""),
            "expected_urgency": r.get("expected_urgency", ""),
        })
    for r in _read_csv(MOCKUP / "whatsapp" / "whatsapp_mock_1k.csv"):
        rows.append({
            "channel": "whatsapp",
            "payload": {
                "msg_id":       r["msg_id"],
                "sender_phone": r["sender_phone"],
                "body":         r["body"],
                "received_at":  r["received_at"],
            },
            "expected_owner": r.get("expected_owner", ""),
            "expected_urgency": r.get("expected_urgency", ""),
        })
    for r in _read_csv(MOCKUP / "email" / "email_mock_1k.csv"):
        rows.append({
            "payload": {
                "msg_id":       r["msg_id"],
                "sender_email": r["sender_email"],
                "subject":      r.get("subject", ""),
                "body":         r["body"],
                "received_at":  r["received_at"],
            },
            "expected_owner": r.get("expected_owner", ""),
            "expected_urgency": r.get("expected_urgency", ""),
        })
    return rows

async def _post(session: aiohttp.ClientSession, base_url: str, item: dict[str, Any]) -> dict[str, Any]:
    url = f"{base_url}/webhook/{item['channel']}"
    try:
        async with session.post(url, json=item["payload"], timeout=aiohttp.ClientTimeout(total=30)) as r:
            body = await r.json(content_type=None)
            return {"ok": r.status == 200, "status": r.status, "body": body, "item": item}
    except Exception as e:
        return {"ok": False, "status": 0, "body": {"error": str(e)}, "item": item}

async def main_async(args) -> None:
    rows = _all_rows()
    random.seed(20260518)
    random.shuffle(rows)
    if args.limit:
        rows = rows[:args.limit]
    print(f"Replaying {len(rows)} rows -> {args.base_url} at {args.rate}rps")
    sent = ok = 0
    correct_owner = correct_urgency = 0
    delay = 1.0 / max(args.rate, 1)
    start = time.perf_counter()
    sem = asyncio.Semaphore(args.concurrency)
    async with aiohttp.ClientSession() as session:
        async def _one(item):
            nonlocal sent, ok, correct_owner, correct_urgency
            async with sem:
                res = await _post(session, args.base_url, item)
                sent += 1
                if res["ok"]:
                    ok += 1
                    body = res["body"] or {}
                    if (item.get("expected_owner") or "").lower() == \
                       (body.get("owner") or "").lower():
                        correct_owner += 1
                    if (item.get("expected_urgency") or "").lower() == \
                       (body.get("urgency") or "").lower():
                        correct_urgency += 1
                if sent % 50 == 0:
                    elapsed = time.perf_counter() - start
                    print(f"  {sent:5d} sent | {ok:5d} ok | owner-acc={correct_owner/max(sent,1):.2f} | urgency-acc={correct_urgency/max(sent,1):.2f} | {sent/elapsed:.1f}rps")
                await asyncio.sleep(delay)
        await asyncio.gather(*[_one(item) for item in rows])
    elapsed = time.perf_counter() - start
    print(f"\nDone: {sent} sent / {ok} ok in {elapsed:.1f}s ({sent/elapsed:.1f}rps)")
    if sent:
        print(f"Owner accuracy:   {correct_owner/sent:.1%}")
        print(f"Urgency accuracy: {correct_urgency/sent:.1%}")

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url",   default="http://localhost:8000",
                    help="Service base URL")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N rows (0 = all 3000)")
    ap.add_argument("--rate", type=float, default=10.0,
                    help="Requests per second per worker")
    ap.add_argument("--concurrency", type=int, default=5,
                    help="Concurrent in-flight requests")
    args = ap.parse_args()
    asyncio.run(main_async(args))

if __name__ == "__main__":
    sys.exit(main() or 0)
