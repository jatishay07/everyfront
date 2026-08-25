"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { USING_MOCK } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Command Center" },
  { href: "/activity", label: "Live Activity" },
  { href: "/intake", label: "Intake" },
];

export function TopNav() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-ink-800 bg-ink-950/85 backdrop-blur supports-[backdrop-filter]:bg-ink-950/70">
      <div className="mx-auto flex max-w-7xl items-center gap-8 px-6 py-3">
        <Link href="/" className="flex items-center gap-2 shrink-0">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-signal-blue/15 text-signal-blue">
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
              <path
                d="M12 2 3 6.5v5.7C3 17.4 6.8 21.7 12 23c5.2-1.3 9-5.6 9-10.8V6.5L12 2Z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinejoin="round"
              />
              <path d="M8.5 12.2l2.4 2.4 4.6-4.9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <span className="text-sm font-semibold tracking-tight text-ink-100">
            Every Front
          </span>
        </Link>

        <nav className="flex items-center gap-1">
          {LINKS.map((link) => {
            const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                  active
                    ? "bg-ink-800 text-ink-100"
                    : "text-ink-400 hover:bg-ink-900 hover:text-ink-200"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          {USING_MOCK && (
            <span className="hidden rounded-full border border-signal-amber/30 bg-signal-amber/10 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-signal-amber sm:inline-block">
              Mock data layer
            </span>
          )}
          <span className="flex items-center gap-1.5 rounded-full border border-ink-700 bg-ink-900 px-2.5 py-1 text-[11px] font-medium text-ink-300">
            <span className="h-1.5 w-1.5 animate-pulseDot rounded-full bg-signal-green" />
            Live
          </span>
        </div>
      </div>
    </header>
  );
}
