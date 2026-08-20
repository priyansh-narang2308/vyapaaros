
import { EventEmitter } from "events";


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

class WebhookEventEmitter extends EventEmitter {
  constructor() {
    super();
    
    this.setMaxListeners(100);
  }

    emitWebhook(event: WebhookEvent): void {
    this.emit("webhook", event);
  }

    subscribe(callback: (event: WebhookEvent) => void): () => void {
    this.on("webhook", callback);
    return () => {
      this.off("webhook", callback);
    };
  }
}


const WEBHOOK_EMITTER_KEY = Symbol.for("acp.webhook-emitter");


const globalForWebhook = globalThis as typeof globalThis & {
  [WEBHOOK_EMITTER_KEY]?: WebhookEventEmitter;
};



export const webhookEmitter =
  globalForWebhook[WEBHOOK_EMITTER_KEY] ??
  (globalForWebhook[WEBHOOK_EMITTER_KEY] = new WebhookEventEmitter());
