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

"""Protocol-agnostic checkout session business logic service."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlmodel import Session, select

from src.merchant.db.models import (
    CheckoutSession,
    CheckoutStatus,
    PaymentAttemptState,
    Product,
)
from src.merchant.domain.checkout.calculations import (
    DEFAULT_CURRENCY,
    DEFAULT_SHOP_URL,
    address_input_to_dict,
    apply_discount_codes,
    buyer_input_to_dict,
    calculate_line_item_with_promotion,
    calculate_totals,
    check_ready_for_payment,
    generate_default_links,
    generate_fulfillment_options,
    generate_session_id,
    recalculate_line_item_from_existing,
    session_to_response,
)
from src.merchant.domain.checkout.models import (
    BuyerInput,
    CheckoutSessionResponse,
    CreateCheckoutRequest,
    PaymentDataInput,
    UpdateCheckoutRequest,
)
from src.merchant.domain.payments.attempts import (
    AmountResolutionError,
    authoritative_total_paise,
    create_payment_attempt,
    ensure_order_for_attempt,
    find_granted_approval,
    get_attempt_by_order_id,
    get_open_attempt,
    mark_attempt_captured,
    mark_attempt_failed,
    open_approval_request,
)
from src.merchant.domain.payments.audit import (
    ACTOR_PROVIDER_RAZORPAY,
    ACTOR_SYSTEM,
    ACTOR_USER,
    AuditAction,
    AuditOutcome,
    record_audit_event,
)
from src.merchant.domain.payments.policy import (
    PolicyDecision,
    evaluate_order_value_policy,
)
from src.merchant.domain.payments.provider import (
    PaymentProviderError,
    get_payment_provider,
    is_sandbox_active,
)
from src.merchant.domain.payments.state_machine import (
    InvalidPaymentTransitionError,
    assert_transition,
    is_transition_allowed,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Service Exceptions
# =============================================================================


class CheckoutServiceError(Exception):
    """Base exception for checkout service errors."""

    def __init__(self, message: str, code: str = "internal_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class SessionNotFoundError(CheckoutServiceError):
    """Raised when a checkout session is not found."""

    def __init__(self, session_id: str):
        super().__init__(
            message=f"Checkout session '{session_id}' not found",
            code="session_not_found",
        )


class ProductNotFoundError(CheckoutServiceError):
    """Raised when a product is not found."""

    def __init__(self, product_id: str):
        super().__init__(
            message=f"Product '{product_id}' not found",
            code="product_not_found",
        )


class InvalidStateTransitionError(CheckoutServiceError):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current_status: str, action: str):
        super().__init__(
            message=f"Cannot {action} session with status '{current_status}'",
            code="invalid_status_transition",
        )


class PaymentVerificationError(CheckoutServiceError):
    """Raised when a payment cannot be verified as authentic and authorised.

    Distinct from :class:`InvalidStateTransitionError` so the API can answer a
    forged signature, a spoofed order id, or an amount mismatch with a clean
    ``402 Payment Required`` instead of a generic 500. This is the "one failure
    handled gracefully" path: the money never moves and the reason is auditable.
    """

    def __init__(self, message: str, code: str = "payment_verification_failed"):
        super().__init__(message=message, code=code)


# =============================================================================
# Service Functions
# =============================================================================


def _extract_discount_codes_from_request(
    discounts: dict[str, list[str]] | None,
    coupons: list[str] | None,
) -> list[str] | None:
    """Extract submitted discount codes from request fields."""
    if discounts is not None:
        return discounts.get("codes", [])
    if coupons is not None:
        return coupons
    return None


def _get_existing_discount_codes(session: CheckoutSession) -> list[str]:
    """Read previously submitted discount codes from session metadata."""
    if not session.metadata_json:
        return []
    try:
        metadata_obj = json.loads(session.metadata_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(metadata_obj, dict):
        return []
    metadata = cast(dict[str, Any], metadata_obj)
    raw_discounts_obj = metadata.get("discounts", {})
    if not isinstance(raw_discounts_obj, dict):
        return []
    raw_discounts = cast(dict[str, Any], raw_discounts_obj)
    raw_codes_obj = raw_discounts.get("codes", [])
    if not isinstance(raw_codes_obj, list):
        return []
    raw_codes = cast(list[Any], raw_codes_obj)
    return [str(code) for code in raw_codes]


async def create_checkout_session(
    db: Session, request: CreateCheckoutRequest, protocol: str = "acp"
) -> CheckoutSessionResponse:
    """Create a new checkout session.

    Calls the Promotion Agent to get dynamic pricing for each item.
    Uses fail-open behavior: if agent is unavailable, continues without discounts.

    Args:
        db: Database session.
        request: CreateCheckoutRequest with items and optional buyer/address.

    Returns:
        CheckoutSessionResponse with the new session.

    Raises:
        ProductNotFoundError: If a product in items is not found.
    """
    session_id = generate_session_id()
    item_count = len(request.items)
    total_quantity = sum(item.quantity for item in request.items)

    logger.info(
        f"Creating checkout session {session_id} with {item_count} item(s), "
        f"total qty={total_quantity}"
    )

    # Build line items from products with promotion discounts
    line_items: list[dict[str, Any]] = []
    products_by_id: dict[str, Product] = {}
    for item in request.items:
        product = db.exec(select(Product).where(Product.id == item.id)).first()
        if product is None:
            logger.warning(f"Product not found: {item.id}")
            raise ProductNotFoundError(item.id)
        products_by_id[item.id] = product

        # Get line item with promotion discount (async call to agent)
        line_item = await calculate_line_item_with_promotion(db, product, item.quantity)
        line_items.append(line_item)

    submitted_codes = _extract_discount_codes_from_request(
        request.discounts, request.coupons
    )
    (
        line_items,
        discounts_payload,
        discount_warning_messages,
    ) = apply_discount_codes(line_items, products_by_id, submitted_codes)

    # Process optional buyer
    buyer_json = None
    if request.buyer:
        buyer_json = json.dumps(buyer_input_to_dict(request.buyer))

    # Process optional fulfillment address
    fulfillment_address_json = None
    has_address = request.fulfillment_address is not None
    if request.fulfillment_address:
        fulfillment_address_json = json.dumps(
            address_input_to_dict(request.fulfillment_address)
        )

    # Generate fulfillment options and auto-select default
    fulfillment_options: list[dict[str, Any]] = generate_fulfillment_options(
        has_address
    )
    selected_fulfillment_option_id: str | None = (
        fulfillment_options[0]["id"] if fulfillment_options else None
    )

    # Calculate totals (includes fulfillment cost when a default is selected)
    totals: list[dict[str, Any]] = calculate_totals(
        line_items, fulfillment_options, selected_fulfillment_option_id
    )

    # Generate default links
    links: list[dict[str, Any]] = generate_default_links()

    # Generate welcome message
    messages: list[dict[str, Any]] = [
        {
            "type": "info",
            "param": "$",
            "content_type": "plain",
            "content": "Welcome to checkout! Please complete all required fields.",
        }
    ]
    messages.extend(discount_warning_messages)
    metadata = {"discounts": discounts_payload}

    # Create database record
    checkout_session = CheckoutSession(
        id=session_id,
        protocol=protocol,
        status=CheckoutStatus.NOT_READY_FOR_PAYMENT,
        currency=DEFAULT_CURRENCY.upper(),
        line_items_json=json.dumps(line_items),
        buyer_json=buyer_json,
        fulfillment_address_json=fulfillment_address_json,
        fulfillment_options_json=json.dumps(fulfillment_options),
        selected_fulfillment_option_id=selected_fulfillment_option_id,
        totals_json=json.dumps(totals),
        messages_json=json.dumps(messages),
        links_json=json.dumps(links),
        metadata_json=json.dumps(metadata),
    )

    db.add(checkout_session)
    db.commit()
    db.refresh(checkout_session)

    # Calculate total for logging
    total_amount = next((t["amount"] for t in totals if t["type"] == "total"), 0)
    logger.info(
        f"Checkout session {session_id} created | "
        f"status={checkout_session.status.value} | "
        f"total=${total_amount / 100:.2f}"
    )

    return session_to_response(checkout_session)


async def create_checkout_session_from_data(
    db: Session,
    *,
    items: list[dict[str, Any]],
    buyer: dict[str, Any] | None = None,
    fulfillment_address: dict[str, Any] | None = None,
    discounts: dict[str, list[str]] | None = None,
    coupons: list[str] | None = None,
    protocol: str = "acp",
) -> CheckoutSessionResponse:
    """Protocol-agnostic create entry point used by protocol adapters."""
    request = CreateCheckoutRequest.model_validate(
        {
            "items": items,
            "buyer": buyer,
            "fulfillment_address": fulfillment_address,
            "discounts": discounts,
            "coupons": coupons,
        }
    )
    return await create_checkout_session(db, request, protocol=protocol)


def get_checkout_session(db: Session, session_id: str) -> CheckoutSessionResponse:
    """Get a checkout session by ID.

    Args:
        db: Database session.
        session_id: Checkout session ID.

    Returns:
        CheckoutSessionResponse with the session.

    Raises:
        SessionNotFoundError: If session is not found.
    """
    session = db.exec(
        select(CheckoutSession).where(CheckoutSession.id == session_id)
    ).first()

    if session is None:
        raise SessionNotFoundError(session_id)

    return session_to_response(session)


# =============================================================================
# Payment preparation (Phase 2/3/6/11): the only path that makes a cart payable
# =============================================================================


def _policy_message(text: str) -> dict[str, Any]:
    """Build a client-visible policy-gate message."""
    return {
        "type": "error",
        "param": "policy_gate",
        "content_type": "plain",
        "content": f"[POLICY GATE]: {text}",
    }


def _transition(
    session: CheckoutSession,
    target: CheckoutStatus,
    *,
    reason: str,
    action: str = "update",
) -> None:
    """Route a status write through the closed-whitelist state machine.

    The LLM never assigns ``session.status`` directly; every write goes through
    here. Assigning the current state is treated as a no-op (so re-preparing an
    already-ready session does not trip the "no self-loop" rule), and an illegal
    move is surfaced as a clean ``405`` rather than an uncaught 500.
    """
    if session.status == target:
        return
    try:
        session.status = assert_transition(session.status, target, reason=reason)
    except InvalidPaymentTransitionError as exc:
        logger.warning(
            "Blocked illegal transition %s -> %s for session %s: %s",
            session.status.value,
            target.value,
            session.id,
            reason,
        )
        raise InvalidStateTransitionError(session.status.value, action) from exc


def _walk_back_to_not_ready(session: CheckoutSession, *, reason: str) -> None:
    """Move a session back to NOT_READY when it cannot be made payable."""
    if is_transition_allowed(session.status, CheckoutStatus.NOT_READY_FOR_PAYMENT):
        session.status = assert_transition(
            session.status, CheckoutStatus.NOT_READY_FOR_PAYMENT, reason=reason
        )


def _prepare_session_for_payment(
    db: Session, session: CheckoutSession
) -> tuple[str, list[dict[str, Any]]]:
    """Price, gate, and (if permitted) open a payable order for a ready session.

    This is the single place a checkout becomes payable, and it is a backend
    path the model cannot reach. It recomputes the amount from server-side
    totals, runs the deterministic policy engine, and then either

    * denies (``-> not_ready_for_payment``) an order that violates a hard limit,
    * raises an approval gate (``-> awaiting_approval``) for a high-value order
      with no matching human approval, or
    * opens exactly one payment attempt + provider order and moves the session
      to ``ready_for_payment``.

    Returns the banner message and any policy messages to surface to the client.
    """
    session_id = session.id
    messages: list[dict[str, Any]] = []

    # 1. The amount is ours -- recomputed from stored totals, never the client's.
    try:
        amount_paise = authoritative_total_paise(session)
    except AmountResolutionError as exc:
        logger.error("Cannot price session %s: %s", session_id, exc)
        _walk_back_to_not_ready(session, reason="amount could not be resolved")
        return "Unable to price this order. Please review your cart.", messages

    # 2. Deterministic policy decision, recorded before anything acts on it.
    decision = evaluate_order_value_policy(amount_paise)
    record_audit_event(
        db,
        action=AuditAction.POLICY_EVALUATED,
        actor=ACTOR_SYSTEM,
        session_id=session_id,
        reason_code=decision.reason_code.value,
        reason=decision.reason,
        policy_decision=decision.decision.value,
        outcome=AuditOutcome.INFO,
        amount_paise=amount_paise,
        detail=decision.to_dict(),
    )

    if decision.denied:
        record_audit_event(
            db,
            action=AuditAction.POLICY_DENIED,
            actor=ACTOR_SYSTEM,
            session_id=session_id,
            reason_code=decision.reason_code.value,
            reason=decision.reason,
            policy_decision=decision.decision.value,
            outcome=AuditOutcome.BLOCKED,
            amount_paise=amount_paise,
        )
        _walk_back_to_not_ready(session, reason=decision.reason_code.value)
        messages.append(_policy_message(decision.reason))
        return "This order cannot be completed as configured.", messages

    if decision.requires_approval:
        approval = find_granted_approval(
            db, session_id=session_id, amount_paise=amount_paise
        )
        if approval is None:
            open_approval_request(
                db, session_id=session_id, decision=decision, amount_paise=amount_paise
            )
            _transition(
                session,
                CheckoutStatus.AWAITING_APPROVAL,
                reason=decision.reason_code.value,
            )
            record_audit_event(
                db,
                action=AuditAction.APPROVAL_REQUIRED,
                actor=ACTOR_SYSTEM,
                session_id=session_id,
                reason_code=decision.reason_code.value,
                reason=decision.reason,
                policy_decision=decision.decision.value,
                outcome=AuditOutcome.PENDING,
                amount_paise=amount_paise,
            )
            messages.append(
                _policy_message(f"{decision.reason} Manual approval is required.")
            )
            return "This order needs approval before payment.", messages

        # A live, exact-amount approval exists: record the grant and continue.
        record_audit_event(
            db,
            action=AuditAction.APPROVAL_GRANTED,
            actor=ACTOR_USER,
            session_id=session_id,
            reason_code="APPROVAL_GRANTED",
            reason=f"Approval {approval.id} authorises this amount.",
            policy_decision=decision.decision.value,
            outcome=AuditOutcome.SUCCESS,
            amount_paise=amount_paise,
            detail={"approval_id": approval.id, "approved_by": approval.approved_by},
        )

    # 3. Open (or reuse) exactly one payment attempt and its provider order.
    return _open_payable_order(db, session, amount_paise, decision, messages)


def _open_payable_order(
    db: Session,
    session: CheckoutSession,
    amount_paise: int,
    decision: PolicyDecision,
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Ensure one attempt + one provider order exist, then mark session ready."""
    session_id = session.id
    provider = get_payment_provider()

    # Reuse the open attempt unless the cart value changed since it was opened;
    # a stale attempt (and its order) must never be payable at the new amount.
    attempt = get_open_attempt(db, session_id)
    if attempt is not None and attempt.amount_paise != amount_paise:
        attempt.state = PaymentAttemptState.ABANDONED
        attempt.updated_at = datetime.now(UTC)
        db.add(attempt)
        db.commit()
        attempt = None

    if attempt is None:
        attempt = create_payment_attempt(
            db,
            session_id=session_id,
            amount_paise=amount_paise,
            currency=session.currency,
            provider=provider.name,
            policy_decision=decision,
        )
        record_audit_event(
            db,
            action=AuditAction.PAYMENT_ATTEMPT_CREATED,
            actor=ACTOR_SYSTEM,
            session_id=session_id,
            reason_code=decision.reason_code.value,
            reason="Payment attempt opened.",
            policy_decision=decision.decision.value,
            outcome=AuditOutcome.SUCCESS,
            amount_paise=amount_paise,
            payment_attempt_id=attempt.id,
        )

    try:
        order = ensure_order_for_attempt(
            db, attempt=attempt, provider=provider, receipt=session_id
        )
    except PaymentProviderError as exc:
        logger.error("Provider order creation failed for %s: %s", session_id, exc)
        record_audit_event(
            db,
            action=AuditAction.PAYMENT_ORDER_CREATION_FAILED,
            actor=ACTOR_SYSTEM,
            session_id=session_id,
            reason="Provider order creation failed.",
            outcome=AuditOutcome.FAILURE,
            amount_paise=amount_paise,
            payment_attempt_id=attempt.id,
            detail={"error": str(exc)},
        )
        _walk_back_to_not_ready(session, reason="provider order creation failed")
        return "Unable to create payment order. Please try again shortly.", messages

    session.order_json = json.dumps(
        {
            "provider": order.provider,
            "id": order.id,
            "checkout_session_id": session_id,
            "amount": order.amount_paise,
            "currency": order.currency,
            "payment_attempt_id": attempt.id,
        }
    )
    _transition(
        session,
        CheckoutStatus.READY_FOR_PAYMENT,
        reason="policy allowed; provider order created",
    )
    record_audit_event(
        db,
        action=AuditAction.PAYMENT_ORDER_CREATED,
        actor=ACTOR_SYSTEM,
        session_id=session_id,
        reason_code=decision.reason_code.value,
        reason="Provider order created; session ready for payment.",
        policy_decision=decision.decision.value,
        outcome=AuditOutcome.SUCCESS,
        amount_paise=amount_paise,
        provider_order_id=order.id,
        payment_attempt_id=attempt.id,
    )
    return "Ready for payment! Review your order and proceed.", messages


