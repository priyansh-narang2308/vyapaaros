"use client";

import { useReducer, useCallback, useRef } from "react";
import type {
  CheckoutProtocol,
  CheckoutFlowState,
  CheckoutFlowContext,
  CheckoutFlowAction,
  Product,
  CheckoutSessionResponse,
  UpdateCheckoutRequest,
  PaymentFormData,
  BillingAddressFormData,
} from "@/types";
import {
  createCheckoutSessionByProtocol,
  getCheckoutSessionByProtocol,
  updateCheckoutSessionByProtocol,
  completeCheckoutByProtocol,
  delegatePayment,
} from "@/lib/api-client";
import type { ProtocolSessionRef } from "@/lib/api-client";
import { createAPIError } from "@/lib/errors";
import type { ACPEventType, ACPEventStatus } from "@/hooks/useACPLog";
import type {
  AgentType,
  AgentActivityStatus,
  PromotionInputSignals,
  PromotionDecision,
  LineItem,
  Product as ProductType,
} from "@/types";

const DEFAULT_SHIPPING_ID = "ship_standard";

function truncateId(id: string): string {
  if (id.length <= 12) return id;
  return `...${id.slice(-8)}`;
}

function buildProtocolSessionRef(
  sessionId: string,
  contextId?: string | null,
  paymentHandlerId?: string | null
): ProtocolSessionRef {
  const sessionRef: ProtocolSessionRef = { sessionId };
  if (contextId != null) {
    sessionRef.contextId = contextId;
  }
  if (paymentHandlerId != null) {
    sessionRef.paymentHandlerId = paymentHandlerId;
  }
  return sessionRef;
}

function getUCPLogEndpoint(action: string): string {
  return `/api/proxy/merchant/a2a -> /a2a (jsonrpc:message/send action:${action})`;
}

function shortCapName(name: string): string {
  const parts = name.split(".");
  return parts.at(-1) ?? name;
}

function formatUCPStatusSummary(
  session: CheckoutSessionResponse,
  details?: string,
  showNegotiated = false
): string {
  const statusText = `Status: ${session.ucpRawStatus ?? session.status}`;
  const metadata: string[] = [];

  if (details) {
    metadata.push(details);
  }

  if (showNegotiated) {
    const capabilities =
      session.capabilities?.extensions?.map((ext) => shortCapName(ext.name)).join(", ") ?? "";
    if (capabilities) {
      metadata.push(`caps: ${capabilities}`);
    }
    if (session.ucpPaymentHandlerIds && session.ucpPaymentHandlerIds.length > 0) {
      metadata.push(`handlers: ${session.ucpPaymentHandlerIds.join(", ")}`);
    }
    if (session.ucpPlatformProfileUrl) {
      metadata.push(`platform: ${session.ucpPlatformProfileUrl}`);
    }
  }

  return metadata.length > 0 ? `${statusText} | ${metadata.join(" | ")}` : statusText;
}

function inferPromotionAction(baseAmount: number, discountAmount: number): string {
  if (discountAmount <= 0 || baseAmount <= 0) {
    return "NO_PROMO";
  }

  const ratio = discountAmount / baseAmount;
  if (Math.abs(ratio - 0.05) <= 0.015) {
    return "DISCOUNT_5_PCT";
  }
  if (Math.abs(ratio - 0.1) <= 0.015) {
    return "DISCOUNT_10_PCT";
  }
  if (Math.abs(ratio - 0.15) <= 0.02) {
    return "DISCOUNT_15_PCT";
  }
  return "DISCOUNT_APPLIED";
}

export interface ACPLogger {
  logEvent: (
    type: ACPEventType,
    method: "POST" | "GET" | "PUT",
    endpoint: string,
    requestSummary?: string
  ) => string;
  completeEvent: (
    id: string,
    status: ACPEventStatus,
    responseSummary?: string,
    statusCode?: number
  ) => void;
  clear: () => void;
}

export interface AgentActivityLogger {
  addAgentEvent: (
    agentType: AgentType,
    inputSignals: PromotionInputSignals,
    decision: PromotionDecision | undefined,
    status: AgentActivityStatus
  ) => void;
  clear: () => void;
}

