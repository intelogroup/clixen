import { NextRequest, NextResponse } from "next/server";
import { backendUrl, localTokenHeaders } from "@/lib/backend";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const session = request.cookies.get("g4l_session")?.value ?? "";
  const upstream = await fetch(backendUrl("/api/documents/restore"), {
    method: "POST",
    headers: {
      ...localTokenHeaders(),
      "Content-Type": "application/json",
      ...(session ? { Cookie: `g4l_session=${session}` } : {}),
    },
    body: await request.text(),
  });
  const data = await upstream.json().catch(() => ({ error: "Unable to request restore" }));
  return NextResponse.json(data, { status: upstream.status });
}
