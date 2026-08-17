




export interface Product {
  id: string;
  sku: string;
  name: string;
  basePrice: number;
  stockCount: number;
  variant?: string;
  size?: string;
  imageUrl?: string;
  recommendationRequestId?: string;
  recommendationPosition?: number;
  recommendationSource?: string;
}





export type LoyaltyTier = "Bronze" | "Silver" | "Gold" | "Platinum";

export interface MerchantUser {
  id: string;
  name: string;
  email: string;
  loyaltyPoints: number;
  tier: LoyaltyTier;
  memberSince?: string;
}





export interface CartItem {
  id: string;
  name: string;
  basePrice: number;
  quantity: number;
  variant?: string;
  size?: string;
  imageUrl?: string;
  recommendationRequestId?: string;
  recommendationPosition?: number;
  recommendationSource?: string;
}

export interface CartState {
  cartId: string;
  items: CartItem[];
  itemCount: number;
  subtotal: number;
  shipping: number;
  tax: number;
  total: number;
  discount: number;
    isCalculating: boolean;
}

export const EMPTY_CART_STATE: CartState = {
  cartId: "",
  items: [],
  itemCount: 0,
  subtotal: 0,
  shipping: 0,
  tax: 0,
  total: 0,
  discount: 0,
  isCalculating: false,
};

// Note: All fee calculations happen on the backend.
// No frontend constants needed - shipping rates come from merchant API.

// =============================================================================
// Widget State Types
// =============================================================================

export type WidgetView = "browse" | "cart" | "checkout" | "confirmation";

export interface WidgetState {
  view: WidgetView;
  cart: CartState;
  selectedProductId: string | null;
  checkoutResult: CheckoutResult | null;
}

export type CheckoutStatus = "confirmed" | "failed" | "pending";

export interface CheckoutResult {
  success: boolean;
  status: CheckoutStatus | "ready_for_payment";
  orderId?: string;
  razorpayOrderId?: string;
  sessionId?: string;
  message?: string;
  error?: string;
  total?: number;
  itemCount?: number;
  orderUrl?: string;
}





export interface PromotionMetadata {
  action: string; 
  reason_codes: string[]; 
  reasoning: string; 
}

export interface ACPLineItem {
  id: string;
  item: {
    id: string;
    quantity: number;
  };
  name?: string; 
  base_amount: number;
  discount: number;
  subtotal: number;
  tax: number;
  total: number;
  promotion?: PromotionMetadata;
}

export interface ACPDiscountAllocation {
  path: string;
  amount: number;
}

export interface ACPAppliedDiscount {
  id: string;
  code?: string;
  amount: number;
  automatic?: boolean;
  coupon: {
    id: string;
    name: string;
    percent_off?: number;
    amount_off?: number;
    currency?: string;
  };
  allocations?: ACPDiscountAllocation[];
}

export interface ACPRejectedDiscount {
  code: string;
  reason: string;
  message?: string;
}

export interface ACPDiscounts {
  codes: string[];
  applied: ACPAppliedDiscount[];
  rejected?: ACPRejectedDiscount[];
}

export interface ACPTotal {
  type: string;
  display_text: string;
  amount: number;
}

export interface ACPFulfillmentOption {
  id: string;
  type: string;
  title: string;
  subtitle: string;
  subtotal: number;
  tax: number;
  total: number;
}

export interface ACPSessionResponse {
  id: string;
  status: string;
  currency: string;
  line_items: ACPLineItem[];
  fulfillment_options?: ACPFulfillmentOption[];
  fulfillment_option_id?: string | null;
  totals: ACPTotal[];
  discounts?: ACPDiscounts;
  messages?: Array<{
    type: "info" | "warning" | "error";
    content: string;
    code?: string;
  }>;
  capabilities?: {
    extensions?: Array<{
      name: string;
      extends?: string[];
      schema?: string;
    }>;
  };
  
}





export type DisplayMode = "pip" | "inline" | "fullscreen";
export type Theme = "light" | "dark";

