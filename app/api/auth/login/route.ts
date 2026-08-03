import { NextRequest, NextResponse } from "next/server";
import { backendUrl } from "@/lib/backend";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({}));
  const token = request.cookies.get("g4l_session")?.value ?? "";

  const upstream = await fetch(backendUrl("/api/auth/login"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Cookie: `g4l_session=${token}` } : {}),
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  const data = await upstream.json().catch(() => ({}));
  const setCookie = upstream.headers.get("set-cookie");

  const response = NextResponse.json(data, { status: upstream.status });
  if (setCookie) response.headers.set("set-cookie", setCookie);
  return response;
}