function logPromotionAgentActivity(
  lineItems: LineItem[],
  product: ProductType | null,
  agentLogger?: AgentActivityLogger
): void {
  if (!agentLogger || !product) return;

  for (const lineItem of lineItems) {
    
    const signals = lineItem.promotion?.signals;
    const inputSignals: PromotionInputSignals = {
      productId: product.id,
      productName: lineItem.name ?? product.name,
      stockCount: product.stockCount,
      basePrice: lineItem.base_amount,
      competitorPrice: null,
      inventoryPressure:
        (signals?.inventory_pressure as "high" | "low") ??
        (product.stockCount > 50 ? "high" : "low"),
      competitionPosition:
        (signals?.competition_position as PromotionInputSignals["competitionPosition"]) ??
        "unknown",
      seasonalUrgency: signals?.seasonal_urgency ?? "off_season",
      productLifecycle: signals?.product_lifecycle ?? "mature",
      demandVelocity: signals?.demand_velocity ?? "flat",
    };

    
    if (lineItem.promotion) {
      const decision: PromotionDecision = {
        action: lineItem.promotion.action,
        discountAmount: lineItem.discount,
        reasonCodes: lineItem.promotion.reason_codes,
        reasoning: lineItem.promotion.reasoning,
      };

      agentLogger.addAgentEvent("promotion", inputSignals, decision, "success");
    } else if (lineItem.discount > 0) {
      
      const decision: PromotionDecision = {
        action: inferPromotionAction(lineItem.base_amount, lineItem.discount),
        discountAmount: lineItem.discount,
        reasonCodes: ["PROMOTION_METADATA_UNAVAILABLE"],
        reasoning:
          "A promotion discount was applied in checkout totals, but detailed promotion reasoning was not included in this response.",
      };
      agentLogger.addAgentEvent("promotion", inputSignals, decision, "success");
    } else {
      
      const decision: PromotionDecision = {
        action: "NO_PROMO",
        discountAmount: 0,
        reasonCodes: ["NO_DISCOUNT_IN_TOTALS"],
        reasoning: "No promotion discount is present in the checkout totals for this response.",
      };
      agentLogger.addAgentEvent("promotion", inputSignals, decision, "success");
    }
  }
}


const DEFAULT_BUYER = {
  first_name: "John",
  last_name: "Doe",
  email: "john@example.com",
  phone_number: "+15551234567",
};


const DEFAULT_FULFILLMENT_ADDRESS = {
  name: "John Doe",
  line_one: "123 Main St",
  city: "San Francisco",
  state: "CA",
  country: "US",
  postal_code: "94102",
};

const initialContext: CheckoutFlowContext = {
  state: "product_selection",
  selectedProduct: null,
  quantity: 1,
  selectedShippingId: DEFAULT_SHIPPING_ID,
  orderId: null,
  sessionId: null,
  ucpContextId: null,
  session: null,
  vaultToken: null,
  isLoading: false,
  error: null,
  checkoutStep: "summary",
  paymentInfo: null,
  billingAddress: null,
};

const validTransitions: Record<CheckoutFlowState, CheckoutFlowState[]> = {
  product_selection: ["checkout", "error"],
  checkout: ["processing", "error", "product_selection"],
  processing: ["confirmation", "error", "checkout"],
  confirmation: ["product_selection"],
  error: ["product_selection", "checkout"],
};

function isValidTransition(from: CheckoutFlowState, to: CheckoutFlowState): boolean {
  return validTransitions[from].includes(to);
}

function getTotalFromSession(session: CheckoutSessionResponse): number {
  const totalItem = session.totals.find((t) => t.type === "total");
  return totalItem?.amount ?? 0;
}

