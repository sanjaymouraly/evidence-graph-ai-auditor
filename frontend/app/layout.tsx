import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { Shell } from "@/components/shell";

export const metadata: Metadata = {
  title: "Evidence-Graph AI Auditor",
  description: "Deterministic, provenance-aware evidence auditing for high-stakes business decisions.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <AuthProvider>
          <Shell>{children}</Shell>
        </AuthProvider>
      </body>
    </html>
  );
}
