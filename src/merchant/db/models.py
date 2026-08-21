"""SQLModel database models for the Agentic Commerce middleware."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Optional

from sqlmodel import Field, Relationship, SQLModel


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(UTC)


class CheckoutStatus(StrEnum):
    """Authoritative checkout/payment state.

    Legal transitions between these values are defined exclusively by
    ``src.merchant.domain.payments.state_machine.ALLOWED_TRANSITIONS``. Do not
    assign this field directly; route through ``assert_transition`` so that
    illegal moves (e.g. ``completed -> payment_pending``) are rejected.
    """

    NOT_READY_FOR_PAYMENT = "not_ready_for_payment"
    AWAITING_APPROVAL = "awaiting_approval"
    READY_FOR_PAYMENT = "ready_for_payment"
    PAYMENT_PENDING = "payment_pending"
    FAILED = "failed"
    RETRY_ALLOWED = "retry_allowed"
    CAPTURED = "captured"
    COMPLETED = "completed"
    CANCELED = "canceled"


class PaymentAttemptState(StrEnum):
    """Lifecycle of a single attempt to collect money for a session."""

    CREATED = "created"
    PENDING = "pending"
    CAPTURED = "captured"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ApprovalState(StrEnum):
    """Lifecycle of a human approval request raised by the policy engine."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class WebhookProcessingState(StrEnum):
    """Processing lifecycle for a durably recorded inbound webhook."""

    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"


class AgentInvocationStatus(StrEnum):
    """Normalized status for recorded agent invocations."""

    SUCCESS = "success"
    FALLBACK_SUCCESS = "fallback_success"
    ERROR_TIMEOUT = "error_timeout"
    ERROR_UPSTREAM = "error_upstream"
    ERROR_VALIDATION = "error_validation"
    ERROR_INTERNAL = "error_internal"


class AgentInvocationChannel(StrEnum):
    """Invocation channel for agent calls."""

    ACP = "acp"
    APPS_SDK = "apps_sdk"
    UCP = "ucp"


class RecommendationAttributionEventType(StrEnum):
    """Lifecycle events for recommendation attribution."""

    IMPRESSION = "impression"
    CLICK = "click"
    PURCHASE = "purchase"


