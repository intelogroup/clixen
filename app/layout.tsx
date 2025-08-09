import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ConvexClientProvider } from "./ConvexClientProvider";
import { AuthProvider } from "@/lib/auth-context";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Clixen - AI-Powered Workflow Automation",
  description: "Create powerful automation workflows using natural language. Automate anything with Clixen's AI-powered platform.",
  keywords: ["automation", "AI", "workflow", "no-code", "productivity"],
  authors: [{ name: "Clixen Team" }],
  openGraph: {
    title: "Clixen - AI-Powered Workflow Automation",
    description: "Create powerful automation workflows using natural language",
    type: "website",
    url: "https://clixen.com",
  },
  twitter: {
    card: "summary_large_image",
    title: "Clixen - AI-Powered Workflow Automation",
    description: "Create powerful automation workflows using natural language",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={inter.className} suppressHydrationWarning>
        <ConvexClientProvider>
          <AuthProvider>
            {children}
          </AuthProvider>
        </ConvexClientProvider>
      </body>
    </html>
  );
}
