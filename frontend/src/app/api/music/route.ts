import { createProxyResponse, fetchBackend } from "@/server/backend-api";

export async function GET() {
  return createProxyResponse(await fetchBackend("/music", { cache: "no-store" }));
}
