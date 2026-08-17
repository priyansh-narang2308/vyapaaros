"use client";

import { useRef, useEffect, useCallback, useState } from "react";
import { useACPLog } from "@/hooks/useACPLog";
import { useMCPClient } from "@/hooks/useMCPClient";
import { useAgentActivityLog } from "@/hooks/useAgentActivityLog";

interface MerchantIframeContainerProps {
    onCheckoutComplete?: (orderId: string) => void;
    searchRequest?: { query: string; requestId: number } | null;
}

const MCP_SERVER_URL = process.env.NEXT_PUBLIC_MCP_SERVER_URL || "http://localhost:2091";

const FALLBACK_WIDGET_URL =
  MCP_SERVER_URL === "/apps-sdk"
    ? "/apps-sdk/widget/merchant-app.html" 
    : "http://localhost:3001"; 

const LOADING_DELAY_MS = 4000;

const MIN_SEARCH_DELAY_MS = 800;

export function MerchantIframeContainer({
  onCheckoutComplete,
  searchRequest,
}: MerchantIframeContainerProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const mcpCalledRef = useRef(false);
  const bridgeInitializedRef = useRef(false);
  const lastSearchRequestIdRef = useRef<number | null>(null);
  const searchLoadingTokenRef = useRef(0);
  const latestGlobalsRef = useRef<Record<string, unknown> | null>(null);
  const [isIframeLoaded, setIsIframeLoaded] = useState(false);
  const [isAnimationComplete, setIsAnimationComplete] = useState(false);
  const [isSearchLoading, setIsSearchLoading] = useState(false);
  const [iframeSrc, setIframeSrc] = useState<string | null>(null);
  const [mcpStatus, setMcpStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [discoveredWidgetUri, setDiscoveredWidgetUri] = useState<string | null>(null);
  const shouldRevealIframe = isIframeLoaded && !isSearchLoading;

  
  const { logEvent, completeEvent } = useACPLog();

  
  const { getWidgetUrl, callToolWithWidget } = useMCPClient();

  
  const { logAgentCall, completeAgentCall } = useAgentActivityLog();

    const postGlobalsToIframe = useCallback((globals: Record<string, unknown>, type: string) => {
    if (!iframeRef.current?.contentWindow) {
      return;
    }
    iframeRef.current.contentWindow.postMessage(
      {
        type,
        globals,
      },
      "*"
    );
  }, []);

  const initializeMCPWidget = useCallback(
    async (query: string) => {
      if (mcpCalledRef.current) return;
      mcpCalledRef.current = true;

      setMcpStatus("loading");

      const trimmedQuery = query.trim();
      const searchEventId = logAgentCall("search", {
        query: trimmedQuery,
        limit: 3,
      });
      const searchLoadingToken = Date.now();
      searchLoadingTokenRef.current = searchLoadingToken;
      setIsSearchLoading(true);

      
      const eventId = logEvent(
        "session_create",
        "POST",
        "/api/mcp (tools/call: search-products)",
        "Calling MCP tool to discover widget URI"
      );

      try {
        
        const { widgetUrl, widgetUri, error, result } = await getWidgetUrl(query, 3);
        const toolError =
          error ?? (typeof result?.error === "string" ? (result.error as string) : null);

        if (widgetUrl && widgetUri) {
          
          
          if (toolError) {
            completeEvent(eventId, "error", toolError, 500);
          } else {
            completeEvent(eventId, "success", `Discovered: ${widgetUri}`, 200);
          }

          setDiscoveredWidgetUri(widgetUri);
          setIframeSrc(widgetUrl);
          setMcpStatus("success");

          const toolOutput = result ?? (toolError ? { error: toolError, products: [] } : null);

          if (toolError) {
            completeAgentCall(searchEventId, "error", undefined, toolError);
          } else if (toolOutput) {
            const productCount = Array.isArray(toolOutput.products)
              ? toolOutput.products.length
              : 0;
            const decision = {
              results: Array.isArray(toolOutput.products)
                ? toolOutput.products.map((product: { id?: string; name?: string }) => ({
                    productId: product.id ?? "",
                    productName: product.name ?? "Product",
                  }))
                : [],
              totalResults:
                typeof toolOutput.totalResults === "number"
                  ? toolOutput.totalResults
                  : productCount,
            };
            completeAgentCall(searchEventId, "success", decision);
          }

          if (toolOutput) {
            latestGlobalsRef.current = {
              toolInput: { query, limit: 3 },
              toolOutput: toolOutput,
            };
          }
        } else {
          throw new Error(toolError ?? "MCP tool did not return widget URI in _meta");
        }
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : "MCP tool call failed";

        
        completeEvent(eventId, "error", `${errorMessage} → Fallback: ${FALLBACK_WIDGET_URL}`, 500);
        completeAgentCall(searchEventId, "error", undefined, errorMessage);

        setIframeSrc(FALLBACK_WIDGET_URL);
        setMcpStatus("error");
      } finally {
        const elapsed = Date.now() - searchLoadingToken;
        const remaining = MIN_SEARCH_DELAY_MS - elapsed;
        if (remaining > 0) {
          await new Promise((resolve) => setTimeout(resolve, remaining));
        }
        if (searchLoadingTokenRef.current === searchLoadingToken) {
          setIsSearchLoading(false);
        }
      }
    },
    [logAgentCall, completeAgentCall, logEvent, completeEvent, getWidgetUrl]
  );

    const handleIframeLoad = useCallback(() => {
    
    const eventId = logEvent("session_create", "GET", "/apps-sdk/init", "Widget loading...");
    completeEvent(eventId, "success", "Apps SDK widget loaded", 200);

    
    if (isAnimationComplete) {
      setIsIframeLoaded(true);
    }

    if (!bridgeInitializedRef.current && latestGlobalsRef.current) {
      postGlobalsToIframe(latestGlobalsRef.current, "UPDATE_OPENAI_GLOBALS");
      bridgeInitializedRef.current = true;
    }
  }, [logEvent, completeEvent, isAnimationComplete, postGlobalsToIframe]);

    useEffect(() => {
    const timer = setTimeout(() => {
      setIsAnimationComplete(true);
      
      setIsIframeLoaded(true);
    }, LOADING_DELAY_MS);

    return () => clearTimeout(timer);
  }, []);

    const handleIframeError = useCallback(() => {
    if (iframeSrc && iframeSrc !== FALLBACK_WIDGET_URL) {
      setIframeSrc(FALLBACK_WIDGET_URL);
    }
  }, [iframeSrc]);

    const handleMessage = useCallback(
    (event: MessageEvent) => {
      const message = event.data;

      if (typeof message !== "object" || message === null) return;

      
      
      
      if (message.type === "CHECKOUT_COMPLETE" && message.orderId) {
        if (onCheckoutComplete) {
          onCheckoutComplete(message.orderId as string);
        }
      }
    },
    [onCheckoutComplete]
  );

    useEffect(() => {
    window.addEventListener("message", handleMessage);
    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, [handleMessage]);

  const handleSearchRequest = useCallback(
    async (query: string) => {
      const trimmedQuery = query.trim();
      if (!trimmedQuery) return;

      const searchLoadingToken = Date.now();
      searchLoadingTokenRef.current = searchLoadingToken;
      setIsSearchLoading(true);

      const searchEventId = logAgentCall("search", {
        query: trimmedQuery,
        limit: 3,
      });

      const acpEventId = logEvent(
        "session_update",
        "POST",
        "/api/mcp (tools/call: search-products)",
        `Searching for "${trimmedQuery}"...`
      );

      try {
        const { result, error } = await callToolWithWidget("search-products", {
          query: trimmedQuery,
          limit: 3,
        });

        const toolError =
          error ?? (typeof result?.error === "string" ? (result.error as string) : null);
        const toolOutput = result ?? (toolError ? { error: toolError, products: [] } : null);

        if (toolOutput) {
          const productCount = Array.isArray(toolOutput.products) ? toolOutput.products.length : 0;
          const globals = {
            toolInput: { query: trimmedQuery, limit: 3 },
            toolOutput: toolOutput,
          };
          latestGlobalsRef.current = globals;
          postGlobalsToIframe(globals, "UPDATE_OPENAI_GLOBALS");
          bridgeInitializedRef.current = true;

          if (toolError) {
            completeEvent(acpEventId, "error", toolError, 500);
            completeAgentCall(searchEventId, "error", undefined, toolError);
          } else {
            completeEvent(acpEventId, "success", `Found ${productCount} products`, 200);
            const decision = {
              results: Array.isArray(toolOutput.products)
                ? toolOutput.products.map((product: { id?: string; name?: string }) => ({
                    productId: product.id ?? "",
                    productName: product.name ?? "Product",
                  }))
                : [],
              totalResults:
                typeof toolOutput.totalResults === "number"
                  ? toolOutput.totalResults
                  : productCount,
            };
            completeAgentCall(searchEventId, "success", decision);
          }
        } else if (toolError) {
          throw new Error(toolError);
        }
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : "Failed to search products";
        completeEvent(acpEventId, "error", errorMessage, 500);
        completeAgentCall(searchEventId, "error", undefined, errorMessage);
      } finally {
        const elapsed = Date.now() - searchLoadingToken;
        const remaining = MIN_SEARCH_DELAY_MS - elapsed;
        if (remaining > 0) {
          await new Promise((resolve) => setTimeout(resolve, remaining));
        }
        if (searchLoadingTokenRef.current === searchLoadingToken) {
          setIsSearchLoading(false);
        }
      }
    },
    [
      callToolWithWidget,
      logAgentCall,
      completeAgentCall,
      logEvent,
      completeEvent,
      postGlobalsToIframe,
    ]
  );

  useEffect(() => {
    if (!searchRequest?.query) return;
    if (searchRequest.requestId === lastSearchRequestIdRef.current) return;
    lastSearchRequestIdRef.current = searchRequest.requestId;

    if (!mcpCalledRef.current) {
      initializeMCPWidget(searchRequest.query);
      return;
    }

    handleSearchRequest(searchRequest.query);
  }, [searchRequest?.requestId, searchRequest?.query, initializeMCPWidget, handleSearchRequest]);

  return (
    <div className={`merchant-iframe-container${isSearchLoading ? " is-search-loading" : ""}`}>
      {}
      {mcpStatus === "loading" && (
        <div className="mcp-status">
          <span className="mcp-dot loading" />
          <span>POST /api/mcp tools/call search-products...</span>
        </div>
      )}
      {mcpStatus === "success" && discoveredWidgetUri && (
        <div className="mcp-status success">
          <span className="mcp-dot success" />
          <span>Discovered: {discoveredWidgetUri}</span>
        </div>
      )}
      {mcpStatus === "error" && (
        <div className="mcp-status error">
          <span className="mcp-dot error" />
          <span>MCP tool call failed - using fallback</span>
        </div>
      )}

      {}
      {(!isIframeLoaded || isSearchLoading) && (
        <div className="loader">
          <div className="topbar">
            <div className="pill shimmer"></div>
            <div className="pill right shimmer"></div>
          </div>

          <div className="body">
            <div className="skeleton-grid">
              <div className="row w60 shimmer"></div>
              <div className="row w80 shimmer"></div>
              <div className="row w40 shimmer"></div>

              <div className="card-row">
                <div className="card shimmer"></div>
                <div className="card shimmer"></div>
                <div className="card shimmer"></div>
              </div>

              <div className="row w80 shimmer" style={{ marginTop: "10px" }}></div>
              <div className="row w60 shimmer"></div>
            </div>
          </div>

          <div className="hint">
            <span className="dots">
              <span className="dot"></span>
              <span className="dot"></span>
              <span className="dot"></span>
            </span>
            <span>{isSearchLoading ? "Refreshing results..." : "Loading..."}</span>
          </div>
        </div>
      )}

      {}
      {iframeSrc && (
        <iframe
          ref={iframeRef}
          src={iframeSrc}
          title="Merchant Widget"
          style={{
            opacity: shouldRevealIframe ? 1 : 0,
            transform: shouldRevealIframe ? "scale(1)" : "scale(0.995)",
            filter: shouldRevealIframe ? "none" : "saturate(0.98)",
            visibility: shouldRevealIframe ? "visible" : "hidden",
            pointerEvents: shouldRevealIframe ? "auto" : "none",
          }}
          className="merchant-iframe"
          onLoad={handleIframeLoad}
          onError={handleIframeError}
          sandbox="allow-scripts allow-same-origin allow-forms"
          allow="payment"
        />
      )}

      <style jsx>{`
        .merchant-iframe-container {
          position: relative;
          flex: 1;
          display: flex;
          flex-direction: column;
          overflow: hidden;
          border-radius: 0 0 var(--glass-radius-sm, 14px) var(--glass-radius-sm, 14px);
          background: #1a1a1a;
        }

        /* MCP Status indicator */
        .mcp-status {
          position: absolute;
          top: 8px;
          left: 8px;
          right: 8px;
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          border-radius: 8px;
          background: rgba(0, 0, 0, 0.6);
          backdrop-filter: blur(4px);
          font-size: 11px;
          color: rgba(255, 255, 255, 0.7);
          z-index: 15;
          animation: fadeIn 0.3s ease;
        }

        .mcp-status.success {
          background: rgba(118, 185, 0, 0.15);
          color: #76b900;
          animation: fadeOut 2s ease forwards;
          animation-delay: 1s;
        }

        .mcp-status.error {
          background: rgba(239, 68, 68, 0.15);
          color: #ef4444;
        }

        .mcp-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: rgba(255, 255, 255, 0.5);
        }

        .mcp-dot.loading {
          animation: pulse 1s ease-in-out infinite;
        }

        .mcp-dot.success {
          background: #76b900;
        }

        .mcp-dot.error {
          background: #ef4444;
        }

        @keyframes fadeIn {
          from {
            opacity: 0;
            transform: translateY(-4px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        @keyframes fadeOut {
          from {
            opacity: 1;
          }
          to {
            opacity: 0;
            visibility: hidden;
          }
        }

        @keyframes pulse {
          0%,
          100% {
            opacity: 0.4;
          }
          50% {
            opacity: 1;
          }
        }

        /* Iframe styling with smooth reveal */
        .merchant-iframe {
          flex: 1;
          width: 100%;
          border: none;
          background: #1a1a1a;
          transition:
            opacity 220ms ease,
            transform 220ms ease,
            filter 220ms ease;
        }

        /* Loader overlay with breathing animation */
        .loader {
          position: absolute;
          inset: 0;
          display: grid;
          grid-template-rows: auto 1fr;
          padding: 18px;
          padding-top: 48px; /* Space for MCP status */
          gap: 14px;
          pointer-events: none;
          opacity: 1;
          transition: opacity 200ms ease;
          background: linear-gradient(to bottom, rgba(26, 26, 26, 0.98), rgba(26, 26, 26, 0.96));
          animation: breathe 1.6s ease-in-out infinite;
          z-index: 10;
        }

        /* Top header skeleton row */
        .topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }

        .pill {
          height: 12px;
          width: 140px;
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.08);
          position: relative;
          overflow: hidden;
        }

        .pill.right {
          width: 92px;
        }

        /* Main skeleton body */
        .body {
          border-radius: 12px;
          border: 1px solid rgba(255, 255, 255, 0.06);
          background: rgba(255, 255, 255, 0.02);
          overflow: hidden;
          position: relative;
        }

        .skeleton-grid {
          padding: 18px;
          display: grid;
          gap: 14px;
        }

        .row {
          height: 12px;
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.08);
          overflow: hidden;
          position: relative;
        }

        .row.w60 {
          width: 60%;
        }
        .row.w80 {
          width: 80%;
        }
        .row.w40 {
          width: 40%;
        }

        .card-row {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 12px;
          margin-top: 6px;
        }

        .card {
          height: 86px;
          border-radius: 14px;
          background: rgba(255, 255, 255, 0.08);
          position: relative;
          overflow: hidden;
        }

        /* Shimmer effect */
        .shimmer::before {
          content: "";
          position: absolute;
          inset: 0;
          transform: translateX(-120%);
          background: linear-gradient(
            90deg,
            transparent 0%,
            rgba(255, 255, 255, 0.08) 45%,
            transparent 90%
          );
          animation: shimmer 1.2s ease-in-out infinite;
        }

        @keyframes shimmer {
          0% {
            transform: translateX(-120%);
          }
          100% {
            transform: translateX(120%);
          }
        }

        @keyframes breathe {
          0%,
          100% {
            opacity: 0.95;
          }
          50% {
            opacity: 0.78;
          }
        }

        /* Loading hint with animated dots */
        .hint {
          position: absolute;
          left: 18px;
          bottom: 14px;
          font-size: 12px;
          color: rgba(255, 255, 255, 0.6);
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .dots {
          display: inline-flex;
          gap: 6px;
        }

        .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: rgba(255, 255, 255, 0.25);
          animation: dots 900ms ease-in-out infinite;
        }

        .dot:nth-child(2) {
          animation-delay: 120ms;
        }

        .dot:nth-child(3) {
          animation-delay: 240ms;
        }

        @keyframes dots {
          0%,
          100% {
            transform: translateY(0);
            opacity: 0.35;
          }
          50% {
            transform: translateY(-2px);
            opacity: 0.85;
          }
        }

        /* Reduced motion preference */
        @media (prefers-reduced-motion: reduce) {
          .loader,
          .shimmer::before,
          .dot {
            animation: none !important;
          }
          .merchant-iframe {
            transition: none !important;
          }
        }
      `}</style>
    </div>
  );
}
