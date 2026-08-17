                                                    
                                     

"""Deterministic policy engine for bounding and gating LLM financial actions."""

import logging

logger = logging.getLogger(__name__)

                              
MAX_AUTONOMOUS_ORDER_VALUE_PAISE = 1_000_000              
MAX_DISCOUNT_PERCENT = 0.15       


class PolicyDecision:
    """Represents a decision made by the Policy Engine."""
    def __init__(self, allowed: bool, requires_approval: bool, reason: str):
        self.allowed = allowed
        self.requires_approval = requires_approval
        self.reason = reason


def evaluate_order_value_policy(total_paise: int) -> PolicyDecision:
    """Evaluate if an order total exceeds autonomous limits."""
    if total_paise > MAX_AUTONOMOUS_ORDER_VALUE_PAISE:
        logger.info(f"POLICY GATE: Order {total_paise} > {MAX_AUTONOMOUS_ORDER_VALUE_PAISE}. Requires user approval.")
        return PolicyDecision(
            allowed=True,
            requires_approval=True,
            reason=f"Order value exceeds autonomous limit of {MAX_AUTONOMOUS_ORDER_VALUE_PAISE/100:.2f}. User approval required for payment.",
        )
    return PolicyDecision(
        allowed=True,
        requires_approval=False,
        reason="Order value within autonomous limits. Proceeding.",
    )


def clamp_discount(requested_discount_percent: float) -> tuple[float, PolicyDecision]:
    """Clamp an agent-proposed discount to the merchant's maximum allowed policy."""
    if requested_discount_percent > MAX_DISCOUNT_PERCENT:
        logger.warning(
            f"POLICY BLOCK: Agent proposed {requested_discount_percent*100}%. "
            f"Clamping to {MAX_DISCOUNT_PERCENT*100}%."
        )
        return MAX_DISCOUNT_PERCENT, PolicyDecision(
            allowed=False,
            requires_approval=False,
            reason=f"Discount clamped to maximum allowed: {MAX_DISCOUNT_PERCENT*100}%.",
        )
    return requested_discount_percent, PolicyDecision(
        allowed=True,
        requires_approval=False,
        reason="Discount proposal accepted.",
    )
