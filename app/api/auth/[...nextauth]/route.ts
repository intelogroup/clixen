// Temporarily commented out for demo - auth integration pending
// import { convexAuth } from "@convex-dev/auth/nextjs/server";

// export const { GET, POST } = convexAuth;

// Placeholder API routes for demo
export function GET() {
  return Response.json({ message: 'Auth GET endpoint - demo mode' });
}

export function POST() {
  return Response.json({ message: 'Auth POST endpoint - demo mode' });
}
