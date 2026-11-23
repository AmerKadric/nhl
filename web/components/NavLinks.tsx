"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Games", match: (p: string) => p === "/" || p.startsWith("/game") },
  { href: "/season", label: "Season", match: (p: string) => p.startsWith("/season") },
  { href: "/teams", label: "Teams", match: (p: string) => p.startsWith("/teams") },
  { href: "/accuracy", label: "Accuracy", match: (p: string) => p.startsWith("/accuracy") },
  { href: "/methodology", label: "Method", match: (p: string) => p.startsWith("/methodology") },
];

export function NavLinks() {
  const path = usePathname() ?? "/";
  return (
    <nav aria-label="Primary" className="ml-2 flex items-center gap-0.5 sm:ml-6 sm:gap-1">
      {LINKS.map((l) => {
        const on = l.match(path);
        return (
          <Link
            key={l.href}
            href={l.href}
            aria-current={on ? "page" : undefined}
            className={`rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors sm:px-3 ${
              on ? "bg-raised text-ink" : "text-ink-2 hover:bg-surface hover:text-ink"
            }`}
          >
            {l.label}
          </Link>
        );
      })}
    </nav>
  );
}
