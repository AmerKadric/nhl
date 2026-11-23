import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { StaleBanner } from "@/components/Chrome";
import { GamesBoard } from "@/components/GamesBoard";
import { Recap } from "@/components/Recap";
import { PageSkeleton, Unavailable } from "@/components/ui";
import { apiCached } from "@/lib/api";
import { addDays, dayLabel, isDate, todayET } from "@/lib/format";
import type { GamesResponse, Meta } from "@/lib/types";

const VIEWS = [
  { key: "today", label: "Today" },
  { key: "tomorrow", label: "Tomorrow" },
  { key: "week", label: "This week" },
  { key: "all", label: "All games" },
  { key: "completed", label: "Completed" },
] as const;

type View = (typeof VIEWS)[number]["key"] | "date";

export default function Home({ searchParams }: PageProps<"/">) {
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-2">
        <div>
          <h1 className="display text-[2rem] font-bold leading-none tracking-wide sm:text-[2.4rem]">
            Know the numbers. <span className="text-ice">Before the puck drops.</span>
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-ink-2">
            Projected final scores for every NHL game, from a statistical model whose{" "}
            <Link href="/accuracy" className="text-ice hover:underline">
              full track record
            </Link>{" "}
            is public.
          </p>
        </div>
        <Suspense fallback={null}>
          <ModelLine />
        </Suspense>
      </div>
      <Suspense fallback={<div className="mt-6"><PageSkeleton /></div>}>
        <Slate searchParams={searchParams} />
      </Suspense>
    </>
  );
}

async function ModelLine() {
  let meta: Meta | null = null;
  await connection();
  try {
    meta = await apiCached<Meta>("/v1/meta");
  } catch {
    return null;
  }
  if (!meta?.model) return null;
  return (
    <div className="num text-right text-[0.7rem] leading-relaxed text-mute">
      <div>
        model <span className="text-ink-2">{meta.model.version}</span>
      </div>
      <div>
        {meta.model.n_sims.toLocaleString("en-US")} simulations per game · results through {meta.freshness.results_through}
      </div>
    </div>
  );
}

