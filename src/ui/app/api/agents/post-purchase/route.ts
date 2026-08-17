

import { NextRequest, NextResponse } from "next/server";

const POST_PURCHASE_AGENT_URL = process.env.POST_PURCHASE_AGENT_URL || "http://localhost:8003";

interface BrandPersona {
  company_name: string;
  tone: "friendly" | "professional" | "casual" | "urgent";
  preferred_language: "en" | "es" | "fr";
}

interface OrderContext {
  order_id: string;
  customer_name: string;
  items: Array<{
    name: string;
    quantity: number;
  }>;
  tracking_url: string | null;
  estimated_delivery: string;
}

interface PostPurchaseMessageRequest {
  brand_persona: BrandPersona;
  order: OrderContext;
  status: "order_confirmed" | "order_shipped" | "out_for_delivery" | "delivered";
}

export async function POST(request: NextRequest) {
  try {
    const body: PostPurchaseMessageRequest = await request.json();

    
    if (!body.brand_persona || !body.order || !body.status) {
      return NextResponse.json(
        { error: "Missing required fields: brand_persona, order, status" },
        { status: 400 }
      );
    }

    
    const response = await fetch(`${POST_PURCHASE_AGENT_URL}/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        query: JSON.stringify(body),
      }),
    });

    if (!response.ok) {
      console.error("[PostPurchase] Agent returned error:", response.status, await response.text());
      return NextResponse.json(
        { error: `Agent error: ${response.status}` },
        { status: response.status }
      );
    }

    const data = await response.json();

    
    try {
      const parsed = JSON.parse(data.value);
      return NextResponse.json(parsed);
    } catch {
      console.error("[PostPurchase] Failed to parse agent response:", data);
      return NextResponse.json({ error: "Failed to parse agent response" }, { status: 500 });
    }
  } catch (error) {
    console.error("[PostPurchase] Proxy error:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
