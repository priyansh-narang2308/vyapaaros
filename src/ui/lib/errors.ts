
import type { APIError, ErrorCode } from "@/types";

const ERROR_MESSAGES: Record<string, string> = {
  
  missing: "Required information is missing. Please check your details.",
  invalid: "Some information is invalid. Please review and correct.",
  out_of_stock: "This item is no longer available.",
  payment_declined: "Your payment was declined. Please try a different payment method.",
  requires_sign_in: "Please sign in to continue.",
  requires_3ds: "Additional authentication is required to complete your payment.",

  
  request_not_idempotent: "This request conflicts with a previous one. Please try again.",
  invalid_status_transition: "This action is not available at this time.",
  session_not_found: "Your checkout session has expired. Please start over.",
  product_not_found: "The requested product could not be found.",
  invalid_payment: "Payment information is invalid.",
  validation_error: "Please check your information and try again.",
  missing_api_key: "Authentication failed. Please refresh the page.",
  invalid_api_key: "Authentication failed. Please contact support.",
  configuration_error: "A configuration error occurred. Please try again later.",

  
  checkout_session_not_found: "Your checkout session could not be found.",
  vault_token_not_found: "Payment token not found. Please try again.",
  vault_token_consumed: "This payment has already been processed.",
  vault_token_expired: "Your payment session has expired. Please try again.",
  amount_exceeds_allowance: "The payment amount exceeds the allowed limit.",
  currency_mismatch: "Currency mismatch detected.",
  idempotency_conflict: "A duplicate request was detected.",

  
  network_error: "Network error. Please check your connection and try again.",
  parse_error: "An unexpected error occurred. Please try again.",
  unknown: "An unexpected error occurred. Please try again.",
};

export function getErrorMessage(error: APIError): string {
  return (
    ERROR_MESSAGES[error.code] ??
    error.message ??
    ERROR_MESSAGES.unknown ??
    "An unexpected error occurred"
  );
}

export function isRetryableError(error: APIError): boolean {
  const retryableTypes = ["network_error", "service_unavailable", "processing_error"];
  const retryableCodes = ["configuration_error"];

  return retryableTypes.includes(error.type) || retryableCodes.includes(error.code);
}

export function requiresUserAction(error: APIError): boolean {
  const userActionCodes = [
    "missing",
    "invalid",
    "out_of_stock",
    "payment_declined",
    "requires_sign_in",
    "requires_3ds",
    "vault_token_expired",
    "vault_token_consumed",
  ];

  return userActionCodes.includes(error.code);
}

export function isAuthError(error: APIError): boolean {
  return (
    error.type === "unauthorized" ||
    error.type === "forbidden" ||
    error.code === "missing_api_key" ||
    error.code === "invalid_api_key"
  );
}

export function requires3DS(error: APIError): boolean {
  return error.code === "requires_3ds";
}

export function hasErrorCode(
  messages: { type: string; code?: string }[],
  code: ErrorCode
): boolean {
  return messages.some((msg) => msg.type === "error" && msg.code === code);
}

export function getSessionErrors(
  messages: { type: string; code?: string; content?: string }[]
): string[] {
  return messages
    .filter((msg) => msg.type === "error")
    .map(
      (msg) =>
        msg.content ??
        ERROR_MESSAGES[msg.code ?? "unknown"] ??
        ERROR_MESSAGES.unknown ??
        "An error occurred"
    );
}

export function createAPIError(error: unknown): APIError {
  if (isAPIError(error)) {
    return error;
  }

  if (error instanceof Error) {
    return {
      type: "unknown_error",
      code: "unknown",
      message: error.message,
    };
  }

  return {
    type: "unknown_error",
    code: "unknown",
    message: "An unexpected error occurred",
  };
}

export function isAPIError(error: unknown): error is APIError {
  return (
    typeof error === "object" &&
    error !== null &&
    "type" in error &&
    "code" in error &&
    "message" in error
  );
}

export interface ErrorDisplayProps {
  error: APIError;
  onRetry?: () => void;
  onDismiss?: () => void;
}

export function getSuggestedAction(error: APIError): string | null {
  switch (error.code) {
    case "out_of_stock":
      return "Choose a different item";
    case "payment_declined":
      return "Try a different payment method";
    case "requires_sign_in":
      return "Sign in to continue";
    case "requires_3ds":
      return "Complete authentication";
    case "vault_token_expired":
    case "session_not_found":
      return "Start a new checkout";
    default:
      return isRetryableError(error) ? "Try again" : null;
  }
}
