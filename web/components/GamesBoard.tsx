"use client";

import { useEffect, useRef, useState } from "react";

import { dayLabel } from "@/lib/format";
import type { Game, GamesResponse } from "@/lib/types";

import { GameCard } from "./GameCard";

const DONE = new Set(["OFF", "FINAL"]);

/** Renders the slate and keeps live games current. Polling only runs while
 * a game on screen is live or about to start, and pauses in hidden tabs. */
export function GamesBoard({ initial, start, end, today }: { initial: Game[]; start: string; end: string; today: string }) {
  const [games, setGames] = useState(initial);
  const [failed, setFailed] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => setGames(initial), [initial]);

  const active = games.some((g) => {
    if (DONE.has(g.state) || g.schedule_state !== "OK" || !g.start_time_utc) return false;
    const mins = (new Date(g.start_time_utc).getTime() - Date.now()) / 60000;
    return g.state === "LIVE" || g.state === "CRIT" || g.state === "PRE" || (mins < 45 && mins > -300);
  });

  useEffect(() => {
    if (!active) return;
    const tick = async () => {
      if (document.hidden) return;
      try {
        const r = await fetch(`/api/puckiq/v1/games?start=${start}&end=${end}`, { cache: "no-store" });
        if (!r.ok) throw new Error(String(r.status));
        const d = (await r.json()) as GamesResponse;
        setGames(d.games);
        setFailed(false);
      } catch {
        setFailed(true);
      }
    };
    timer.current = setInterval(tick, 30000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [active, start, end]);

  const byDate = new Map<string, Game[]>();
  for (const g of games) byDate.set(g.date, [...(byDate.get(g.date) ?? []), g]);
  const dates = [...byDate.keys()].sort();
  const many = dates.length > 1;

  return (
    <div>
      {failed && (
        <p role="status" className="mb-3 text-xs text-warn">
          Live updates are temporarily unavailable. Scores shown may be behind.
        </p>
      )}
      {dates.map((d) => (
        <section key={d} className="mb-8" aria-label={dayLabel(d)}>
          {many && (
            <h2 className="mb-3 flex items-baseline gap-3">
              <span className="display text-xl font-semibold tracking-wide">{dayLabel(d)}</span>
              {d === today && <span className="eyebrow text-ice">Today</span>}
              <span className="num text-xs text-mute">{byDate.get(d)!.length} games</span>
            </h2>
          )}
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {byDate.get(d)!.map((g) => (
              <GameCard key={g.id} g={g} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
