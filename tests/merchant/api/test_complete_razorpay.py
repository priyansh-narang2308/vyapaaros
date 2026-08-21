# Copyright (c) 2026 VyapaarOS. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Security and correctness tests for the *synchronous* Razorpay completion rail.

Distinct from the webhook rail (see ``test_webhooks.py``): here the client posts
a Razorpay Standard Checkout callback token to
``POST /checkout_sessions/{id}/complete`` and the merchant verifies it inline --
the order id must equal the one on our own payment attempt, and the HMAC
signature must verify -- before any state moves.

This path is what the Apps SDK tool drives (``provider: "razorpay"``). It was
previously unreachable: ``PaymentProviderEnum`` only knew ``stripe``/``adyen``,
so the request body was rejected with a schema (422) error before the service
ran. These tests pin the fix and the money semantics:

* happy path -- a correctly signed callback for our order completes the session
  (CAPTURED -> COMPLETED), marks the attempt captured, and audits each step as
  coming from the provider;
* forged signature -- refused with 402, the session stays payable, nothing is
  captured, and the rejection is audited;
* order-id mismatch -- a token naming an order that is not ours is refused with
  402 (the authoritative order id is the attempt's, never the client's);
* schema guard -- ``provider: "razorpay"`` is accepted by the wire schema, so
  the enum regression cannot silently return.