async def update_checkout_session(
    db: Session, session_id: str, request: UpdateCheckoutRequest
) -> CheckoutSessionResponse:
    """Update a checkout session.

    Reuses existing promotion data when items are updated to avoid
    re-calling the promotion agent. Only session creation triggers
    the promotion agent.

    Args:
        db: Database session.
        session_id: Checkout session ID.
        request: UpdateCheckoutRequest with fields to update.

    Returns:
        CheckoutSessionResponse with the updated session.

    Raises:
        SessionNotFoundError: If session is not found.
        ProductNotFoundError: If a product in items is not found.
        InvalidStateTransitionError: If session is completed or canceled.
    """
    session = db.exec(
        select(CheckoutSession).where(CheckoutSession.id == session_id)
    ).first()

    if session is None:
        logger.warning(f"Session not found for update: {session_id}")
        raise SessionNotFoundError(session_id)

    # Check if session can be updated
    if session.status in (CheckoutStatus.COMPLETED, CheckoutStatus.CANCELED):
        logger.warning(
            f"Invalid update attempt on session {session_id} | "
            f"status={session.status.value}"
        )
        raise InvalidStateTransitionError(session.status.value, "update")

    # Track what's being updated for logging
    update_fields: list[str] = []
    if request.items is not None:
        update_fields.append("items")
    if request.buyer is not None:
        update_fields.append("buyer")
    if request.fulfillment_address is not None:
        update_fields.append("address")
    if request.fulfillment_option_id is not None:
        update_fields.append("shipping")
    if request.discounts is not None or request.coupons is not None:
        update_fields.append("discounts")

    logger.debug(f"Updating session {session_id} | fields={update_fields}")

    # Update items if provided (reuse existing promotion data, no agent call)
    products_by_id: dict[str, Product] = {}
    if request.items is not None:
        # Build lookup of existing line items by product ID
        existing_line_items: list[dict[str, Any]] = json.loads(session.line_items_json)
        existing_by_product_id: dict[str, dict[str, Any]] = {
            li["item"]["id"]: li for li in existing_line_items
        }

        new_line_items: list[dict[str, Any]] = []
        for item in request.items:
            product = db.exec(select(Product).where(Product.id == item.id)).first()
            if product is None:
                raise ProductNotFoundError(item.id)
            products_by_id[item.id] = product

            # Check if this product has existing promotion data
            existing_li = existing_by_product_id.get(item.id)
            if existing_li is not None:
                # Reuse existing promotion, just recalculate totals for new quantity
                line_item = recalculate_line_item_from_existing(
                    product, item.quantity, existing_li
                )
            else:
                # New product added to cart - call promotion agent
                line_item = await calculate_line_item_with_promotion(
                    db, product, item.quantity
                )
            new_line_items.append(line_item)
        session.line_items_json = json.dumps(new_line_items)
    else:
        current_line_items: list[dict[str, Any]] = json.loads(session.line_items_json)
        for line_item in current_line_items:
            product_id = str(line_item["item"]["id"])
            product = db.exec(select(Product).where(Product.id == product_id)).first()
            if product is not None:
                products_by_id[product_id] = product

    # Update buyer if provided
    if request.buyer is not None:
        session.buyer_json = json.dumps(buyer_input_to_dict(request.buyer))

    # Update fulfillment address if provided
    if request.fulfillment_address is not None:
        session.fulfillment_address_json = json.dumps(
            address_input_to_dict(request.fulfillment_address)
        )
        # Regenerate fulfillment options when address changes
        new_options: list[dict[str, Any]] = generate_fulfillment_options(
            has_address=True
        )
        session.fulfillment_options_json = json.dumps(new_options)

    # Update fulfillment option selection if provided
    if request.fulfillment_option_id is not None:
        # Validate the option exists
        current_options: list[dict[str, Any]] = json.loads(
            session.fulfillment_options_json
        )
        valid_ids = [opt["id"] for opt in current_options]
        if request.fulfillment_option_id in valid_ids:
            session.selected_fulfillment_option_id = request.fulfillment_option_id

    # Recalculate totals
    current_line_items: list[dict[str, Any]] = json.loads(session.line_items_json)
    submitted_codes = _extract_discount_codes_from_request(
        request.discounts, request.coupons
    )
    if submitted_codes is None:
        submitted_codes = _get_existing_discount_codes(session)

    (
        current_line_items,
        discounts_payload,
        discount_warning_messages,
    ) = apply_discount_codes(current_line_items, products_by_id, submitted_codes)
    session.line_items_json = json.dumps(current_line_items)

    current_fulfillment_options: list[dict[str, Any]] = json.loads(
        session.fulfillment_options_json
    )
    updated_totals: list[dict[str, Any]] = calculate_totals(
        current_line_items,
        current_fulfillment_options,
        session.selected_fulfillment_option_id,
    )
    session.totals_json = json.dumps(updated_totals)

    # Check if ready for payment and update status. All pricing, policy gating,
    # provider-order creation, and the state transition live in the payments
    # domain (_prepare_session_for_payment) so the agent path cannot reach them.
    base_message = "Welcome to checkout! Please complete all required fields."
    policy_messages: list[dict[str, Any]] = []

    if check_ready_for_payment(session):
        base_message, policy_messages = _prepare_session_for_payment(db, session)
    else:
        _transition(
            session,
            CheckoutStatus.NOT_READY_FOR_PAYMENT,
            reason="required checkout fields incomplete",
        )

    session.messages_json = json.dumps(
        [
            {
                "type": "info",
                "param": "$",
                "content_type": "plain",
                "content": base_message,
            },
            *discount_warning_messages,
            *policy_messages,
        ]
    )
    metadata: dict[str, Any] = {}
    if session.metadata_json:
        try:
            metadata_obj = json.loads(session.metadata_json)
        except json.JSONDecodeError:
            metadata_obj = {}
        if isinstance(metadata_obj, dict):
            metadata = cast(dict[str, Any], metadata_obj)
    metadata["discounts"] = discounts_payload
    session.metadata_json = json.dumps(metadata)

    session.updated_at = datetime.now(UTC)
    db.add(session)
    db.commit()
    db.refresh(session)

    # Log status transition if it happened
    logger.info(
        f"Session {session_id} updated | "
        f"status={session.status.value} | "
        f"fields={update_fields}"
    )

    return session_to_response(session)