function checkoutFlowReducer(
  context: CheckoutFlowContext,
  action: CheckoutFlowAction
): CheckoutFlowContext {
  switch (action.type) {
    case "SELECT_PRODUCT": {
      return {
        ...context,
        selectedProduct: action.product,
        quantity: 1,
        selectedShippingId: DEFAULT_SHIPPING_ID,
        isLoading: true,
        error: null,
      };
    }

    case "SESSION_CREATED": {
      if (!isValidTransition(context.state, "checkout")) {
        return context;
      }
      
      const firstShippingId =
        action.session.fulfillment_options.find((o) => o.type === "shipping")?.id ??
        DEFAULT_SHIPPING_ID;
      return {
        ...context,
        state: "checkout",
        sessionId: action.session.id,
        ucpContextId: action.session.ucpContextId ?? null,
        session: action.session,
        selectedShippingId: firstShippingId,
        isLoading: false,
        error: null,
      };
    }

    case "SESSION_UPDATED": {
      return {
        ...context,
        sessionId: action.session.id,
        ucpContextId: action.session.ucpContextId ?? context.ucpContextId,
        session: action.session,
        isLoading: false,
        error: null,
      };
    }

    case "UPDATE_QUANTITY": {
      if (context.state !== "checkout") {
        return context;
      }
      const quantity = Math.max(1, Math.min(10, action.quantity));
      return {
        ...context,
        quantity,
      };
    }

    case "SELECT_SHIPPING": {
      if (context.state !== "checkout") {
        return context;
      }
      return {
        ...context,
        selectedShippingId: action.shippingId,
      };
    }

    case "SUBMIT_PAYMENT": {
      if (!isValidTransition(context.state, "processing")) {
        return context;
      }
      return {
        ...context,
        state: "processing",
        isLoading: true,
        error: null,
      };
    }

    case "PAYMENT_DELEGATED": {
      return {
        ...context,
        vaultToken: action.vaultToken,
      };
    }

    case "PAYMENT_COMPLETE": {
      if (!isValidTransition(context.state, "confirmation")) {
        return context;
      }
      return {
        ...context,
        state: "confirmation",
        session: action.session,
        orderId: action.session.order?.id ?? null,
        isLoading: false,
        error: null,
      };
    }

    case "AUTHENTICATION_REQUIRED": {
      
      return {
        ...context,
        session: action.session,
        isLoading: false,
        
      };
    }

    case "SET_LOADING": {
      return {
        ...context,
        isLoading: action.isLoading,
      };
    }

    case "SET_ERROR": {
      return {
        ...context,
        state: "error",
        isLoading: false,
        error: action.error,
      };
    }

    case "CLEAR_ERROR": {
      return {
        ...context,
        error: null,
        state: context.session ? "checkout" : "product_selection",
      };
    }

    case "RESET": {
      return initialContext;
    }

    case "SET_PAYMENT_INFO": {
      return {
        ...context,
        paymentInfo: action.paymentInfo,
      };
    }

    case "SET_BILLING_ADDRESS": {
      return {
        ...context,
        billingAddress: action.billingAddress,
      };
    }

    case "PROCEED_TO_PAYMENT": {
      if (context.state !== "checkout") {
        return context;
      }
      return {
        ...context,
        checkoutStep: "payment",
      };
    }

    case "BACK_TO_SUMMARY": {
      if (context.state !== "checkout") {
        return context;
      }
      return {
        ...context,
        checkoutStep: "summary",
      };
    }

    default:
      return context;
  }
}

