import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./index.css";
import { applyDocumentTheme } from "@openai/apps-sdk-ui/theme";
import type { OpenAiGlobals, ToolOutput } from "./types";


const THEME_STORAGE_KEY = "acp-widget-theme";
const savedTheme = localStorage.getItem(THEME_STORAGE_KEY) as "light" | "dark" | null;
const systemPrefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
applyDocumentTheme(savedTheme ?? (systemPrefersDark ? "dark" : "light"));


let simulatedToolOutput: ToolOutput | null = null;


let simulatedWidgetState: unknown = null;

function getMcpBaseUrl(): string {
  const isViteDevServer = window.location.port === "3001" || window.location.port === "3002";
  const isAppsSdkPath = window.location.pathname.startsWith("/apps-sdk/");

  if (isViteDevServer) {
    return "http://localhost:2091";
  } else if (isAppsSdkPath) {
    return "/apps-sdk";
  } else {
    return "";
  }
}

interface McpJsonRpcResponse {
  result?: {
    structuredContent?: Record<string, unknown>;
    content?: Array<{ type: string; text: string }>;
    isError?: boolean;
  };
  error?: { code: number; message: string };
}

/**
 * Parse the raw response text (SSE stream or plain JSON) into a JSON-RPC response.
 */
function parseMcpResponse(text: string): McpJsonRpcResponse | null {
  // Try SSE stream first — look for "data: " lines containing a JSON-RPC result
  for (const line of text.split("\n")) {
    if (!line.startsWith("data: ")) continue;
    try {
      const data = JSON.parse(line.slice(6)) as McpJsonRpcResponse;
      if (data.result || data.error) return data;
    } catch {
      
    }
  }
  
  try {
    return JSON.parse(text) as McpJsonRpcResponse;
  } catch {
    return null;
  }
}

function extractToolResult(mcpResponse: McpJsonRpcResponse): string {
  const structured = mcpResponse.result?.structuredContent;
  if (structured) return JSON.stringify(structured);
  const textContent = mcpResponse.result?.content?.[0]?.text;
  return textContent ?? JSON.stringify({ success: false, error: "Empty response" });
}

async function callMcpTool(
  name: string,
  args: Record<string, unknown>
): Promise<{ result: string }> {
  const mcpBaseUrl = getMcpBaseUrl();
  const response = await fetch(`${mcpBaseUrl}/api/mcp`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: Date.now(),
      method: "tools/call",
      params: { name, arguments: args },
    }),
    signal: AbortSignal.timeout(65000),
  });

  if (!response.ok) {
    throw new Error(`MCP server returned ${response.status}: ${response.statusText || "Unknown error"}`);
  }

  const text = await response.text();
  const mcpResponse = parseMcpResponse(text);

  if (!mcpResponse) {
    throw new Error("No valid response from MCP server");
  }
  if (mcpResponse.error) {
    throw new Error(`MCP error: ${mcpResponse.error.message}`);
  }

  return { result: extractToolResult(mcpResponse) };
}

function createSimulatedOpenAi(globals?: Partial<OpenAiGlobals>): OpenAiGlobals {
  return {
    theme: globals?.theme ?? "dark",
    locale: globals?.locale ?? "en-US",
    maxHeight: globals?.maxHeight ?? 600,
    displayMode: globals?.displayMode ?? "inline",
    toolInput: globals?.toolInput ?? {},
    toolOutput: globals?.toolOutput ?? simulatedToolOutput,
    widgetState: globals?.widgetState ?? (simulatedWidgetState as OpenAiGlobals["widgetState"]),

    
    setWidgetState: async (state: unknown) => {
      console.log("[SimulatedBridge] setWidgetState:", state);
      simulatedWidgetState = state;
    },

    
    callTool: async (name: string, args: Record<string, unknown>) => {
      console.log("[SimulatedBridge] callTool:", name, args);
      try {
        return await callMcpTool(name, args);
      } catch (error) {
        console.error("[SimulatedBridge] callTool failed:", error);
        const msg = error instanceof Error ? error.message : "callTool failed";
        return { result: JSON.stringify({ success: false, error: msg }) };
      }
    },

    sendFollowUpMessage: async (args: { prompt: string }) => {
      console.log("[SimulatedBridge] sendFollowUpMessage:", args);
    },

    openExternal: (payload: { href: string }) => {
      window.open(payload.href, "_blank");
    },

    requestDisplayMode: async (args: { mode: "pip" | "inline" | "fullscreen" }) => {
      console.log("[SimulatedBridge] requestDisplayMode:", args);
      return { mode: args.mode };
    },

    requestModal: async (args: { title?: string; template?: string; params?: unknown }) => {
      console.log("[SimulatedBridge] requestModal:", args);
      return {};
    },

    requestClose: async () => {
      console.log("[SimulatedBridge] requestClose");
    },
  };
}

function setupBridgeFromParent(): Promise<void> {
  return new Promise((resolve) => {
    const handleMessage = (event: MessageEvent) => {
      if (event.data?.type === "INIT_OPENAI_BRIDGE") {
        const globals = event.data.globals as Partial<OpenAiGlobals> | undefined;
        
        
        if (globals?.toolOutput) {
          simulatedToolOutput = globals.toolOutput;
        }
        
        
        window.openai = createSimulatedOpenAi(globals);
        
        
        window.dispatchEvent(
          new CustomEvent("openai:set_globals", {
            detail: { globals: window.openai },
          })
        );
        
        console.log("[SimulatedBridge] Bridge initialized from parent");
        window.removeEventListener("message", handleMessage);
        resolve();
      }
    };
    
    window.addEventListener("message", handleMessage);
    
    
    setTimeout(() => {
      if (!window.openai) {
        window.openai = createSimulatedOpenAi();
        console.log("[SimulatedBridge] Bridge initialized with defaults (timeout)");
      }
      window.removeEventListener("message", handleMessage);
      resolve();
    }, 2000);
  });
}

function applyGlobalsUpdate(globals?: Partial<OpenAiGlobals>) {
  if (!globals) return;

  if (globals.toolOutput) {
    simulatedToolOutput = globals.toolOutput as ToolOutput;
  }

  if (!window.openai) {
    window.openai = createSimulatedOpenAi(globals);
  } else {
    window.openai = { ...window.openai, ...globals };
  }

  window.dispatchEvent(
    new CustomEvent("openai:set_globals", {
      detail: { globals: window.openai },
    })
  );
}

function setupUpdateListener() {
  window.addEventListener("message", (event: MessageEvent) => {
    if (
      event.data?.type === "UPDATE_OPENAI_GLOBALS" ||
      event.data?.type === "INIT_OPENAI_BRIDGE"
    ) {
      applyGlobalsUpdate(event.data.globals as Partial<OpenAiGlobals> | undefined);
    }
  });
}

async function initializeBridge(): Promise<void> {
  setupUpdateListener();

  
  if (window.openai) {
    console.log("[Bridge] Using real window.openai from client agent");
    return;
  }
  
  
  await setupBridgeFromParent();
}

async function mount() {
  
  await initializeBridge();

  const root = document.getElementById("root");
  if (root) {
    ReactDOM.createRoot(root).render(
      <React.StrictMode>
        <App />
      </React.StrictMode>
    );
  }
}

mount();
