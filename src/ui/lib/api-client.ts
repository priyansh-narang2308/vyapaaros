
import type {
  CheckoutProtocol,
  CheckoutSessionResponse,
  CreateCheckoutRequest,
  UpdateCheckoutRequest,
  CompleteCheckoutRequest,
  DelegatePaymentRequest,
  DelegatePaymentResponse,
  APIError,
  TimeRange,
  MetricsDashboardAPIResponse,
  Total,
  Message,
  LineItem,
  PaymentProvider,
  FulfillmentOption,
} from "@/types";






const isServer = typeof window === "undefined";




const API_URL = isServer
  ? process.env.MERCHANT_API_URL || "http://localhost:8000"
  : "/api/proxy/merchant";

const PSP_URL = isServer ? process.env.PSP_API_URL || "http://localhost:8001" : "/api/proxy/psp";


const MERCHANT_API_KEY = isServer ? process.env.MERCHANT_API_KEY || "" : "";
const PSP_API_KEY = isServer ? process.env.PSP_API_KEY || "" : "";

const API_VERSION = process.env.NEXT_PUBLIC_API_VERSION || "2026-01-16";
const UCP_A2A_EXTENSION_URL = "https://ucp.dev/2026-01-23/specification/reference/";
const UCP_CHECKOUT_KEY = "a2a.ucp.checkout";
const UCP_PLATFORM_PROFILE_URL =
  process.env.NEXT_PUBLIC_UCP_PLATFORM_PROFILE_URL || "https://platform.example/profile";

export interface ProtocolSessionRef {
  sessionId: string | null;
  contextId?: string | null;
  paymentHandlerId?: string | null;
}

interface UCPA2ATotal {
  type: string;
  label: string;
  amount: number;
}

interface UCPA2ALineItem {
  id: string;
  item: {
    id: string;
    title: string;
    price: number;
  };
  quantity: number;
  totals: UCPA2ATotal[];
}

interface UCPA2AMessage {
  type: "info" | "warning" | "error";
  code?: string;
  path?: string;
  content: string;
}

interface UCPA2ADiscountAllocation {
  path: string;
  amount: number;
}

interface UCPA2AAppliedDiscount {
  id: string;
  code?: string;
  title: string;
  amount: number;
  automatic?: boolean;
  method?: string;
  priority?: number;
  allocations?: UCPA2ADiscountAllocation[];
}

interface UCPA2ADiscounts {
  codes: string[];
  applied: UCPA2AAppliedDiscount[];
}

interface UCPA2AOrder {
  id: string;
  permalink_url?: string;
}

interface UCPA2ACheckout {
  id: string;
  status: string;
  currency: string;
  line_items: UCPA2ALineItem[];
  totals: UCPA2ATotal[];
  messages: UCPA2AMessage[];
  discounts?: UCPA2ADiscounts;
  order?: UCPA2AOrder;
  continue_url?: string;
  ucp?: {
    capabilities?: Record<
      string,
      Array<{
        version: string;
        extends?: string | string[] | null;
      }>
    >;
    payment_handlers?: Record<string, Array<{ id: string }>>;
  };
}

interface A2AResultPart {
  data?: Record<string, unknown>;
}

interface A2AResultMessage {
  contextId: string;
  parts: A2AResultPart[];
}

interface A2AJsonRpcError {
  code: number;
  message: string;
  data?: {
    detail?: string;
  };
}

interface A2AJsonRpcResponse {
  jsonrpc: "2.0";
  id: string | number | null;
  result?: A2AResultMessage;
  error?: A2AJsonRpcError;
}

export function generateIdempotencyKey(): string {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).substring(2, 11);
  return `idem_${timestamp}_${random}`;
}

export function generateRequestId(): string {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).substring(2, 9);
  return `req_${timestamp}_${random}`;
}

function getBaseHeaders(): HeadersInit {
  return {
    "Content-Type": "application/json",
    "API-Version": API_VERSION,
    "Request-Id": generateRequestId(),
  };
}

function getMerchantHeaders(idempotencyKey?: string): HeadersInit {
  const headers: HeadersInit = {
    ...getBaseHeaders(),
    ...(MERCHANT_API_KEY ? { Authorization: `Bearer ${MERCHANT_API_KEY}` } : {}),
  };

  if (idempotencyKey) {
    (headers as Record<string, string>)["Idempotency-Key"] = idempotencyKey;
  }

  return headers;
}

