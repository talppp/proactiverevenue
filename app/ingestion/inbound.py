"""
L1 - Inbound webhook routers (one per channel).

Each handler does the minimum required to acknowledge the provider, then
hands off to the pipeline orchestrator.  Real production code would verify
HMAC signatures here (Twilio X-Twilio-Signature, Meta X-Hub-Signature-256,
Google Pub/Sub JWT, etc.); we keep stubs for the demo so feed_mockup.py
can post unsigned payloads.

The router exposes:
    POST /webhook/sms
    POST /webhook/whatsapp
    POST /webhook/apple_mail

It also exposes:
    POST /webhook/test       - convenience for verify_e2e.py; takes a
                               minimal payload + channel name.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.logging_setup import get_logger

log = get_logger("L1_inbound")
router = APIRouter(prefix="/webhook", tags=["inbound"])

# Webhook signing secrets - in production these come from Render env vars.
# For the demo we accept unsigned payloads if the env var is empty.
_HMAC_SECRETS = {
    "sms":        os.environ.get("TWILIO_WEBHOOK_SECRET", ""),
    "whatsapp":   os.environ.get("META_WHATSAPP_APP_SECRET", ""),
    "apple_mail": os.environ.get("APPLE_MAIL_SHARED_TOKEN", ""),
}

def _verify_signature(channel: str, raw_body: bytes, signature: str | None) -> None:
    """Conservative HMAC check. Raises 401 on mismatch when a secret is set;
    skips silently when the secret is empty (demo / local mode)."""
    secret = _HMAC_SECRETS.get(channel, "")
    if not secret:
        return
    if not signature:
        raise HTTPException(status_code=401, detail="missing signature header")
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    if not hmac.compare_digest(signature, expected):
        log.warning("bad_signature", channel=channel)
        raise HTTPException(status_code=401, detail="bad signature")

# ---------------------------------------------------------------------------
# Generic webhook handler.  The actual pipeline call is wired in app/main.py
# to avoid an import cycle (this module is imported by main.py).
# ---------------------------------------------------------------------------
async def _handle(channel: str, request: Request,
                  signature: str | None = None) -> dict[str, Any]:
    raw = await request.body()
    _verify_signature(channel, raw, signature)
    try:
        payload = await request.json() if raw else {}
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid JSON") from e
    pipeline = request.app.state.pipeline
    try:
        result = await pipeline.handle_inbound(channel, payload)
    except Exception as e:
        log.exception("pipeline_failure", channel=channel, error=str(e))
        raise HTTPException(status_code=500, detail="pipeline failure") from e
    return result

# ---------------------------------------------------------------------------
# Per-channel endpoints (kept distinct for clearer logs + provider-specific
# header binding).
# ---------------------------------------------------------------------------
@router.post("/sms", status_code=status.HTTP_200_OK)
async def webhook_sms(request: Request,
                      x_twilio_signature: str | None = Header(default=None)):
    return await _handle("sms", request, x_twilio_signature)

@router.post("/whatsapp", status_code=status.HTTP_200_OK)
async def webhook_whatsapp(request: Request,
                           x_hub_signature_256: str | None = Header(default=None)):
    return await _handle("whatsapp", request, x_hub_signature_256)

@router.post("/apple_mail", status_code=status.HTTP_200_OK)
async def webhook_apple_mail(request: Request,
                             x_apple_token: str | None = Header(default=None)):
    return await _handle("apple_mail", request, x_apple_token)

class _TestPayload(BaseModel):
    channel: str = Field(..., description="One of sms|whatsapp|sms_phone|apple_mail")
    body: str
    sender_handle: str = "+14045550199"
    subject: str | None = None
    msg_id: str | None = None

@router.post("/test", status_code=status.HTTP_200_OK)
async def webhook_test(payload: _TestPayload, request: Request):
    """Convenience endpoint for verify_e2e.py and curl-based smoke tests.
    Bypasses signature verification."""
    pipeline = request.app.state.pipeline
    body = {
        "msg_id":        payload.msg_id,
        "body":          payload.body,
        "subject":       payload.subject,
        "sender_phone":  payload.sender_handle,
        "sender_email":  payload.sender_handle,
        "From":          payload.sender_handle,
    }
    return await pipeline.handle_inbound(payload.channel, body)
