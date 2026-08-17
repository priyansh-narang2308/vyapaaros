

import { NextRequest, NextResponse } from "next/server";
import crypto from "crypto";
import { webhookEmitter } from "@/lib/webhook-emitter";



const webhookEvents: WebhookEvent[] = [];


export interface WebhookEvent {
  id: string;
  type: "order_created" | "order_updated" | "shipping_update";
  receivedAt: string;
  protocol?: "acp" | "ucp";
  data: OrderEventData | ShippingUpdateData;
}

export interface OrderEventData {
  type: "order";
  checkout_session_id: string;
  order_id?: string;
  permalink_url: string;
  status: "created" | "manual_review" | "confirmed" | "canceled" | "shipped" | "fulfilled";
  refunds: Array<{
    type: "store_credit" | "original_payment";
    amount: number;
  }>;
}

export interface ShippingUpdateData {
  type: "shipping_update";
  checkout_session_id: string;
  order_id: string;
  status: "order_confirmed" | "order_shipped" | "out_for_delivery" | "delivered";
  language: "en" | "es" | "fr";
  subject: string;
  message: string;
  tracking_url?: string;
}


const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || "whsec_demo_secret";

function verifySignature(
  payload: string,
  signature: string | null,
  timestamp: string | null
): boolean {
  
  if (!signature && process.env.NODE_ENV === "development") {
    console.warn("[Webhook] Skipping signature verification in development");
    return true;
  }

  if (!signature || !timestamp) {
    return false;
  }

  
  const signedPayload = `${timestamp}.${payload}`;

  
  const expectedSignature = crypto
    .createHmac("sha256", WEBHOOK_SECRET)
    .update(signedPayload)
    .digest("hex");

  
  try {
    return crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expectedSignature));
  } catch {
    return false;
  }
}

function generateEventId(): string {
  return `evt_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
}

export async function POST(request: NextRequest) {
  try {
    
    const rawBody = await request.text();

    
    const signature = request.headers.get("X-Webhook-Signature");
    const timestamp = request.headers.get("X-Webhook-Timestamp");

    
    if (process.env.NODE_ENV === "production") {
      if (!verifySignature(rawBody, signature, timestamp)) {
        console.error("[Webhook] Invalid signature");
        return NextResponse.json({ error: "Invalid webhook signature" }, { status: 401 });
      }
    }

    
    let payload;
    try {
      payload = JSON.parse(rawBody);
    } catch {
      return NextResponse.json({ error: "Invalid JSON payload" }, { status: 400 });
    }

    
    if (!payload.type || !payload.data) {
      return NextResponse.json({ error: "Missing required fields: type, data" }, { status: 400 });
    }

    
    if (!payload.data.checkout_session_id) {
      return NextResponse.json(
        { error: "Missing required field: data.checkout_session_id" },
        { status: 400 }
      );
    }

    
    const event: WebhookEvent = {
      id: generateEventId(),
      type: payload.type,
      receivedAt: new Date().toISOString(),
      protocol: "acp",
      data: payload.data,
    };

    
    webhookEvents.push(event);

    
    if (webhookEvents.length > 100) {
      webhookEvents.shift();
    }

    
    
    webhookEmitter.emitWebhook(event);

    console.log(`[Webhook] Received and pushed event: ${event.type}`, {
      id: event.id,
      checkout_session_id: payload.data.checkout_session_id,
    });

    
    return NextResponse.json(
      {
        received: true,
        event_id: event.id,
      },
      { status: 200 }
    );
  } catch (error) {
    console.error("[Webhook] Error processing webhook:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const checkoutSessionId = searchParams.get("checkout_session_id");
  const since = searchParams.get("since"); 

  let filteredEvents = webhookEvents;

  
  if (checkoutSessionId) {
    filteredEvents = filteredEvents.filter(
      (event) => event.data.checkout_session_id === checkoutSessionId
    );
  }

  
  if (since) {
    const sinceDate = new Date(since);
    filteredEvents = filteredEvents.filter((event) => new Date(event.receivedAt) > sinceDate);
  }

  return NextResponse.json({
    events: filteredEvents,
    count: filteredEvents.length,
  });
}

export async function DELETE() {
  const count = webhookEvents.length;
  webhookEvents.length = 0; 
  console.log(`[Webhook] Cleared ${count} stored events`);
  return NextResponse.json({ cleared: count });
}
