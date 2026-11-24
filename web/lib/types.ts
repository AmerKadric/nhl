export type TeamBrief = { id: number; abbrev: string; name: string; color: string; logo: string };

export type GoalieOption = { pid: number; name: string; prob: number; status: "projected" | "unknown" };

export type ForecastCard = {
  id: number;
  version_no: number;
  published_at: string;
  goalie_state: "projected";
  model_version: string;
  p_home_win: number;
  p_away_win: number;
  exp_home_goals: number;
  exp_away_goals: number;
  ml_home_goals: number;
  ml_away_goals: number;
  ml_score_prob: number;
  expected_total: number;
  p_extra_time: number;
  confidence_label: string;
  headline?: string | null;
  goalies: { home: GoalieOption[]; away: GoalieOption[] };
  stale_sources: string[];
};

export type LiveState = {
  home_score: number | null;
  away_score: number | null;
  home_sog: number | null;
  away_sog: number | null;
  period: number | null;
  period_type: string | null;
  clock: string | null;
  in_intermission: boolean | null;
};

export type Game = {
  id: number;
  season: number;
  game_type: number;
  date: string;
  start_time_utc: string | null;
  venue: string | null;
  state: string;
  schedule_state: string;
  home: TeamBrief;
  away: TeamBrief;
  result: { home_score: number; away_score: number; last_period_type: string | null } | null;
  live: LiveState | null;
  forecast: ForecastCard | null;
  /** `provisional` means graded on the fly from the NHL's final score; the
   * pipeline has not yet added the game to the stored track record. */
  evaluation: { winner_correct: boolean; exact_score_hit: boolean; p_actual_score: number | null; log_loss: number; provisional?: boolean } | null;
};

export type GamesResponse = { start: string; end: string; today: string; games: Game[] };

export type Driver = {
  key: string;
  label: string;
  home_goal_effect: number;
  away_goal_effect: number;
  home_win_prob_effect: number;
  inputs: Record<string, number | null>;
};

export type ScoreP = { home: number; away: number; p: number };

export type ForecastDetail = {
  id: number;
  version_no: number;
  published_at: string;
  information_cutoff: string;
  model_version: string;
  goalie_state: string;
  n_sims: number;
  seed: number;
  p_home_win: number;
  p_away_win: number;
  p_home_reg: number;
  p_tie_reg: number;
  p_away_reg: number;
  p_overtime: number;
  p_shootout: number;
  exp_home_goals: number;
  exp_away_goals: number;
  lam_home: number;
  lam_away: number;
  ml_home_goals: number;
  ml_away_goals: number;
  ml_score_prob: number;
  top_scores: ScoreP[];
  totals: number[];
  diffs: Record<string, number>;
  expected_total: number;
  extras: Record<string, number>;
  confidence: { label: string; favourite_prob: number; mc_standard_error: number; rate_sd_home: number; rate_sd_away: number; most_likely_score_prob: number };
  drivers: Driver[];
  goalies: { home: GoalieModel[]; away: GoalieModel[] };
  data_freshness: Freshness | null;
  availability: Record<string, string> | null;
  lineup_watch?: { home: LineupWatch; away: LineupWatch } | null;
  headline?: string | null;
  matrix: { max_goals: number; final: number[][]; regulation: number[][]; overflow: number };
};

export type LineupWatch = {
  available: boolean;
  as_of: string | null;
  absent: { pid: number; name: string; dress_rate: number; toi: number; game_score: number; defence: boolean }[];
};

export type GoalieModel = { pid: number; name: string; prob: number; rating: number; xga_n: number; status: string };

export type GoalieAgg = { gp: number; gs: number; shots_against: number; goals_against: number; sv_pct: number | null; xga: number; gsax: number | null; gsax_per_60: number | null };

export type GoalieMatchup = GoalieModel & { season?: GoalieAgg; last5?: GoalieAgg; last_start?: string | null };

export type Rec = { w: number; l: number; otl: number };