Hermetic: ``tests/conftest.py`` forces ``PAYMENT_PROVIDER=sandbox``, whose
``sign_payment`` uses the same real HMAC the merchant verifies, so the signature
computed here is exactly the one the app checks -- offline, no live Razorpay.
"""

import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from src.merchant.db.database import get_engine
from src.merchant.db.models import (
    AuditEvent,
    CheckoutSession,
    CheckoutStatus,
    PaymentAttempt,
    PaymentAttemptState,
)
from src.merchant.domain.payments.audit import (
    ACTOR_PROVIDER_RAZORPAY,
    AuditAction,
    AuditOutcome,
)
from src.merchant.domain.payments.provider import get_payment_provider

COMPLETE_URL = "/checkout_sessions/{session_id}/complete"


# ---------------------------------------------------------------------------
# Setup helper -- drive a session to ready_for_payment and read the attempt
# ---------------------------------------------------------------------------


def _drive_to_ready(auth_client: TestClient) -> tuple[str, str, int]:
    """Create a session and move it to ready_for_payment.

    Returns ``(session_id, provider_order_id, amount_paise)``. Reaching
    ``ready_for_payment`` opens exactly one payment attempt whose
    ``provider_order_id`` is the authoritative order id the callback token must
    match.
    """
    create = auth_client.post(
        "/checkout_sessions",
        json={
            "items": [{"id": "prod_1", "quantity": 1}],
            "buyer": {"first_name": "Sync", "email": "sync@example.com"},
            "fulfillment_address": {
                "name": "Sync User",
                "line_one": "1 Callback Ave",
                "city": "Verifytown",
                "state": "VT",
                "country": "US",
                "postal_code": "00000",
            },
        },
    )
    assert create.status_code == 201, create.text
    session_id = create.json()["id"]
    fulfillment_option_id = create.json()["fulfillment_options"][0]["id"]

    update = auth_client.post(
        f"/checkout_sessions/{session_id}",
        json={"fulfillment_option_id": fulfillment_option_id},
    )
    assert update.status_code == 200, update.text
    assert update.json()["status"] == CheckoutStatus.READY_FOR_PAYMENT.value

    attempt = _get_attempt(session_id)
    assert attempt is not None, "a ready session must have a payment attempt"
    assert attempt.provider_order_id, "the attempt must carry a provider order id"
    return session_id, attempt.provider_order_id, attempt.amount_paise


# ---------------------------------------------------------------------------
# Token helpers -- build the Razorpay callback token the browser would return
# ---------------------------------------------------------------------------


def _token(order_id: str, payment_id: str, signature: str) -> str:
    """Serialize a Razorpay Standard Checkout callback into the token string."""
    return json.dumps(
        {
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        }
    )


def _valid_token(order_id: str) -> tuple[str, str]:
    """A correctly signed callback token for ``order_id``.

    Returns ``(token, payment_id)``. The sandbox provider signs with the same
    HMAC the merchant verifies, so this is a genuine, verifiable signature.
    """
    provider = get_payment_provider()
    payment_id = provider.payment_id_for(order_id)
    signature = provider.sign_payment(order_id, payment_id)
    return _token(order_id, payment_id, signature), payment_id


def _complete(
    client: TestClient, session_id: str, token: str, *, provider: str = "razorpay"
):
    return client.post(
        COMPLETE_URL.format(session_id=session_id),
        json={"payment_data": {"token": token, "provider": provider}},
    )


# ---------------------------------------------------------------------------
# DB read helpers -- file-backed DB is not reset; scope to this session_id.
# ---------------------------------------------------------------------------


def _get_attempt(session_id: str) -> PaymentAttempt | None:
    with Session(get_engine()) as db:
        return db.exec(
            select(PaymentAttempt).where(PaymentAttempt.session_id == session_id)
        ).first()


def _get_status(session_id: str) -> CheckoutStatus | None:
    with Session(get_engine()) as db:
        session = db.exec(
            select(CheckoutSession).where(CheckoutSession.id == session_id)
        ).first()
        return session.status if session else None


def _audit_actions(session_id: str) -> list[str]:
    with Session(get_engine()) as db:
        rows = db.exec(
            select(AuditEvent).where(AuditEvent.session_id == session_id)
        ).all()
        return [row.action for row in rows]


def _audit_rows(session_id: str, action: AuditAction) -> list[AuditEvent]:
    with Session(get_engine()) as db:
        return db.exec(
            select(AuditEvent)
            .where(AuditEvent.session_id == session_id)
            .where(AuditEvent.action == action.value)
        ).all()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestCompleteRazorpayHappyPath:
    def test_valid_razorpay_callback_completes_order(
        self, auth_client: TestClient
    ) -> None:
        session_id, order_id, amount = _drive_to_ready(auth_client)
        token, payment_id = _valid_token(order_id)

        resp = _complete(auth_client, session_id, token)

        # The provider-labelled request is now accepted (was 422 before the fix).
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == CheckoutStatus.COMPLETED.value
        assert data["order"] is not None
        assert data["order"]["checkout_session_id"] == session_id
        assert "permalink_url" in data["order"]

        # The attempt is captured against the verified payment id.
        attempt = _get_attempt(session_id)
        assert attempt is not None
        assert attempt.state == PaymentAttemptState.CAPTURED
        assert attempt.provider_payment_id == payment_id
        assert attempt.signature_verified is True

        # The full money trail is auditable and attributed to the provider.
        actions = _audit_actions(session_id)
        assert AuditAction.PAYMENT_SIGNATURE_VERIFIED.value in actions
        assert AuditAction.PAYMENT_CAPTURED.value in actions
        assert AuditAction.ORDER_COMPLETED.value in actions

        verified = _audit_rows(session_id, AuditAction.PAYMENT_SIGNATURE_VERIFIED)
        assert len(verified) == 1
        assert verified[0].actor == ACTOR_PROVIDER_RAZORPAY
        assert verified[0].outcome == AuditOutcome.SUCCESS.value
        assert int(verified[0].amount_paise) == amount


# ---------------------------------------------------------------------------
# Attacks
# ---------------------------------------------------------------------------


class TestCompleteRazorpaySignature:
    def test_forged_signature_is_refused_with_402(
        self, auth_client: TestClient
    ) -> None:
        session_id, order_id, _ = _drive_to_ready(auth_client)
        provider = get_payment_provider()
        payment_id = provider.payment_id_for(order_id)
        # Correct order id and payment id, but a signature the attacker made up.
        token = _token(order_id, payment_id, "0" * 64)

        resp = _complete(auth_client, session_id, token)

        assert resp.status_code == 402, resp.text

        # Nothing moved: the session is still payable and the attempt is not captured.
        assert _get_status(session_id) == CheckoutStatus.READY_FOR_PAYMENT
        attempt = _get_attempt(session_id)
        assert attempt is not None
        assert attempt.state != PaymentAttemptState.CAPTURED
        assert attempt.provider_payment_id is None

        # The refusal is audited, and no capture/complete was recorded.
        actions = _audit_actions(session_id)
        assert AuditAction.PAYMENT_SIGNATURE_REJECTED.value in actions
        assert AuditAction.PAYMENT_CAPTURED.value not in actions
        assert AuditAction.ORDER_COMPLETED.value not in actions
        rejected = _audit_rows(session_id, AuditAction.PAYMENT_SIGNATURE_REJECTED)
        assert rejected[0].outcome == AuditOutcome.BLOCKED.value


class TestCompleteRazorpayOrderBinding:
    def test_order_id_mismatch_is_refused_with_402(
        self, auth_client: TestClient
    ) -> None:
        session_id, order_id, _ = _drive_to_ready(auth_client)
        # A token that is internally valid for a DIFFERENT order the attacker
        # controls -- but not the order id bound to this session's attempt.
        foreign_order = "order_attacker_controlled"
        token, _ = _valid_token(foreign_order)

        resp = _complete(auth_client, session_id, token)

        assert resp.status_code == 402, resp.text
        assert _get_status(session_id) == CheckoutStatus.READY_FOR_PAYMENT
        attempt = _get_attempt(session_id)
        assert attempt is not None
        assert attempt.state != PaymentAttemptState.CAPTURED

        actions = _audit_actions(session_id)
        assert AuditAction.PAYMENT_SIGNATURE_REJECTED.value in actions
        assert AuditAction.PAYMENT_CAPTURED.value not in actions


class TestCompleteRazorpaySchemaGuard:
    def test_provider_razorpay_is_accepted_by_wire_schema(self) -> None:
        """Regression guard: the wire schema must accept ``provider: "razorpay"``.

        Before the fix, ``PaymentProviderEnum`` lacked ``razorpay`` and this
        raised a validation error, 422-ing the real Razorpay completion path at
        the boundary before the money-verifying service ever ran.
        """
        from src.merchant.domain.checkout.models import CompleteCheckoutRequest

        req = CompleteCheckoutRequest.model_validate(
            {
                "payment_data": {
                    "token": _token("order_x", "pay_x", "sig_x"),
                    "provider": "razorpay",
                }
            }
        )
        assert req.payment_data.provider.value == "razorpay"
