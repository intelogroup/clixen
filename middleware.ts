import { NextRequest, NextResponse } from "next/server";
import { convexAuthNextjsMiddleware, createRouteMatcher, isAuthenticatedNextjs, nextjsMiddlewareRedirect } from "@convex-dev/auth/nextjs/server";

// Define protected routes
const isProtectedRoute = createRouteMatcher([
  "/dashboard(.*)",
  "/chat(.*)",
  "/billing(.*)",
  "/modals(.*)",
  "/transitions(.*)",
]);

// Define auth routes
const isAuthRoute = createRouteMatcher([
  "/auth/signin",
  "/auth/signup", 
  "/auth/forgot-password",
]);

export default convexAuthNextjsMiddleware((request) => {
  // Allow public routes
  if (!isProtectedRoute(request) && !isAuthRoute(request)) {
    return NextResponse.next();
  }

  // If user is authenticated and trying to access auth pages, redirect to dashboard
  if (isAuthenticatedNextjs() && isAuthRoute(request)) {
    return nextjsMiddlewareRedirect(request, "/dashboard");
  }

  // If user is not authenticated and trying to access protected routes, redirect to signin
  if (!isAuthenticatedNextjs() && isProtectedRoute(request)) {
    return nextjsMiddlewareRedirect(request, "/auth/signin");
  }

  // Allow the request to continue
  return NextResponse.next();
});

export const config = {
  // The following matcher runs middleware on all routes
  // except static assets.
  matcher: ["/((?!.*\\..*|_next).*)", "/", "/(api|trpc)(.*)"],
};
