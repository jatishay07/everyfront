import type { Metadata } from "next";
import { TopNav } from "@/components/TopNav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Every Front — Command Center",
  description:
    "An agent fleet that finds every legal avenue to reduce or erase a medical bill, and files the forms autonomously.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-ink-950 text-ink-100 antialiased">
        <TopNav />
        <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
