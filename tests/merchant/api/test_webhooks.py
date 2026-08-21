# Copyright (c) 2026 VyapaarOS. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Security and correctness tests for the Razorpay webhook money path.

The webhook is a public, unauthenticated surface (its only auth is the HMAC
signature) that can move a checkout to CAPTURED or FAILED. These tests drive a
real session to ``ready_for_payment`` -- which opens exactly one payment attempt
with an authoritative ``provider_order_id`` and signed ``amount_paise`` -- and
then exercise the handler the way Razorpay (and an attacker) would:

* happy path -- a correctly signed ``payment.captured`` for the authorised
  amount moves the session to CAPTURED, marks the attempt captured, and audits
  it as coming from the provider;
* forged / missing signature -- rejected with 400, no state change, audited;
* replay -- an identical delivery (same event id) is processed exactly once;
* amount tampering -- a validly signed capture for the *wrong* amount is refused
  and audited (``PAYMENT_AMOUNT_MISMATCH_REJECTED``); the session is not captured;
* ``payment.failed`` -- moves the session to FAILED and records the reason;
* unknown order id / unhandled event -- recorded and ignored, never a 500;
* contradiction -- a ``payment.failed`` after capture does not force an illegal
  CAPTURED -> FAILED transition; the captured money stands.

The suite is hermetic: ``tests/conftest.py`` forces ``PAYMENT_PROVIDER=sandbox``,
which performs real HMAC-SHA256 over a fixed local secret, so the signature I
compute here is the exact one the app verifies -- offline, no live Razorpay.
"""

import json
import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from src.merchant.db.database import get_engine
from src.merchant.db.models import (
    AuditEvent,
    CheckoutSession,
    CheckoutStatus,
    PaymentAttempt,
    PaymentAttemptState,
    WebhookEvent,
    WebhookProcessingState,
)
from src.merchant.domain.payments.audit import (
    ACTOR_PROVIDER_RAZORPAY,
    AuditAction,
    AuditOutcome,
)
from src.merchant.domain.payments.provider import get_payment_provider

WEBHOOK_URL = "/webhooks/razorpay"


def _evt(prefix: str) -> str:
    """A globally-unique webhook event id.

    Real Razorpay event ids are unique per delivery, and the handler dedups on
    them durably. The merchant DB is file-backed and not reset between runs, so
    a hardcoded id would collide with a prior run's row and be (correctly)
    rejected as a replay -- unique ids keep each test's delivery first-seen.
    """
    return f"{prefix}_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Setup helpers -- drive a session to ready_for_payment and read the attempt
# ---------------------------------------------------------------------------


def _drive_to_ready(auth_client: TestClient) -> tuple[str, str, int]:
    """Create a session and move it to ready_for_payment.

    Returns ``(session_id, provider_order_id, amount_paise)``. Reaching
    ``ready_for_payment`` is what opens the single payment attempt and its
    provider order, so afterwards the DB holds the authoritative order id and
    signed amount the webhook must be checked against.
    """
    create = auth_client.post(
        "/checkout_sessions",
        json={
            "items": [{"id": "prod_1", "quantity": 1}],
            "buyer": {"first_name": "Webhook", "email": "webhook@example.com"},
            "fulfillment_address": {
                "name": "Webhook User",
                "line_one": "1 Webhook Way",
                "city": "Hookville",
                "state": "WH",
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
# Payload + signing helpers -- sign the EXACT bytes we POST
# ---------------------------------------------------------------------------


def _capture_payload(
    order_id: str, amount: int, payment_id: str = "pay_capture_test"
) -> dict:
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": amount,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }


def _failed_payload(
    order_id: str, amount: int, payment_id: str = "pay_failed_test"
) -> dict:
    return {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": amount,
                    "currency": "INR",
                    "status": "failed",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Card declined by issuing bank.",
                }
            }
        },
    }


def _post_webhook(
    client: TestClient,
    payload: dict,
    *,
    event_id: str | None,
    signature: str | None = None,
    sign: bool = True,
):
    """Serialize ``payload`` once, sign those exact bytes, and POST them.

    Signing the serialized bytes (not re-serializing inside the client) is what
    makes the test faithful: the app verifies the signature over the raw body,
    so any drift between signed and sent bytes would fail verification.
    """
    raw = json.dumps(payload).encode("utf-8")
    if sign and signature is None:
        signature = get_payment_provider().sign_webhook(raw)

    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Razorpay-Signature"] = signature
    if event_id is not None:
        headers["X-Razorpay-Event-Id"] = event_id
    return client.post(WEBHOOK_URL, content=raw, headers=headers)


# ---------------------------------------------------------------------------
# DB read helpers -- the merchant DB is file-backed and NOT reset between
# tests, so everything is scoped to this test's unique session_id.
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


def _count_action(action: AuditAction) -> int:
    """Global count of an audit action (for events with no session scope)."""
    with Session(get_engine()) as db:
        return len(
            db.exec(
                select(AuditEvent).where(AuditEvent.action == action.value)
            ).all()
        )


def _webhook_rows(event_id: str) -> list[WebhookEvent]:
    with Session(get_engine()) as db:
        return db.exec(
            select(WebhookEvent).where(WebhookEvent.id == event_id)
        ).all()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestWebhookHappyPath:
    def test_valid_capture_moves_session_to_captured(
        self, auth_client: TestClient
    ) -> None:
        session_id, order_id, amount = _drive_to_ready(auth_client)
        event_id = _evt("evt_capture_ok")

        resp = _post_webhook(
            auth_client,
            _capture_payload(order_id, amount, payment_id="pay_ok_1"),
            event_id=event_id,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "captured"
        assert body["session_id"] == session_id

        # The session walked ready -> pending -> captured through the state machine.
        assert _get_status(session_id) == CheckoutStatus.CAPTURED

        # The attempt is captured and carries the provider payment id.
        attempt = _get_attempt(session_id)
        assert attempt is not None
        assert attempt.state == PaymentAttemptState.CAPTURED
        assert attempt.provider_payment_id == "pay_ok_1"

        # The capture is attributed to the provider, not to us or the model.
        captured = _audit_rows(session_id, AuditAction.PAYMENT_CAPTURED)
        assert len(captured) == 1
        assert captured[0].actor == ACTOR_PROVIDER_RAZORPAY
        assert captured[0].outcome == AuditOutcome.SUCCESS.value

        # The delivery was durably recorded as processed.
        rows = _webhook_rows(event_id)
        assert len(rows) == 1
        assert rows[0].processing_state == WebhookProcessingState.PROCESSED
        assert rows[0].session_id == session_id


# ---------------------------------------------------------------------------
# Attack: forged / missing signature
# ---------------------------------------------------------------------------


class TestWebhookSignature:
    def test_forged_signature_is_rejected_and_audited(
        self, auth_client: TestClient
    ) -> None:
        session_id, order_id, amount = _drive_to_ready(auth_client)
        event_id = _evt("evt_forged")
        before = _count_action(AuditAction.WEBHOOK_SIGNATURE_REJECTED)

        resp = _post_webhook(
            auth_client,
            _capture_payload(order_id, amount, payment_id="pay_forged"),
            event_id=event_id,
            signature="deadbeefnotavalidsignature",
        )

        assert resp.status_code == 400
        # The money never moved: the session is untouched, no capture audited.
        assert _get_status(session_id) == CheckoutStatus.READY_FOR_PAYMENT
        assert AuditAction.PAYMENT_CAPTURED.value not in _audit_actions(session_id)
        # The forgery was recorded as blocked.
        after = _count_action(AuditAction.WEBHOOK_SIGNATURE_REJECTED)
        assert after == before + 1
        # A rejected delivery is not durably stored as a processed event.
        assert _webhook_rows(event_id) == []

    def test_missing_signature_is_rejected(self, auth_client: TestClient) -> None:
        session_id, order_id, amount = _drive_to_ready(auth_client)
        before = _count_action(AuditAction.WEBHOOK_SIGNATURE_REJECTED)

        resp = _post_webhook(
            auth_client,
            _capture_payload(order_id, amount),
            event_id=_evt("evt_nosig"),
            sign=False,
        )

        assert resp.status_code == 400
        assert _get_status(session_id) == CheckoutStatus.READY_FOR_PAYMENT
        assert _count_action(AuditAction.WEBHOOK_SIGNATURE_REJECTED) == before + 1

    def test_signature_over_different_body_is_rejected(
        self, auth_client: TestClient
    ) -> None:
        """A signature valid for one body must not authenticate a tampered body."""
        session_id, order_id, amount = _drive_to_ready(auth_client)

        # Sign the honest amount, then tamper the amount before sending.
        honest = _capture_payload(order_id, amount, payment_id="pay_tamper")
        honest_sig = get_payment_provider().sign_webhook(
            json.dumps(honest).encode("utf-8")
        )
        tampered = _capture_payload(order_id, amount + 100_00, payment_id="pay_tamper")

        resp = _post_webhook(
            auth_client,
            tampered,
            event_id=_evt("evt_tamper"),
            signature=honest_sig,
        )

        assert resp.status_code == 400
        assert _get_status(session_id) == CheckoutStatus.READY_FOR_PAYMENT


# ---------------------------------------------------------------------------
# Attack: replay / duplicate delivery
# ---------------------------------------------------------------------------


class TestWebhookReplay:
    def test_duplicate_event_id_is_processed_once(
        self, auth_client: TestClient
    ) -> None:
        session_id, order_id, amount = _drive_to_ready(auth_client)
        payload = _capture_payload(order_id, amount, payment_id="pay_replay")
        event_id = _evt("evt_replay")

        first = _post_webhook(auth_client, payload, event_id=event_id)
        second = _post_webhook(auth_client, payload, event_id=event_id)

        assert first.status_code == 200
        assert first.json()["outcome"] == "captured"
        assert second.status_code == 200
        assert "uplicate" in second.json()["message"]

        # Exactly one capture happened despite two deliveries.
        assert len(_audit_rows(session_id, AuditAction.PAYMENT_CAPTURED)) == 1
        # One durable row for the id; the replay was recorded as a duplicate.
        assert len(_webhook_rows(event_id)) == 1
        assert len(_audit_rows(session_id, AuditAction.WEBHOOK_DUPLICATE_IGNORED)) == 1
        # The session is captured, not double-moved.
        assert _get_status(session_id) == CheckoutStatus.CAPTURED


# ---------------------------------------------------------------------------
# Attack: amount tampering with a VALID signature
# ---------------------------------------------------------------------------


class TestWebhookAmountVerification:
    def test_valid_signature_wrong_amount_is_refused(
        self, auth_client: TestClient
    ) -> None:
        session_id, order_id, amount = _drive_to_ready(auth_client)

        # Correctly signed, but claims a different amount than we authorised.
        resp = _post_webhook(
            auth_client,
            _capture_payload(order_id, amount + 1, payment_id="pay_mismatch"),
            event_id=_evt("evt_mismatch"),
        )

        # Answered 200 (no provider retry storm) but the capture is refused.
        assert resp.status_code == 200, resp.text
        assert resp.json()["outcome"] == "amount_mismatch"

        # The session is NOT captured -- the amount we authorised is ours.
        assert _get_status(session_id) == CheckoutStatus.READY_FOR_PAYMENT
        attempt = _get_attempt(session_id)
        assert attempt is not None
        assert attempt.state != PaymentAttemptState.CAPTURED

        # The mismatch was audited as blocked.
        mismatch = _audit_rows(
            session_id, AuditAction.PAYMENT_AMOUNT_MISMATCH_REJECTED
        )
        assert len(mismatch) == 1
        assert mismatch[0].outcome == AuditOutcome.BLOCKED.value
        assert AuditAction.PAYMENT_CAPTURED.value not in _audit_actions(session_id)


# ---------------------------------------------------------------------------
# payment.failed
# ---------------------------------------------------------------------------


class TestWebhookFailure:
    def test_failed_webhook_marks_session_failed(
        self, auth_client: TestClient
    ) -> None:
        session_id, order_id, amount = _drive_to_ready(auth_client)

        resp = _post_webhook(
            auth_client,
            _failed_payload(order_id, amount, payment_id="pay_fail_1"),
            event_id=_evt("evt_failed"),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["outcome"] == "failed"

        assert _get_status(session_id) == CheckoutStatus.FAILED
        attempt = _get_attempt(session_id)
        assert attempt is not None
        assert attempt.state == PaymentAttemptState.FAILED
        assert attempt.failure_reason

        failed = _audit_rows(session_id, AuditAction.PAYMENT_FAILED)
        assert len(failed) == 1
        assert failed[0].actor == ACTOR_PROVIDER_RAZORPAY
        assert failed[0].outcome == AuditOutcome.FAILURE.value


# ---------------------------------------------------------------------------
# Ignored / not-found -- recorded, never a 500
# ---------------------------------------------------------------------------


class TestWebhookIgnored:
    def test_unknown_order_id_is_ignored(self, auth_client: TestClient) -> None:
        # A validly signed event for an order we never issued.
        event_id = _evt("evt_unknown")
        resp = _post_webhook(
            auth_client,
            _capture_payload("order_does_not_exist", 5000, payment_id="pay_ghost"),
            event_id=event_id,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["outcome"] == "not_found"
        # Still durably recorded (as ignored), so a replay is cheap and safe.
        rows = _webhook_rows(event_id)
        assert len(rows) == 1
        assert rows[0].processing_state == WebhookProcessingState.IGNORED

    def test_unhandled_event_type_is_ignored(self, auth_client: TestClient) -> None:
        session_id, order_id, amount = _drive_to_ready(auth_client)
        payload = {
            "event": "payment.authorized",
            "payload": {
                "payment": {"entity": {"id": "pay_auth", "order_id": order_id}}
            },
        }

        resp = _post_webhook(auth_client, payload, event_id=_evt("evt_authorized"))

        assert resp.status_code == 200, resp.text
        assert resp.json()["outcome"] == "ignored"
        # An unhandled event must not move a ready session.
        assert _get_status(session_id) == CheckoutStatus.READY_FOR_PAYMENT

    def test_invalid_json_after_valid_signature_is_rejected(
        self, auth_client: TestClient
    ) -> None:
        raw = b"{not valid json"
        signature = get_payment_provider().sign_webhook(raw)
        resp = auth_client.post(
            WEBHOOK_URL,
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": signature,
                "X-Razorpay-Event-Id": _evt("evt_badjson"),
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Contradiction: a failure after capture must not undo the capture
# ---------------------------------------------------------------------------


class TestWebhookContradiction:
    def test_failed_after_capture_does_not_reverse_capture(
        self, auth_client: TestClient
    ) -> None:
        session_id, order_id, amount = _drive_to_ready(auth_client)

        captured = _post_webhook(
            auth_client,
            _capture_payload(order_id, amount, payment_id="pay_c_then_f"),
            event_id=_evt("evt_capture_first"),
        )
        assert captured.status_code == 200
        assert _get_status(session_id) == CheckoutStatus.CAPTURED

        # A late payment.failed for the same order arrives afterwards.
        failed = _post_webhook(
            auth_client,
            _failed_payload(order_id, amount, payment_id="pay_c_then_f"),
            event_id=_evt("evt_failed_after"),
        )

        # It is acknowledged but does not force an illegal CAPTURED -> FAILED move.
        assert failed.status_code == 200, failed.text
        assert failed.json()["outcome"] == "already_processed"
        assert _get_status(session_id) == CheckoutStatus.CAPTURED
        # No PAYMENT_FAILED audit was written -- the captured money stands.
        assert AuditAction.PAYMENT_FAILED.value not in _audit_actions(session_id)