async function Slate({ searchParams }: { searchParams: PageProps<"/">["searchParams"] }) {
  const sp = await searchParams;
  await connection(); // the slate depends on the current date, so render at request time
  const today = todayET();
  const d = typeof sp.d === "string" && isDate(sp.d) ? sp.d : null;
  const raw = typeof sp.view === "string" ? sp.view : "today";
  const view: View = d ? "date" : VIEWS.some((v) => v.key === raw) ? (raw as View) : "today";

  let start = today;
  let end = today;
  if (view === "tomorrow") start = end = addDays(today, 1);
  else if (view === "week") end = addDays(today, 6);
  else if (view === "all") [start, end] = [addDays(today, -7), addDays(today, 14)];
  else if (view === "completed") [start, end] = [addDays(today, -14), today];
  else if (view === "date" && d) start = end = d;

  const data = await apiCached<GamesResponse>(`/v1/games?start=${start}&end=${end}`, "live");
  let games = data?.games ?? [];
  // Past games PUCKIQ never forecast (before launch) are left out of the
  // browsing views; they are in the backtest history on the Accuracy page.
  const total = games.length;
  if (view === "completed") games = games.filter((g) => g.result && g.forecast).reverse();
  else if (view === "all") games = games.filter((g) => !g.result || g.forecast);
  const hidden = view === "completed" ? data?.games.filter((g) => g.result && !g.forecast).length ?? 0 : total - games.length;

  // The most recent slate that has settled forecasts, for the recap.
  let recap: { date: string; games: GamesResponse["games"] } | null = null;
  if (view === "today") {
    const past = await apiCached<GamesResponse>(`/v1/games?start=${addDays(today, -5)}&end=${addDays(today, -1)}`);
    const settled = (past?.games ?? []).filter((g) => g.result && g.forecast && g.evaluation);
    const last = settled.map((g) => g.date).sort().pop();
    if (last) recap = { date: last, games: settled.filter((g) => g.date === last) };
  }

  // On an off day, point at the next slate instead of leaving the page empty.
  let next: GamesResponse | null = null;
  let nextDate: string | null = null;
  if (games.length === 0 && (view === "today" || view === "tomorrow")) {
    const ahead = await apiCached<GamesResponse>(`/v1/games?start=${addDays(start, 1)}&end=${addDays(start, 10)}`, "live");
    nextDate = ahead?.games[0]?.date ?? null;
    if (ahead && nextDate) next = { ...ahead, games: ahead.games.filter((g) => g.date === nextDate) };
  }

  const title = view === "today" ? dayLabel(today) : view === "tomorrow" ? dayLabel(start) : view === "date" ? dayLabel(start) : VIEWS.find((v) => v.key === view)!.label;

  return (
    <div className="mt-6">
      <StaleBanner />
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <nav aria-label="Game filters" className="flex flex-wrap gap-1 rounded-xl border border-line bg-surface p-1">
          {VIEWS.map((v) => (
            <Link
              key={v.key}
              href={v.key === "today" ? "/" : `/?view=${v.key}`}
              aria-current={view === v.key ? "page" : undefined}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${view === v.key ? "bg-raised text-ink shadow-[inset_0_-2px_0_var(--color-ice)]" : "text-ink-2 hover:text-ink"}`}
            >
              {v.label}
            </Link>
          ))}
        </nav>
        <form action="/" className="flex items-center gap-2">
          <label htmlFor="d" className="sr-only">
            Pick a date
          </label>
          <input
            id="d"
            name="d"
            type="date"
            defaultValue={d ?? ""}
            className={`num h-[38px] rounded-xl border bg-surface px-3 text-sm text-ink [color-scheme:dark] ${view === "date" ? "border-ice/60" : "border-line"}`}
          />
          <button type="submit" className="h-[38px] rounded-xl border border-line bg-surface px-3 text-sm font-medium text-ink-2 transition-colors hover:border-ice/40 hover:text-ink active:scale-95">
            Go
          </button>
        </form>
        <div className="ml-auto flex items-baseline gap-3">
          <span className="display text-lg font-semibold tracking-wide">{title}</span>
          <span className="num text-xs text-mute">
            {games.length} {games.length === 1 ? "game" : "games"}
          </span>
        </div>
      </div>

      {games.length > 0 ? (
        <GamesBoard initial={games} start={start} end={end} today={today} />
      ) : (
        <Unavailable title={view === "completed" ? "No completed games in the last two weeks." : `No NHL games ${view === "today" ? "today" : view === "tomorrow" ? "tomorrow" : "on this date"}.`}>
          {view === "date" ? "Pick another date, or view this week." : next ? "The next slate is shown below." : "Nothing is scheduled in the next ten days."}
        </Unavailable>
      )}

      {hidden > 0 && (view === "completed" || view === "all") && (
        <p className="mt-2 text-xs text-mute">
          {hidden} earlier {hidden === 1 ? "game is" : "games are"} not shown because PUCKIQ had not started publishing forecasts.{" "}
          <Link href="/accuracy?source=backtest#history" className="text-ice hover:underline">
            See the backtest history
          </Link>
          .
        </p>
      )}

      {next && nextDate && (
        <div className="mt-8">
          <h2 className="mb-3 flex items-baseline gap-3">
            <span className="eyebrow text-ice">Next slate</span>
            <span className="display text-xl font-semibold tracking-wide">{dayLabel(nextDate)}</span>
            <span className="num text-xs text-mute">{next.games.length} games</span>
          </h2>
          <GamesBoard initial={next.games} start={nextDate} end={nextDate} today={today} />
        </div>
      )}
      {recap && <Recap date={recap.date} games={recap.games} />}
      <p className="mt-8 text-xs text-mute">Slates are grouped by US Eastern date; puck-drop times are shown in your local time zone.</p>
    </div>
  );
}