export type TeamSummary = {
  gp: number;
  record: Rec;
  points: number;
  point_pct: number;
  gf_pg: number;
  ga_pg: number;
  xgf60_5v5: number | null;
  xga60_5v5: number | null;
  xg_share_5v5: number | null;
  xgf_pg: number | null;
  xga_pg: number | null;
  pp_xg60: number | null;
  pp_g60: number | null;
  pk_xga60: number | null;
  pk_ga60: number | null;
  sh_pct: number | null;
  sv_pct: number | null;
  goals_above_expected: number | null;
  goals_saved_above_expected: number | null;
  last10: Rec & { gf_pg: number; ga_pg: number; xg_share_5v5: number | null; results: string[] };
  home: Rec;
  away: Rec;
  xg_games: number;
  ratings: { elo: number; off_g: number; def_g: number; off_xg: number; def_xg: number } | null;
};

export type RatingRanges = Record<string, { min: number; max: number; median: number }>;

export type Version = {
  version_no: number;
  published_at: string;
  reason: string;
  goalie_state: string;
  model_version: string;
  p_home_win: number;
  exp_home_goals: number;
  exp_away_goals: number;
  ml_home_goals: number;
  ml_away_goals: number;
  is_latest: boolean;
  evaluation: { winner_correct: boolean; exact_score_hit: boolean; log_loss: number; p_actual_score: number } | null;
};

export type LabInfo = {
  goalies: { home: { pid: number; name: string; prob: number; rating: number }[]; away: { pid: number; name: string; prob: number; rating: number }[] };
  current: { home_b2b: boolean; away_b2b: boolean; home_g7: number; away_g7: number; home_rest_days: number | null; away_rest_days: number | null };
  domain: { g7: [number, number] };
  unsupported: { control: string; reason: string }[];
  skaters: { home: LabSkater[]; away: LabSkater[] } | null;
};

export type LabSkater = { pid: number; name: string; toi: number; defence: boolean };

export type GameDetail = {
  game: Game;
  forecast: ForecastDetail | null;
  versions: Version[];
  evaluation: Record<string, number | boolean | string> | null;
  comparison: { home: TeamSummary | null; away: TeamSummary | null };
  rating_ranges: RatingRanges;
  goalie_matchup: { home: GoalieMatchup[]; away: GoalieMatchup[] } | null;
  lab: LabInfo | null;
  stakes?: (StakeGame & { date: string }) | null;
};

export type LabMini = {
  p_home_win: number;
  p_away_win: number;
  p_home_reg: number;
  p_tie_reg: number;
  p_away_reg: number;
  p_overtime: number;
  p_shootout: number;
  exp_home_goals: number;
  exp_away_goals: number;
  ml_home_goals: number;
  ml_away_goals: number;
  ml_score_prob: number;
  top_scores: ScoreP[];
  expected_total: number;
  extras: Record<string, number>;
  confidence_label: string;
  mc_standard_error: number;
  n_sims: number;
  matrix: number[][];
  drivers: Driver[];
};

export type LabResponse = { baseline: LabMini; scenario: LabMini; warnings: string[]; note: string };

export type Freshness = {
  checked_at: string;
  sources: Record<string, { last_success: string | null; age_hours: number | null; stale_after_hours: number; stale: boolean }>;
  stale: string[];
  results_through: string;
  xg_through: string;
  finals_missing_xg: number;
  /** Forecast games more than twelve hours old that the pipeline has not graded. */
  ungraded_games?: number;
  ungraded_since?: string | null;
  /** When any pipeline step last succeeded (UTC, no zone suffix). */
  last_update?: string | null;
};

export type Meta = {
  season: number;
  today: string;
  model: { version: string; architecture: string; trained_through: string; n_sims: number; feature_groups: string[] } | null;
  freshness: Freshness;
  sources: { key: string; name: string; url: string; license: string; attribution: string; status: string }[];
  counts: { forecast_versions: number; games_forecast: number; games_evaluated: number };
};

export type Metrics = {
  n: number;
  accuracy?: number;
  log_loss?: number;
  brier?: number;
  exact_score_rate?: number;
  score_log_loss?: number;
  mae_team_goals?: number;
  mae_total_goals?: number;
  mae_goal_diff?: number;
  home_win_rate?: number;
  pred_extra_time_rate?: number;
  actual_extra_time_rate?: number;
};