function getUCPHeaders(idempotencyKey: string): HeadersInit {
  const headers = getMerchantHeaders(idempotencyKey) as Record<string, string>;
  headers["UCP-Agent"] = `profile="${UCP_PLATFORM_PROFILE_URL}"`;
  headers["X-A2A-Extensions"] = UCP_A2A_EXTENSION_URL;
  return headers;
}

function getPSPHeaders(idempotencyKey: string): HeadersInit {
  return {
    ...getBaseHeaders(),
    ...(PSP_API_KEY ? { Authorization: `Bearer ${PSP_API_KEY}` } : {}),
    "Idempotency-Key": idempotencyKey,
  };
}

async function parseErrorResponse(response: Response): Promise<APIError> {
  try {
    const data = await response.json();
    return {
      type: data.type || "unknown_error",
      code: data.code || "unknown",
      message: data.message || `HTTP ${response.status} error`,
      param: data.param,
    };
  } catch {
    return {
      type: "network_error",
      code: "parse_error",
      message: `HTTP ${response.status}: ${response.statusText}`,
    };
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const error = await parseErrorResponse(response);
    throw error;
  }
  return response.json();
}

function mapUCPStatusToACP(status: string): CheckoutSessionResponse["status"] {
  switch (status) {
    case "ready_for_complete":
      return "ready_for_payment";
    case "complete_in_progress":
      return "in_progress";
    case "completed":
      return "completed";
    case "canceled":
      return "canceled";
    case "requires_escalation":
    case "incomplete":
    default:
      return "not_ready_for_payment";
  }
}

function mapUCPTotalType(type: string): Total["type"] {
  switch (type) {
    case "items_discount":
      return "items_discount";
    case "discount":
      return "discount";
    case "tax":
      return "tax";
    case "total":
      return "total";
    case "subtotal":
    default:
      return "subtotal";
  }
}

function mapUCPMessages(messages: UCPA2AMessage[]): Message[] {
  return messages.map((message) => {
    if (message.type === "warning") {
      return {
        type: "warning",
        code: message.code ?? "warning",
        content_type: "plain",
        content: message.content,
        ...(message.path ? { param: message.path } : {}),
      };
    }
    if (message.type === "error") {
      return {
        type: "error",
        code: "invalid",
        content_type: "plain",
        content: message.content,
        ...(message.path ? { param: message.path } : {}),
      };
    }
    return {
      type: "info",
      content_type: "plain",
      content: message.content,
      ...(message.path ? { param: message.path } : {}),
    };
  });
}

function normalizeUCPCheckout(
  checkout: UCPA2ACheckout,
  contextId: string
): CheckoutSessionResponse {
  const handlerNamespaces = Object.keys(checkout.ucp?.payment_handlers ?? {});
  const handlerIds = Object.values(checkout.ucp?.payment_handlers ?? {})
    .flatMap((handlers) => handlers.map((handler) => handler.id))
    .filter((handlerId): handlerId is string => Boolean(handlerId));
  const negotiatedHandlerId = handlerIds[0];

  const lineItems: LineItem[] = checkout.line_items.map((item) => {
    const subtotal = item.totals.find((total) => total.type === "subtotal")?.amount ?? 0;
    const tax = item.totals.find((total) => total.type === "tax")?.amount ?? 0;
    const total = item.totals.find((total) => total.type === "total")?.amount ?? subtotal + tax;
    const baseAmount = item.item.price * item.quantity;
    const inferredDiscount = Math.max(0, baseAmount - subtotal);
    return {
      id: item.id,
      item: {
        id: item.item.id,
        quantity: item.quantity,
      },
      name: item.item.title,
      base_amount: baseAmount,
      
      
      discount: inferredDiscount,
      subtotal,
      tax,
      total,
    };
  });

  const totals: Total[] = checkout.totals.map((total) => ({
    type: mapUCPTotalType(total.type),
    display_text: total.label,
    amount: total.amount,
  }));

  const paymentProvider: PaymentProvider = {
    provider: "stripe",
    supported_payment_methods: [
      {
        type: "card",
        supported_card_networks: ["visa", "mastercard", "amex", "discover"],
      },
    ],
  };

  
  
  const fulfillmentOptions: FulfillmentOption[] = [
    {
      type: "shipping",
      id: "shipping_standard",
      title: "Standard Shipping",
      subtitle: "5-7 business days",
      subtotal: 599,
      tax: 0,
      total: 599,
    },
    {
      type: "shipping",
      id: "shipping_express",
      title: "Express Shipping",
      subtitle: "2-3 business days",
      subtotal: 1299,
      tax: 0,
      total: 1299,
    },
  ];

  const response: CheckoutSessionResponse = {
    id: checkout.id,
    status: mapUCPStatusToACP(checkout.status),
    currency: checkout.currency.toLowerCase(),
    protocol: "ucp",
    ucpContextId: contextId,
    ucpRawStatus: checkout.status,
    ucpPlatformProfileUrl: UCP_PLATFORM_PROFILE_URL,
    ...(handlerIds.length > 0 ? { ucpPaymentHandlerIds: handlerIds } : {}),
    ...(handlerNamespaces.length > 0 ? { ucpPaymentHandlerNamespaces: handlerNamespaces } : {}),
    ...(negotiatedHandlerId ? { ucpPaymentHandlerId: negotiatedHandlerId } : {}),
    payment_provider: paymentProvider,
    ...(checkout.ucp?.capabilities
      ? {
          capabilities: {
            extensions: Object.keys(checkout.ucp.capabilities).map((name) => ({ name })),
          },
        }
      : {}),
    line_items: lineItems,
    fulfillment_options: fulfillmentOptions,
    totals,
    ...(checkout.discounts
      ? {
          discounts: {
            codes: checkout.discounts.codes,
            applied: checkout.discounts.applied.map((discount) => {
              const mappedDiscount = {
                id: discount.id,
                coupon: {
                  id: discount.id,
                  name: discount.title,
                },
                amount: discount.amount,
                ...(discount.code !== undefined ? { code: discount.code } : {}),
                ...(discount.automatic !== undefined ? { automatic: discount.automatic } : {}),
                ...(discount.method !== undefined ? { method: discount.method } : {}),
                ...(discount.priority !== undefined ? { priority: discount.priority } : {}),
                ...(discount.allocations !== undefined
                  ? { allocations: discount.allocations }
                  : {}),
              };
              return mappedDiscount;
            }),
            rejected: [],
          },
        }
      : {}),
    messages: mapUCPMessages(checkout.messages),
    links: [],
    ...(checkout.order
      ? {
          order: {
            id: checkout.order.id,
            checkout_session_id: checkout.id,
            permalink_url: checkout.order.permalink_url ?? "#",
          },
        }
      : {}),
    ...(checkout.continue_url ? { continue_url: checkout.continue_url } : {}),
  };

  return response;
}

