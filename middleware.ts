import { NextRequest, NextResponse } from "next/server";

// Define protected routes
const protectedRoutes = [
  "/dashboard",
  "/chat",
  "/billing",
  "/modals",
  "/transitions",
];

// Define auth routes
const authRoutes = [
  "/auth/signin",
  "/auth/signup",
  "/auth/forgot-password",
];

function isProtectedRoute(pathname: string): boolean {
  return protectedRoutes.some(route => pathname.startsWith(route));
}

function isAuthRoute(pathname: string): boolean {
  return authRoutes.some(route => pathname.startsWith(route));
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow public routes (home page, etc.)
  if (!isProtectedRoute(pathname) && !isAuthRoute(pathname)) {
    return NextResponse.next();
  }

  // For now, allow all routes to work while we fix auth
  // TODO: Implement proper authentication check
  return NextResponse.next();
}

export const config = {
  // The following matcher runs middleware on all routes
  // except static assets.
  matcher: ["/((?!.*\\..*|_next).*)", "/", "/(api|trpc)(.*)"],
};
