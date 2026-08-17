# Copyright (c) 2026 VyapaarOS. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Razorpay webhook handler for async payment state updates."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlmodel import Session, select

from src.merchant.db.database import get_session
from src.merchant.db.models import CheckoutSession, CheckoutStatus
from src.merchant.services.payment_adapter import RazorpayAdapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# In-memory store for webhook deduplication (for MVP)
processed_events: set[str] = set()

@router.post("/razorpay")
async def handle_razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None),
    x_razorpay_event_id: str = Header(None),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Idempotent webhook handler for Razorpay events."""
    if not x_razorpay_signature:
        raise HTTPException(status_code=400, detail="Missing signature")

    if not x_razorpay_event_id:
        logger.warning("Received webhook without event ID")
    elif x_razorpay_event_id in processed_events:
        logger.info(f"Duplicate event {x_razorpay_event_id} rejected. Returning 200 OK.")
        return {"status": "success", "message": "Duplicate event ignored"}

    raw_body = await request.body()
    raw_body_str = raw_body.decode("utf-8")
    adapter = RazorpayAdapter()

    # 1. Verify Signature
    if not adapter.verify_webhook_signature(raw_body_str, x_razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 2. Parse Payload
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from err

    event_type = payload.get("event")
    logger.info(f"Received Razorpay Webhook: {event_type} (Event ID: {x_razorpay_event_id})")

    if event_type not in ("payment.captured", "payment.failed"):
        # Ignore unhandled events
        return {"status": "ignored", "event": event_type}

    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    order_id = payment_entity.get("order_id")

    if not order_id:
        logger.error("Webhook payload missing order_id")
        raise HTTPException(status_code=400, detail="Missing order_id in payload")

    # 3. Find associated session (we stored order info as JSON, but we can query it or simply find by looking at all active sessions)
    # Since order_json is a string in SQLite, we have to fetch and filter, or just use a LIKE clause (for MVP).
    # A robust system would have a dedicated Order table, but we use order_json.
    statement = select(CheckoutSession).where(CheckoutSession.order_json.like(f'%"{order_id}"%'))
    session = db.exec(statement).first()

    if not session:
        logger.warning(f"No checkout session found for order {order_id}")
        return {"status": "not_found", "order_id": order_id}

    # 4. Handle Idempotency (Already processed?)
    if session.status in (CheckoutStatus.COMPLETED, CheckoutStatus.CAPTURED) and event_type == "payment.captured":
        logger.info(f"Webhook already processed for session {session.id}")
        return {"status": "already_processed"}

    # 5. Process state transition
    if event_type == "payment.captured":
        logger.info(f"Payment captured for session {session.id}")
        session.status = CheckoutStatus.CAPTURED

        # We can update order_json with payment_id
        order_data = json.loads(session.order_json) if session.order_json else {}
        order_data["payment_id"] = payment_entity.get("id")
        session.order_json = json.dumps(order_data)

    elif event_type == "payment.failed":
        logger.warning(f"Payment failed for session {session.id}")
        session.status = CheckoutStatus.FAILED

        failure_reason = payment_entity.get("error_description", "Payment failed")

        messages = json.loads(session.messages_json) if session.messages_json else []
        messages.append({
            "type": "error",
            "param": "$",
            "content_type": "plain",
            "content": f"Payment failed: {failure_reason}. Please retry.",
        })
        session.messages_json = json.dumps(messages)

    db.add(session)
    db.commit()

    if x_razorpay_event_id:
        processed_events.add(x_razorpay_event_id)

    return {"status": "success", "event": event_type, "session_id": session.id}
