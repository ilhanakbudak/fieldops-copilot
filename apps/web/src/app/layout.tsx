import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { SessionProvider } from "@/lib/session";
import "./globals.css";

export const metadata: Metadata = {
  title: "FieldOps Copilot",
  description: "Private AI assistant for service businesses.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Not `maximum-scale=1`: blocking zoom is a common way to make a layout look
  // tidy and an equally common accessibility failure.
  themeColor: "#ffffff",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
