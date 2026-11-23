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
    <nav aria-label="Primary" className="flex min-w-0 items-center gap-0.5 overflow-x-auto [scrollbar-width:none] sm:ml-6 sm:gap-1 [&::-webkit-scrollbar]:hidden">
      {LINKS.map((l) => {
        const on = l.match(path);
        return (
          <Link
            key={l.href}
            href={l.href}
            aria-current={on ? "page" : undefined}
            className={`shrink-0 rounded-md px-2 py-1.5 text-[0.8125rem] font-medium transition-colors sm:px-3 sm:text-sm ${
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