function buildA2AMessage(
  action: string,
  data: Record<string, unknown>,
  contextId?: string | null,
  extraParts?: Array<Record<string, unknown>>
): Record<string, unknown> {
  const parts: Array<Record<string, unknown>> = [{ kind: "data", data: { action, ...data } }];
  if (extraParts) {
    parts.push(...extraParts);
  }

  const message: Record<string, unknown> = {
    role: "user",
    messageId: generateIdempotencyKey(),
    kind: "message",
    parts,
  };
  if (contextId) {
    message.contextId = contextId;
  }
  return {
    jsonrpc: "2.0",
    id: generateRequestId(),
    method: "message/send",
    params: { message },
  };
}

async function postA2AAction(
  action: string,
  data: Record<string, unknown>,
  contextId?: string | null,
  extraParts?: Array<Record<string, unknown>>
): Promise<CheckoutSessionResponse> {
  const response = await fetch(`${API_URL}/a2a`, {
    method: "POST",
    headers: getUCPHeaders(generateIdempotencyKey()),
    body: JSON.stringify(buildA2AMessage(action, data, contextId, extraParts)),
  });

  const json = (await handleResponse<A2AJsonRpcResponse>(response)) as A2AJsonRpcResponse;

  if (json.error) {
    throw {
      type: "invalid_request",
      code: "jsonrpc_error",
      message: json.error.data?.detail ?? json.error.message,
    } satisfies APIError;
  }

  const result = json.result;
  const checkoutData = result?.parts?.[0]?.data?.[UCP_CHECKOUT_KEY];
  if (!result || !checkoutData || typeof checkoutData !== "object") {
    throw {
      type: "unknown_error",
      code: "parse_error",
      message: "Invalid A2A response: missing checkout payload",
    } satisfies APIError;
  }

  return normalizeUCPCheckout(checkoutData as UCPA2ACheckout, result.contextId);
}





export async function createCheckoutSession(
  request: CreateCheckoutRequest
): Promise<CheckoutSessionResponse> {
  const response = await fetch(`${API_URL}/checkout_sessions`, {
    method: "POST",
    headers: getMerchantHeaders(generateIdempotencyKey()),
    body: JSON.stringify(request),
  });

  return handleResponse<CheckoutSessionResponse>(response);
}

