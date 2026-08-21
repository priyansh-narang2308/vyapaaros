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

"""Payment attempts, authoritative amounts, and server-side approvals.

Three invariants live here.

**The amount is ours.** :func:`authoritative_total_paise` derives the payable
amount from the session's own stored totals, which are computed from product
rows in our database. No client-supplied amount is ever trusted, and the value
is re-derived at every decision point rather than carried along in a request.

**One order per attempt.** A :class:`PaymentAttempt` is the anti-duplicate
boundary. :func:`ensure_order_for_attempt` creates a provider order the first
time and returns the stored one thereafter, so a retried or replayed request
cannot mint a second order for the same attempt.

**Approval is server state.** A client cannot approve its own high-value order
by sending ``approved=true``. The backend raises an :class:`ApprovalRequest`,
the backend flips it to APPROVED, and :func:`find_granted_approval` only honours
a grant whose amount still matches the current cart to the paise.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, func, select

from src.merchant.db.models import (
    ApprovalRequest,
    ApprovalState,
    CheckoutSession,
    PaymentAttempt,
    PaymentAttemptState,
)
from src.merchant.domain.payments.policy import PolicyDecision
from src.merchant.domain.payments.provider import PaymentProvider, ProviderOrder

logger = logging.getLogger(__name__)


#: Attempt states that can still lead to a capture. A session with one of these
#: outstanding must not open a second attempt.
OPEN_ATTEMPT_STATES: frozenset[PaymentAttemptState] = frozenset(
    {PaymentAttemptState.CREATED, PaymentAttemptState.PENDING}
)


class AmountResolutionError(RuntimeError):
    """Raised when a session's payable amount cannot be established."""


# ---------------------------------------------------------------------------
# Authoritative amount (Phase 2)
# ---------------------------------------------------------------------------


def authoritative_total_paise(session: CheckoutSession) -> int:
    """Return the amount we are willing to charge, in paise.

    Read from ``session.totals_json``, which is produced server-side by
    ``calculate_totals`` from product rows. Raises rather than defaulting to
    zero: a zero-amount order must never reach a payment provider, and silently
    substituting 0 would turn a bug into a free order.
    """
    try:
        totals: Any = json.loads(session.totals_json or "[]")
    except json.JSONDecodeError as exc:
        raise AmountResolutionError(
            f"Session {session.id} has unreadable totals; refusing to price it."
        ) from exc

    if not isinstance(totals, list):
        raise AmountResolutionError(
            f"Session {session.id} totals are not a list; refusing to price it."
        )

    for entry in totals:
        if isinstance(entry, dict) and entry.get("type") == "total":
            amount = entry.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool):
                raise AmountResolutionError(
                    f"Session {session.id} total amount {amount!r} is not an integer "
                    "number of paise."
                )
            return amount

    raise AmountResolutionError(
        f"Session {session.id} has no 'total' line in its totals; refusing to price it."
    )


# ---------------------------------------------------------------------------
# Payment attempts (Phase 3)
# ---------------------------------------------------------------------------


def get_attempts(db: Session, session_id: str) -> list[PaymentAttempt]:
    """Return a session's payment attempts, oldest first."""
    return list(
        db.exec(
            select(PaymentAttempt)
            .where(PaymentAttempt.session_id == session_id)
            .order_by(col(PaymentAttempt.attempt_number).asc())
        ).all()
    )


def count_attempts(db: Session, session_id: str) -> int:
    """Count attempts made for a session, for the retry policy."""
    return db.exec(
        select(func.count())
        .select_from(PaymentAttempt)
        .where(PaymentAttempt.session_id == session_id)
    ).one()


def get_open_attempt(db: Session, session_id: str) -> PaymentAttempt | None:
    """Return the newest attempt that could still be captured, if any."""
    return db.exec(
        select(PaymentAttempt)
        .where(PaymentAttempt.session_id == session_id)
        .where(col(PaymentAttempt.state).in_(OPEN_ATTEMPT_STATES))
        .order_by(col(PaymentAttempt.attempt_number).desc())
        .limit(1)
    ).first()


def get_attempt_by_order_id(db: Session, provider_order_id: str) -> PaymentAttempt | None:
    """Look up the attempt that owns a provider order id.

    Used by the webhook path, where the provider order id is the only handle on
    the session. Returns the newest match, so that even if an id were ever
    reused the most recent attempt wins rather than a stale one.
    """
    return db.exec(
        select(PaymentAttempt)
        .where(PaymentAttempt.provider_order_id == provider_order_id)
        .order_by(col(PaymentAttempt.attempt_number).desc())
        .limit(1)
    ).first()