async def update_checkout_session_from_data(
    db: Session,
    session_id: str,
    *,
    items: list[dict[str, Any]] | None = None,
    buyer: dict[str, Any] | None = None,
    fulfillment_address: dict[str, Any] | None = None,
    fulfillment_option_id: str | None = None,
    discounts: dict[str, list[str]] | None = None,
    coupons: list[str] | None = None,
) -> CheckoutSessionResponse:
    """Protocol-agnostic update entry point used by protocol adapters."""
    request = UpdateCheckoutRequest.model_validate(
        {
            "items": items,
            "buyer": buyer,
            "fulfillment_address": fulfillment_address,
            "fulfillment_option_id": fulfillment_option_id,
            "discounts": discounts,
            "coupons": coupons,
        }
    )
    return await update_checkout_session(db, session_id, request)


#: States from which a checkout may still be completed. Terminal states
#: (COMPLETED, CANCELED) and pre-payment states (NOT_READY, AWAITING_APPROVAL)
#: are excluded so completion cannot skip the gate or double-fire.
_COMPLETABLE_STATES: frozenset[CheckoutStatus] = frozenset(
    {
        CheckoutStatus.READY_FOR_PAYMENT,
        CheckoutStatus.PAYMENT_PENDING,
        CheckoutStatus.CAPTURED,
    }
)


