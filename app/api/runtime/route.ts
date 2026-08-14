import { NextRequest, NextResponse } from "next/server";
import { backendUrl, localTokenHeaders } from "@/lib/backend";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const session = request.cookies.get("g4l_session")?.value ?? "";

  const upstream = await fetch(backendUrl("/health/ollama"), {
    headers: {
      ...localTokenHeaders(),
      ...(session ? { Cookie: `g4l_session=${session}` } : {}),
    },
    cache: "no-store",
  });

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, { status: upstream.status });
}
