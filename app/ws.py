"""
WebSocket hub.

One channel per team member (nathalie, fabi, noa).  L6 publishes a JSON
card; every connected dashboard tab receives it instantly.  When no
client is connected, cards are still buffered (so a late-attaching tab
sees the recent backlog).

This module is intentionally tiny - the hub is in-process state, fine
for a single-instance Render free-tier service.  Multi-instance would
need Redis pubsub.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.logging_setup import get_logger

log = get_logger("ws_hub")


_BUFFER_PER_MEMBER = 50          # keep last 50 cards per member


@dataclass
class _MemberHub:
    name: str
    clients: set[WebSocket]
    buffer: deque[dict[str, Any]]


class Hub:
    def __init__(self) -> None:
        self._members: dict[str, _MemberHub] = {}
        self._lock = asyncio.Lock()

    def _get(self, name: str) -> _MemberHub:
        key = name.lower()
        if key not in self._members:
            self._members[key] = _MemberHub(
                name=key, clients=set(),
                buffer=deque(maxlen=_BUFFER_PER_MEMBER),
            )
        return self._members[key]

    async def connect(self, name: str, ws: WebSocket) -> None:
        await ws.accept()
        hub = self._get(name)
        hub.clients.add(ws)
        # Replay buffered cards so a fresh tab sees recent activity.
        for card in list(hub.buffer):
            try:
                await ws.send_text(json.dumps(card))
            except Exception:
                break
        log.info("ws_client_connected", member=name, clients=len(hub.clients))

    async def disconnect(self, name: str, ws: WebSocket) -> None:
        hub = self._get(name)
        hub.clients.discard(ws)
        log.info("ws_client_disconnected", member=name, clients=len(hub.clients))

    async def publish(self, name: str, card: dict[str, Any]) -> int:
        """Returns the number of clients that received the card."""
        hub = self._get(name)
        hub.buffer.append(card)
        if not hub.clients:
            return 0
        encoded = json.dumps(card, default=str)
        dead: list[WebSocket] = []
        delivered = 0
        for client in list(hub.clients):
            try:
                await client.send_text(encoded)
                delivered += 1
            except Exception:
                dead.append(client)
        for d in dead:
            hub.clients.discard(d)
        return delivered

    async def serve(self, name: str, ws: WebSocket) -> None:
        """Connect + idle loop until the client disconnects."""
        await self.connect(name, ws)
        try:
            while True:
                # Keep the connection alive; ignore any client-sent payloads
                # (the dashboard is read-only for now).
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await self.disconnect(name, ws)


# Process-wide singleton.
HUB = Hub()
