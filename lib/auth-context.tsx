"use client";

import { createContext, useContext, ReactNode, useState, useEffect, useCallback } from "react";

interface User {
  id: string;
  email: string;
  firstName?: string;
  lastName?: string;
  displayName?: string;
  workspaceName?: string;
  role?: string;
  avatar?: string;
}

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  signIn: (provider: string, options?: any) => Promise<void>;
  signOut: () => Promise<void>;
  resetPassword: (email: string, password: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

function splitDisplayName(displayName: string): { firstName?: string; lastName?: string } {
  const parts = displayName.trim().split(/\s+/);
  return {
    firstName: parts[0] || undefined,
    lastName: parts.length > 1 ? parts.slice(1).join(" ") : undefined,
  };
}

function mapUser(backendUser: any): User | null {
  if (!backendUser) return null;
  const { firstName, lastName } = splitDisplayName(backendUser.display_name ?? "");
  return {
    id: backendUser.email,
    email: backendUser.email ?? "",
    firstName,
    lastName,
    displayName: backendUser.display_name,
    workspaceName: backendUser.workspace_name,
    role: backendUser.role,
    avatar: backendUser.avatar,
  };
}

async function apiFetch(path: string, init?: RequestInit): Promise<any> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.message || `Request failed (${res.status})`);
  }
  return data;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await apiFetch("/api/auth/session");
      setUser(data.authenticated ? mapUser(data.user) : null);
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setIsLoading(false));
  }, [refresh]);

  const signIn = async (provider: string, options?: any) => {
    if (provider !== "password") {
      throw new Error("Social sign-in is not available locally — use email and password");
    }
    setIsLoading(true);
    try {
      if (options?.flow === "signUp") {
        const data = await apiFetch("/api/auth/register", {
          method: "POST",
          body: JSON.stringify({
            display_name: [options.firstName, options.lastName].filter(Boolean).join(" ").trim() || "Local User",
            email: options.email,
            password: options.password,
          }),
        });
        setUser(data.user ? mapUser(data.user) : { id: options.email, email: options.email, firstName: options.firstName, lastName: options.lastName });
      } else {
        const data = await apiFetch("/api/auth/login", {
          method: "POST",
          body: JSON.stringify({ email: options.email, password: options.password }),
        });
        setUser(data.user ? mapUser(data.user) : { id: options.email, email: options.email });
      }
    } catch (error) {
      setUser(null);
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const signOut = async () => {
    await apiFetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    setUser(null);
  };

  const resetPassword = async (email: string, password: string) => {
    await apiFetch("/api/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setUser({ id: email, email });
  };

  return (
    <AuthContext.Provider value={{ user, isLoading, signIn, signOut, resetPassword, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuthActions() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuthActions must be used within an AuthProvider");
  }
  return { signIn: context.signIn, signOut: context.signOut, resetPassword: context.resetPassword };
}

export function useCurrentUser() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useCurrentUser must be used within an AuthProvider");
  }
  return context.user;
}

export function useAuthLoading() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuthLoading must be used within an AuthProvider");
  }
  return context.isLoading;
}
