import { NextRequest, NextResponse } from "next/server";
import { backendUrl, localTokenHeaders } from "@/lib/backend";

export const runtime = "nodejs";

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;
  const session = request.cookies.get("g4l_session")?.value ?? "";
  const { searchParams } = new URL(request.url);
  const action = searchParams.get("action") ?? "";

  const valid = ["pause", "resume", "run-now"];
  if (!valid.includes(action)) {
    return NextResponse.json({ error: `unsupported action: ${action}` }, { status: 400 });
  }

  const upstream = await fetch(backendUrl(`/workflows/${id}/${action}`), {
    method: "POST",
    headers: {
      ...localTokenHeaders(),
      ...(session ? { Cookie: `g4l_session=${session}` } : {}),
    },
    cache: "no-store",
  });

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, { status: upstream.status });
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;
  const session = request.cookies.get("g4l_session")?.value ?? "";

  const upstream = await fetch(backendUrl(`/workflows/${id}`), {
    method: "DELETE",
    headers: {
      ...localTokenHeaders(),
      ...(session ? { Cookie: `g4l_session=${session}` } : {}),
    },
    cache: "no-store",
  });

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, { status: upstream.status });
}
