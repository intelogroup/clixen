import { NextRequest } from "next/server";
import { backendUrl, localTokenHeaders } from "@/lib/backend";

export const runtime = "nodejs";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const session = request.cookies.get("g4l_session")?.value ?? "";
  const { searchParams } = new URL(request.url);
  const message = searchParams.get("message") ?? "";
  const chatId = searchParams.get("chat_id") ?? "web_ui";

  if (!message.trim()) {
    return new Response(JSON.stringify({ error: "message is required" }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  }

  const upstreamUrl = new URL(backendUrl("/chat/stream"));
  upstreamUrl.searchParams.set("message", message);
  upstreamUrl.searchParams.set("chat_id", chatId);

  const upstream = await fetch(upstreamUrl.toString(), {
    headers: {
      ...localTokenHeaders(),
      ...(session ? { Cookie: `g4l_session=${session}` } : {}),
    },
    cache: "no-store",
  });

  if (!upstream.ok || !upstream.body) {
    const body = await upstream.text().catch(() => "");
    return new Response(
      JSON.stringify({ error: `backend stream failed (${upstream.status})`, detail: body }),
      { status: upstream.status, headers: { "content-type": "application/json" } }
    );
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
