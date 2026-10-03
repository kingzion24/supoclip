import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { path } = await params;
  const search = new URL(request.url).search;
  const upstream = await fetchBackend(`/discover/${path.join("/")}${search}`, {
    method: "GET",
    userId: session.user.id,
    cache: "no-store",
  });
  return createProxyResponse(upstream);
}
