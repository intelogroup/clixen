"use client";

import { createContext, useContext, ReactNode, useState } from 'react';

interface User {
  id: string;
  email: string;
  firstName?: string;
  lastName?: string;
}

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  signIn: (provider: string, options?: any) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const signIn = async (provider: string, options?: any) => {
    setIsLoading(true);
    try {
      // Temporary mock authentication
      console.log('Sign in with:', provider, options);

      // Simulate API call delay
      await new Promise(resolve => setTimeout(resolve, 1000));

      // Mock successful authentication
      if (provider === 'password') {
        if (options?.flow === 'signUp') {
          // Mock signup success
          setUser({
            id: '2',
            email: options.email,
            firstName: options.firstName || 'New',
            lastName: options.lastName || 'User'
          });
        } else if (options?.email === 'demo@clixen.com') {
          // Mock signin success
          setUser({
            id: '1',
            email: 'demo@clixen.com',
            firstName: 'Demo',
            lastName: 'User'
          });
        } else {
          throw new Error('Invalid credentials');
        }
      } else {
        // Mock social auth success
        setUser({
          id: '3',
          email: `user@${provider}.com`,
          firstName: 'Social',
          lastName: 'User'
        });
      }
    } catch (error) {
      console.error('Auth error:', error);
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const signOut = async () => {
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, isLoading, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuthActions() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuthActions must be used within an AuthProvider');
  }
  return { signIn: context.signIn, signOut: context.signOut };
}

export function useCurrentUser() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useCurrentUser must be used within an AuthProvider');
  }
  return context.user;
}

export function useAuthLoading() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuthLoading must be used within an AuthProvider');
  }
  return context.isLoading;
}
