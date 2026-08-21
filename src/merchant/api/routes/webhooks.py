# Copyright (c) 2026 VyapaarOS. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Razorpay webhook handler for async payment state updates.

The route owns the *transport* guarantees; the money semantics live in the
checkout domain (:func:`apply_razorpay_webhook_event`). In order, a delivery is:

1. verified -- the HMAC signature is checked over the exact raw bytes, before
   the body is parsed, using the configured provider (never a client secret);
2. deduplicated durably -- the provider event id is the primary key of the
   ``webhook_event`` table, so a replay is rejected across restarts and workers,
   unlike the previous in-memory ``set()``;
3. applied -- the verified, first-seen event is handed to the domain, which
   routes every status change through the payment state machine, verifies the
   captured amount against what we authorised, and audits each step.

Every branch is auditable, and an anomaly (bad signature, replay, unknown order,
amount mismatch, illegal transition) is recorded and answered without moving
money or provoking a provider retry storm.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from src.merchant.db.database import get_session
from src.merchant.db.models import WebhookEvent, WebhookProcessingState
from src.merchant.domain.checkout.service import (
    WEBHOOK_OUTCOME_AMOUNT_MISMATCH,
    WEBHOOK_OUTCOME_IGNORED,
    WEBHOOK_OUTCOME_NOT_FOUND,
    InvalidStateTransitionError,
    WebhookApplyResult,
    apply_razorpay_webhook_event,
)
from src.merchant.domain.payments.audit import (
    ACTOR_PROVIDER_RAZORPAY,
    ACTOR_SYSTEM,
    AuditAction,
    AuditOutcome,
    record_audit_event,
)
from src.merchant.domain.payments.provider import get_payment_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

#: Outcomes that mean "we durably recorded the event but chose not to act".
_IGNORED_OUTCOMES = frozenset({WEBHOOK_OUTCOME_IGNORED, WEBHOOK_OUTCOME_NOT_FOUND})


def _webhook_id(event_id: str | None, payload_hash: str) -> str:
    """Return the dedup key: the provider event id, or the body hash as fallback.

    Razorpay sends ``X-Razorpay-Event-Id`` on every delivery; if it is ever
    absent, the sha256 of the raw body still gives a stable, replay-safe key so
    an identical redelivery collides on the primary key rather than being
    processed twice.
    """
    return event_id or f"sha256:{payload_hash}"