def _finalize_completed(
    db: Session,
    session: CheckoutSession,
    *,
    actor: str,
    reason: str,
) -> CheckoutSessionResponse:
    """Move a captured session to COMPLETED and record the terminal audit row."""
    _transition(session, CheckoutStatus.COMPLETED, reason=reason, action="complete")

    order_data = json.loads(session.order_json) if session.order_json else {}
    order_id = order_data.get("id", "Unknown")

    session.messages_json = json.dumps(
        [
            {
                "type": "info",
                "param": "$",
                "content_type": "plain",
                "content": f"Order {order_id} confirmed! Thank you for your purchase.",
            }
        ]
    )
    session.updated_at = datetime.now(UTC)
    db.add(session)
    db.commit()
    db.refresh(session)

    record_audit_event(
        db,
        action=AuditAction.ORDER_COMPLETED,
        actor=actor,
        session_id=session.id,
        reason=reason,
        outcome=AuditOutcome.SUCCESS,
        amount_paise=order_data.get("amount"),
        provider_order_id=order_data.get("id"),
        provider_payment_id=order_data.get("payment_id"),
    )
    logger.info("Order %s completed | session=%s", order_id, session.id)
    return session_to_response(session)


def _reject_payment(
    db: Session,
    session: CheckoutSession,
    attempt: Any | None,
    *,
    reason: str,
    action: AuditAction = AuditAction.PAYMENT_SIGNATURE_REJECTED,
) -> None:
    """Record a rejected payment. The session is left payable so a genuine
    submission can still succeed; only the forged/mismatched attempt is refused.
    """
    logger.error("Payment rejected for session %s: %s", session.id, reason)
    record_audit_event(
        db,
        action=action,
        actor=ACTOR_USER,
        session_id=session.id,
        reason=reason,
        outcome=AuditOutcome.BLOCKED,
        payment_attempt_id=getattr(attempt, "id", None),
        provider_order_id=getattr(attempt, "provider_order_id", None),
    )