export function useCheckoutFlow(
  logger?: ACPLogger,
  agentLogger?: AgentActivityLogger,
  protocol: CheckoutProtocol = "acp"
) {
  const [context, dispatch] = useReducer(checkoutFlowReducer, initialContext);

  
  const loggerRef = useRef(logger);
  const agentLoggerRef = useRef(agentLogger);
  loggerRef.current = logger;
  agentLoggerRef.current = agentLogger;

    const selectProduct = useCallback(
    async (product: Product) => {
      dispatch({ type: "SELECT_PRODUCT", product });

      const eventId = loggerRef.current?.logEvent(
        "session_create",
        "POST",
        protocol === "ucp" ? getUCPLogEndpoint("create_checkout") : "/checkout_sessions",
        protocol === "ucp"
          ? `message/send:create_checkout (${product.name})`
          : `Create session for ${product.name}`
      );

      try {
        const session = await createCheckoutSessionByProtocol(protocol, {
          items: [{ id: product.id, quantity: 1 }],
          buyer: DEFAULT_BUYER,
          fulfillment_address: DEFAULT_FULFILLMENT_ADDRESS,
        });

        if (eventId) {
          loggerRef.current?.completeEvent(
            eventId,
            "success",
            formatUCPStatusSummary(session, `Session ${session.id.slice(0, 8)}... created`, true),
            201
          );
        }

        
        logPromotionAgentActivity(session.line_items, product, agentLoggerRef.current);

        dispatch({ type: "SESSION_CREATED", session });

        
        
        const firstOption = session.fulfillment_options[0];
        if (protocol === "ucp" || firstOption) {
          const updateEventId = loggerRef.current?.logEvent(
            "session_update",
            "POST",
            protocol === "ucp"
              ? getUCPLogEndpoint("update_checkout")
              : `/checkout_sessions/${truncateId(session.id)}`,
            protocol === "ucp"
              ? "message/send:update_checkout"
              : `Select shipping: ${firstOption?.title ?? "default"}`
          );

          try {
            const updatedSession = await updateCheckoutSessionByProtocol(
              protocol,
              buildProtocolSessionRef(
                session.id,
                session.ucpContextId,
                session.ucpPaymentHandlerId
              ),
              firstOption ? { fulfillment_option_id: firstOption.id } : {}
            );

            if (updateEventId) {
              loggerRef.current?.completeEvent(
                updateEventId,
                "success",
                protocol === "ucp"
                  ? formatUCPStatusSummary(updatedSession)
                  : `Status: ${updatedSession.status}`,
                200
              );
            }

            dispatch({ type: "SESSION_UPDATED", session: updatedSession });
          } catch (error) {
            if (updateEventId) {
              loggerRef.current?.completeEvent(updateEventId, "error", "Update failed", 400);
            }
            dispatch({ type: "SET_ERROR", error: createAPIError(error) });
          }
        }
      } catch (error) {
        if (eventId) {
          loggerRef.current?.completeEvent(eventId, "error", "Session creation failed", 400);
        }
        dispatch({ type: "SET_ERROR", error: createAPIError(error) });
      }
    },
    [protocol]
  );

    const updateQuantity = useCallback(
    async (quantity: number) => {
      dispatch({ type: "UPDATE_QUANTITY", quantity });

      if (!context.sessionId || !context.selectedProduct) {
        return;
      }

      dispatch({ type: "SET_LOADING", isLoading: true });

      const eventId = loggerRef.current?.logEvent(
        "session_update",
        "POST",
        protocol === "ucp"
          ? getUCPLogEndpoint("update_checkout")
          : `/checkout_sessions/${truncateId(context.sessionId)}`,
        protocol === "ucp" ? "message/send:update_checkout" : `Update quantity: ${quantity}`
      );

      try {
        const request: UpdateCheckoutRequest = {
          items: [{ id: context.selectedProduct.id, quantity }],
        };

        const session = await updateCheckoutSessionByProtocol(
          protocol,
          buildProtocolSessionRef(
            context.sessionId,
            context.ucpContextId,
            context.session?.ucpPaymentHandlerId
          ),
          request
        );

        if (eventId) {
          const total = session.totals.find((t) => t.type === "total")?.amount ?? 0;
          loggerRef.current?.completeEvent(
            eventId,
            "success",
            protocol === "ucp"
              ? formatUCPStatusSummary(session, `Total: $${(total / 100).toFixed(2)}`)
              : `Total: $${(total / 100).toFixed(2)}`,
            200
          );
        }

        dispatch({ type: "SESSION_UPDATED", session });
      } catch (error) {
        if (eventId) {
          loggerRef.current?.completeEvent(eventId, "error", "Update failed", 400);
        }
        dispatch({ type: "SET_ERROR", error: createAPIError(error) });
      }
    },
    [context.sessionId, context.selectedProduct, context.session, context.ucpContextId, protocol]
  );

    const selectShipping = useCallback(
    async (shippingId: string) => {
      dispatch({ type: "SELECT_SHIPPING", shippingId });

      if (!context.sessionId || !context.selectedProduct) {
        return;
      }

      dispatch({ type: "SET_LOADING", isLoading: true });

      
      const shippingName =
        context.session?.fulfillment_options.find((o) => o.id === shippingId)?.title ?? shippingId;

      const eventId = loggerRef.current?.logEvent(
        "session_update",
        "POST",
        protocol === "ucp"
          ? getUCPLogEndpoint("update_checkout")
          : `/checkout_sessions/${truncateId(context.sessionId)}`,
        protocol === "ucp" ? "message/send:update_checkout" : `Select: ${shippingName}`
      );

      try {
        const session = await updateCheckoutSessionByProtocol(
          protocol,
          buildProtocolSessionRef(
            context.sessionId,
            context.ucpContextId,
            context.session?.ucpPaymentHandlerId
          ),
          {
            fulfillment_option_id: shippingId,
          }
        );

        if (eventId) {
          const total = session.totals.find((t) => t.type === "total")?.amount ?? 0;
          loggerRef.current?.completeEvent(
            eventId,
            "success",
            protocol === "ucp"
              ? formatUCPStatusSummary(session, `Total: $${(total / 100).toFixed(2)}`)
              : `Total: $${(total / 100).toFixed(2)}`,
            200
          );
        }

        dispatch({ type: "SESSION_UPDATED", session });
      } catch (error) {
        if (eventId) {
          loggerRef.current?.completeEvent(eventId, "error", "Update failed", 400);
        }
        dispatch({ type: "SET_ERROR", error: createAPIError(error) });
      }
    },
    [context.sessionId, context.selectedProduct, context.session, context.ucpContextId, protocol]
  );

    const applyCouponCode = useCallback(
    async (couponCode: string) => {
      if (!context.sessionId) {
        return;
      }

      dispatch({ type: "SET_LOADING", isLoading: true });

      const normalized = couponCode.trim().toUpperCase();
      const eventId = loggerRef.current?.logEvent(
        "session_update",
        "POST",
        protocol === "ucp"
          ? getUCPLogEndpoint("update_checkout")
          : `/checkout_sessions/${truncateId(context.sessionId)}`,
        protocol === "ucp"
          ? "message/send:update_checkout"
          : normalized
            ? `Apply coupon: ${normalized}`
            : "Clear coupons"
      );

      try {
        const session = await updateCheckoutSessionByProtocol(
          protocol,
          buildProtocolSessionRef(
            context.sessionId,
            context.ucpContextId,
            context.session?.ucpPaymentHandlerId
          ),
          {
            discounts: {
              codes: normalized ? [normalized] : [],
            },
          }
        );

        if (eventId) {
          const total = session.totals.find((t) => t.type === "total")?.amount ?? 0;
          loggerRef.current?.completeEvent(
            eventId,
            "success",
            protocol === "ucp"
              ? formatUCPStatusSummary(session, `Total: $${(total / 100).toFixed(2)}`)
              : `Total: $${(total / 100).toFixed(2)}`,
            200
          );
        }

        dispatch({ type: "SESSION_UPDATED", session });
      } catch (error) {
        if (eventId) {
          loggerRef.current?.completeEvent(eventId, "error", "Coupon update failed", 400);
        }
        dispatch({ type: "SET_ERROR", error: createAPIError(error) });
      }
    },
    [context.sessionId, context.session, context.ucpContextId, protocol]
  );

    const submitRazorpayPayment = useCallback(
    async (razorpayPaymentId: string, razorpayOrderId: string, razorpaySignature: string) => {
      if (!context.sessionId || !context.session) {
        return;
      }

      dispatch({ type: "SUBMIT_PAYMENT" });

      const completeEventId = loggerRef.current?.logEvent(
        "session_complete",
        "POST",
        `/checkout_sessions/${truncateId(context.sessionId)}/complete`,
        "Complete Checkout with Razorpay"
      );

      try {
        const completionSessionRef = buildProtocolSessionRef(
          context.sessionId,
          context.ucpContextId,
          context.session?.ucpPaymentHandlerId
        );

        const tokenPayload = JSON.stringify({
          razorpay_payment_id: razorpayPaymentId,
          razorpay_order_id: razorpayOrderId,
          razorpay_signature: razorpaySignature,
        });

        const completedSession = await completeCheckoutByProtocol(protocol, completionSessionRef, {
          payment_data: {
            token: tokenPayload,
            provider: "stripe",
          },
          buyer: {
            first_name: "Customer",
            email: "test@example.com",
          },
        });

        if (completeEventId) {
          loggerRef.current?.completeEvent(completeEventId, "success", "Payment successful", 200);
        }

        dispatch({ type: "PAYMENT_COMPLETE", session: completedSession });
      } catch (error) {
        if (completeEventId) {
          loggerRef.current?.completeEvent(completeEventId, "error", "Payment failed", 400);
        }
        dispatch({ type: "SET_ERROR", error: createAPIError(error) });
      }
    },
    [context.sessionId, context.session, context.ucpContextId, protocol]
  );

    const submitPayment = useCallback(
    async (paymentInfoParam?: PaymentFormData, billingAddressParam?: BillingAddressFormData) => {
      
      const paymentInfo = paymentInfoParam ?? context.paymentInfo;
      const billingAddress = billingAddressParam ?? context.billingAddress;

      if (!context.sessionId || !context.session || !paymentInfo || !billingAddress) {
        return;
      }

      dispatch({ type: "SUBMIT_PAYMENT" });
      const expiresAt = new Date(Date.now() + 15 * 60 * 1000).toISOString();

      
      const cardNumber = paymentInfo.cardNumber;
      const expirationParts = paymentInfo.expirationDate.split("/");
      const expMonth = expirationParts[0] ?? "12";
      const expYear = expirationParts[1] ?? "28";
      const fullExpYear = expYear.length === 2 ? `20${expYear}` : expYear;
      const last4 = cardNumber.slice(-4);

      
      
      const addressParts = billingAddress.address.split(",").map((s) => s.trim());
      const billingAddressData = {
        name: billingAddress.fullName,
        line_one: addressParts[0] || "123 Main St",
        city: addressParts[1] || "San Francisco",
        state: addressParts[2]?.split(" ")[0] || "CA",
        country: "US",
        postal_code: addressParts[2]?.split(" ")[1] || "94102",
      };
      let delegateEventId: string | undefined;

      try {
        
        const latestSession = await getCheckoutSessionByProtocol(
          protocol,
          buildProtocolSessionRef(
            context.sessionId,
            context.ucpContextId,
            context.session?.ucpPaymentHandlerId
          )
        );
        dispatch({ type: "SESSION_UPDATED", session: latestSession });

        const totalAmount = getTotalFromSession(latestSession);
        const completionSessionRef = buildProtocolSessionRef(
          latestSession.id,
          latestSession.ucpContextId ?? context.ucpContextId,
          latestSession.ucpPaymentHandlerId ?? context.session?.ucpPaymentHandlerId
        );

        delegateEventId = loggerRef.current?.logEvent(
          "delegate_payment",
          "POST",
          "/agentic_commerce/delegate_payment",
          `Delegate $${(totalAmount / 100).toFixed(2)}`
        );

        const delegateResponse = await delegatePayment({
          payment_method: {
            type: "card",
            card_number_type: "fpan",
            virtual: false,
            number: cardNumber,
            exp_month: expMonth,
            exp_year: fullExpYear,
            display_card_funding_type: "credit",
            display_last4: last4,
          },
          allowance: {
            reason: "one_time",
            max_amount: totalAmount,
            currency: latestSession.currency,
            checkout_session_id: latestSession.id,
            merchant_id: "merchant_nvshop",
            expires_at: expiresAt,
          },
          risk_signals: [
            {
              type: "card_testing",
              action: "authorized",
            },
          ],
          billing_address: billingAddressData,
        });

        if (delegateEventId) {
          loggerRef.current?.completeEvent(
            delegateEventId,
            "success",
            `Vault token: ${delegateResponse.id.slice(0, 10)}...`,
            201
          );
        }

        dispatch({ type: "PAYMENT_DELEGATED", vaultToken: delegateResponse.id });

        
        const completeEventId = loggerRef.current?.logEvent(
          "session_complete",
          "POST",
          protocol === "ucp"
            ? getUCPLogEndpoint("complete_checkout")
            : `/checkout_sessions/${truncateId(context.sessionId)}/complete`,
          protocol === "ucp" ? "message/send:complete_checkout" : "Process payment"
        );

        const completedSession = await completeCheckoutByProtocol(protocol, completionSessionRef, {
          payment_data: {
            token: delegateResponse.id,
            provider: "stripe",
            billing_address: billingAddressData,
          },
          preferred_language: billingAddress.preferredLanguage,
        });

        
        if (completedSession.status === "authentication_required") {
          if (completeEventId) {
            loggerRef.current?.completeEvent(
              completeEventId,
              "success",
              protocol === "ucp"
                ? formatUCPStatusSummary(completedSession, "3DS required")
                : "3DS required",
              200
            );
          }
          dispatch({ type: "AUTHENTICATION_REQUIRED", session: completedSession });
          
          
          setTimeout(async () => {
            const authEventId = loggerRef.current?.logEvent(
              "session_complete",
              "POST",
              protocol === "ucp"
                ? getUCPLogEndpoint("complete_checkout")
                : `/checkout_sessions/${truncateId(context.sessionId!)}/complete`,
              protocol === "ucp" ? "message/send:complete_checkout" : "3DS authentication"
            );
            try {
              const finalSession = await completeCheckoutByProtocol(
                protocol,
                completionSessionRef,
                {
                  payment_data: {
                    token: delegateResponse.id,
                    provider: "stripe",
                  },
                  authentication_result: {
                    outcome: "authenticated",
                  },
                  preferred_language: billingAddress.preferredLanguage,
                }
              );
              if (authEventId) {
                loggerRef.current?.completeEvent(
                  authEventId,
                  "success",
                  protocol === "ucp"
                    ? formatUCPStatusSummary(
                        finalSession,
                        `Order: ${finalSession.order?.id.slice(0, 10)}...`
                      )
                    : `Order: ${finalSession.order?.id.slice(0, 10)}...`,
                  200
                );
              }
              dispatch({ type: "PAYMENT_COMPLETE", session: finalSession });
              
            } catch (error) {
              if (authEventId) {
                loggerRef.current?.completeEvent(authEventId, "error", "Payment failed", 400);
              }
              dispatch({ type: "SET_ERROR", error: createAPIError(error) });
            }
          }, 2000);
        } else if (completedSession.status === "completed") {
          if (completeEventId) {
            loggerRef.current?.completeEvent(
              completeEventId,
              "success",
              protocol === "ucp"
                ? formatUCPStatusSummary(
                    completedSession,
                    `Order: ${completedSession.order?.id.slice(0, 10)}...`
                  )
                : `Order: ${completedSession.order?.id.slice(0, 10)}...`,
              200
            );
          }
          dispatch({ type: "PAYMENT_COMPLETE", session: completedSession });
          
        } else {
          if (completeEventId) {
            loggerRef.current?.completeEvent(
              completeEventId,
              "success",
              protocol === "ucp"
                ? formatUCPStatusSummary(completedSession)
                : `Status: ${completedSession.status}`,
              200
            );
          }
          
          dispatch({ type: "SESSION_UPDATED", session: completedSession });
        }
      } catch (error) {
        if (delegateEventId) {
          loggerRef.current?.completeEvent(delegateEventId, "error", "Payment failed", 400);
        }
        dispatch({ type: "SET_ERROR", error: createAPIError(error) });
      }
    },
    [
      context.sessionId,
      context.ucpContextId,
      context.session,
      context.paymentInfo,
      context.billingAddress,
      protocol,
    ]
  );

    const reset = useCallback(() => {
    loggerRef.current?.clear();
    agentLoggerRef.current?.clear();
    dispatch({ type: "RESET" });
  }, []);

    const clearError = useCallback(() => {
    dispatch({ type: "CLEAR_ERROR" });
  }, []);

    const setPaymentInfo = useCallback((paymentInfo: PaymentFormData) => {
    dispatch({ type: "SET_PAYMENT_INFO", paymentInfo });
  }, []);

    const setBillingAddress = useCallback((billingAddress: BillingAddressFormData) => {
    dispatch({ type: "SET_BILLING_ADDRESS", billingAddress });
  }, []);

    const proceedToPayment = useCallback(() => {
    dispatch({ type: "PROCEED_TO_PAYMENT" });
  }, []);

    const backToSummary = useCallback(() => {
    dispatch({ type: "BACK_TO_SUMMARY" });
  }, []);

  return {
    context,
    dispatch,
    selectProduct,
    updateQuantity,
    selectShipping,
    applyCouponCode,
    submitPayment,
    submitRazorpayPayment,
    reset,
    clearError,
    setPaymentInfo,
    setBillingAddress,
    proceedToPayment,
    backToSummary,
  };
}
