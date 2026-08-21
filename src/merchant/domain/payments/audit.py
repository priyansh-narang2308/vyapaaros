# Copyright (c) 2026 VyapaarOS. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Append-only, hash-chained audit trail for money-affecting actions.

This is the artifact that answers "why did this happen?" without anyone reading
application logs. Every entry carries:

* **who** (``actor``) -- ``user``, ``agent:<name>``, ``system``, ``provider:razorpay``
* **what** (``action``) -- a value from :class:`AuditAction`, never free text
* **why** (``reason_code`` + ``reason``) -- a stable code plus one human sentence
* **the decision** (``policy_decision``) -- ALLOW / DENY / REQUIRE_APPROVAL
* **the outcome** (``outcome``) -- what actually happened

Entries are chained: each row stores the SHA-256 of the previous row for its
session, so deleting or editing an intermediate event is detectable by
:func:`verify_audit_chain`. Nothing in this module ever updates or deletes a row.

``detail_json`` holds structured facts only -- reason codes, limits, amounts,
signals. It must never contain model chain-of-thought.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlmodel import Session, col, func, select

from src.merchant.db.models import AuditEvent

logger = logging.getLogger(__name__)


class AuditAction(StrEnum):
    """Every money-affecting action worth an audit row.

    Closed set on purpose: a caller cannot invent an action name, so the trail
    stays queryable and the dashboard cannot drift from what is recorded.
    """

    # -- session lifecycle ------------------------------------------------
    CHECKOUT_SESSION_CREATED = "checkout_session_created"
    CHECKOUT_SESSION_UPDATED = "checkout_session_updated"
    CHECKOUT_CANCELED = "checkout_canceled"

    # -- cart / revenue actions -------------------------------------------
    UPSELL_PROPOSED = "upsell_proposed"
    UPSELL_ACCEPTED = "upsell_accepted"
    UPSELL_REJECTED = "upsell_rejected"
    CROSS_SELL_PROPOSED = "cross_sell_proposed"
    CROSS_SELL_ACCEPTED = "cross_sell_accepted"
    DISCOUNT_PROPOSED = "discount_proposed"
    DISCOUNT_CLAMPED = "discount_clamped"
    DISCOUNT_APPLIED = "discount_applied"

    # -- policy and approval ----------------------------------------------
    POLICY_EVALUATED = "policy_evaluated"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"

    # -- money movement ----------------------------------------------------
    AMOUNT_RECOMPUTED = "amount_recomputed"
    PAYMENT_ATTEMPT_CREATED = "payment_attempt_created"
    PAYMENT_ORDER_CREATED = "payment_order_created"
    PAYMENT_ORDER_CREATION_FAILED = "payment_order_creation_failed"
    PAYMENT_INITIATED = "payment_initiated"
    PAYMENT_SIGNATURE_VERIFIED = "payment_signature_verified"
    PAYMENT_SIGNATURE_REJECTED = "payment_signature_rejected"
    PAYMENT_AMOUNT_MISMATCH_REJECTED = "payment_amount_mismatch_rejected"
    PAYMENT_CAPTURED = "payment_captured"
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_RETRY_ALLOWED = "payment_retry_allowed"
    PAYMENT_RETRY_DENIED = "payment_retry_denied"
    ORDER_COMPLETED = "order_completed"

    # -- webhooks ----------------------------------------------------------
    WEBHOOK_RECEIVED = "webhook_received"
    WEBHOOK_SIGNATURE_REJECTED = "webhook_signature_rejected"
    WEBHOOK_DUPLICATE_IGNORED = "webhook_duplicate_ignored"
    WEBHOOK_PROCESSED = "webhook_processed"
    WEBHOOK_PROCESSING_FAILED = "webhook_processing_failed"

    # -- guardrails --------------------------------------------------------
    STATE_TRANSITION_BLOCKED = "state_transition_blocked"
    AGENT_TOOL_CALL_REJECTED = "agent_tool_call_rejected"


class AuditOutcome(StrEnum):
    """What actually happened, independent of what was decided."""

    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    PENDING = "pending"
    INFO = "info"


#: Actor constants. Agents use ``agent:<name>`` so the specific agent is visible.
ACTOR_USER = "user"
ACTOR_SYSTEM = "system"
ACTOR_PROVIDER_RAZORPAY = "provider:razorpay"


def agent_actor(name: str) -> str:
    """Build an agent actor string, e.g. ``agent_actor("upsell")``."""
    return f"agent:{name}"


def _canonical_timestamp(value: datetime) -> str:
    """Render a timestamp for hashing, tolerant of storage that drops tzinfo.

    We always write timezone-aware UTC, but SQLite hands the value back naive.
    Normalising to naive-UTC on both the write and the verify path keeps the
    hash stable across a round trip; without this every chain verifies as
    tampered the moment it is reloaded.
    """
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.replace(tzinfo=None).isoformat(timespec="microseconds")


def _canonical_payload(
    *,
    sequence: int,
    session_id: str | None,
    timestamp: str,
    actor: str,
    action: str,
    reason_code: str,
    reason: str,
    policy_decision: str,
    outcome: str,
    amount_paise: int | None,
    provider_order_id: str | None,
    provider_payment_id: str | None,
    payment_attempt_id: str | None,
    detail_json: str,
    prev_hash: str,
) -> str:
    """Serialise the fields covered by the chain hash, deterministically.

    Field order and separators are fixed here rather than derived from the model
    so that adding an unrelated column later cannot silently invalidate the hash
    of existing rows.
    """
    return "\x1f".join(
        [
            str(sequence),
            session_id or "",
            timestamp,
            actor,
            action,
            reason_code,
            reason,
            policy_decision,
            outcome,
            "" if amount_paise is None else str(amount_paise),
            provider_order_id or "",
            provider_payment_id or "",
            payment_attempt_id or "",
            detail_json,
            prev_hash,
        ]
    )