export interface ToolOutput {
  products?: Product[];
  recommendations?: Product[];
  error?: string;
  user?: MerchantUser;
  theme?: Theme;
  locale?: string;
  [key: string]: unknown;
}

export interface OpenAiGlobals {
  
  theme: Theme;
  locale: string;
  maxHeight: number;
  displayMode: DisplayMode;
  toolInput: Record<string, unknown>;
  toolOutput: ToolOutput | null;
  widgetState: WidgetState | null;

  
  setWidgetState: (state: unknown) => Promise<void>;
  callTool: (
    name: string,
    args: Record<string, unknown>,
  ) => Promise<{ result: string }>;
  sendFollowUpMessage: (args: { prompt: string }) => Promise<void>;
  openExternal: (payload: { href: string }) => void;
  requestDisplayMode: (args: {
    mode: DisplayMode;
  }) => Promise<{ mode: DisplayMode }>;
  requestModal: (args: {
    title?: string;
    template?: string;
    params?: unknown;
  }) => Promise<unknown>;
  requestClose: () => Promise<void>;
}


declare global {
  interface Window {
    openai?: OpenAiGlobals;
    Razorpay: any;
  }
}





export function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export function cartStateFromSession(
  session: ACPSessionResponse | null,
  items: CartItem[],
  cartId: string = "",
): CartState {
  if (!session) {
    // No session yet - return items with zero totals, mark as calculating
    return {
      cartId,
      items,
      itemCount: items.reduce((sum, item) => sum + item.quantity, 0),
      subtotal: 0,
      shipping: 0,
      tax: 0,
      total: 0,
      discount: 0,
      isCalculating: items.length > 0, // Calculating if we have items but no session
    };
  }

  // Extract totals from ACP session
  // Backend uses these total types:
  // - items_base_amount: Original item prices before discounts
  // - items_discount: Total discount amount
  // - subtotal: Items after discounts
  // - tax: Tax amount
  // - fulfillment: Shipping cost (called "fulfillment" in backend)
  
  const findTotal = (type: string): number =>
    session.totals?.find((t) => t.type === type)?.amount ?? 0;

  
  const itemsBase = findTotal("items_base_amount");
  const subtotalAfterDiscount = findTotal("subtotal");
  const tax = findTotal("tax");
  const shipping = findTotal("fulfillment");
  const total = findTotal("total");

  
  const totalsDiscount = findTotal("items_discount") + findTotal("discount");
  const discountFromLines =
    session.line_items?.reduce((sum, li) => sum + (li.discount || 0), 0) ?? 0;
  const discount = totalsDiscount > 0 ? totalsDiscount : discountFromLines;

  return {
    cartId: session.id || cartId,
    items,
    itemCount: items.reduce((sum, item) => sum + item.quantity, 0),
    
    subtotal: itemsBase > 0 ? itemsBase : subtotalAfterDiscount,
    shipping,
    tax,
    
    total,
    discount,
    isCalculating: false,
  };
}

export function calculateCartTotals(
  cartId: string,
  items: CartItem[],
): CartState {
  
  return {
    cartId,
    items,
    itemCount: items.reduce((sum, item) => sum + item.quantity, 0),
    subtotal: 0,
    shipping: 0,
    tax: 0,
    total: 0,
    discount: 0,
    isCalculating: true, 
  };
}

export function getWidgetAssetBasePath(): string {
  const isViteDevServer =
    window.location.port === "3001" || window.location.port === "3002";
  const isAppsSdkPath = window.location.pathname.startsWith("/apps-sdk/");

  if (isViteDevServer) {
    
    return "";
  } else if (isAppsSdkPath) {
    // Docker via nginx - images under /apps-sdk/widget/
    return "/apps-sdk/widget";
  } else {
    
    return "/widget";
  }
}

export function getProductImage(productId?: string): string {
  const basePath = getWidgetAssetBasePath();

  if (productId && productId.startsWith("prod_")) {
    return `${basePath}/${productId}.jpeg`;
  }
  
  return `${basePath}/prod_1.jpeg`;
}