export async function getCheckoutSession(sessionId: string): Promise<CheckoutSessionResponse> {
  const response = await fetch(`${API_URL}/checkout_sessions/${sessionId}`, {
    method: "GET",
    headers: getMerchantHeaders(),
    cache: "no-store",
  });

  return handleResponse<CheckoutSessionResponse>(response);
}

export async function getCheckoutSessionByProtocol(
  protocol: CheckoutProtocol,
  sessionRef: ProtocolSessionRef
): Promise<CheckoutSessionResponse> {
  if (!sessionRef.sessionId) {
    const error: APIError = {
      type: "invalid_request",
      code: "session_not_found",
      message: "Missing checkout session ID",
    };
    throw Object.assign(new Error(error.message), error);
  }

  if (protocol === "acp") {
    return getCheckoutSession(sessionRef.sessionId);
  }

  if (!sessionRef.contextId) {
    const error: APIError = {
      type: "invalid_request",
      code: "session_not_found",
      message: "Missing UCP context ID for checkout fetch",
    };
    throw Object.assign(new Error(error.message), error);
  }

  return postA2AAction("get_checkout", {}, sessionRef.contextId);
}

export async function updateCheckoutSession(
  sessionId: string,
  request: UpdateCheckoutRequest
): Promise<CheckoutSessionResponse> {
  const response = await fetch(`${API_URL}/checkout_sessions/${sessionId}`, {
    method: "POST",
    headers: getMerchantHeaders(generateIdempotencyKey()),
    body: JSON.stringify(request),
  });

  return handleResponse<CheckoutSessionResponse>(response);
}

export async function completeCheckout(
  sessionId: string,
  request: CompleteCheckoutRequest
): Promise<CheckoutSessionResponse> {
  const response = await fetch(`${API_URL}/checkout_sessions/${sessionId}/complete`, {
    method: "POST",
    headers: getMerchantHeaders(generateIdempotencyKey()),
    body: JSON.stringify(request),
  });

  return handleResponse<CheckoutSessionResponse>(response);
}

export async function cancelCheckout(sessionId: string): Promise<CheckoutSessionResponse> {
  const response = await fetch(`${API_URL}/checkout_sessions/${sessionId}/cancel`, {
    method: "POST",
    headers: getMerchantHeaders(generateIdempotencyKey()),
    body: JSON.stringify({}),
  });

  return handleResponse<CheckoutSessionResponse>(response);
}

export async function createCheckoutSessionByProtocol(
  protocol: CheckoutProtocol,
  request: CreateCheckoutRequest
): Promise<CheckoutSessionResponse> {
  if (protocol === "acp") {
    return createCheckoutSession(request);
  }

  const firstItem = request.items[0];
  if (!firstItem) {
    throw {
      type: "invalid_request",
      code: "missing",
      message: "At least one item is required to create a checkout session",
    } satisfies APIError;
  }

  return postA2AAction("create_checkout", {
    line_items: request.items.map((item) => ({ item: { id: item.id }, quantity: item.quantity })),
    buyer: request.buyer,
    fulfillment_address: request.fulfillment_address,
    discounts: request.discounts,
  });
}

export async function updateCheckoutSessionByProtocol(
  protocol: CheckoutProtocol,
  sessionRef: ProtocolSessionRef,
  request: UpdateCheckoutRequest
): Promise<CheckoutSessionResponse> {
  if (!sessionRef.sessionId) {
    throw {
      type: "invalid_request",
      code: "session_not_found",
      message: "Missing checkout session ID",
    } satisfies APIError;
  }

  if (protocol === "acp") {
    return updateCheckoutSession(sessionRef.sessionId, request);
  }

  if (!sessionRef.contextId) {
    throw {
      type: "invalid_request",
      code: "session_not_found",
      message: "Missing UCP context ID for checkout update",
    } satisfies APIError;
  }

  const payload: Record<string, unknown> = {};
  if (request.items !== undefined) {
    payload.line_items = request.items.map((item) => ({
      item: { id: item.id },
      quantity: item.quantity,
    }));
  }
  if (request.buyer !== undefined) {
    payload.buyer = request.buyer;
  }
  if (request.fulfillment_address !== undefined) {
    payload.fulfillment_address = request.fulfillment_address;
  }
  if (request.fulfillment_option_id !== undefined) {
    payload.fulfillment_option_id = request.fulfillment_option_id;
  }
  if (request.discounts !== undefined) {
    payload.discounts = request.discounts;
  }

  return postA2AAction("update_checkout", payload, sessionRef.contextId);
}

