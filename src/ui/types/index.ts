export interface Product {
  id: string;
  sku: string;
  name: string;
  description: string;
  basePrice: number; 
  stockCount: number;
  minMargin: number;
  imageUrl: string;
  variant?: string;
  size?: string;
}





export interface Item {
  id: string;
  quantity: number;
}

export interface PromotionMetadata {
  action: string; 
  reason_codes: string[]; 
  reasoning: string; 
  signals?: Record<string, string>; 
}

export interface LineItem {
  id: string;
  item: Item;
  name?: string;
  description?: string;
  images?: string[];
  unit_amount?: number;
  base_amount: number;
  discount: number;
  subtotal: number;
  tax: number;
  total: number;
  promotion?: PromotionMetadata;
}





export interface ShippingFulfillmentOption {
  type: "shipping";
  id: string;
  title: string;
  subtitle: string;
  carrier_info?: string;
  earliest_delivery_time?: string;
  latest_delivery_time?: string;
  subtotal: number;
  tax: number;
  total: number;
}

export interface DigitalFulfillmentOption {
  type: "digital";
  id: string;
  title: string;
  subtitle?: string;
  subtotal: number;
  tax: number;
  total: number;
}

export type FulfillmentOption = ShippingFulfillmentOption | DigitalFulfillmentOption;

export interface LegacyFulfillmentOption {
  id: string;
  name: string;
  description: string;
  price: number;
  estimatedDelivery: string;
}

export interface SelectedFulfillmentOption {
  type: "shipping" | "digital";
  shipping?: {
    option_id: string;
    item_ids: string[];
  };
  digital?: {
    option_id: string;
    item_ids: string[];
  };
}





export interface Address {
  name: string;
  line_one: string;
  line_two?: string;
  city: string;
  state: string;
  country: string;
  postal_code: string;
  phone_number?: string;
}

export interface FulfillmentDetails {
  name?: string;
  phone_number?: string;
  email?: string;
  address: Address;
}





export type CardNetwork = "visa" | "mastercard" | "amex" | "discover";

export interface PaymentMethod {
  type: "card";
  supported_card_networks: CardNetwork[];
}

export interface PaymentProvider {
  provider: "stripe" | "adyen";
  supported_payment_methods: PaymentMethod[];
}

export interface SellerPaymentMethod {
  method: string;
  brands?: string[];
  funding_types?: ("credit" | "debit" | "prepaid")[];
}

export interface SellerInterventions {
  required: string[];
  supported: string[];
  enforcement?: "always" | "conditional" | "optional";
}

export interface SellerCapabilities {
  payment_methods: (SellerPaymentMethod | string)[];
  interventions: SellerInterventions;
  features?: {
    partial_auth?: boolean;
    saved_payment_methods?: boolean;
    network_tokenization?: boolean;
  };
}

export interface AgentInterventions {
  supported: string[];
  max_redirects?: number;
  redirect_context?: "in_app" | "external_browser" | "none";
  max_interaction_depth?: number;
  display_context?: "native" | "webview" | "modal" | "redirect";
}

export interface AgentCapabilities {
  interventions?: AgentInterventions;
  features?: {
    async_completion?: boolean;
    session_persistence?: boolean;
  };
}

export interface ExtensionDeclaration {
  name: string;
  extends?: string[];
  schema?: string;
}

export interface CheckoutCapabilities {
  extensions?: ExtensionDeclaration[];
}

export interface DiscountAllocation {
  path: string;
  amount: number;
}

export interface CouponDetails {
  id: string;
  name: string;
  percent_off?: number;
  amount_off?: number;
  currency?: string;
}

export interface AppliedDiscount {
  id: string;
  code?: string;
  coupon: CouponDetails;
  amount: number;
  automatic?: boolean;
  method?: "each" | "across" | string;
  priority?: number;
  allocations?: DiscountAllocation[];
}

export interface RejectedDiscount {
  code: string;
  reason: string;
  message?: string;
}

export interface DiscountsResponse {
  codes: string[];
  applied: AppliedDiscount[];
  rejected?: RejectedDiscount[];
}





export type TotalType =
  | "items_base_amount"
  | "items_discount"
  | "subtotal"
  | "discount"
  | "fulfillment"
  | "tax"
  | "fee"
  | "total";

