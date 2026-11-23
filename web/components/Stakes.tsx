import Link from "next/link";

import { addDays, dayLabel, odds } from "@/lib/format";
import type { LeverageCheck, SeasonOutlook, SeasonTeam, StakeGame, StakeSide, Stakes, TeamBrief } from "@/lib/types";

import { Chip, Section, TeamLogo } from "./ui";

/** A swing is reported only when it is clearly larger than the simulation's own noise. */
const isReal = (s: StakeSide) => Math.abs(s.swing) >= 2 * s.se && Math.abs(s.swing) >= 0.005;
const points = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(Math.abs(v) < 0.0995 ? 1 : 0)} pts`;

function StakeRow({ s, tone }: { s: StakeSide; tone: "home" | "away" }) {
  const lo = Math.min(s.if_loss, s.if_win), hi = Math.max(s.if_loss, s.if_win);
  const real = isReal(s);
  const name = s.team?.name ?? String(s.team_id);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_3.4rem_3.4rem_5.2rem] items-center gap-x-3 py-1.5 sm:grid-cols-[minmax(0,13rem)_3.4rem_minmax(5rem,1fr)_3.4rem_5.2rem]">
      <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-ink">
        {s.team && <TeamLogo team={s.team} size={22} />}
        {s.team ? (
          <Link href={`/teams/${s.team.abbrev}`} className="truncate hover:text-ice">
            <span className="sm:hidden">{s.team.abbrev}</span>
            <span className="hidden sm:inline">{name}</span>
          </Link>
        ) : (
          name
        )}
      </span>
      <span className="num text-right text-sm text-ink-2">{odds(s.if_loss)}</span>
      <span
        className="relative hidden h-2 rounded-sm bg-raised sm:block"
        role="img"
        aria-label={`${name}: ${odds(s.if_loss)} to make the playoffs with a loss, ${odds(s.if_win)} with a win, ${odds(s.now)} now`}
      >
        <span className={`absolute inset-y-0 rounded-sm ${tone === "home" ? "bg-home" : "bg-away"}`} style={{ left: `${lo * 100}%`, width: `max(3px, ${(hi - lo) * 100}%)` }} />
        <span className="absolute -inset-y-1 w-0.5 -translate-x-1/2 bg-ink" style={{ left: `${s.now * 100}%` }} />
      </span>
      <span className="num text-right text-sm text-ink">{odds(s.if_win)}</span>
      <span className={`num text-right text-sm ${real ? "font-semibold text-ink" : "text-mute"}`}>{real ? points(s.swing) : "no clear effect"}</span>
    </div>
  );
}

function Legend() {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_3.4rem_3.4rem_5.2rem] gap-x-3 border-b border-line-soft pb-1.5 text-[0.65rem] font-semibold uppercase tracking-wider text-mute sm:grid-cols-[minmax(0,13rem)_3.4rem_minmax(5rem,1fr)_3.4rem_5.2rem]">
      <span>Make the playoffs</span>
      <span className="text-right">With a loss</span>
      <span className="hidden sm:block">0% to 100% · tick = today</span>
      <span className="text-right">With a win</span>
      <span className="text-right">Swing</span>
    </div>
  );
}

function StakeCard({ g, top }: { g: StakeGame; top?: boolean }) {
  return (
    <div className="panel px-4 py-3">
      <div className="mb-1.5 flex items-center gap-3 text-xs text-ink-2">
        <span className="font-medium text-ink">
          {g.away.team?.abbrev ?? g.away.team_id} at {g.home.team?.abbrev ?? g.home.team_id}
        </span>
        {top && <Chip tone="ice">Most at stake</Chip>}
        {g.game_id != null && (
          <Link href={`/game/${g.game_id}`} className="ml-auto text-ice hover:underline">
            Forecast
          </Link>
        )}
      </div>
      <Legend />
      <StakeRow s={g.away} tone="away" />
      <StakeRow s={g.home} tone="home" />
    </div>
  );
}

const weight = (g: StakeGame) => (isReal(g.home) ? Math.abs(g.home.swing) : 0) + (isReal(g.away) ? Math.abs(g.away.swing) : 0);

/** Season page: every game on the next slate, ordered by how much rides on it. */
export function StakesSection({ stakes, check }: { stakes: Stakes; check?: LeverageCheck | null }) {
  const games = [...stakes.games].sort((a, b) => weight(b) - weight(a));
  if (games.length === 0) return null;
  const sc = check && check.n > 0 ? check[stakes.method ?? "raw"] : undefined;
  const ratio = sc && check?.mean_actual_winners != null && check.mean_actual_losers != null
    ? (sc.mean_predicted_winners - sc.mean_predicted_losers) / (check.mean_actual_winners - check.mean_actual_losers)
    : null;
  return (
    <Section
      id="stakes"
      eyebrow="Playoff race"
      title={`What ${dayLabel(stakes.date)}'s games mean`}
      note="Each team's chance of making the playoffs if it wins this game, against its chance if it loses, from the same simulated seasons as the odds below. Splitting the simulation in two leaves up to a point of noise, so smaller swings are not reported."
    >
      <div className="grid gap-3 lg:grid-cols-2">
        {games.map((g, i) => (
          <StakeCard key={g.game_id ?? i} g={g} top={i === 0 && weight(g) >= 0.06} />
        ))}
      </div>
      {check && sc && check.mean_actual_winners != null && check.mean_actual_losers != null ? (
        <p className="mt-3 max-w-4xl text-xs text-mute">
          Checked against this season so far ({check.n} team-games, reconstructed after the fact): teams that won saw their playoff odds move {points(check.mean_actual_winners)} by
          the next morning on average, where these figures had said {points(sc.mean_predicted_winners)}; teams that lost moved {points(check.mean_actual_losers)}, against{" "}
          {points(sc.mean_predicted_losers)} predicted.
          {ratio != null && ratio > 1.08 && ` So far the swings shown have run about ${Math.round((ratio - 1) * 100)}% larger than what happened.`}
          {ratio != null && ratio < 0.92 && ` So far the swings shown have run about ${Math.round((1 - ratio) * 100)}% smaller than what happened.`} Other results the same
          night move a team&apos;s odds too, so a single game can differ a lot from the figure shown.
        </p>
      ) : (
        <p className="mt-3 text-xs text-mute">These figures have not yet been checked against how odds actually moved after past games.</p>
      )}
    </Section>
  );
}