export async function completeCheckoutByProtocol(
  protocol: CheckoutProtocol,
  sessionRef: ProtocolSessionRef,
  request: CompleteCheckoutRequest
): Promise<CheckoutSessionResponse> {
  if (!sessionRef.sessionId) {
    throw {
      type: "invalid_request",
      code: "session_not_found",
      message: "Missing checkout session ID",
    } satisfies APIError;
  }

  if (protocol === "acp") {
    return completeCheckout(sessionRef.sessionId, request);
  }

  if (!sessionRef.contextId) {
    throw {
      type: "invalid_request",
      code: "session_not_found",
      message: "Missing UCP context ID for checkout completion",
    } satisfies APIError;
  }

  const handlerId = sessionRef.paymentHandlerId?.trim();
  if (!handlerId) {
    throw {
      type: "invalid_request",
      code: "missing",
      message: "Missing negotiated UCP payment handler ID for checkout completion",
    } satisfies APIError;
  }

  return postA2AAction("complete_checkout", {}, sessionRef.contextId, [
    {
      kind: "data",
      data: {
        "a2a.ucp.checkout.payment": {
          instruments: [
            {
              id: request.payment_data.token,
              type: "tokenized_card",
              handler_id: handlerId,
              credential: {
                token: request.payment_data.token,
              },
            },
          ],
        },
      },
    },
  ]);
}

export async function getMetricsDashboard(
  timeRange: TimeRange
): Promise<MetricsDashboardAPIResponse> {
  const response = await fetch(`${API_URL}/metrics/dashboard?time_range=${timeRange}`, {
    method: "GET",
    headers: getMerchantHeaders(),
  });

  return handleResponse<MetricsDashboardAPIResponse>(response);
}





export async function delegatePayment(
  request: DelegatePaymentRequest
): Promise<DelegatePaymentResponse> {
  const idempotencyKey = generateIdempotencyKey();

  const response = await fetch(`${PSP_URL}/agentic_commerce/delegate_payment`, {
    method: "POST",
    headers: getPSPHeaders(idempotencyKey),
    body: JSON.stringify(request),
  });

  return handleResponse<DelegatePaymentResponse>(response);
}





export interface BrandPersona {
  company_name: string;
  tone: "friendly" | "professional" | "casual" | "urgent";
  preferred_language: "en" | "es" | "fr";
}

export interface OrderContext {
  order_id: string;
  customer_name: string;
  items: Array<{
    name: string;
    quantity: number;
  }>;
  tracking_url: string | null;
  estimated_delivery: string;
}

export interface PostPurchaseMessageRequest {
  brand_persona: BrandPersona;
  order: OrderContext;
  status: "order_confirmed" | "order_shipped" | "out_for_delivery" | "delivered";
}

export interface PostPurchaseMessageResponse {
  order_id: string;
  status: string;
  language: string;
  subject: string;
  message: string;
}

export async function generatePostPurchaseMessage(
  request: PostPurchaseMessageRequest
): Promise<PostPurchaseMessageResponse> {
  const response = await fetch("/api/agents/post-purchase", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw {
      type: "processing_error",
      code: "agent_error",
      message: errorData.error || `Post-Purchase Agent error: ${response.status}`,
    };
  }

  return response.json();
}

export interface WebhookShippingPayload {
  type: "shipping_update";
  data: {
    type: "shipping_update";
    checkout_session_id: string;
    order_id: string;
    status: "order_confirmed" | "order_shipped" | "out_for_delivery" | "delivered";
    language: string;
    subject: string;
    message: string;
    tracking_url?: string;
  };
}

export interface WebhookResponse {
  received: boolean;
  event_id: string;
}

export async function postWebhookShippingUpdate(
  payload: WebhookShippingPayload,
  protocol: CheckoutProtocol = "acp"
): Promise<WebhookResponse> {
  const webhookEndpoint = protocol === "ucp" ? "/api/webhooks/ucp" : "/api/webhooks/acp";
  const response = await fetch(webhookEndpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Webhook-Timestamp": new Date().toISOString(),
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw {
      type: "processing_error",
      code: "webhook_error",
      message: errorData.error || `Webhook error: ${response.status}`,
    };
  }

  return response.json();
}





export const apiClient = {
  
  createCheckoutSession,
  createCheckoutSessionByProtocol,
  getCheckoutSession,
  getCheckoutSessionByProtocol,
  updateCheckoutSession,
  updateCheckoutSessionByProtocol,
  completeCheckout,
  completeCheckoutByProtocol,
  cancelCheckout,

  
  delegatePayment,

  
  generatePostPurchaseMessage,

  
  postWebhookShippingUpdate,

  
  generateIdempotencyKey,
  generateRequestId,
};

export default apiClient;