export type CalBin = { lo: number; hi: number; n: number; mean_pred: number; observed: number };
export type ConfBin = { lo: number; hi: number; n: number; mean_confidence: number; accuracy: number };
export type Boot = { mean_diff: number; ci95: [number, number]; significant: boolean; n: number };

export type BacktestModel = {
  description: string;
  overall: Metrics;
  selection_window: Metrics;
  confirmation_window: Metrics;
  regular_season: Metrics;
  playoffs: Metrics;
};

export type BacktestDetail = BacktestModel & {
  by_season: Record<string, Metrics>;
  by_month?: Record<string, Metrics>;
  calibration: CalBin[];
  ece: number;
  by_confidence: ConfBin[];
};

export type MarketScore = { accuracy: number; log_loss: number; brier: number };

export type MarketBlock = {
  n: number;
  puckiq: MarketScore;
  market: MarketScore;
  log_loss_diff: number;
  log_loss_diff_se: number | null;
  median_minutes_before: number;
  mean_abs_gap: number;
  totals?: { n: number; puckiq_brier: number; market_brier: number; over_rate: number };
};

export type Accuracy = {
  market?: { generated_at: string; games_with_odds: number; providers: string[]; latest: MarketBlock | null; first: MarketBlock | null } | null;
  forward: {
    first_forecast_at: string | null;
    games_pending: number;
    final: Metrics;
    first_version?: Metrics;
    by_month?: Record<string, Metrics>;
    by_confidence?: ConfBin[];
    calibration?: CalBin[];
  };
  model_versions: {
    id: number;
    version: string;
    architecture: string;
    created_at: string;
    trained_through: string;
    is_active: boolean;
    n_forecasts: number;
    feature_groups: string[];
    coefficients: Record<string, number>;
    simulator: Record<string, number>;
    n_sims: number;
  }[];
  backtest: {
    generated_at: string;
    method: string;
    first_training_season: number;
    test_seasons: number[];
    selection_seasons: [number, number];
    confirmation_seasons: [number, number];
    chosen_model: string;
    chosen_groups?: string[] | null;
    selection_reason: string;
    primary: string;
    significance: Record<string, Boot | null>;
    ablation: Record<string, { features: string[]; delta_log_loss: Boot; delta_score_log_loss: Boot }>;
    additions: Record<string, { features: string[]; delta_log_loss: Boot; delta_score_log_loss: Boot }>;
    models: Record<string, BacktestModel>;
    detail: Record<string, BacktestDetail>;
    selection_tests: Record<string, { significantly_worse: boolean; score_log_loss: number; log_loss: number }>;
  } | null;
};

export type HistoryRow = {
  game_id: number;
  date: string;
  home: TeamBrief;
  away: TeamBrief;
  home_score: number;
  away_score: number;
  last_period_type: string | null;
  p_home_win: number;
  exp_home_goals: number;
  exp_away_goals: number;
  ml_home_goals: number;
  ml_away_goals: number;
  p_actual_score: number;
  winner_correct: boolean;
  exact_score_hit: boolean;
  goalie_state?: string;
  version_no?: number;
  published_at?: string;
};

export type History = { source: string; total: number; limit: number; offset: number; rows: HistoryRow[] };

export type TeamFull = TeamBrief & { conference: string | null; division: string | null; active: boolean };

export type TeamsResponse = { season: number; rating_ranges: RatingRanges; teams: (TeamFull & { summary: TeamSummary | null })[] };

export type TeamPage = {
  team: TeamFull;
  season: number;
  summary: TeamSummary | null;
  rating_ranges: RatingRanges;
  rating_history: { date: string; elo: number; off_g: number; def_g: number; off_xg: number; def_xg: number }[];
  goalies: { pid: number; name: string; last_game: string; season?: GoalieAgg; last5?: GoalieAgg }[];
  upcoming: Game[];
  recent: Game[];
  games_remaining: number;
  forward_history: HistoryRow[];
  backtest_history: HistoryRow[];
  outlook?: (SeasonTeam & Partial<PlayoffTeam>) | null;
  odds_history?: OddsHistory | null;
};

