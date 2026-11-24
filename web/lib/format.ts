const ET = "America/New_York";

export const pct = (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
export const fx = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toFixed(d));
export const signed = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}`);

/** Simulated odds. The extremes are shown as bounds, because a simulation
 * can only say an event was very rare or very common, not that it is settled. */
export const odds = (p: number | null | undefined) =>
  p == null ? "—" : p < 0.001 ? "<0.1%" : p > 0.999 ? ">99.9%" : p < 0.1 || p > 0.9 ? `${(p * 100).toFixed(1)}%` : `${(p * 100).toFixed(0)}%`;

/** "about 1 in 12" for small probabilities. */
export const oneIn = (p: number) => (p > 0 ? `about 1 in ${Math.max(1, Math.round(1 / p))}` : "—");

export function timeET(iso: string | null | undefined): string {
  if (!iso) return "TBD";
  return new Intl.DateTimeFormat("en-US", { timeZone: ET, hour: "numeric", minute: "2-digit" }).format(new Date(iso)) + " ET";
}

export function stampET(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-US", { timeZone: ET, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(iso)) + " ET";
}

/** Format a YYYY-MM-DD calendar date without time-zone drift. */
export function dayLabel(date: string, style: "long" | "short" = "long"): string {
  const [y, m, d] = date.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d, 12));
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: style === "long" ? "long" : "short",
    month: style === "long" ? "long" : "short",
    day: "numeric",
  }).format(dt);
}

export function addDays(date: string, n: number): string {
  const [y, m, d] = date.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + n, 12));
  return dt.toISOString().slice(0, 10);
}

export function todayET(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: ET }).format(new Date());
}

export const isDate = (s: string | undefined | null): s is string => !!s && /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s));

/** Team brand colours are often too dark for a near-black surface. Lift
 * them toward white just enough to read, preserving hue. */
export function onDark(hex: string): string {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  const t = lum < 0.05 ? 0.55 : lum < 0.12 ? 0.38 : lum < 0.2 ? 0.18 : 0;
  const mix = (c: number) => Math.round(c + (255 - c) * t);
  return `#${[r, g, b].map((c) => mix(c).toString(16).padStart(2, "0")).join("")}`;
}

export const seasonLabel = (start: number | string) => `${start}–${String(Number(start) + 1).slice(2)}`;

export const recordStr = (r: { w: number; l: number; otl: number } | null | undefined) => (r ? `${r.w}–${r.l}–${r.otl}` : "—");

export function periodLabel(period: number | null, type: string | null): string {
  if (type === "SO") return "SO";
  if (type === "OT") return period && period > 4 ? `${period - 3}OT` : "OT";
  if (!period) return "";
  return ["1st", "2nd", "3rd"][period - 1] ?? `P${period}`;
}

export const finalLabel = (lpt: string | null | undefined) => (lpt === "OT" ? "Final / OT" : lpt === "SO" ? "Final / SO" : "Final");

export const MODEL_NAMES: Record<string, string> = {
  baseline_home: "Home-ice baseline",
  elo_raw: "Elo (win probability only)",
  elo_goals: "Elo → goals",
  scoring_avg: "Scoring-average baseline",
  xg_lean_proj: "PUCKIQ · projected goalies",
  xg_lean: "PUCKIQ · actual starters known",
  add_lineup_toi_proj: "PUCKIQ + lineups · projected goalies",
  add_lineup_toi: "PUCKIQ + lineups · actual starters known",
  add_lineup_gs_proj: "PUCKIQ + lineups (game score) · projected goalies",
  add_lineup_gs: "PUCKIQ + lineups (game score) · actual starters known",
  xg_glm: "Full xG Poisson (more features)",
  xg_glm_elo: "Full xG Poisson + Elo",
  gbm: "Gradient boosting (LightGBM)",
  blend: "Poisson + boosting blend",
};

export const GROUP_NAMES: Record<string, string> = {
  xga: "Chance quality (xG ratings)",
  goals: "Goals for / against ratings",
  elo: "Elo (results) rating",
  goalie: "Goaltending",
  rest: "Rest and travel",
  xg5: "Separate five-on-five xG ratings",
  special: "Special teams",
  finishing: "Finishing above expected",
  lineup_toi: "Lineup availability, by ice time",
  lineup_gs: "Lineup availability, by game score",
  lineup_toi_conf: "Confirmed game-day lineup, by ice time",
  lineup_gs_conf: "Confirmed game-day lineup, by game score",
};
