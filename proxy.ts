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

  // Auth pages: an already-logged-in user should go to the app.
  if (isAuthRoute(pathname)) {
    if (hasSession) {
      return NextResponse.redirect(new URL("/dashboard", request.url));
    }
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
