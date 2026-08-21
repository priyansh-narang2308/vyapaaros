
"""Deterministic policy engine.

Contract: **the AI proposes, the policy engine decides, the backend enforces.**

Every function here is pure and total -- no I/O, no LLM call, no randomness --
so a given proposal always yields the same decision and the decision can be
replayed from the audit trail. Limits are module constants rather than
LLM-supplied arguments, which is what makes them unoverridable by a prompt.

All monetary values are integer **paise** (1 INR = 100 paise). Names carry the
unit so a caller cannot silently pass rupees.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Merchant limits. These are the guardrails; an LLM cannot reach them.
# ---------------------------------------------------------------------------

#: Above this order value the agent may not settle autonomously; a human must
#: approve. 1_000_000 paise = INR 10,000.
MAX_AUTONOMOUS_ORDER_VALUE_PAISE: Final[int] = 1_000_000

#: Hard ceiling on any agent-proposed discount, as a fraction of line subtotal.
MAX_DISCOUNT_PERCENT: Final[float] = 0.15

#: Largest single upsell/cross-sell an agent may add without confirmation.
MAX_AUTONOMOUS_UPSELL_VALUE_PAISE: Final[int] = 200_000  # INR 2,000

#: Value at or above which explicit user approval is always required, even if
#: below the autonomous ceiling. Kept separate so the two can diverge.
USER_APPROVAL_THRESHOLD_PAISE: Final[int] = 1_000_000

#: Absolute refusal ceiling. Nothing autonomous or approvable happens above it
#: in test-mode; the request is denied outright.
MAX_ORDER_VALUE_PAISE: Final[int] = 50_000_000  # INR 500,000

#: Maximum number of payment attempts per session before retries are refused.
MAX_PAYMENT_ATTEMPTS: Final[int] = 3


class Decision(StrEnum):
    """Outcome of a policy evaluation."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class ReasonCode(StrEnum):
    """Stable machine-readable reasons. Prose may change; these may not."""

    WITHIN_LIMITS = "WITHIN_LIMITS"
    ORDER_EXCEEDS_AUTONOMOUS_LIMIT = "ORDER_EXCEEDS_AUTONOMOUS_LIMIT"
    ORDER_EXCEEDS_ABSOLUTE_LIMIT = "ORDER_EXCEEDS_ABSOLUTE_LIMIT"
    ORDER_VALUE_INVALID = "ORDER_VALUE_INVALID"
    DISCOUNT_WITHIN_LIMIT = "DISCOUNT_WITHIN_LIMIT"
    DISCOUNT_EXCEEDS_LIMIT = "DISCOUNT_EXCEEDS_LIMIT"
    DISCOUNT_NEGATIVE = "DISCOUNT_NEGATIVE"
    UPSELL_WITHIN_LIMIT = "UPSELL_WITHIN_LIMIT"
    UPSELL_EXCEEDS_LIMIT = "UPSELL_EXCEEDS_LIMIT"
    RETRY_ALLOWED = "RETRY_ALLOWED"
    RETRY_LIMIT_EXCEEDED = "RETRY_LIMIT_EXCEEDED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_MISSING = "APPROVAL_MISSING"
    APPROVAL_STALE_AMOUNT = "APPROVAL_STALE_AMOUNT"
    ACTION_NOT_PERMITTED_FOR_ACTOR = "ACTION_NOT_PERMITTED_FOR_ACTOR"