def _capture_razorpay_payment(
    db: Session,
    session: CheckoutSession,
    attempt: Any | None,
    token: str,
) -> None:
    """Verify a Razorpay Standard Checkout callback and capture the attempt.

    The authoritative order id is the one on our own :class:`PaymentAttempt`, not
    the value in the client token. The token's order id must equal it, and the
    HMAC signature must verify, before any state moves. A malformed token is a
    hard failure -- it is never silently ignored.
    """
    try:
        token_data = json.loads(token)
    except json.JSONDecodeError as exc:
        _reject_payment(db, session, attempt, reason="Payment token is not valid JSON.")
        raise PaymentVerificationError("Payment token is not valid JSON.") from exc

    if not isinstance(token_data, dict):
        _reject_payment(db, session, attempt, reason="Payment token is not an object.")
        raise PaymentVerificationError("Payment token is not an object.")

    rzp_order_id = str(token_data.get("razorpay_order_id") or "")
    rzp_payment_id = str(token_data.get("razorpay_payment_id") or "")
    rzp_signature = token_data.get("razorpay_signature")

    if attempt is None or not attempt.provider_order_id:
        _reject_payment(
            db, session, attempt, reason="No open payment attempt for this session."
        )
        raise PaymentVerificationError("No open payment attempt for this session.")

    # The order id is ours, never the client's. Compare, do not substitute.
    if rzp_order_id != attempt.provider_order_id:
        _reject_payment(
            db,
            session,
            attempt,
            reason=(
                f"Order id mismatch: token {rzp_order_id!r} != "
                f"attempt {attempt.provider_order_id!r}."
            ),
        )
        raise PaymentVerificationError("Payment order id does not match this checkout.")

    provider = get_payment_provider()
    if not provider.verify_payment_signature(
        order_id=rzp_order_id, payment_id=rzp_payment_id, signature=rzp_signature
    ):
        _reject_payment(db, session, attempt, reason="Payment signature verification failed.")
        raise PaymentVerificationError("Payment signature verification failed.")

    # Verified. The amount is anchored to the signed order (which we created with
    # our server-side amount), so there is no client amount to re-check here.
    _transition(
        session, CheckoutStatus.PAYMENT_PENDING, reason="payment submitted", action="complete"
    )
    record_audit_event(
        db,
        action=AuditAction.PAYMENT_SIGNATURE_VERIFIED,
        actor=ACTOR_PROVIDER_RAZORPAY,
        session_id=session.id,
        reason="Razorpay payment signature verified.",
        outcome=AuditOutcome.SUCCESS,
        amount_paise=attempt.amount_paise,
        provider_order_id=rzp_order_id,
        provider_payment_id=rzp_payment_id,
        payment_attempt_id=attempt.id,
    )

    mark_attempt_captured(db, attempt=attempt, provider_payment_id=rzp_payment_id)
    _transition(
        session, CheckoutStatus.CAPTURED, reason="signature verified", action="complete"
    )

    order_data = json.loads(session.order_json) if session.order_json else {}
    order_data["payment_id"] = rzp_payment_id
    order_data["permalink_url"] = f"{DEFAULT_SHOP_URL}/orders/{rzp_order_id}"
    order_data.setdefault("checkout_session_id", session.id)
    session.order_json = json.dumps(order_data)

    record_audit_event(
        db,
        action=AuditAction.PAYMENT_CAPTURED,
        actor=ACTOR_PROVIDER_RAZORPAY,
        session_id=session.id,
        reason="Payment captured after signature verification.",
        outcome=AuditOutcome.SUCCESS,
        amount_paise=attempt.amount_paise,
        provider_order_id=rzp_order_id,
        provider_payment_id=rzp_payment_id,
        payment_attempt_id=attempt.id,
    )


