
import { useState, useCallback } from "react";

const MCP_SERVER_URL = process.env.NEXT_PUBLIC_MCP_SERVER_URL || "http://localhost:2091";

interface MCPToolResponse {
  jsonrpc: string;
  id: number;
  result?: {
    content?: Array<{ type: string; text: string }>;
    structuredContent?: Record<string, unknown>;
    isError?: boolean;
    _meta?: {
      "openai/outputTemplate"?: string;
      "openai/widgetAccessible"?: boolean;
      [key: string]: unknown;
    };
  };
  error?: {
    code: number;
    message: string;
  };
}

interface MCPClientState {
  isLoading: boolean;
  error: string | null;
  widgetUrl: string | null;
  widgetUri: string | null;
  toolResult: Record<string, unknown> | null;
}

function resolveWidgetUri(uri: string, mcpServerUrl: string): string {
  if (uri.startsWith("ui://widget/")) {
    const path = uri.replace("ui://widget/", "/widget/");
    return `${mcpServerUrl}${path}`;
  }
  if (uri.startsWith("http://") || uri.startsWith("https://")) {
    return uri;
  }
  return `${mcpServerUrl}${uri}`;
}

export function useMCPClient() {
  const [state, setState] = useState<MCPClientState>({
    isLoading: false,
    error: null,
    widgetUrl: null,
    widgetUri: null,
    toolResult: null,
  });

    const callTool = useCallback(
    async (
      toolName: string,
      args: Record<string, unknown> = {}
    ): Promise<{
      widgetUrl: string | null;
      widgetUri: string | null;
      result: Record<string, unknown> | null;
      error: string | null;
    }> => {
      setState((prev) => ({ ...prev, isLoading: true, error: null }));

      try {
        
        
        const response = await fetch(`${MCP_SERVER_URL}/api/mcp`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json, text/event-stream",
          },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: Date.now(),
            method: "tools/call",
            params: {
              name: toolName,
              arguments: args,
            },
          }),
          signal: AbortSignal.timeout(65000), 
        });

        if (!response.ok) {
          throw new Error(`MCP server returned ${response.status}: ${response.statusText}`);
        }

        let mcpResponse: MCPToolResponse | null = null;

        
        const text = await response.text();

        
        const lines = text.split("\n");
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.result || data.error) {
                mcpResponse = data;
              }
            } catch {
              
            }
          }
        }

        
        if (!mcpResponse) {
          try {
            mcpResponse = JSON.parse(text);
          } catch {
            
          }
        }

        if (!mcpResponse) {
          throw new Error("No valid response from MCP server");
        }

        if (mcpResponse.error) {
          throw new Error(`MCP error: ${mcpResponse.error.message}`);
        }

        const structuredContent = mcpResponse.result?.structuredContent || null;
        const errorFromStructured =
          structuredContent &&
          typeof structuredContent === "object" &&
          typeof (structuredContent as { error?: unknown }).error === "string"
            ? (structuredContent as { error: string }).error
            : null;
        const contentErrorText = mcpResponse.result?.content?.[0]?.text;
        const toolErrorMessage =
          mcpResponse.result?.isError || errorFromStructured
            ? (errorFromStructured ?? contentErrorText ?? "Tool call failed")
            : null;

        
        const meta = mcpResponse.result?._meta;
        const widgetUri = meta?.["openai/outputTemplate"] || null;

        if (!widgetUri) {
          throw new Error(
            "MCP tool response does not contain widget URI in _meta.openai/outputTemplate"
          );
        }

        
        const widgetUrl = resolveWidgetUri(widgetUri, MCP_SERVER_URL);

        setState({
          isLoading: false,
          error: toolErrorMessage,
          widgetUrl,
          widgetUri,
          toolResult: structuredContent,
        });

        return {
          widgetUrl,
          widgetUri,
          result: structuredContent,
          error: toolErrorMessage,
        };
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : "MCP call failed";

        setState({
          isLoading: false,
          error: errorMessage,
          widgetUrl: null,
          widgetUri: null,
          toolResult: null,
        });

        return {
          widgetUrl: null,
          widgetUri: null,
          result: null,
          error: errorMessage,
        };
      }
    },
    []
  );

    const callToolSimple = useCallback(
    async (
      toolName: string,
      args: Record<string, unknown> = {}
    ): Promise<Record<string, unknown> | null> => {
      try {
        const response = await fetch(`${MCP_SERVER_URL}/api/mcp`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json, text/event-stream",
          },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: Date.now(),
            method: "tools/call",
            params: {
              name: toolName,
              arguments: args,
            },
          }),
          signal: AbortSignal.timeout(65000), 
        });

        if (!response.ok) {
          throw new Error(`MCP server returned ${response.status}`);
        }

        let mcpResponse: MCPToolResponse | null = null;
        const text = await response.text();
        console.log("[MCP] Raw response text (first 500 chars):", text.slice(0, 500));

        
        const lines = text.split("\n");
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.result || data.error) {
                mcpResponse = data;
                console.log("[MCP] Parsed SSE data:", data);
              }
            } catch {
              
            }
          }
        }

        
        if (!mcpResponse) {
          try {
            mcpResponse = JSON.parse(text);
            console.log("[MCP] Parsed as plain JSON:", mcpResponse);
          } catch {
            
          }
        }

        if (!mcpResponse) {
          throw new Error("No valid response from MCP server");
        }

        if (mcpResponse.error) {
          throw new Error(`MCP error: ${mcpResponse.error.message}`);
        }

        const structuredContent = mcpResponse.result?.structuredContent || null;
        const errorFromStructured =
          structuredContent &&
          typeof structuredContent === "object" &&
          typeof (structuredContent as { error?: unknown }).error === "string"
            ? (structuredContent as { error: string }).error
            : null;
        const contentErrorText = mcpResponse.result?.content?.[0]?.text;
        const toolErrorMessage =
          mcpResponse.result?.isError || errorFromStructured
            ? (errorFromStructured ?? contentErrorText ?? "Tool call failed")
            : null;

        if (toolErrorMessage) {
          throw new Error(toolErrorMessage);
        }

        console.log("[MCP] Returning structuredContent:", structuredContent);
        return structuredContent;
      } catch (error) {
        console.error("MCP tool call failed:", error);
        throw error;
      }
    },
    []
  );

    const getWidgetUrl = useCallback(
    async (query: string = "tee", limit: number = 3) => {
      return callTool("search-products", { query, limit });
    },
    [callTool]
  );

  return {
    ...state,
    callTool: callToolSimple,
    callToolWithWidget: callTool,
    getWidgetUrl,
    mcpServerUrl: MCP_SERVER_URL,
  };
}
