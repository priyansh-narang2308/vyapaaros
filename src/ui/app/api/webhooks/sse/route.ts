
import { webhookEmitter, WebhookEvent } from "@/lib/webhook-emitter";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const encoder = new TextEncoder();

  
  const stream = new ReadableStream({
    start(controller) {
      
      const connectMessage = `data: ${JSON.stringify({ type: "connected", timestamp: new Date().toISOString() })}\n\n`;
      controller.enqueue(encoder.encode(connectMessage));

      
      const handleWebhook = (event: WebhookEvent) => {
        try {
          const message = `data: ${JSON.stringify(event)}\n\n`;
          controller.enqueue(encoder.encode(message));
        } catch {
          
        }
      };

      
      const unsubscribe = webhookEmitter.subscribe(handleWebhook);

      
      const heartbeatInterval = setInterval(() => {
        try {
          const heartbeat = `data: ${JSON.stringify({ type: "heartbeat", timestamp: new Date().toISOString() })}\n\n`;
          controller.enqueue(encoder.encode(heartbeat));
        } catch {
          
          clearInterval(heartbeatInterval);
          unsubscribe();
        }
      }, 30000);

      
      
      
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no", 
    },
  });
}
