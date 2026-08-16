# VyapaarOS Attribution

This project is a modified version of the **VyapaarOS Agentic Commerce**.
Original Copyright (c) 2026 VyapaarOS CORPORATION & AFFILIATES. All rights reserved.
Licensed under the Apache License, Version 2.0.

## Modifications made for the Razorpay Buildathon

The following components were substantially modified or implemented from scratch for VyapaarOS:

- **Razorpay Adapter (`src/merchant/services/payment_adapter.py`)**: Abstracted the legacy mock PSP into a generic interface, and built a native `RazorpayAdapter` that creates real orders on the Razorpay test environment.
- **Webhooks & Security (`src/merchant/api/routes/webhooks.py`)**: Added Razorpay webhook processing with `HMAC-SHA256` signature verification, deduplication, and idempotent checkout session status management.
- **Policy Engine (`src/merchant/domain/checkout/policy.py`)**: Designed and integrated a localized policy engine enforcing maximum autonomous order values in Paise, ensuring explainability and bounded agent operations.
- **UI Integration (`src/ui/components/agent/AgentPanel.tsx`)**: Rebranded the frontend dashboard to VyapaarOS, injected standard `window.Razorpay` JS flow alongside the UCP mock agent protocol, enabling native payment completion.
- **State Machine Compliance**: Fortified `CheckoutService` to securely transition through `READY_FOR_PAYMENT`, `PAYMENT_PENDING`, and `COMPLETED` asynchronously based on cryptographically signed PSP events.

All original source headers have been preserved in compliance with the Apache 2.0 license.
