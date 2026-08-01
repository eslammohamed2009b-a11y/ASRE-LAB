import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ASRE–Lab · Evidence-backed engineering",
  description: "Bounded simulation, scientific validation, reproducible evidence, and human-reviewed engineering decisions."
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
