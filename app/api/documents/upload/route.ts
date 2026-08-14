import { NextRequest, NextResponse } from "next/server";
import { backendUrl, localTokenHeaders } from "@/lib/backend";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const session = request.cookies.get("g4l_session")?.value ?? "";
  const body = await request.formData();
  const upstream = await fetch(backendUrl("/upload"), {
    method: "POST",
    headers: {
      ...localTokenHeaders(),
      ...(session ? { Cookie: `g4l_session=${session}` } : {}),
    },
    body,
  });
  const data = await upstream.json().catch(() => ({ error: "Upload failed" }));
  return NextResponse.json(data, { status: upstream.status });
}