class Customer(SQLModel, table=True):
    """Customer model representing shoppers in the system.

    Attributes:
        id: Unique customer identifier (e.g., "cust_1")
        email: Customer email address
        name: Customer display name
        created_at: Account creation timestamp
    """

    id: str = Field(primary_key=True)
    email: str = Field(unique=True, index=True)
    name: str
    created_at: datetime = Field(default_factory=_utc_now)

    browse_history: list["BrowseHistory"] = Relationship(
        back_populates="customer",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class BrowseHistory(SQLModel, table=True):
    """Browse history model for tracking customer browsing behavior.

    Used by the recommendation agent to understand user preferences.
    Price range can be computed from min/max of prices in browse history.

    Attributes:
        id: Auto-generated primary key
        customer_id: Foreign key to Customer
        category: Product category viewed (e.g., "tops", "bottoms")
        search_term: Optional search term used (e.g., "casual wear")
        product_id: Optional product ID if specific product was viewed
        price_viewed: Price of product viewed in cents (for price range computation)
        viewed_at: Timestamp when item was viewed
    """

    __tablename__: ClassVar[str] = "browse_history"                            

    id: int | None = Field(default=None, primary_key=True)
    customer_id: str = Field(foreign_key="customer.id", index=True)
    category: str = Field(index=True)
    search_term: str | None = Field(default=None)
    product_id: str | None = Field(default=None, foreign_key="product.id")
    price_viewed: int = Field(default=0)                  
    viewed_at: datetime = Field(default_factory=_utc_now)

    customer: Optional["Customer"] = Relationship(back_populates="browse_history")
    product: Optional["Product"] = Relationship(back_populates="browse_views")


class Product(SQLModel, table=True):
    """Product model representing items available for purchase.

    Attributes:
        id: Unique product identifier (e.g., "prod_1")
        sku: Stock keeping unit code
        name: Product display name
        base_price: Base price in cents (e.g., 2500 = $25.00)
        stock_count: Current inventory quantity
        min_margin: Minimum profit margin (e.g., 0.15 = 15%)
        image_url: URL to product image
    """

    id: str = Field(primary_key=True)
    sku: str = Field(unique=True, index=True)
    name: str
    base_price: int
    stock_count: int
    min_margin: float
    image_url: str
    lifecycle: str = Field(default="mature")
    demand_velocity: str = Field(default="flat")

    competitor_prices: list["CompetitorPrice"] = Relationship(
        back_populates="product",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    browse_views: list["BrowseHistory"] = Relationship(back_populates="product")


class CompetitorPrice(SQLModel, table=True):
    """Competitor pricing data for dynamic pricing logic.

    Attributes:
        id: Auto-generated primary key
        product_id: Foreign key to Product
        retailer_name: Name of the competing retailer
        price: Competitor's price in cents
        updated_at: Timestamp of last price update
    """

    __tablename__: ClassVar[str] = "competitor_price"                            

    id: int | None = Field(default=None, primary_key=True)
    product_id: str = Field(foreign_key="product.id", index=True)
    retailer_name: str
    price: int
    updated_at: datetime = Field(default_factory=_utc_now)

    product: Product | None = Relationship(back_populates="competitor_prices")


class CheckoutSession(SQLModel, table=True):
    """Checkout session model representing the ACP checkout state.

    Attributes:
        id: Unique session identifier (e.g., "checkout_abc123")
        protocol: Protocol origin ("acp" or "ucp")
        status: Current checkout status
        currency: ISO 4217 currency code (default: USD)
        locale: BCP 47 language tag (default: en-US)
        line_items_json: JSON string of line items array
        buyer_json: JSON string of buyer information
        fulfillment_address_json: JSON string of shipping address
        fulfillment_options_json: JSON string of available shipping options
        selected_fulfillment_option_id: ID of selected shipping option
        totals_json: JSON string of price totals
        order_json: JSON string of order details (after completion)
        messages_json: JSON string of messages array
        links_json: JSON string of HATEOAS links
        metadata_json: JSON string of additional metadata
        created_at: Session creation timestamp
        updated_at: Last modification timestamp
        expires_at: Session expiration timestamp
    """

    __tablename__: ClassVar[str] = "checkout_session"                            

    id: str = Field(primary_key=True)
    protocol: str = Field(default="acp")
    status: CheckoutStatus = Field(default=CheckoutStatus.NOT_READY_FOR_PAYMENT)
    currency: str = Field(default="INR")
    locale: str = Field(default="en-US")

    line_items_json: str = Field(default="[]")
    buyer_json: str | None = Field(default=None)
    fulfillment_address_json: str | None = Field(default=None)
    fulfillment_options_json: str = Field(default="[]")
    selected_fulfillment_option_id: str | None = Field(default=None)
    totals_json: str = Field(default="{}")
    order_json: str | None = Field(default=None)
    messages_json: str = Field(default="[]")
    links_json: str = Field(default="[]")
    metadata_json: str = Field(default="{}")

    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime | None = Field(default=None)


class AgentInvocationOutcome(SQLModel, table=True):
    """Recorded outcome for each agent invocation."""

    __tablename__: ClassVar[str] = "agent_invocation_outcome"                            

    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=_utc_now, index=True)
    agent_type: str = Field(index=True)
    channel: AgentInvocationChannel = Field(
        default=AgentInvocationChannel.ACP, index=True
    )
    status: AgentInvocationStatus = Field(
        default=AgentInvocationStatus.SUCCESS, index=True
    )
    latency_ms: int = Field(default=0)

    request_id: str | None = Field(default=None, index=True)
    session_id: str | None = Field(default=None, index=True)
    error_code: str | None = Field(default=None, index=True)


class RecommendationAttributionEvent(SQLModel, table=True):
    """Recommendation attribution event for conversion funnel analytics."""

    __tablename__: ClassVar[str] = "recommendation_attribution_event"                            

    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=_utc_now, index=True)
    event_type: RecommendationAttributionEventType = Field(
        default=RecommendationAttributionEventType.IMPRESSION,
        index=True,
    )
    session_id: str | None = Field(default=None, index=True)
    recommendation_request_id: str | None = Field(default=None, index=True)
    product_id: str = Field(index=True)
    position: int | None = Field(default=None)
    order_id: str | None = Field(default=None, index=True)
    quantity: int = Field(default=1)
    revenue_cents: int = Field(default=0)
    source: str = Field(default="apps_sdk", index=True)


class PaymentAttempt(SQLModel, table=True):
    """One attempt to collect money for a checkout session.

    A session may have several attempts (initial failure plus retries). Exactly
    one Razorpay order is created per attempt; the attempt is the anti-duplicate
    boundary. ``provider_order_id`` is the *only* authoritative order id -- the
    value returned by a browser callback is untrusted input and must be compared
    against this column, never substituted for it.
    """

    __tablename__: ClassVar[str] = "payment_attempt"  # pyright: ignore[reportIncompatibleVariableOverride]

    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="checkout_session.id", index=True)
    attempt_number: int = Field(default=1)

    provider: str = Field(default="razorpay", index=True)
    provider_order_id: str | None = Field(default=None, index=True)
    provider_payment_id: str | None = Field(default=None, index=True)

    #: Authoritative amount, recomputed server-side at order-creation time.
    amount_paise: int = Field(default=0)
    currency: str = Field(default="INR")

    state: PaymentAttemptState = Field(default=PaymentAttemptState.CREATED, index=True)

    signature_verified: bool = Field(default=False)
    failure_reason: str | None = Field(default=None)
    failure_code: str | None = Field(default=None)

    #: Serialised PolicyDecision that authorised this attempt.
    policy_decision_json: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=_utc_now, index=True)
    updated_at: datetime = Field(default_factory=_utc_now)