def _capture_delegated_payment(
    db: Session,
    session: CheckoutSession,
    attempt: Any | None,
    payment_data: PaymentDataInput,
) -> None:
    """Handle the legacy ACP delegated-token rail (Stripe/Adyen-style tokens).

    A delegated token is charged by the PSP off-platform, so we cannot verify it
    cryptographically here. It is therefore accepted **only** in sandbox/test
    mode; in live mode it is refused so real money never moves on an unverifiable
    token. Callers must use the Razorpay rail in production.
    """
    token = (payment_data.token or "").strip()
    if not token:
        _reject_payment(db, session, attempt, reason="Missing payment token.")
        raise PaymentVerificationError("A payment token is required.")

    if not is_sandbox_active():
        _reject_payment(
            db,
            session,
            attempt,
            reason="Delegated payment token refused outside sandbox mode.",
        )
        raise PaymentVerificationError(
            "Delegated payment tokens are only accepted in sandbox/test mode. "
            "Use the Razorpay payment rail in production.",
            code="delegated_not_allowed",
        )

    provider = get_payment_provider()
    provider_order_id = getattr(attempt, "provider_order_id", None) or f"order_{session.id}"
    payment_id_for = getattr(provider, "payment_id_for", None)
    payment_id = (
        payment_id_for(provider_order_id)
        if callable(payment_id_for)
        else f"pay_sandbox_{session.id}"
    )

    _transition(
        session,
        CheckoutStatus.PAYMENT_PENDING,
        reason="delegated payment submitted (sandbox)",
        action="complete",
    )
    if attempt is not None:
        mark_attempt_captured(db, attempt=attempt, provider_payment_id=payment_id)
    _transition(
        session,
        CheckoutStatus.CAPTURED,
        reason="delegated payment simulated (sandbox)",
        action="complete",
    )

    order_data = json.loads(session.order_json) if session.order_json else {}
    if not order_data:
        order_data = {"provider": provider.name, "id": provider_order_id}
    order_data["payment_id"] = payment_id
    order_data["permalink_url"] = f"{DEFAULT_SHOP_URL}/orders/{order_data.get('id')}"
    order_data.setdefault("checkout_session_id", session.id)
    session.order_json = json.dumps(order_data)

    record_audit_event(
        db,
        action=AuditAction.PAYMENT_CAPTURED,
        actor=ACTOR_SYSTEM,
        session_id=session.id,
        reason="Delegated payment simulated and captured in sandbox/test mode.",
        outcome=AuditOutcome.SUCCESS,
        amount_paise=getattr(attempt, "amount_paise", None),
        provider_order_id=order_data.get("id"),
        provider_payment_id=payment_id,
        payment_attempt_id=getattr(attempt, "id", None),
        detail={"test_mode": True, "rail": "delegated"},
    )


def complete_checkout_session(
    db: Session,
    session_id: str,
    payment_data: PaymentDataInput,
    buyer: BuyerInput | None = None,
) -> CheckoutSessionResponse:
    """Complete a checkout session with a verified payment.

    Dispatches on the payment token: a Razorpay callback token is verified with a
    real HMAC signature check against the order id we created, while a legacy
    delegated token is accepted only in sandbox/test mode. Every status change is
    routed through the payment state machine, and each step is audited. The money
    path the model can trigger ends here at the backend, never in the model.
    """
    logger.info(f"Completing checkout session {session_id}")

    session = db.exec(
        select(CheckoutSession).where(CheckoutSession.id == session_id)
    ).first()

    if session is None:
        logger.warning(f"Session not found for completion: {session_id}")
        raise SessionNotFoundError(session_id)

    # Update buyer if provided (allowed regardless of the rail taken below).
    if buyer is not None:
        session.buyer_json = json.dumps(buyer_input_to_dict(buyer))

    # Idempotent finish: already captured out-of-band (e.g. by a webhook).
    if session.status == CheckoutStatus.CAPTURED:
        logger.info(f"Session {session_id} was already captured; finalizing.")
        return _finalize_completed(
            db,
            session,
            actor=ACTOR_SYSTEM,
            reason="Order confirmed after out-of-band capture.",
        )

    # A caller may complete a session that was never explicitly moved to READY.
    # The UCP flow in particular completes right after create with no update
    # step. If the session now satisfies every required field, run the same
    # server-side gate the update path uses -- pricing the amount from stored
    # totals, applying the deterministic policy, and opening exactly one payment
    # attempt + provider order -- before any payment is accepted. Sessions that
    # remain incomplete, that policy denies, or that policy routes to
    # AWAITING_APPROVAL do not become payable here and still fall through to the
    # 405 below. This runs entirely in the backend domain; the LLM cannot reach
    # it, and the amount/policy/order are always ours, never the client's.
    if (
        session.status
        in (
            CheckoutStatus.NOT_READY_FOR_PAYMENT,
            CheckoutStatus.AWAITING_APPROVAL,
        )
        and check_ready_for_payment(session)
    ):
        _prepare_session_for_payment(db, session)
        session.updated_at = datetime.now(UTC)
        db.add(session)
        db.commit()
        db.refresh(session)

    # Only genuinely payable sessions may be completed.
    if session.status not in _COMPLETABLE_STATES:
        logger.warning(
            f"Session {session_id} cannot be completed from status {session.status.value}"
        )
        raise InvalidStateTransitionError(session.status.value, "complete")

    attempt = get_open_attempt(db, session_id)
    token = payment_data.token or ""

    # Dispatch: a Razorpay callback token carries "razorpay_order_id"; anything
    # else is treated as a legacy delegated token (sandbox-only).
    if "razorpay_order_id" in token:
        _capture_razorpay_payment(db, session, attempt, token)
    else:
        _capture_delegated_payment(db, session, attempt, payment_data)

    return _finalize_completed(
        db,
        session,
        actor=ACTOR_USER,
        reason="Payment verified; order confirmed.",
    )



def complete_checkout_session_from_data(
    db: Session,
    session_id: str,
    *,
    payment_data: dict[str, Any],
    buyer: dict[str, Any] | None = None,
) -> CheckoutSessionResponse:
    """Protocol-agnostic complete entry point used by protocol adapters."""
    typed_payment = PaymentDataInput.model_validate(payment_data)
    typed_buyer = BuyerInput.model_validate(buyer) if buyer is not None else None
    return complete_checkout_session(
        db,
        session_id,
        typed_payment,
        buyer=typed_buyer,
    )