export interface Total {
  type: TotalType;
  display_text: string;
  amount: number;
  description?: string;
}

export type MessageType = "info" | "warning" | "error";

export type ErrorCode =
  | "missing"
  | "invalid"
  | "out_of_stock"
  | "payment_declined"
  | "requires_sign_in"
  | "requires_3ds";

export interface InfoMessage {
  type: "info";
  param?: string;
  content_type: "plain" | "markdown";
  content: string;
}

export interface ErrorMessage {
  type: "error";
  code: ErrorCode;
  param?: string;
  content_type: "plain" | "markdown";
  content: string;
}

export interface WarningMessage {
  type: "warning";
  code: string;
  param?: string;
  content_type: "plain" | "markdown";
  content: string;
}

export type Message = InfoMessage | WarningMessage | ErrorMessage;

export type LinkType = "terms_of_use" | "privacy_policy" | "return_policy";

export interface Link {
  type: LinkType;
  url: string;
}





export type AuthenticationOutcome = "authenticated" | "denied" | "canceled" | "processing_error";

export interface AuthenticationOutcomeDetails {
  three_ds_cryptogram: string;
  electronic_commerce_indicator: string;
  transaction_id: string;
  version: string;
}

export interface AuthenticationResult {
  outcome: AuthenticationOutcome;
  outcome_details?: AuthenticationOutcomeDetails;
}

export interface AuthenticationMetadata {
  channel?: {
    type: "browser" | "app";
    browser?: {
      accept_header?: string;
      ip_address?: string;
      javascript_enabled?: boolean;
      language?: string;
      user_agent?: string;
      color_depth?: number;
      screen_height?: number;
      screen_width?: number;
      timezone_offset?: number;
    };
  };
  acquirer_details?: {
    acquirer_bin?: string;
    acquirer_country?: string;
    acquirer_merchant_id?: string;
    merchant_name?: string;
  };
  directory_server?: string;
  flow_preference?: {
    type: string;
    challenge?: { type: string };
  };
  redirect_url?: string;
}





export interface Order {
  id: string;
  checkout_session_id: string;
  permalink_url: string;
}





export interface Buyer {
  first_name: string;
  last_name?: string;
  email: string;
  phone_number?: string;
}





export type CheckoutStatus =
  | "not_ready_for_payment"
  | "ready_for_payment"
  | "authentication_required"
  | "in_progress"
  | "completed"
  | "canceled";

export type CheckoutProtocol = "acp" | "ucp";





export interface CheckoutSessionResponse {
  id: string;
  status: CheckoutStatus;
  currency: string;
  protocol?: CheckoutProtocol;
  ucpContextId?: string;
  ucpPaymentHandlerId?: string;
  ucpRawStatus?: string;
  ucpPlatformProfileUrl?: string;
  ucpPaymentHandlerIds?: string[];
  ucpPaymentHandlerNamespaces?: string[];
  continue_url?: string;
  buyer?: Buyer;
  capabilities?: CheckoutCapabilities;
  payment_provider: PaymentProvider;
  seller_capabilities?: SellerCapabilities;
  discounts?: DiscountsResponse;
  line_items: LineItem[];
  fulfillment_details?: FulfillmentDetails;
  fulfillment_options: FulfillmentOption[];
  selected_fulfillment_options?: SelectedFulfillmentOption[];
  fulfillment_option_id?: string;
  totals: Total[];
  messages: Message[];
  links: Link[];
  authentication_metadata?: AuthenticationMetadata;
  order?: Order;
  provider_session_id?: string;
}

export interface CheckoutSession {
  id: string;
  status: CheckoutStatus;
  currency: string;
  lineItems: LineItem[];
  subtotal: number;
  discount: number;
  tax: number;
  shipping: number;
  total: number;
  fulfillmentOptions?: LegacyFulfillmentOption[];
  selectedFulfillmentOptionId?: string;
  paymentProvider?: {
    provider: string;
    supportedPaymentMethods: string[];
  };
  createdAt: string;
  updatedAt: string;
}





export interface ItemInput {
  id: string;
  quantity: number;
}

