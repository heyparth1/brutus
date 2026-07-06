import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Brutus",
  description: "Browse and pick open-source issues to solve",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