# =============================================================================
# Provider webhook application (Phase 7-9)
# =============================================================================


#: Outcome tokens returned by :func:`apply_razorpay_webhook_event`. They map onto
#: the durable ``WebhookProcessingState`` in the route layer.
WEBHOOK_OUTCOME_CAPTURED = "captured"
WEBHOOK_OUTCOME_FAILED = "failed"
WEBHOOK_OUTCOME_ALREADY_PROCESSED = "already_processed"
WEBHOOK_OUTCOME_AMOUNT_MISMATCH = "amount_mismatch"
WEBHOOK_OUTCOME_NOT_FOUND = "not_found"
WEBHOOK_OUTCOME_IGNORED = "ignored"

#: Events we act on. Anything else is durably recorded but ignored.
_WEBHOOK_HANDLED_EVENTS = frozenset({"payment.captured", "payment.failed"})


@dataclass(frozen=True)
class WebhookApplyResult:
    """The result of applying one verified provider webhook to our own state.

    ``outcome`` is one of the ``WEBHOOK_OUTCOME_*`` tokens; ``session_id`` and
    ``provider_order_id`` are echoed back so the route can annotate the durable
    :class:`WebhookEvent` row for later inspection.
    """

    outcome: str
    session_id: str | None = None
    provider_order_id: str | None = None


def apply_razorpay_webhook_event(
    db: Session,
    *,
    event_type: str,
    payment_entity: dict[str, Any],
) -> WebhookApplyResult:
    """Apply an already-verified, already-deduped Razorpay webhook to our state.

    The caller (the webhook route) is responsible for the transport-level
    guarantees -- verifying the HMAC signature over the *raw* body before this
    runs, and durably rejecting duplicate deliveries. This function owns the
    money semantics, and every one of them is defensive:

    * The order id in the webhook is resolved to *our* :class:`PaymentAttempt`;
      an unknown order id is recorded and ignored, never trusted.
    * A ``payment.captured`` amount is checked against the amount we signed onto
      that attempt. A mismatch is refused and audited
      (:class:`AuditAction.PAYMENT_AMOUNT_MISMATCH_REJECTED`) -- a capture for a
      different amount than we authorised never moves the session to captured.
    * Every status change is routed through :func:`_transition`, so the webhook
      obeys the same closed state machine as the in-band path (a late webhook on
      an already-terminal session is a no-op, not an illegal jump).
    * Each money step writes an audit row, so the async path is as explainable
      as the synchronous one.

    Raises nothing for expected anomalies (unknown order, mismatch, replay);
    they are returned as outcomes so the route can answer 200 and avoid a
    provider retry storm. Genuinely illegal transitions surface as
    :class:`InvalidStateTransitionError` for the route to record.
    """
    if event_type not in _WEBHOOK_HANDLED_EVENTS:
        return WebhookApplyResult(WEBHOOK_OUTCOME_IGNORED)

    order_id = str(payment_entity.get("order_id") or "")
    payment_id = str(payment_entity.get("id") or "")
    if not order_id:
        logger.warning("Razorpay webhook %s missing order_id; ignoring.", event_type)
        return WebhookApplyResult(WEBHOOK_OUTCOME_NOT_FOUND)

    # The order id is resolved to our own attempt -- the authoritative link
    # between a provider order and a checkout session (and its signed amount).
    attempt = get_attempt_by_order_id(db, order_id)
    if attempt is None:
        logger.warning("No payment attempt for webhook order %s; ignoring.", order_id)
        return WebhookApplyResult(
            WEBHOOK_OUTCOME_NOT_FOUND, provider_order_id=order_id
        )

    session = db.exec(
        select(CheckoutSession).where(CheckoutSession.id == attempt.session_id)
    ).first()
    if session is None:
        logger.warning(
            "Attempt %s references missing session %s; ignoring webhook.",
            attempt.id,
            attempt.session_id,
        )
        return WebhookApplyResult(
            WEBHOOK_OUTCOME_NOT_FOUND, provider_order_id=order_id
        )

    if event_type == "payment.captured":
        return _apply_captured_webhook(
            db, session, attempt, payment_entity, order_id, payment_id
        )
    return _apply_failed_webhook(
        db, session, attempt, payment_entity, order_id, payment_id
    )


def _apply_captured_webhook(
    db: Session,
    session: CheckoutSession,
    attempt: Any,
    payment_entity: dict[str, Any],
    order_id: str,
    payment_id: str,
) -> WebhookApplyResult:
    """Move a payable session to CAPTURED on an authentic payment.captured."""
    # Idempotent: an in-band verify (or an earlier webhook) may already have
    # captured this session. Do not re-audit or re-transition.
    if session.status in (CheckoutStatus.CAPTURED, CheckoutStatus.COMPLETED):
        return WebhookApplyResult(
            WEBHOOK_OUTCOME_ALREADY_PROCESSED,
            session_id=session.id,
            provider_order_id=order_id,
        )

    # The captured amount must equal the amount we signed onto the attribute.
    # A signature-valid webhook that reports a different amount is refused: the
    # money we authorised is ours, not whatever the payload claims.
    reported_amount = payment_entity.get("amount")
    if reported_amount is not None and int(reported_amount) != attempt.amount_paise:
        logger.error(
            "Webhook amount %s != authorised %s for order %s; refusing capture.",
            reported_amount,
            attempt.amount_paise,
            order_id,
        )
        record_audit_event(
            db,
            action=AuditAction.PAYMENT_AMOUNT_MISMATCH_REJECTED,
            actor=ACTOR_PROVIDER_RAZORPAY,
            session_id=session.id,
            reason=(
                f"Webhook amount {reported_amount} paise does not match "
                f"authorised {attempt.amount_paise} paise."
            ),
            outcome=AuditOutcome.BLOCKED,
            amount_paise=attempt.amount_paise,
            provider_order_id=order_id,
            provider_payment_id=payment_id,
            payment_attempt_id=attempt.id,
            detail={"reported_amount_paise": int(reported_amount)},
        )
        return WebhookApplyResult(
            WEBHOOK_OUTCOME_AMOUNT_MISMATCH,
            session_id=session.id,
            provider_order_id=order_id,
        )

    # READY_FOR_PAYMENT -> PAYMENT_PENDING -> CAPTURED, the same walk the in-band
    # path takes; the state machine forbids any illegal shortcut.
    _transition(
        session,
        CheckoutStatus.PAYMENT_PENDING,
        reason="razorpay webhook: payment.captured",
        action="capture",
    )
    mark_attempt_captured(db, attempt=attempt, provider_payment_id=payment_id)
    _transition(
        session,
        CheckoutStatus.CAPTURED,
        reason="razorpay webhook: payment.captured",
        action="capture",
    )

    order_data = json.loads(session.order_json) if session.order_json else {}
    order_data["payment_id"] = payment_id
    order_data["permalink_url"] = f"{DEFAULT_SHOP_URL}/orders/{order_id}"
    order_data.setdefault("checkout_session_id", session.id)
    session.order_json = json.dumps(order_data)
    session.updated_at = datetime.now(UTC)
    db.add(session)
    db.commit()
    db.refresh(session)

    record_audit_event(
        db,
        action=AuditAction.PAYMENT_CAPTURED,
        actor=ACTOR_PROVIDER_RAZORPAY,
        session_id=session.id,
        reason="Payment captured via Razorpay webhook.",
        outcome=AuditOutcome.SUCCESS,
        amount_paise=attempt.amount_paise,
        provider_order_id=order_id,
        provider_payment_id=payment_id,
        payment_attempt_id=attempt.id,
        detail={"source": "webhook"},
    )
    return WebhookApplyResult(
        WEBHOOK_OUTCOME_CAPTURED,
        session_id=session.id,
        provider_order_id=order_id,
    )