export type SeasonTeam = {
  team_id: number;
  team: TeamBrief | null;
  conference: string;
  division: string;
  gp: number;
  w: number;
  l: number;
  otl: number;
  points: number;
  games_remaining: number;
  proj_points: number;
  points_p10: number;
  points_p50: number;
  points_p90: number;
  p_playoffs: number;
  p_division: number;
  p_round2: number;
  p_conf_final: number;
  p_final: number;
  p_cup: number;
};

export type SeasonValidationBlock = {
  team_seasons: number;
  playoff_brier: number;
  playoff_brier_in_position_baseline: number;
  playoff_log_loss: number;
  points_mae: number;
  points_interval_coverage: number;
  champion_mean_prob: number;
  champion_uniform_prob: number;
  n_champions: number;
};

/** One team's playoff odds depending on the result of one game. `se` is the
 * standard error of `swing` from the simulation. */
export type StakeSide = { team_id: number; team?: TeamBrief | null; now: number; if_win: number; if_loss: number; swing: number; se: number };
export type StakeGame = { game_id: number | null; p_home_win: number; home: StakeSide; away: StakeSide };
export type Stakes = { date: string; method?: "adjusted" | "raw"; games: StakeGame[] };

type LeverageScore = { rmse: number; slope: number; mean_predicted_winners: number; mean_predicted_losers: number };
/** Predicted stakes against the actual next-morning move in odds, this season. */
export type LeverageCheck = {
  n: number;
  days?: number;
  first_day?: string;
  last_day?: string;
  mean_actual_winners?: number;
  mean_actual_losers?: number;
  rmse_no_change?: number;
  adjusted?: LeverageScore;
  raw?: LeverageScore;
};

/** A team's simulated odds on each day; days up to `reconstructed_through`
 * were computed after the fact, not published at the time. */
export type OddsHistory = {
  dates: string[];
  reconstructed_through?: string | null;
  p_playoffs: (number | null)[];
  p_cup: (number | null)[];
  proj_points: (number | null)[];
};

export type PlayoffSeries = {
  round: number;
  matchup: number;
  top: number;
  bottom: number;
  top_team: TeamBrief | null;
  bottom_team: TeamBrief | null;
  wins_top: number;
  wins_bottom: number;
  done: boolean;
  winner: number | null;
  p_top: number | null;
};

export type PlayoffTeam = { team_id: number; team: TeamBrief | null; alive: boolean; p_round: Record<string, number>; p_cup: number };

export type PlayoffSim = {
  phase: "playoffs" | "complete";
  season: number;
  generated_at: string;
  results_through: string;
  model_version: string;
  n_sims: number;
  round: number;
  round_name: string;
  series: PlayoffSeries[];
  teams: PlayoffTeam[];
  champion: number | null;
};

export type PlayoffValidation = {
  generated_at: string;
  seasons: number[];
  series: { n: number; brier: number; accuracy: number; home_ice_win_rate: number; brier_home_ice_always: number; brier_base_rate: number; mean_stated: number };
  calibration: CalBin[];
  cup: { team_seasons: number; n_champions: number; champion_mean_prob: number; champion_uniform_prob: number; brier: number; brier_uniform: number };
};

export type SeasonOutlook = {
  season: number;
  playoff_validation?: PlayoffValidation | null;
  leverage_validation?: LeverageCheck | null;
  simulation: {
    phase?: "regular" | "awaiting_bracket" | "playoffs" | "complete";
    season: number;
    generated_at: string;
    results_through: string;
    model_version: string;
    n_sims: number;
    games_remaining: number;
    teams: SeasonTeam[];
    stakes?: Stakes | null;
  } | null;
  history: { dates: string[]; reconstructed_through?: string | null; teams: Record<string, { p_playoffs: (number | null)[]; p_cup: (number | null)[]; proj_points: (number | null)[] }> } | null;
  validation: {
    generated_at: string;
    seasons: number[];
    checkpoints: string[];
    overall: SeasonValidationBlock;
    by_checkpoint: Record<string, SeasonValidationBlock>;
    calibration: CalBin[];
    strength_sd_scale?: number;
    scale_study?: { fit_seasons: number[]; grid: { scale: number; fit_coverage: number | null; later_coverage: number | null }[] };
  } | null;
};