@router.post("/razorpay")
async def handle_razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None),
    x_razorpay_event_id: str = Header(None),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Idempotent, signature-verified handler for Razorpay payment webhooks."""
    raw_body = await request.body()
    payload_hash = hashlib.sha256(raw_body).hexdigest()

    # 1. Signature -- verified over the RAW bytes, before any parsing. A missing
    #    or invalid signature is rejected and audited; the body is never trusted.
    if not x_razorpay_signature:
        record_audit_event(
            db,
            action=AuditAction.WEBHOOK_SIGNATURE_REJECTED,
            actor=ACTOR_PROVIDER_RAZORPAY,
            reason="Webhook received without a signature header.",
            outcome=AuditOutcome.BLOCKED,
            detail={"payload_hash": payload_hash},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing signature"
        )

    provider = get_payment_provider()
    if not provider.verify_webhook_signature(
        raw_body=raw_body, signature=x_razorpay_signature
    ):
        record_audit_event(
            db,
            action=AuditAction.WEBHOOK_SIGNATURE_REJECTED,
            actor=ACTOR_PROVIDER_RAZORPAY,
            reason="Webhook signature verification failed.",
            outcome=AuditOutcome.BLOCKED,
            detail={"payload_hash": payload_hash},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature"
        )

    # 2. Parse only after the signature proves authenticity.
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload"
        ) from err

    event_type = str(payload.get("event") or "")
    event_id = _webhook_id(x_razorpay_event_id, payload_hash)
    provider_created_at = payload.get("created_at")

    # 3. Durable dedup. The provider event id is the primary key, so a replayed
    #    delivery collides here regardless of process restarts or worker count.
    existing = db.exec(
        select(WebhookEvent).where(WebhookEvent.id == event_id)
    ).first()
    if existing is not None:
        logger.info("Duplicate webhook %s ignored (already %s).", event_id, existing.processing_state)
        record_audit_event(
            db,
            action=AuditAction.WEBHOOK_DUPLICATE_IGNORED,
            actor=ACTOR_PROVIDER_RAZORPAY,
            session_id=existing.session_id,
            reason=f"Duplicate delivery of webhook {event_id}.",
            outcome=AuditOutcome.INFO,
            provider_order_id=existing.provider_order_id,
            detail={"event_type": event_type},
        )
        return {"status": "success", "message": "Duplicate event ignored"}

    webhook_row = WebhookEvent(
        id=event_id,
        provider="razorpay",
        event_type=event_type,
        payload_hash=payload_hash,
        processing_state=WebhookProcessingState.RECEIVED,
        provider_created_at=int(provider_created_at) if provider_created_at else None,
        raw_payload=raw_body.decode("utf-8", errors="replace"),
    )
    db.add(webhook_row)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent delivery won the race to insert this id. Treat as duplicate.
        db.rollback()
        logger.info("Concurrent duplicate webhook %s ignored.", event_id)
        record_audit_event(
            db,
            action=AuditAction.WEBHOOK_DUPLICATE_IGNORED,
            actor=ACTOR_PROVIDER_RAZORPAY,
            reason=f"Concurrent duplicate delivery of webhook {event_id}.",
            outcome=AuditOutcome.INFO,
            detail={"event_type": event_type},
        )
        return {"status": "success", "message": "Duplicate event ignored"}

    db.refresh(webhook_row)
    record_audit_event(
        db,
        action=AuditAction.WEBHOOK_RECEIVED,
        actor=ACTOR_PROVIDER_RAZORPAY,
        reason=f"Accepted webhook {event_id} ({event_type}).",
        outcome=AuditOutcome.INFO,
        detail={"event_type": event_type},
    )

    payment_entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )

    # 4. Apply the money semantics in the domain (state machine + amount check +
    #    audit). Anomalies come back as outcomes; an illegal transition is the
    #    only thing that raises, and we record it rather than 500 the provider.
    try:
        result = apply_razorpay_webhook_event(
            db, event_type=event_type, payment_entity=payment_entity
        )
    except InvalidStateTransitionError as exc:
        logger.error("Webhook %s could not be applied: %s", event_id, exc.message)
        _finalize_webhook_row(
            db,
            webhook_row,
            state=WebhookProcessingState.FAILED,
            error=exc.message,
        )
        record_audit_event(
            db,
            action=AuditAction.WEBHOOK_PROCESSING_FAILED,
            actor=ACTOR_SYSTEM,
            reason=exc.message,
            outcome=AuditOutcome.FAILURE,
            detail={"event_id": event_id, "event_type": event_type},
        )
        return {"status": "error", "message": "Event could not be applied"}

    _record_webhook_result(db, webhook_row, event_id, event_type, result)
    return {
        "status": "success",
        "event": event_type,
        "outcome": result.outcome,
        "session_id": result.session_id,
    }


def _record_webhook_result(
    db: Session,
    webhook_row: WebhookEvent,
    event_id: str,
    event_type: str,
    result: WebhookApplyResult,
) -> None:
    """Persist the terminal state of a processed webhook and audit it."""
    if result.outcome in _IGNORED_OUTCOMES:
        processing_state = WebhookProcessingState.IGNORED
    elif result.outcome == WEBHOOK_OUTCOME_AMOUNT_MISMATCH:
        processing_state = WebhookProcessingState.FAILED
    else:
        processing_state = WebhookProcessingState.PROCESSED

    _finalize_webhook_row(
        db,
        webhook_row,
        state=processing_state,
        session_id=result.session_id,
        provider_order_id=result.provider_order_id,
    )

    if processing_state is WebhookProcessingState.PROCESSED:
        record_audit_event(
            db,
            action=AuditAction.WEBHOOK_PROCESSED,
            actor=ACTOR_SYSTEM,
            session_id=result.session_id,
            reason=f"Webhook {event_id} applied: {result.outcome}.",
            outcome=AuditOutcome.SUCCESS,
            provider_order_id=result.provider_order_id,
            detail={"event_type": event_type, "outcome": result.outcome},
        )
    else:
        record_audit_event(
            db,
            action=AuditAction.WEBHOOK_PROCESSING_FAILED
            if processing_state is WebhookProcessingState.FAILED
            else AuditAction.WEBHOOK_PROCESSED,
            actor=ACTOR_SYSTEM,
            session_id=result.session_id,
            reason=f"Webhook {event_id} outcome: {result.outcome}.",
            outcome=AuditOutcome.INFO
            if processing_state is WebhookProcessingState.IGNORED
            else AuditOutcome.FAILURE,
            provider_order_id=result.provider_order_id,
            detail={"event_type": event_type, "outcome": result.outcome},
        )


def _finalize_webhook_row(
    db: Session,
    webhook_row: WebhookEvent,
    *,
    state: WebhookProcessingState,
    session_id: str | None = None,
    provider_order_id: str | None = None,
    error: str | None = None,
) -> None:
    """Write the durable outcome back onto the ``webhook_event`` row."""
    webhook_row.processing_state = state
    webhook_row.processed_at = datetime.now(UTC)
    if session_id is not None:
        webhook_row.session_id = session_id
    if provider_order_id is not None:
        webhook_row.provider_order_id = provider_order_id
    if error is not None:
        webhook_row.processing_error = error
    db.add(webhook_row)
    db.commit()