@dataclass(frozen=True)
class PolicyDecision:
    """Structured, serialisable result of a policy evaluation.

    Frozen so a caller cannot mutate a decision after the fact (for example
    flipping ``approval_required`` to ``False`` before persisting it).
    """

    action: str
    decision: Decision
    reason_code: ReasonCode
    reason: str
    limits: dict[str, Any] = field(default_factory=dict)
    approval_required: bool = False

    # -- Convenience predicates used at call sites -------------------------
    @property
    def allowed(self) -> bool:
        """Whether the action may proceed *now*, without further gating."""
        return self.decision is Decision.ALLOW

    @property
    def denied(self) -> bool:
        """Whether the action must not proceed at all."""
        return self.decision is Decision.DENY

    @property
    def requires_approval(self) -> bool:
        """Whether the action needs a human before it may proceed."""
        return self.decision is Decision.REQUIRE_APPROVAL or self.approval_required

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for persistence and audit."""
        return {
            "action": self.action,
            "decision": self.decision.value,
            "reason_code": self.reason_code.value,
            "reason": self.reason,
            "limits": self.limits,
            "approval_required": self.approval_required,
        }

    def to_json(self) -> str:
        """Serialise for storage in a ``*_json`` column."""
        return json.dumps(self.to_dict(), sort_keys=True)


# ---------------------------------------------------------------------------
# Actor capability matrix (Phase 11: money action gating)
# ---------------------------------------------------------------------------

#: For each money-affecting action: may an agent *propose* it, and may an agent
#: *execute* it autonomously? Execution of anything that moves money is `False`
#: by design -- the backend executes, never the model.
ACTION_CAPABILITIES: Final[dict[str, dict[str, bool]]] = {
    "discount": {"agent_propose": True, "agent_execute": False, "policy_required": True},
    "upsell": {"agent_propose": True, "agent_execute": False, "policy_required": True},
    "promotion": {"agent_propose": True, "agent_execute": False, "policy_required": True},
    "cart_update": {"agent_propose": True, "agent_execute": False, "policy_required": True},
    "create_order": {"agent_propose": True, "agent_execute": False, "policy_required": True},
    "capture_payment": {
        "agent_propose": False,
        "agent_execute": False,
        "policy_required": True,
    },
    "complete_order": {
        "agent_propose": False,
        "agent_execute": False,
        "policy_required": True,
    },
    "refund": {"agent_propose": False, "agent_execute": False, "policy_required": True},
    "retry_payment": {
        "agent_propose": True,
        "agent_execute": False,
        "policy_required": True,
    },
}


def can_actor_perform(action: str, actor: str) -> PolicyDecision:
    """Gate an action by actor class before any value arithmetic runs.

    ``actor`` is ``"user"``, ``"system"``, or ``"agent"``/``"agent:<name>"``.
    Unknown actions are denied (fail closed).
    """
    caps = ACTION_CAPABILITIES.get(action)
    if caps is None:
        return PolicyDecision(
            action=action,
            decision=Decision.DENY,
            reason_code=ReasonCode.ACTION_NOT_PERMITTED_FOR_ACTOR,
            reason=f"Unknown money action '{action}' is denied by default.",
            limits={},
        )

    is_agent = actor.startswith("agent")
    if is_agent and not caps["agent_execute"]:
        if caps["agent_propose"]:
            return PolicyDecision(
                action=action,
                decision=Decision.REQUIRE_APPROVAL,
                reason_code=ReasonCode.ACTION_NOT_PERMITTED_FOR_ACTOR,
                reason=(
                    f"An agent may propose '{action}' but may not execute it; "
                    "the backend executes after policy and approval."
                ),
                limits={"agent_execute": False},
                approval_required=True,
            )
        return PolicyDecision(
            action=action,
            decision=Decision.DENY,
            reason_code=ReasonCode.ACTION_NOT_PERMITTED_FOR_ACTOR,
            reason=f"Agents may not propose or execute '{action}'.",
            limits={"agent_propose": False, "agent_execute": False},
        )

    return PolicyDecision(
        action=action,
        decision=Decision.ALLOW,
        reason_code=ReasonCode.WITHIN_LIMITS,
        reason=f"Actor '{actor}' may perform '{action}'.",
        limits={},
    )


# ---------------------------------------------------------------------------
# Value policies
# ---------------------------------------------------------------------------


def evaluate_order_value_policy(total_paise: int) -> PolicyDecision:
    """Decide whether an order of ``total_paise`` may be settled autonomously.

    Returns ``DENY`` above the absolute ceiling, ``REQUIRE_APPROVAL`` above the
    autonomous ceiling, otherwise ``ALLOW``. Non-positive totals are denied so a
    zero-value order can never be pushed to a payment provider.
    """
    limits = {
        "max_autonomous_order_value_paise": MAX_AUTONOMOUS_ORDER_VALUE_PAISE,
        "user_approval_threshold_paise": USER_APPROVAL_THRESHOLD_PAISE,
        "max_order_value_paise": MAX_ORDER_VALUE_PAISE,
        "evaluated_total_paise": total_paise,
    }

    if total_paise <= 0:
        return PolicyDecision(
            action="create_order",
            decision=Decision.DENY,
            reason_code=ReasonCode.ORDER_VALUE_INVALID,
            reason="Order total must be greater than zero.",
            limits=limits,
        )

    if total_paise > MAX_ORDER_VALUE_PAISE:
        return PolicyDecision(
            action="create_order",
            decision=Decision.DENY,
            reason_code=ReasonCode.ORDER_EXCEEDS_ABSOLUTE_LIMIT,
            reason=(
                f"Order total of INR {total_paise / 100:,.2f} exceeds the merchant "
                f"maximum of INR {MAX_ORDER_VALUE_PAISE / 100:,.2f}."
            ),
            limits=limits,
        )

    threshold = min(MAX_AUTONOMOUS_ORDER_VALUE_PAISE, USER_APPROVAL_THRESHOLD_PAISE)
    if total_paise > threshold:
        return PolicyDecision(
            action="create_order",
            decision=Decision.REQUIRE_APPROVAL,
            reason_code=ReasonCode.ORDER_EXCEEDS_AUTONOMOUS_LIMIT,
            reason=(
                f"Order total of INR {total_paise / 100:,.2f} exceeds the autonomous "
                f"payment limit of INR {threshold / 100:,.2f}. Explicit user "
                "approval is required before payment can be initiated."
            ),
            limits=limits,
            approval_required=True,
        )

    return PolicyDecision(
        action="create_order",
        decision=Decision.ALLOW,
        reason_code=ReasonCode.WITHIN_LIMITS,
        reason=(
            f"Order total of INR {total_paise / 100:,.2f} is within the autonomous "
            f"payment limit of INR {threshold / 100:,.2f}."
        ),
        limits=limits,
    )


def evaluate_discount_policy(requested_discount_percent: float) -> PolicyDecision:
    """Evaluate an agent-proposed discount fraction against the merchant cap."""
    limits = {
        "max_discount_percent": MAX_DISCOUNT_PERCENT,
        "requested_discount_percent": requested_discount_percent,
    }

    if requested_discount_percent < 0:
        return PolicyDecision(
            action="discount",
            decision=Decision.DENY,
            reason_code=ReasonCode.DISCOUNT_NEGATIVE,
            reason="A negative discount would increase the customer's total.",
            limits=limits,
        )

    if requested_discount_percent > MAX_DISCOUNT_PERCENT:
        return PolicyDecision(
            action="discount",
            decision=Decision.DENY,
            reason_code=ReasonCode.DISCOUNT_EXCEEDS_LIMIT,
            reason=(
                f"Requested discount of {requested_discount_percent * 100:.1f}% exceeds "
                f"the merchant maximum of {MAX_DISCOUNT_PERCENT * 100:.1f}%."
            ),
            limits=limits,
        )

    return PolicyDecision(
        action="discount",
        decision=Decision.ALLOW,
        reason_code=ReasonCode.DISCOUNT_WITHIN_LIMIT,
        reason=(
            f"Requested discount of {requested_discount_percent * 100:.1f}% is at or "
            f"below the merchant maximum of {MAX_DISCOUNT_PERCENT * 100:.1f}%."
        ),
        limits=limits,
    )


def clamp_discount(requested_discount_percent: float) -> tuple[float, PolicyDecision]:
    """Clamp a proposed discount to the merchant cap.

    Returns the *effective* discount alongside the decision, so a caller can
    never apply a larger discount than policy permits even if it ignores the
    decision object. Negative proposals clamp to zero.
    """
    decision = evaluate_discount_policy(requested_discount_percent)
    if decision.reason_code is ReasonCode.DISCOUNT_NEGATIVE:
        return 0.0, decision
    if decision.reason_code is ReasonCode.DISCOUNT_EXCEEDS_LIMIT:
        logger.warning(
            "POLICY CLAMP: proposed discount %.4f clamped to %.4f",
            requested_discount_percent,
            MAX_DISCOUNT_PERCENT,
        )
        return MAX_DISCOUNT_PERCENT, decision
    return requested_discount_percent, decision


def evaluate_upsell_policy(upsell_value_paise: int) -> PolicyDecision:
    """Evaluate whether an agent may add an upsell of this value autonomously."""
    limits = {
        "max_autonomous_upsell_value_paise": MAX_AUTONOMOUS_UPSELL_VALUE_PAISE,
        "requested_upsell_value_paise": upsell_value_paise,
    }

    if upsell_value_paise <= 0:
        return PolicyDecision(
            action="upsell",
            decision=Decision.DENY,
            reason_code=ReasonCode.ORDER_VALUE_INVALID,
            reason="Upsell value must be greater than zero.",
            limits=limits,
        )

    if upsell_value_paise > MAX_AUTONOMOUS_UPSELL_VALUE_PAISE:
        return PolicyDecision(
            action="upsell",
            decision=Decision.REQUIRE_APPROVAL,
            reason_code=ReasonCode.UPSELL_EXCEEDS_LIMIT,
            reason=(
                f"Upsell of INR {upsell_value_paise / 100:,.2f} exceeds the autonomous "
                f"upsell limit of INR {MAX_AUTONOMOUS_UPSELL_VALUE_PAISE / 100:,.2f}; "
                "customer confirmation is required."
            ),
            limits=limits,
            approval_required=True,
        )

    return PolicyDecision(
        action="upsell",
        decision=Decision.ALLOW,
        reason_code=ReasonCode.UPSELL_WITHIN_LIMIT,
        reason=(
            f"Upsell of INR {upsell_value_paise / 100:,.2f} is within the autonomous "
            f"upsell limit."
        ),
        limits=limits,
    )


def evaluate_retry_policy(previous_attempts: int) -> PolicyDecision:
    """Decide whether another payment attempt is permitted.

    ``previous_attempts`` is the count of attempts already made for the session.
    Bounding retries prevents an agent from looping on a declining card.
    """
    limits = {
        "max_payment_attempts": MAX_PAYMENT_ATTEMPTS,
        "previous_attempts": previous_attempts,
    }

    if previous_attempts >= MAX_PAYMENT_ATTEMPTS:
        return PolicyDecision(
            action="retry_payment",
            decision=Decision.DENY,
            reason_code=ReasonCode.RETRY_LIMIT_EXCEEDED,
            reason=(
                f"This checkout has already used {previous_attempts} of "
                f"{MAX_PAYMENT_ATTEMPTS} permitted payment attempts."
            ),
            limits=limits,
        )

    return PolicyDecision(
        action="retry_payment",
        decision=Decision.ALLOW,
        reason_code=ReasonCode.RETRY_ALLOWED,
        reason=(
            f"Retry {previous_attempts + 1} of {MAX_PAYMENT_ATTEMPTS} is permitted."
        ),
        limits=limits,
    )
