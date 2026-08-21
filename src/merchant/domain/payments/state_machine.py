
"""Deterministic payment state machine.

Every money-affecting state change in VyapaarOS passes through
:func:`assert_transition`. The transition table below is the single source of
truth; it is a closed whitelist, so a state pair that is not explicitly
enumerated is rejected.

Design rules enforced here:

1. The LLM never chooses a transition. Callers pass the *event* that occurred
   (a verified signature, a verified webhook, a policy decision); this module
   decides whether the resulting state is reachable.
2. Terminal states are terminal. ``COMPLETED`` and ``CANCELED`` have no
   outgoing edges, so a replayed callback or duplicated webhook cannot move a
   settled order.
3. Failure is recoverable only along an explicit path
   (``FAILED -> RETRY_ALLOWED -> PAYMENT_PENDING``), which keeps retries
   bounded and auditable rather than implicit.
"""

from __future__ import annotations

import logging
from typing import Final

from src.merchant.db.models import CheckoutStatus

logger = logging.getLogger(__name__)

# Alias kept deliberately explicit: the session status *is* the authoritative
# money state. Introducing a parallel enum would create two truths.
PaymentState = CheckoutStatus

#: Closed whitelist of legal transitions.
#:
#: ``CREATED`` in the Track 01 brief maps onto the ACP-native initial status
#: ``not_ready_for_payment``; we preserve the protocol vocabulary rather than
#: inventing a synonym (see src/merchant/AGENTS.md, "Architecture Contracts").
ALLOWED_TRANSITIONS: Final[dict[CheckoutStatus, frozenset[CheckoutStatus]]] = {
    # CREATED
    CheckoutStatus.NOT_READY_FOR_PAYMENT: frozenset(
        {
            CheckoutStatus.NOT_READY_FOR_PAYMENT,  # idempotent re-validation
            CheckoutStatus.READY_FOR_PAYMENT,
            CheckoutStatus.AWAITING_APPROVAL,
            CheckoutStatus.CANCELED,
        }
    ),
    # Policy said the value exceeds the autonomous ceiling.
    CheckoutStatus.AWAITING_APPROVAL: frozenset(
        {
            CheckoutStatus.AWAITING_APPROVAL,
            CheckoutStatus.READY_FOR_PAYMENT,  # a human approved
            CheckoutStatus.NOT_READY_FOR_PAYMENT,  # cart mutated, re-evaluate
            CheckoutStatus.CANCELED,
        }
    ),
    CheckoutStatus.READY_FOR_PAYMENT: frozenset(
        {
            CheckoutStatus.READY_FOR_PAYMENT,
            CheckoutStatus.PAYMENT_PENDING,
            # Cart mutated after the order was created: invalidate.
            CheckoutStatus.NOT_READY_FOR_PAYMENT,
            CheckoutStatus.AWAITING_APPROVAL,
            CheckoutStatus.CANCELED,
        }
    ),
    CheckoutStatus.PAYMENT_PENDING: frozenset(
        {
            CheckoutStatus.PAYMENT_PENDING,
            CheckoutStatus.CAPTURED,
            CheckoutStatus.FAILED,
            # Signature verified in-band before any webhook arrived.
            CheckoutStatus.COMPLETED,
            CheckoutStatus.CANCELED,
        }
    ),
    CheckoutStatus.CAPTURED: frozenset(
        {
            CheckoutStatus.CAPTURED,
            CheckoutStatus.COMPLETED,
        }
    ),
    CheckoutStatus.FAILED: frozenset(
        {
            CheckoutStatus.FAILED,
            CheckoutStatus.RETRY_ALLOWED,
            CheckoutStatus.CANCELED,
        }
    ),
    CheckoutStatus.RETRY_ALLOWED: frozenset(
        {
            CheckoutStatus.RETRY_ALLOWED,
            CheckoutStatus.READY_FOR_PAYMENT,
            CheckoutStatus.PAYMENT_PENDING,
            CheckoutStatus.AWAITING_APPROVAL,
            CheckoutStatus.CANCELED,
        }
    ),
    # Terminal.
    CheckoutStatus.COMPLETED: frozenset(),
    CheckoutStatus.CANCELED: frozenset(),
}

TERMINAL_STATES: Final[frozenset[CheckoutStatus]] = frozenset(
    {CheckoutStatus.COMPLETED, CheckoutStatus.CANCELED}
)

#: States in which it is legal to hand a Razorpay order id to a browser.
PAYABLE_STATES: Final[frozenset[CheckoutStatus]] = frozenset(
    {CheckoutStatus.READY_FOR_PAYMENT, CheckoutStatus.RETRY_ALLOWED}
)


class InvalidPaymentTransitionError(Exception):
    """Raised when a caller attempts a transition outside the whitelist."""

    def __init__(self, current: CheckoutStatus, target: CheckoutStatus, reason: str = ""):
        self.current = current
        self.target = target
        self.reason = reason
        detail = f" ({reason})" if reason else ""
        super().__init__(
            f"Illegal payment state transition {current.value} -> {target.value}{detail}"
        )


def is_transition_allowed(current: CheckoutStatus, target: CheckoutStatus) -> bool:
    """Return whether ``current -> target`` is in the whitelist.

    Unknown source states return ``False`` (fail closed) rather than raising,
    so callers can use this for branching without exception handling.
    """
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(
    current: CheckoutStatus, target: CheckoutStatus, *, reason: str = ""
) -> CheckoutStatus:
    """Validate a transition and return ``target``.

    Args:
        current: The state persisted in the database.
        target: The state the caller wants to move to.
        reason: Short machine-readable cause, recorded on rejection.

    Returns:
        ``target``, so call sites can write ``session.status = assert_transition(...)``.

    Raises:
        InvalidPaymentTransitionError: If the transition is not whitelisted.
    """
    if not is_transition_allowed(current, target):
        logger.error(
            "REJECTED payment transition %s -> %s (%s)",
            current.value,
            target.value,
            reason or "no reason supplied",
        )
        raise InvalidPaymentTransitionError(current, target, reason)
    if current != target:
        logger.info(
            "payment transition %s -> %s (%s)", current.value, target.value, reason
        )
    return target


def is_terminal(state: CheckoutStatus) -> bool:
    """Return whether ``state`` can never transition again."""
    return state in TERMINAL_STATES


def is_payable(state: CheckoutStatus) -> bool:
    """Return whether a payment may be initiated from ``state``."""
    return state in PAYABLE_STATES