/** Game page: the same two rows for one game. */
export function GameStakes({ g }: { g: StakeGame }) {
  return (
    <div className="panel px-4 py-3">
      <Legend />
      <StakeRow s={g.away} tone="away" />
      <StakeRow s={g.home} tone="home" />
    </div>
  );
}

const SW = 132, SH = 30, PAD = 3;

/** Playoff odds over the season, one small line per team. Fixed 0–100% scale so rows compare. */
export function Sparkline({ dates, values, label }: { dates: string[]; values: (number | null)[]; label: string }) {
  const pts = dates.map((d, i) => ({ t: Date.parse(d), v: values[i] })).filter((p): p is { t: number; v: number } => p.v != null);
  if (pts.length < 2) return <span className="block h-[30px] w-[132px]" />;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const x = (t: number) => PAD + ((t - t0) / (t1 - t0 || 1)) * (SW - 2 * PAD - 3);
  const y = (v: number) => SH - PAD - v * (SH - 2 * PAD);
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${SW} ${SH}`} width={SW} height={SH} role="img" aria-label={label} className="shrink-0">
      <line x1={PAD} x2={SW - PAD} y1={y(0.5)} y2={y(0.5)} stroke="var(--color-line)" strokeDasharray="2 3" />
      <polyline points={pts.map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ")} fill="none" stroke="var(--color-home)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last.t)} cy={y(last.v)} r="3" fill="var(--color-home)" stroke="var(--color-surface)" strokeWidth="1.5" />
    </svg>
  );
}

type Mover = { team: TeamBrief; now: number; change: number; values: (number | null)[] };

function MoverList({ title, rows, dates, since }: { title: string; rows: Mover[]; dates: string[]; since: string }) {
  return (
    <div className="panel p-4">
      <div className="eyebrow mb-2">{title}</div>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-2">No team moved by a point or more.</p>
      ) : (
        <ul className="divide-y divide-line-soft">
          {rows.map((m) => (
            <li key={m.team.id} className="flex items-center gap-3 py-2">
              <Link href={`/teams/${m.team.abbrev}`} className="flex min-w-0 flex-1 items-center gap-2.5 text-sm font-medium text-ink hover:text-ice">
                <TeamLogo team={m.team} size={22} />
                <span className="truncate">{m.team.name}</span>
              </Link>
              <span className="hidden sm:block">
                <Sparkline dates={dates} values={m.values} label={`${m.team.name} playoff odds this season, now ${odds(m.now)}`} />
              </span>
              <span className="num w-14 text-right text-sm text-ink">{odds(m.now)}</span>
              <span className="num w-[5.2rem] text-right text-sm font-semibold text-ink" title={`Change since ${since}`}>
                <span aria-hidden className="mr-1 text-[0.6rem] text-ink-2">{m.change >= 0 ? "▲" : "▼"}</span>
                {points(m.change)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Season page: the teams whose playoff odds moved most over the last week. */
export function MoversSection({ history, teams }: { history: NonNullable<SeasonOutlook["history"]>; teams: SeasonTeam[] }) {
  const dates = history.dates;
  const last = dates.length - 1;
  if (last < 1) return null;
  const target = addDays(dates[last], -7);
  let ref = 0;
  for (let i = 0; i < last; i++) if (dates[i] <= target) ref = i;
  const movers: Mover[] = [];
  for (const t of teams) {
    const h = history.teams[String(t.team_id)];
    const a = h?.p_playoffs[ref], b = h?.p_playoffs[last];
    if (!t.team || a == null || b == null) continue;
    movers.push({ team: t.team, now: b, change: b - a, values: h.p_playoffs });
  }
  const up = movers.filter((m) => m.change >= 0.01).sort((a, b) => b.change - a.change).slice(0, 5);
  const down = movers.filter((m) => m.change <= -0.01).sort((a, b) => a.change - b.change).slice(0, 5);
  const cut = history.reconstructed_through;
  const firstPublished = cut ? dates.find((d) => d > cut) : dates[0];
  return (
    <Section
      id="movers"
      eyebrow="Playoff odds"
      title={`Biggest movers since ${dayLabel(dates[ref], "short")}`}
      note={
        cut
          ? `The line beside each team is its playoff odds since opening night. Odds before ${firstPublished ? dayLabel(firstPublished, "short") : "the first published day"} were not on this site at the time: they were reconstructed afterwards by re-running the simulation with only what was known each morning. From then on they are the odds published here each day.`
          : "The line beside each team is its playoff odds on each day this season, as published here."
      }
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <MoverList title="Rising" rows={up} dates={dates} since={dates[ref]} />
        <MoverList title="Falling" rows={down} dates={dates} since={dates[ref]} />
      </div>
    </Section>
  );
}
