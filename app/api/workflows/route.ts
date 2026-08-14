import { NextRequest, NextResponse } from "next/server";
import { backendUrl, localTokenHeaders } from "@/lib/backend";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const session = request.cookies.get("g4l_session")?.value ?? "";
  const { searchParams } = new URL(request.url);
  const status = searchParams.get("status") ?? "";
  const limit = searchParams.get("limit") ?? "100";

  const upstream = await fetch(
    backendUrl(`/workflows?status=${encodeURIComponent(status)}&limit=${encodeURIComponent(limit)}`),
    {
      headers: {
        ...localTokenHeaders(),
        ...(session ? { Cookie: `g4l_session=${session}` } : {}),
      },
      cache: "no-store",
    }
  );

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, { status: upstream.status });
}
