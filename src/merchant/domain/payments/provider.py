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

"""Payment provider abstraction.

The checkout domain must not know which PSP it is talking to, and it must not
hard-depend on network reachability in order to be exercised. This module
defines a single :class:`PaymentProvider` protocol with exactly two
implementations:

``RazorpayProvider``
    The real thing. Talks to ``api.razorpay.com`` through the official
    ``razorpay`` SDK. Used whenever live keys are configured.

``SandboxProvider``
    A hermetic, offline provider. It does **not** bypass signature
    verification -- it computes genuine HMAC-SHA256 signatures with a local
    secret using the exact algorithm Razorpay documents, so a forged signature
    is rejected in sandbox mode just as it is in live mode. That property is
    what makes the security tests meaningful without a network.

Selection is explicit. ``PAYMENT_PROVIDER`` must be set to ``sandbox`` to get
the sandbox; a missing or malformed live configuration raises rather than
silently degrading into a mock. A mock that activates by accident is worse than
no mock at all, because the operator believes money moved.

All monetary values crossing this boundary are integer **paise**.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


#: Value of ``PAYMENT_PROVIDER`` that selects the live Razorpay integration.
PROVIDER_RAZORPAY: Final[str] = "razorpay"

#: Value of ``PAYMENT_PROVIDER`` that selects the offline sandbox.
PROVIDER_SANDBOX: Final[str] = "sandbox"

VALID_PROVIDERS: Final[frozenset[str]] = frozenset({PROVIDER_RAZORPAY, PROVIDER_SANDBOX})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PaymentProviderError(RuntimeError):
    """Base class for payment provider failures."""


class PaymentProviderNotConfiguredError(PaymentProviderError):
    """Raised when the selected provider cannot be constructed.

    Deliberately *not* recoverable by falling back to a mock: the caller has
    asked for a live provider and must be told the configuration is incomplete.
    """


class PaymentProviderRequestError(PaymentProviderError):
    """Raised when a provider API call fails (network, 4xx, 5xx)."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderOrder:
    """A provider-side order, normalised across providers.

    Frozen because the order identity and amount are the anchor for later
    verification; nothing downstream may rewrite them.
    """

    id: str
    amount_paise: int
    currency: str
    receipt: str
    status: str
    provider: str
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for ``CheckoutSession.order_json``."""
        return {
            "provider": self.provider,
            "id": self.id,
            "amount": self.amount_paise,
            "currency": self.currency,
            "receipt": self.receipt,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Signature primitives, shared so both providers agree bit-for-bit
# ---------------------------------------------------------------------------


def payment_signature_message(order_id: str, payment_id: str) -> str:
    """Build the message Razorpay signs for a checkout callback.

    Razorpay's documented construction, mirrored by the official SDK's
    ``Utility.verify_payment_signature``, is ``"<order_id>|<payment_id>"``.
    """
    return f"{order_id}|{payment_id}"


def hmac_sha256_hex(secret: str, message: bytes) -> str:
    """Compute the lowercase hex HMAC-SHA256 of ``message`` under ``secret``."""
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def signatures_match(expected_hex: str, provided: str | None) -> bool:
    """Timing-safe comparison of two hex signatures.

    Returns ``False`` for a missing or non-ASCII signature rather than raising,
    so an attacker cannot turn a malformed signature into a 500.
    """
    if not provided:
        return False
    try:
        return hmac.compare_digest(expected_hex, provided.strip())
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PaymentProvider(Protocol):
    """What the checkout domain is allowed to assume about a PSP."""

    #: Stable provider identifier persisted alongside orders.
    name: str

    #: ``True`` only when real money can move. Drives honest TEST MODE labels.
    is_live: bool

    def create_order(
        self,
        *,
        amount_paise: int,
        currency: str,
        receipt: str,
        notes: dict[str, Any] | None = None,
    ) -> ProviderOrder:
        """Create an order for ``amount_paise`` and return its provider identity."""
        ...

    def verify_payment_signature(
        self, *, order_id: str, payment_id: str, signature: str | None
    ) -> bool:
        """Return whether a checkout callback signature is authentic."""
        ...

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str | None) -> bool:
        """Return whether a webhook body signature is authentic.

        ``raw_body`` is the exact bytes received on the wire. It must not be
        re-serialised before this call: any whitespace or key-order change
        invalidates the signature.
        """
        ...


# ---------------------------------------------------------------------------
# Live provider
# ---------------------------------------------------------------------------


class RazorpayProvider:
    """Live Razorpay integration over the official Python SDK."""

    name = PROVIDER_RAZORPAY
    is_live = True

    def __init__(self, key_id: str, key_secret: str, webhook_secret: str = "") -> None:
        if not key_id or not key_secret:
            raise PaymentProviderNotConfiguredError(
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required for the "
                "live Razorpay provider. Set them, or set PAYMENT_PROVIDER=sandbox "
                "to run offline in test mode."
            )

        import razorpay  # imported lazily so sandbox runs need no SDK import cost

        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        self._client = razorpay.Client(auth=(key_id, key_secret))

    def create_order(
        self,
        *,
        amount_paise: int,
        currency: str,
        receipt: str,
        notes: dict[str, Any] | None = None,
    ) -> ProviderOrder:
        """Create a Razorpay Order. Raises on any transport or API failure."""
        payload: dict[str, Any] = {
            "amount": amount_paise,
            "currency": currency.upper(),
            "receipt": receipt,
        }
        if notes:
            payload["notes"] = notes

        logger.info(
            "Creating Razorpay Order: amount_paise=%s currency=%s receipt=%s",
            amount_paise,
            payload["currency"],
            receipt,
        )
        try:
            raw = self._client.order.create(data=payload)
        except Exception as exc:  # SDK raises a wide range of error types
            raise PaymentProviderRequestError(
                f"Razorpay order creation failed for receipt {receipt}: {exc}"
            ) from exc

        return ProviderOrder(
            id=str(raw["id"]),
            amount_paise=int(raw.get("amount", amount_paise)),
            currency=str(raw.get("currency", payload["currency"])),
            receipt=str(raw.get("receipt", receipt)),
            status=str(raw.get("status", "created")),
            provider=self.name,
            raw=dict(raw),
        )

    def verify_payment_signature(
        self, *, order_id: str, payment_id: str, signature: str | None
    ) -> bool:
        """Verify a Standard Checkout callback signature.

        Computed locally with the documented algorithm rather than through the
        SDK's exception-based helper, so a malformed input yields ``False``
        instead of an exception escaping into the request path. The algorithm is
        identical: hex HMAC-SHA256 of ``"<order_id>|<payment_id>"`` keyed by the
        API secret, compared in constant time.
        """
        if not order_id or not payment_id:
            logger.error("Payment signature verification called without order/payment id")
            return False

        expected = hmac_sha256_hex(
            self._key_secret,
            payment_signature_message(order_id, payment_id).encode("utf-8"),
        )
        ok = signatures_match(expected, signature)
        if ok:
            logger.info("Payment signature verified for order %s", order_id)
        else:
            logger.error("Payment signature verification FAILED for order %s", order_id)
        return ok

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str | None) -> bool:
        """Verify ``X-Razorpay-Signature`` over the exact raw request body.

        Computed on the raw bytes directly. The official SDK's helper calls
        ``bytes(body, "utf-8")`` internally and therefore cannot accept the
        ``bytes`` a web framework hands you at all -- passing bytes to it raises
        ``TypeError: encoding without a string argument``.
        """
        if not self._webhook_secret:
            logger.error(
                "RAZORPAY_WEBHOOK_SECRET is not configured; rejecting webhook. "
                "Refusing to process unverifiable payment events."
            )
            return False

        expected = hmac_sha256_hex(self._webhook_secret, raw_body)
        ok = signatures_match(expected, signature)
        if not ok:
            logger.error("Webhook signature verification FAILED (%d bytes)", len(raw_body))
        return ok


# ---------------------------------------------------------------------------
# Sandbox provider
# ---------------------------------------------------------------------------


class SandboxProvider:
    """Offline provider that performs *real* cryptography against a local secret.

    The only thing it fakes is the network. Order IDs are derived
    deterministically from the receipt and amount, so replaying the same
    checkout produces the same order instead of a duplicate -- the offline
    analogue of provider-side idempotency.

    ``sign_payment`` and ``sign_webhook`` exist here and deliberately nowhere
    else: they play the part of Razorpay's servers for a demo or a test. The
    verification methods do not trust them; they recompute and compare.
    """

    name = PROVIDER_SANDBOX
    is_live = False

    def __init__(self, secret: str, webhook_secret: str) -> None:
        if not secret or not webhook_secret:
            raise PaymentProviderNotConfiguredError(
                "SandboxProvider requires a non-empty secret and webhook secret."
            )
        self._secret = secret
        self._webhook_secret = webhook_secret

    # -- provider-side identity generation ---------------------------------

    @staticmethod
    def _derive_id(prefix: str, *parts: str) -> str:
        """Derive a Razorpay-shaped identifier (``<prefix>_<14 alnum>``)."""
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        return f"{prefix}_{digest[:14]}"

    def create_order(
        self,
        *,
        amount_paise: int,
        currency: str,
        receipt: str,
        notes: dict[str, Any] | None = None,
    ) -> ProviderOrder:
        """Mint a deterministic sandbox order.

        The attempt id from ``notes`` participates in the derivation so that two
        retries of the same cart get two distinct order ids, exactly as Razorpay
        would. Without it, a retry would collide with the original order and a
        webhook could not tell the two attempts apart.
        """
        normalised_currency = currency.upper()
        attempt_id = str((notes or {}).get("payment_attempt_id", ""))
        order_id = self._derive_id(
            "order", receipt, str(amount_paise), normalised_currency, attempt_id
        )
        logger.warning(
            "SANDBOX MODE: minted local order %s for receipt=%s amount_paise=%s. "
            "No real payment can occur.",
            order_id,
            receipt,
            amount_paise,
        )
        return ProviderOrder(
            id=order_id,
            amount_paise=amount_paise,
            currency=normalised_currency,
            receipt=receipt,
            status="created",
            provider=self.name,
            raw={
                "id": order_id,
                "amount": amount_paise,
                "currency": normalised_currency,
                "receipt": receipt,
                "status": "created",
                "notes": notes or {},
                "sandbox": True,
            },
        )

    def payment_id_for(self, order_id: str) -> str:
        """Derive the payment ID the sandbox would issue for ``order_id``."""
        return self._derive_id("pay", order_id)

    # -- the parts a real PSP would perform on its own servers --------------

    def sign_payment(self, order_id: str, payment_id: str) -> str:
        """Produce the signature Razorpay would return to the browser.

        Test-mode only. Present so that verification can be *exercised*, not
        skipped.
        """
        return hmac_sha256_hex(
            self._secret, payment_signature_message(order_id, payment_id).encode("utf-8")
        )

    def sign_webhook(self, raw_body: bytes) -> str:
        """Produce the ``X-Razorpay-Signature`` value for ``raw_body``."""
        return hmac_sha256_hex(self._webhook_secret, raw_body)

    # -- verification: identical algorithm, real comparison ----------------

    def verify_payment_signature(
        self, *, order_id: str, payment_id: str, signature: str | None
    ) -> bool:
        """Verify a sandbox payment signature. Forgeries are rejected."""
        if not order_id or not payment_id:
            logger.error("Payment signature verification called without order/payment id")
            return False
        expected = self.sign_payment(order_id, payment_id)
        ok = signatures_match(expected, signature)
        if not ok:
            logger.error(
                "SANDBOX: payment signature verification FAILED for order %s", order_id
            )
        return ok

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str | None) -> bool:
        """Verify a sandbox webhook signature over the exact raw bytes."""
        expected = self.sign_webhook(raw_body)
        ok = signatures_match(expected, signature)
        if not ok:
            logger.error(
                "SANDBOX: webhook signature verification FAILED (%d bytes)", len(raw_body)
            )
        return ok


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_payment_provider(settings: Any | None = None) -> PaymentProvider:
    """Return the configured payment provider.

    Selection is driven solely by ``PAYMENT_PROVIDER``:

    ``razorpay`` (default)
        Requires live keys. Missing keys raise
        :class:`PaymentProviderNotConfiguredError` -- never a silent mock.

    ``sandbox``
        Offline, real HMAC. Logs at WARNING on every construction so it cannot
        run unnoticed.

    Constructed per call rather than cached, so a settings change or a test's
    environment patch takes effect immediately and no mutable provider state
    leaks between requests.
    """
    if settings is None:
        from src.merchant.config import get_settings

        settings = get_settings()

    selected = (getattr(settings, "payment_provider", "") or PROVIDER_RAZORPAY).strip().lower()

    if selected not in VALID_PROVIDERS:
        raise PaymentProviderError(
            f"PAYMENT_PROVIDER={selected!r} is not recognised. "
            f"Valid values: {', '.join(sorted(VALID_PROVIDERS))}."
        )

    if selected == PROVIDER_SANDBOX:
        logger.warning(
            "PAYMENT_PROVIDER=sandbox -- payments are simulated locally with real "
            "HMAC verification. Never enable this in production."
        )
        return SandboxProvider(
            secret=getattr(settings, "sandbox_payment_secret", "") or "",
            webhook_secret=getattr(settings, "sandbox_webhook_secret", "") or "",
        )

    return RazorpayProvider(
        key_id=getattr(settings, "razorpay_key_id", "") or "",
        key_secret=getattr(settings, "razorpay_key_secret", "") or "",
        webhook_secret=getattr(settings, "razorpay_webhook_secret", "") or "",
    )


def is_sandbox_active(settings: Any | None = None) -> bool:
    """Whether payments are simulated, for honest TEST MODE labelling in the UI."""
    if settings is None:
        from src.merchant.config import get_settings

        settings = get_settings()
    selected = (getattr(settings, "payment_provider", "") or PROVIDER_RAZORPAY).strip().lower()
    return selected == PROVIDER_SANDBOX
