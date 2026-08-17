

import { NextRequest, NextResponse } from "next/server";


export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PSP_API_URL = process.env.PSP_API_URL || "http://localhost:8001";
const PSP_API_KEY = process.env.PSP_API_KEY;



if (!PSP_API_KEY) {
  console.warn("[PSPProxy] PSP_API_KEY not configured - requests will fail");
}


const FORWARD_HEADERS = ["request-id", "idempotency-key", "api-version", "content-type", "accept"];

function validatePathSegments(segments: string[]): boolean {
  for (const segment of segments) {
    if (segment.includes("..") || segment.includes(":") || segment.startsWith("//")) {
      return false;
    }
  }
  return true;
}

function buildUpstreamUrl(baseUrl: string, segments: string[], searchParams: string): URL {
  const url = new URL(baseUrl);
  
  const basePath = url.pathname.replace(/\/+$/, ""); // Remove trailing slashes
  const segmentPath = segments.length > 0 ? "/" + segments.map(encodeURIComponent).join("/") : "";
  url.pathname = basePath + segmentPath;
  // Preserve query string from original request
  url.search = searchParams;
  return url;
}

/**
 * Forward headers from client request, filtering blocked ones.
 */
function buildUpstreamHeaders(request: NextRequest): Headers {
  const headers = new Headers();

  // Forward allowed headers
  for (const name of FORWARD_HEADERS) {
    const value = request.headers.get(name);
    if (value) {
      headers.set(name, value);
    }
  }

  // Inject server-side auth
  headers.set("Authorization", `Bearer ${PSP_API_KEY}`);

  return headers;
}

async function proxyRequest(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<NextResponse> {
  
  if (!PSP_API_KEY) {
    console.error("[PSPProxy] PSP_API_KEY is not configured");
    return NextResponse.json(
      { error: "Proxy misconfigured: PSP_API_KEY not set" },
      { status: 500 }
    );
  }

  const { path: pathSegments = [] } = await params;

  
  if (!validatePathSegments(pathSegments)) {
    console.error("[PSPProxy] Invalid path segments:", pathSegments);
    return NextResponse.json({ error: "Invalid path" }, { status: 400 });
  }

  const upstreamUrl = buildUpstreamUrl(PSP_API_URL, pathSegments, request.nextUrl.search);

  const upstreamHeaders = buildUpstreamHeaders(request);

  
  let body: ArrayBuffer | null = null;
  if (!["GET", "HEAD"].includes(request.method)) {
    body = await request.arrayBuffer();
  }

  try {
    const response = await fetch(upstreamUrl.toString(), {
      method: request.method,
      headers: upstreamHeaders,
      body: body,
    });

    
    const responseBody = await response.arrayBuffer();
    const responseHeaders = new Headers();

    
    for (const [key, value] of response.headers.entries()) {
      
      if (!["transfer-encoding", "connection", "keep-alive"].includes(key.toLowerCase())) {
        responseHeaders.set(key, value);
      }
    }

    return new NextResponse(responseBody, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("[PSPProxy] Fetch error:", error);
    return NextResponse.json({ error: "Upstream request failed" }, { status: 502 });
  }
}

async function handleOptions(): Promise<NextResponse> {
  return new NextResponse(null, { status: 204 });
}


export const GET = proxyRequest;
export const POST = proxyRequest;
export const PUT = proxyRequest;
export const PATCH = proxyRequest;
export const DELETE = proxyRequest;
export const OPTIONS = handleOptions;