def create_payment_attempt(
    db: Session,
    *,
    session_id: str,
    amount_paise: int,
    currency: str,
    provider: str,
    policy_decision: PolicyDecision | None = None,
) -> PaymentAttempt:
    """Open a new attempt at the next attempt number.

    The amount is stamped onto the attempt so that a later callback is checked
    against what policy authorised, not against a cart that may have changed.
    """
    attempt = PaymentAttempt(
        id=f"pay_attempt_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        attempt_number=count_attempts(db, session_id) + 1,
        provider=provider,
        amount_paise=amount_paise,
        currency=currency.upper(),
        state=PaymentAttemptState.CREATED,
        policy_decision_json=policy_decision.to_json() if policy_decision else None,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    logger.info(
        "Opened payment attempt %s (#%s) for session %s amount_paise=%s",
        attempt.id,
        attempt.attempt_number,
        session_id,
        amount_paise,
    )
    return attempt


def ensure_order_for_attempt(
    db: Session,
    *,
    attempt: PaymentAttempt,
    provider: PaymentProvider,
    receipt: str,
) -> ProviderOrder:
    """Return the provider order for ``attempt``, creating it at most once.

    Idempotent by construction: once ``provider_order_id`` is set, the stored
    order is reconstructed instead of calling the provider again. This is what
    stops a duplicate ``POST`` from opening two payable orders for one cart.
    """
    if attempt.provider_order_id:
        logger.info(
            "Reusing existing provider order %s for attempt %s",
            attempt.provider_order_id,
            attempt.id,
        )
        return ProviderOrder(
            id=attempt.provider_order_id,
            amount_paise=attempt.amount_paise,
            currency=attempt.currency,
            receipt=receipt,
            status="created",
            provider=attempt.provider,
        )

    order = provider.create_order(
        amount_paise=attempt.amount_paise,
        currency=attempt.currency,
        receipt=receipt,
        notes={"payment_attempt_id": attempt.id, "checkout_session_id": receipt},
    )

    attempt.provider_order_id = order.id
    attempt.state = PaymentAttemptState.PENDING
    attempt.updated_at = datetime.now(UTC)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return order


def mark_attempt_captured(
    db: Session, *, attempt: PaymentAttempt, provider_payment_id: str
) -> PaymentAttempt:
    """Record a verified capture against an attempt."""
    attempt.provider_payment_id = provider_payment_id
    attempt.signature_verified = True
    attempt.state = PaymentAttemptState.CAPTURED
    attempt.updated_at = datetime.now(UTC)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def mark_attempt_failed(
    db: Session,
    *,
    attempt: PaymentAttempt,
    reason: str,
    code: str = "",
    provider_payment_id: str | None = None,
) -> PaymentAttempt:
    """Record a failed attempt with an operator-readable reason."""
    attempt.failure_reason = reason
    attempt.failure_code = code
    if provider_payment_id:
        attempt.provider_payment_id = provider_payment_id
    attempt.state = PaymentAttemptState.FAILED
    attempt.updated_at = datetime.now(UTC)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


# ---------------------------------------------------------------------------
# Approvals (Phase 11)
# ---------------------------------------------------------------------------


def open_approval_request(
    db: Session,
    *,
    session_id: str,
    decision: PolicyDecision,
    amount_paise: int,
) -> ApprovalRequest:
    """Raise (or reuse) a pending approval gate for ``amount_paise``.

    If a pending request already exists for exactly this amount it is returned
    unchanged, so repeated polling does not spawn a queue of gates. A pending
    request for a *different* amount is expired first: approving INR 8,000 must
    never authorise INR 80,000.
    """
    limit_paise = int(decision.limits.get("max_autonomous_order_value_paise", 0) or 0)

    existing = db.exec(
        select(ApprovalRequest)
        .where(ApprovalRequest.session_id == session_id)
        .where(ApprovalRequest.state == ApprovalState.PENDING)
        .order_by(col(ApprovalRequest.created_at).desc())
    ).all()

    for request in existing:
        if request.amount_paise == amount_paise:
            return request
        request.state = ApprovalState.EXPIRED
        request.resolved_at = datetime.now(UTC)
        db.add(request)
        logger.info(
            "Expired stale approval %s for session %s (amount changed %s -> %s)",
            request.id,
            session_id,
            request.amount_paise,
            amount_paise,
        )

    approval = ApprovalRequest(
        id=f"apr_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        state=ApprovalState.PENDING,
        reason_code=decision.reason_code.value,
        reason=decision.reason,
        amount_paise=amount_paise,
        limit_paise=limit_paise,
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    logger.info(
        "Raised approval %s for session %s amount_paise=%s reason=%s",
        approval.id,
        session_id,
        amount_paise,
        decision.reason_code.value,
    )
    return approval


def find_granted_approval(
    db: Session, *, session_id: str, amount_paise: int
) -> ApprovalRequest | None:
    """Return a live approval that covers exactly ``amount_paise``, else ``None``.

    The exact-amount match is deliberate. An approval granted for a smaller cart
    is not a blank cheque for a larger one, so adding an item after approval
    re-raises the gate instead of riding the old grant.
    """
    return db.exec(
        select(ApprovalRequest)
        .where(ApprovalRequest.session_id == session_id)
        .where(ApprovalRequest.state == ApprovalState.APPROVED)
        .where(ApprovalRequest.amount_paise == amount_paise)
        .order_by(col(ApprovalRequest.resolved_at).desc())
        .limit(1)
    ).first()


def get_pending_approval(db: Session, session_id: str) -> ApprovalRequest | None:
    """Return the session's outstanding approval gate, if one is open."""
    return db.exec(
        select(ApprovalRequest)
        .where(ApprovalRequest.session_id == session_id)
        .where(ApprovalRequest.state == ApprovalState.PENDING)
        .order_by(col(ApprovalRequest.created_at).desc())
        .limit(1)
    ).first()


def resolve_approval(
    db: Session,
    *,
    approval: ApprovalRequest,
    approved: bool,
    approved_by: str,
) -> ApprovalRequest:
    """Grant or deny an approval. Only the backend calls this."""
    if approval.state is not ApprovalState.PENDING:
        logger.warning(
            "Approval %s is already %s; refusing to re-resolve.",
            approval.id,
            approval.state.value,
        )
        return approval

    approval.state = ApprovalState.APPROVED if approved else ApprovalState.DENIED
    approval.approved_by = approved_by
    approval.resolved_at = datetime.now(UTC)
    db.add(approval)
    db.commit()
    db.refresh(approval)
    logger.info(
        "Approval %s resolved as %s by %s", approval.id, approval.state.value, approved_by
    )
    return approval
