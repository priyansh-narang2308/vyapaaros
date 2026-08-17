                                                    
                                     

"""Payment adapter for isolating external payment provider integrations."""

import logging
from typing import Any

import razorpay

from src.merchant.config import get_settings

logger = logging.getLogger(__name__)


class RazorpayAdapter:
    """Adapter for interacting with Razorpay Orders and Payments APIs."""

    def __init__(self):
        self.settings = get_settings()
        if self.settings.razorpay_key_id and self.settings.razorpay_key_secret:
            self.client = razorpay.Client(
                auth=(self.settings.razorpay_key_id, self.settings.razorpay_key_secret)
            )
        else:
            logger.warning("Razorpay keys not configured. Payment integration will fail.")
            self.client = None

    def create_order(self, amount_paise: int, currency: str, receipt: str) -> dict[str, Any]:
        """Create a Razorpay order.

        Args:
            amount_paise: Order amount in paise.
            currency: Currency code (e.g. INR).
            receipt: Internal reference ID (e.g. checkout session ID).

        Returns:
            Razorpay Order dictionary containing 'id' (the order ID).
        """
        if not self.client:
            raise RuntimeError("Razorpay client is not configured.")

        data = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
        }
        logger.info(f"Creating Razorpay Order: amount={amount_paise}, receipt={receipt}")
        return self.client.order.create(data=data)

    def verify_payment_signature(
        self, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str
    ) -> bool:
        """Verify the payment signature returned by the client checkout."""
        if not self.client:
            raise RuntimeError("Razorpay client is not configured.")

        params = {
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature,
        }
        try:
            self.client.utility.verify_payment_signature(params)
            logger.info(f"Signature verified successfully for order {razorpay_order_id}")
            return True
        except razorpay.errors.SignatureVerificationError:
            logger.error(f"Signature verification FAILED for order {razorpay_order_id}")
            return False

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verify the webhook signature securely."""
        if not self.client:
            raise RuntimeError("Razorpay client is not configured.")

        try:
            self.client.utility.verify_webhook_signature(
                raw_body, signature, self.settings.razorpay_webhook_secret
            )
            return True
        except razorpay.errors.SignatureVerificationError:
            logger.error("Webhook signature verification FAILED.")
            return False