class ApprovalRequest(SQLModel, table=True):
    """A human approval gate raised by the deterministic policy engine.

    The approval decision lives server-side. A client cannot self-approve by
    sending ``approved=true``; it must reference an ``ApprovalRequest`` row that
    the backend created, and the backend flips the state.
    """

    __tablename__: ClassVar[str] = "approval_request"  # pyright: ignore[reportIncompatibleVariableOverride]

    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="checkout_session.id", index=True)

    state: ApprovalState = Field(default=ApprovalState.PENDING, index=True)
    reason_code: str = Field(default="")
    reason: str = Field(default="")

    #: Amount the approval was granted *for*. If the cart later changes, the
    #: approval no longer matches and must be re-raised.
    amount_paise: int = Field(default=0)
    limit_paise: int = Field(default=0)

    approved_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utc_now, index=True)
    resolved_at: datetime | None = Field(default=None)


class WebhookEvent(SQLModel, table=True):
    """Durable record of an inbound provider webhook.

    Persisted *before* business logic runs so that duplicate deliveries are
    rejected across process restarts and multiple workers. The previous
    implementation used a module-level ``set()``, which lost all history on
    restart and did not work behind more than one worker.
    """

    __tablename__: ClassVar[str] = "webhook_event"  # pyright: ignore[reportIncompatibleVariableOverride]

    #: Provider event id (Razorpay ``X-Razorpay-Event-Id``). Primary key, so the
    #: uniqueness constraint is enforced by the database, not by application code.
    id: str = Field(primary_key=True)

    provider: str = Field(default="razorpay", index=True)
    event_type: str = Field(default="", index=True)
    payload_hash: str = Field(default="")

    processing_state: WebhookProcessingState = Field(
        default=WebhookProcessingState.RECEIVED, index=True
    )
    processing_error: str | None = Field(default=None)

    session_id: str | None = Field(default=None, index=True)
    provider_order_id: str | None = Field(default=None, index=True)

    #: Provider-reported creation time, used for replay-window checks.
    provider_created_at: int | None = Field(default=None)
    received_at: datetime = Field(default_factory=_utc_now, index=True)
    processed_at: datetime | None = Field(default=None)

    #: Raw body, retained so processing can be replayed after a code fix without
    #: asking the provider to redeliver.
    raw_payload: str = Field(default="")


class AuditEvent(SQLModel, table=True):
    """Append-only, hash-chained audit record for money-affecting actions.

    Each row stores the hash of the previous row for its session, so tampering
    with or deleting an intermediate event is detectable. This is the artifact a
    judge reads to understand a transaction without reading application logs.
    """

    __tablename__: ClassVar[str] = "audit_event"  # pyright: ignore[reportIncompatibleVariableOverride]

    id: int | None = Field(default=None, primary_key=True)
    sequence: int = Field(default=0, index=True)

    session_id: str | None = Field(default=None, index=True)
    timestamp: datetime = Field(default_factory=_utc_now, index=True)

    #: Who caused this. One of: ``user``, ``agent:<name>``, ``system``,
    #: ``provider:razorpay``. Never blank -- accountability is mandatory.
    actor: str = Field(default="system", index=True)
    action: str = Field(default="", index=True)

    #: Stable machine-readable code. Human prose belongs in ``reason``.
    reason_code: str = Field(default="")
    reason: str = Field(default="")

    #: ALLOW / DENY / REQUIRE_APPROVAL / NOT_APPLICABLE
    policy_decision: str = Field(default="not_applicable", index=True)
    outcome: str = Field(default="", index=True)

    amount_paise: int | None = Field(default=None)
    provider_order_id: str | None = Field(default=None, index=True)
    provider_payment_id: str | None = Field(default=None, index=True)
    payment_attempt_id: str | None = Field(default=None, index=True)

    #: Structured, non-chain-of-thought detail (reason codes, limits, signals).
    detail_json: str = Field(default="{}")

    prev_hash: str = Field(default="")
    entry_hash: str = Field(default="", index=True)
