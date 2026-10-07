import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

// Scene clips are uploaded through here, so pass the raw body through untouched.
async function proxy(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { path } = await params;
  const search = new URL(request.url).search;
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const body = hasBody ? await request.arrayBuffer() : undefined;
  const headers: Record<string, string> = {};
  for (const name of ["content-type", "range", "if-range", "accept"]) {
    const value = request.headers.get(name);
    if (value && (name !== "content-type" || body?.byteLength)) headers[name] = value;
  }
  const upstream = await fetchBackend(`/studio/${path.map(encodeURIComponent).join("/")}${search}`, {
    method: request.method,
    userId: session.user.id,
    extraHeaders: headers,
    body: body?.byteLength ? body : undefined,
    cache: "no-store",
  });
  return createProxyResponse(upstream);
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