def record_audit_event(
    db: Session,
    *,
    action: AuditAction,
    actor: str,
    session_id: str | None = None,
    reason_code: str = "",
    reason: str = "",
    policy_decision: str = "not_applicable",
    outcome: AuditOutcome = AuditOutcome.INFO,
    amount_paise: int | None = None,
    provider_order_id: str | None = None,
    provider_payment_id: str | None = None,
    payment_attempt_id: str | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> AuditEvent:
    """Append one audit event and return it.

    The row is linked to the previous event *for the same session*, so each
    session has its own verifiable chain and concurrent sessions do not
    interleave into one another's hashes.

    Audit failure must never take down a payment: if the write raises, the error
    is logged and a detached, unpersisted :class:`AuditEvent` is returned so the
    caller can continue. A missing audit row is a reporting problem; a failed
    capture is a money problem.
    """
    detail_json = json.dumps(detail or {}, sort_keys=True, default=str)
    timestamp = datetime.now(UTC)

    try:
        prev = db.exec(
            select(AuditEvent)
            .where(AuditEvent.session_id == session_id)
            .order_by(col(AuditEvent.sequence).desc())
            .limit(1)
        ).first()
        prev_hash = prev.entry_hash if prev else ""
        sequence = (prev.sequence + 1) if prev else 1

        payload = _canonical_payload(
            sequence=sequence,
            session_id=session_id,
            timestamp=_canonical_timestamp(timestamp),
            actor=actor,
            action=action.value,
            reason_code=reason_code,
            reason=reason,
            policy_decision=policy_decision,
            outcome=outcome.value,
            amount_paise=amount_paise,
            provider_order_id=provider_order_id,
            provider_payment_id=provider_payment_id,
            payment_attempt_id=payment_attempt_id,
            detail_json=detail_json,
            prev_hash=prev_hash,
        )
        entry_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        event = AuditEvent(
            sequence=sequence,
            session_id=session_id,
            timestamp=timestamp,
            actor=actor,
            action=action.value,
            reason_code=reason_code,
            reason=reason,
            policy_decision=policy_decision,
            outcome=outcome.value,
            amount_paise=amount_paise,
            provider_order_id=provider_order_id,
            provider_payment_id=provider_payment_id,
            payment_attempt_id=payment_attempt_id,
            detail_json=detail_json,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        db.add(event)
        if commit:
            db.commit()
            db.refresh(event)

        logger.info(
            "AUDIT seq=%s session=%s actor=%s action=%s decision=%s outcome=%s",
            sequence,
            session_id,
            actor,
            action.value,
            policy_decision,
            outcome.value,
        )
        return event
    except Exception as exc:
        logger.error(
            "Failed to record audit event action=%s session=%s: %s",
            action.value,
            session_id,
            exc,
        )
        return AuditEvent(
            sequence=0,
            session_id=session_id,
            timestamp=timestamp,
            actor=actor,
            action=action.value,
            reason_code=reason_code,
            reason=reason,
            policy_decision=policy_decision,
            outcome=outcome.value,
            amount_paise=amount_paise,
            detail_json=detail_json,
        )


def get_audit_trail(db: Session, session_id: str) -> list[AuditEvent]:
    """Return a session's audit events in chain order."""
    return list(
        db.exec(
            select(AuditEvent)
            .where(AuditEvent.session_id == session_id)
            .order_by(col(AuditEvent.sequence).asc())
        ).all()
    )


def verify_audit_chain(db: Session, session_id: str) -> tuple[bool, str | None]:
    """Recompute a session's hash chain.

    Returns ``(True, None)`` when intact, otherwise ``(False, reason)`` naming
    the first sequence number that fails. Detects edited fields, a broken
    ``prev_hash`` link, and gaps left by a deleted row.
    """
    events = get_audit_trail(db, session_id)
    prev_hash = ""
    expected_sequence = 1

    for event in events:
        if event.sequence != expected_sequence:
            return False, (
                f"sequence gap at {event.sequence}: expected {expected_sequence} "
                "(an event was deleted or inserted out of order)"
            )
        if event.prev_hash != prev_hash:
            return False, f"broken link at sequence {event.sequence}"

        payload = _canonical_payload(
            sequence=event.sequence,
            session_id=event.session_id,
            timestamp=_canonical_timestamp(event.timestamp),
            actor=event.actor,
            action=event.action,
            reason_code=event.reason_code,
            reason=event.reason,
            policy_decision=event.policy_decision,
            outcome=event.outcome,
            amount_paise=event.amount_paise,
            provider_order_id=event.provider_order_id,
            provider_payment_id=event.provider_payment_id,
            payment_attempt_id=event.payment_attempt_id,
            detail_json=event.detail_json,
            prev_hash=event.prev_hash,
        )
        recomputed = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if recomputed != event.entry_hash:
            return False, f"tampered content at sequence {event.sequence}"

        prev_hash = event.entry_hash
        expected_sequence += 1

    return True, None


def count_audit_events(db: Session, session_id: str | None = None) -> int:
    """Count audit events, optionally for one session."""
    statement = select(func.count()).select_from(AuditEvent)
    if session_id is not None:
        statement = statement.where(AuditEvent.session_id == session_id)
    return db.exec(statement).one()