def _apply_failed_webhook(
    db: Session,
    session: CheckoutSession,
    attempt: Any,
    payment_entity: dict[str, Any],
    order_id: str,
    payment_id: str,
) -> WebhookApplyResult:
    """Move a payable session to FAILED on a payment.failed webhook.

    A failure that arrives after we already captured (or completed) is a
    contradiction we do not act on -- the captured money stands and the event is
    recorded as already-processed rather than forced through an illegal
    CAPTURED -> FAILED transition.
    """
    if session.status in (
        CheckoutStatus.CAPTURED,
        CheckoutStatus.COMPLETED,
        CheckoutStatus.FAILED,
    ):
        return WebhookApplyResult(
            WEBHOOK_OUTCOME_ALREADY_PROCESSED,
            session_id=session.id,
            provider_order_id=order_id,
        )

    failure_reason = str(
        payment_entity.get("error_description") or "Payment failed at provider."
    )
    failure_code = str(payment_entity.get("error_code") or "")

    _transition(
        session,
        CheckoutStatus.PAYMENT_PENDING,
        reason="razorpay webhook: payment.failed",
        action="fail",
    )
    _transition(
        session,
        CheckoutStatus.FAILED,
        reason=failure_reason,
        action="fail",
    )
    mark_attempt_failed(
        db,
        attempt=attempt,
        reason=failure_reason,
        code=failure_code,
        provider_payment_id=payment_id or None,
    )

    messages = json.loads(session.messages_json) if session.messages_json else []
    messages.append(
        {
            "type": "error",
            "param": "$",
            "content_type": "plain",
            "content": f"Payment failed: {failure_reason}. Please retry.",
        }
    )
    session.messages_json = json.dumps(messages)
    session.updated_at = datetime.now(UTC)
    db.add(session)
    db.commit()
    db.refresh(session)

    record_audit_event(
        db,
        action=AuditAction.PAYMENT_FAILED,
        actor=ACTOR_PROVIDER_RAZORPAY,
        session_id=session.id,
        reason=failure_reason,
        reason_code=failure_code,
        outcome=AuditOutcome.FAILURE,
        amount_paise=attempt.amount_paise,
        provider_order_id=order_id,
        provider_payment_id=payment_id or None,
        payment_attempt_id=attempt.id,
        detail={"source": "webhook"},
    )
    return WebhookApplyResult(
        WEBHOOK_OUTCOME_FAILED,
        session_id=session.id,
        provider_order_id=order_id,
    )


def cancel_checkout_session(db: Session, session_id: str) -> CheckoutSessionResponse:
    """Cancel a checkout session.

    Args:
        db: Database session.
        session_id: Checkout session ID.

    Returns:
        CheckoutSessionResponse with the canceled session.

    Raises:
        SessionNotFoundError: If session is not found.
        InvalidStateTransitionError: If session is already completed or canceled.
    """
    logger.info(f"Canceling checkout session {session_id}")

    session = db.exec(
        select(CheckoutSession).where(CheckoutSession.id == session_id)
    ).first()

    if session is None:
        logger.warning(f"Session not found for cancellation: {session_id}")
        raise SessionNotFoundError(session_id)

    # Check if session can be canceled
    if session.status == CheckoutStatus.COMPLETED:
        logger.warning(f"Cannot cancel completed session {session_id}")
        raise InvalidStateTransitionError(session.status.value, "cancel")

    if session.status == CheckoutStatus.CANCELED:
        logger.warning(f"Session {session_id} already canceled")
        raise InvalidStateTransitionError(session.status.value, "cancel")

    # Route the status write through the state machine. A CAPTURED (paid) order
    # cannot be canceled -- it must complete -- so that path raises a 405 rather
    # than silently discarding a captured payment.
    _transition(
        session,
        CheckoutStatus.CANCELED,
        reason="checkout canceled by caller",
        action="cancel",
    )

    # Update message
    cancel_messages: list[dict[str, Any]] = [
        {
            "type": "info",
            "param": "$",
            "content_type": "plain",
            "content": "Checkout session has been canceled.",
        }
    ]
    session.messages_json = json.dumps(cancel_messages)

    session.updated_at = datetime.now(UTC)
    db.add(session)
    db.commit()
    db.refresh(session)

    record_audit_event(
        db,
        action=AuditAction.CHECKOUT_CANCELED,
        actor=ACTOR_USER,
        session_id=session.id,
        reason="Checkout session canceled.",
        outcome=AuditOutcome.SUCCESS,
    )
    logger.info(f"Session {session_id} canceled")

    return session_to_response(session)
