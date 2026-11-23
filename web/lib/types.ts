export type TeamBrief = { id: number; abbrev: string; name: string; color: string; logo: string };

export type GoalieOption = { pid: number; name: string; prob: number; status: "projected" | "confirmed" | "unknown" };

export type ForecastCard = {
  id: number;
  version_no: number;
  published_at: string;
  goalie_state: "projected" | "partial" | "confirmed";
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
  evaluation: { winner_correct: boolean; exact_score_hit: boolean; p_actual_score: number; log_loss: number } | null;
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

export type Accuracy = {
  forward: {
    first_forecast_at: string | null;
    games_pending: number;
    final: Metrics;
    first_version?: Metrics;
    by_goalie_state?: Record<string, Metrics>;
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
};