export interface CreateCheckoutRequest {
  items: ItemInput[];
  buyer?: Buyer;
  fulfillment_address?: Address;
  capabilities?: {
    extensions?: string[];
  };
  discounts?: {
    codes: string[];
  };
  coupons?: string[];
}

export interface UpdateCheckoutRequest {
  items?: ItemInput[];
  buyer?: Buyer;
  fulfillment_address?: Address;
  fulfillment_option_id?: string;
  discounts?: {
    codes: string[];
  };
  coupons?: string[];
}

export interface PaymentData {
  token: string;
  provider: "stripe" | "adyen";
  billing_address?: Address;
}

export interface CompleteCheckoutRequest {
  buyer?: Buyer;
  payment_data: PaymentData;
  authentication_result?: AuthenticationResult;
    preferred_language?: SupportedLanguage;
}





export type CardNumberType = "fpan" | "dpan";

export interface PaymentMethodInput {
  type: "card";
  card_number_type: CardNumberType;
  virtual: boolean;
  number: string;
  exp_month: string;
  exp_year: string;
  display_card_funding_type: "credit" | "debit" | "prepaid";
  display_last4: string;
}

export interface Allowance {
  reason: "one_time" | "subscription";
  max_amount: number;
  currency: string;
  checkout_session_id: string;
  merchant_id: string;
  expires_at: string;
}

export interface RiskSignal {
  type: "card_testing" | "fraud" | "velocity";
  action: "authorized" | "blocked" | "review";
}

export interface DelegatePaymentRequest {
  payment_method: PaymentMethodInput;
  allowance: Allowance;
  risk_signals: RiskSignal[];
  billing_address?: Address;
}

export interface VaultTokenMetadata {
  source: string;
  merchant_id: string;
  idempotency_key?: string;
}

export interface DelegatePaymentResponse {
  id: string;
  created: string;
  metadata: VaultTokenMetadata;
}





export type APIErrorType =
  | "invalid_request"
  | "request_not_idempotent"
  | "processing_error"
  | "service_unavailable"
  | "not_found"
  | "method_not_allowed"
  | "unauthorized"
  | "forbidden"
  | "network_error"
  | "unknown_error";

export interface APIError {
  type: APIErrorType;
  code: string;
  message: string;
  param?: string;
}





export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  timestamp: string;
}

export interface ACPRequest {
  id: string;
  method: string;
  endpoint: string;
  timestamp: string;
  status: number;
  payload?: unknown;
  response?: unknown;
}





export type AgentType = "promotion" | "recommendation" | "post_purchase" | "search";

export type AgentActivityStatus = "pending" | "success" | "error" | "skipped";

export interface PromotionInputSignals {
  productId: string;
  productName: string;
  stockCount: number;
  basePrice: number; 
  competitorPrice: number | null; 
  inventoryPressure: "high" | "low";
  competitionPosition: "above_market" | "at_market" | "below_market" | "unknown";
  seasonalUrgency: string;
  productLifecycle: string;
  demandVelocity: string;
}

export interface PromotionDecision {
  action: string; 
  discountAmount: number; 
  reasonCodes: string[];
  reasoning: string;
}

export interface PostPurchaseInputSignals {
  orderId: string;
  customerName: string;
  productName: string;
  status: "order_confirmed" | "order_shipped" | "out_for_delivery" | "delivered";
  tone: "friendly" | "professional" | "casual" | "urgent";
  language: "en" | "es" | "fr";
}

export interface PostPurchaseDecision {
  subject: string;
  message: string;
  status: string;
  language: string;
  trackingUrl?: string;
}

export interface RecommendationInputSignals {
  productId: string;
  productName: string;
  cartItems: Array<{ productId: string; name: string; price: number }>;
}

export interface RecommendationItem {
  productId: string;
  productName: string;
  rank: number;
  reasoning: string;
}

export interface RecommendationPipelineTrace {
  candidatesFound: number;
  afterNliFilter: number;
  finalRanked: number;
}

export interface RecommendationDecision {
  recommendations: RecommendationItem[];
  userIntent?: string;
  pipelineTrace?: RecommendationPipelineTrace;
}

export interface SearchInputSignals {
  query: string;
  limit: number;
}

