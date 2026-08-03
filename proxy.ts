import { NextRequest, NextResponse } from "next/server";

// Protected routes require a valid local session cookie (set by the backend
// proxy after a successful /api/auth/login or /api/auth/register).
const protectedRoutes = [
  "/dashboard",
  "/chat",
  "/billing",
  "/modals",
  "/transitions",
];

const authRoutes = [
  "/auth/signin",
  "/auth/signup",
  "/auth/forgot-password",
];

function isProtectedRoute(pathname: string): boolean {
  return protectedRoutes.some((route) => pathname.startsWith(route));
}

function isAuthRoute(pathname: string): boolean {
  return authRoutes.some((route) => pathname.startsWith(route));
}

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = Boolean(request.cookies.get("g4l_session")?.value);

  // Public routes pass through.
  if (!isProtectedRoute(pathname) && !isAuthRoute(pathname)) {
    return NextResponse.next();
  }

  // Auth pages pass through. We do NOT redirect an already-logged-in user to
  // the app here: a cookie's presence proves nothing about its validity (the
  // backend HMAC-validates it), so a stale/forged cookie would bounce auth
  // pages to /dashboard in an infinite loop with the client-side RequireAuth
  // guard. "Already authenticated" is decided by the client's real session
  // check instead.
  if (isAuthRoute(pathname)) {
    return NextResponse.next();
  }

  // Protected routes: require a session cookie.
  if (!hasSession) {
    const url = new URL("/auth/signin", request.url);
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!.*\\..*|_next).*)", "/", "/(api|trpc)(.*)"],
};
