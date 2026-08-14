import { NextRequest, NextResponse } from "next/server";
import { backendUrl, localTokenHeaders } from "@/lib/backend";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const session = request.cookies.get("g4l_session")?.value ?? "";
  const upstreamUrl = new URL(backendUrl("/api/documents"));
  const chatId = request.nextUrl.searchParams.get("chat_id") ?? "web_ui";
  upstreamUrl.searchParams.set("chat_id", chatId);

  const upstream = await fetch(upstreamUrl, {
    headers: {
      ...localTokenHeaders(),
      ...(session ? { Cookie: `g4l_session=${session}` } : {}),
    },
    cache: "no-store",
  });
  const data = await upstream.json().catch(() => ({ error: "Unable to list documents" }));
  return NextResponse.json(data, { status: upstream.status });
}
