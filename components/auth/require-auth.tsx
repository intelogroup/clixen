"use client";

import { ReactNode, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuthLoading, useCurrentUser } from "@/lib/auth-context";

/**
 * Client-side auth gate for protected pages.
 *
 * The middleware only checks for the presence of a g4l_session cookie — it
 * cannot validate the HMAC signature (that lives in the backend). So a stale,
 * revoked, or forged cookie still renders the page shell until the real
 * session check completes. This component bounces to /auth/signin as soon as
 * the backend confirms the session is invalid.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const user = useCurrentUser();
  const isLoading = useAuthLoading();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (isLoading || user) return;
    const next = encodeURIComponent(pathname);
    router.replace(`/auth/signin?next=${next}`);
  }, [isLoading, user, pathname, router]);

  if (isLoading || !user) {
    return null;
  }

  return <>{children}</>;
}