export interface SearchResultItem {
  productId: string;
  productName: string;
}

export interface SearchDecision {
  results: SearchResultItem[];
  totalResults: number;
}

export type AgentInputSignals =
  | PromotionInputSignals
  | PostPurchaseInputSignals
  | RecommendationInputSignals
  | SearchInputSignals;

export type AgentDecision =
  | PromotionDecision
  | PostPurchaseDecision
  | RecommendationDecision
  | SearchDecision;

export interface AgentActivityEvent {
  id: string;
  timestamp: Date;
  status: AgentActivityStatus;
  duration?: number; 
  agentType: AgentType;
  inputSignals: AgentInputSignals;
  decision?: AgentDecision;
  error?: string;
}





export type CheckoutFlowState =
  | "product_selection"
  | "checkout"
  | "processing"
  | "confirmation"
  | "error";

export interface CheckoutFlowContext {
  state: CheckoutFlowState;
  selectedProduct: Product | null;
  quantity: number;
  selectedShippingId: string;
  orderId: string | null;
  sessionId: string | null;
  ucpContextId: string | null;
  session: CheckoutSessionResponse | null;
  vaultToken: string | null;
  isLoading: boolean;
  error: APIError | null;
  checkoutStep: CheckoutStep;
  paymentInfo: PaymentFormData | null;
  billingAddress: BillingAddressFormData | null;
}

export type CheckoutFlowAction =
  | { type: "SELECT_PRODUCT"; product: Product }
  | { type: "SESSION_CREATED"; session: CheckoutSessionResponse }
  | { type: "SESSION_UPDATED"; session: CheckoutSessionResponse }
  | { type: "UPDATE_QUANTITY"; quantity: number }
  | { type: "SELECT_SHIPPING"; shippingId: string }
  | { type: "SUBMIT_PAYMENT" }
  | { type: "PAYMENT_DELEGATED"; vaultToken: string }
  | { type: "PAYMENT_COMPLETE"; session: CheckoutSessionResponse }
  | { type: "AUTHENTICATION_REQUIRED"; session: CheckoutSessionResponse }
  | { type: "SET_LOADING"; isLoading: boolean }
  | { type: "SET_ERROR"; error: APIError }
  | { type: "CLEAR_ERROR" }
  | { type: "RESET" }
  | { type: "SET_PAYMENT_INFO"; paymentInfo: PaymentFormData }
  | { type: "SET_BILLING_ADDRESS"; billingAddress: BillingAddressFormData }
  | { type: "PROCEED_TO_PAYMENT" }
  | { type: "BACK_TO_SUMMARY" };





export type CheckoutStep = "summary" | "payment";

export interface PaymentFormData {
  cardNumber: string;
  expirationDate: string;
  securityCode: string;
}





export type SupportedLanguage = "en" | "es" | "fr";

export interface LanguageOption {
  code: SupportedLanguage;
  label: string;
  nativeLabel: string;
}

export const LANGUAGE_OPTIONS: LanguageOption[] = [
  { code: "en", label: "English", nativeLabel: "English" },
  { code: "es", label: "Spanish", nativeLabel: "Espanol" },
  { code: "fr", label: "French", nativeLabel: "Francais" },
];

export const DEFAULT_LANGUAGE: SupportedLanguage = "en";

export interface BillingAddressFormData {
  fullName: string;
  address: string;
  preferredLanguage: SupportedLanguage;
}

export const DEFAULT_PAYMENT_FORM: PaymentFormData = {
  cardNumber: "4242424242424242",
  expirationDate: "12/28",
  securityCode: "123",
};

export const DEFAULT_BILLING_ADDRESS: BillingAddressFormData = {
  fullName: "John Doe",
  address: "123 Main St, San Francisco, CA 94102",
  preferredLanguage: "en",
};





export type TimeRange = "1h" | "24h" | "7d" | "30d";

export interface KPIData {
  id: string;
  label: string;
  value: number;
  previousValue?: number;
  format: "currency" | "number" | "percent" | "duration";
  trend?: "up" | "down" | "neutral";
  trendValue?: number;
}

export interface ChartDataPoint {
  timestamp: string;
  value: number;
  label?: string;
}

