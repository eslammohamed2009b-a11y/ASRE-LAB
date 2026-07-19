import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ASRE-LAB",
  description: "ASRE-LAB engineering simulation platform"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
