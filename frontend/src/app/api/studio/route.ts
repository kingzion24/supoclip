import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

async function proxy(request: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const body = request.method === "GET" ? undefined : await request.text();
  const upstream = await fetchBackend("/studio/", {
    method: request.method,
    userId: session.user.id,
    extraHeaders: body ? { "Content-Type": "application/json" } : {},
    body,
    cache: "no-store",
  });
  return createProxyResponse(upstream);
}

export const GET = proxy;
export const POST = proxy;