export interface RevenueDataPoint {
  timestamp: string;
  revenue: number;
  orders: number;
}

export interface AgentPerformanceData {
  agentType: AgentType;
  label: string;
  successRate: number | null;
  avgLatency: number;
  totalCalls: number;
  errors: number;
}

export interface PromotionBreakdownData {
  type: string;
  label: string;
  count: number;
  totalSavings: number;
  color: string;
}

export interface ProductHealthData {
  id: string;
  name: string;
  sku: string;
  stockLevel: number;
  stockStatus: "healthy" | "low" | "critical";
  basePrice: number;
  competitorPrice?: number;
  pricePosition: "above" | "at" | "below" | "unknown";
  lifecycle: string;
  demandVelocity: string;
  needsAttention: boolean;
  attentionReason?: string;
}

export interface RecommendationAttributionTopProductData {
  productId: string;
  productName: string;
  clicks: number;
  purchases: number;
  conversionRate: number | null;
  attributedRevenue: number;
}

export interface RecommendationAttributionData {
  impressions: number;
  clicks: number;
  purchases: number;
  clickThroughRate: number | null;
  conversionRate: number | null;
  attributedRevenue: number;
  topProducts: RecommendationAttributionTopProductData[];
}

export interface MetricsAPIKPI {
  id: string;
  label: string;
  value: number;
  previous_value: number;
  format: "currency" | "number" | "percent" | "duration";
  trend: "up" | "down" | "neutral";
  trend_value: number;
}

export interface MetricsAPIPromotionBreakdown {
  type: string;
  label: string;
  count: number;
  total_savings: number;
}

export interface MetricsAPIProductHealth {
  id: string;
  name: string;
  sku: string;
  stock_level: number;
  stock_status: "healthy" | "low" | "critical";
  base_price: number;
  competitor_price?: number;
  price_position: "above" | "at" | "below" | "unknown";
  lifecycle: string;
  demand_velocity: string;
  needs_attention: boolean;
  attention_reason?: string;
}

export interface MetricsAPIEffectiveWindow {
  requested_time_range: TimeRange;
  start: string;
  end: string;
  fallback_applied: boolean;
}

export interface MetricsAPIAgentOutcome {
  agent_type: AgentType;
  total_calls: number;
  errors: number;
  success_rate: number | null;
  source: "application" | "unavailable";
}

export interface MetricsAPIRecommendationTopProduct {
  product_id: string;
  product_name: string;
  clicks: number;
  purchases: number;
  conversion_rate: number | null;
  attributed_revenue: number;
}

export interface MetricsAPIRecommendationAttribution {
  impressions: number;
  clicks: number;
  purchases: number;
  click_through_rate: number | null;
  conversion_rate: number | null;
  attributed_revenue: number;
  top_products: MetricsAPIRecommendationTopProduct[];
}

export interface MetricsDashboardAPIResponse {
  effective_window: MetricsAPIEffectiveWindow;
  kpis: MetricsAPIKPI[];
  revenue_data: RevenueDataPoint[];
  agent_outcomes: MetricsAPIAgentOutcome[];
  recommendation_attribution: MetricsAPIRecommendationAttribution;
  promotion_breakdown: MetricsAPIPromotionBreakdown[];
  product_health: MetricsAPIProductHealth[];
}

export interface PhoenixTraceData {
  traceId: string;
  spanId: string;
  name: string;
  startTime: string;
  endTime: string;
  duration: number;
  status: "ok" | "error";
  attributes?: Record<string, unknown>;
}

export interface MetricsState {
  timeRange: TimeRange;
  isLoading: boolean;
  lastUpdated: Date | null;
  kpis: KPIData[];
  revenueData: RevenueDataPoint[];
  agentPerformance: AgentPerformanceData[];
  recommendationAttribution: RecommendationAttributionData;
  promotionBreakdown: PromotionBreakdownData[];
  productHealth: ProductHealthData[];
}

export type MetricsAction =
  | { type: "SET_TIME_RANGE"; timeRange: TimeRange }
  | { type: "SET_LOADING"; isLoading: boolean }
  | { type: "UPDATE_METRICS"; metrics: Partial<MetricsState> }
  | { type: "REFRESH" };
