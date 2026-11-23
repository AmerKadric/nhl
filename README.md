# PUCKIQ

**Know the numbers. Before the puck drops.**

PUCKIQ forecasts the final score of every NHL game with a statistical model, shows why, and keeps a public record of how the forecasts turned out. Predictions come from a fitted model and a simulator, never from a language model and never from hand-entered numbers.

## What it does

- Projects a final score, win probabilities, expected goals and the full exact-score distribution for each game.
- Models regulation, overtime and shootouts separately, so a final score is never a tie and an OT/SO result always has a one-goal margin.
- Treats an unconfirmed starting goalie as a probability-weighted mix of the likely starters.
- Explains each forecast with the model's own factor contributions. A one-sentence summary on each game is assembled from those same numbers by fixed rules, not by a language model.
- Lets you re-run a matchup in the Prediction Lab with a different goalie or schedule situation.
- Publishes forecasts as append-only versions and scores every one automatically after the game.
- Simulates the rest of the season every day for projected standings, playoff odds and Stanley Cup odds.
- Records pre-game betting odds from the NHL feed and scores the model against the market on the same games. The model never uses them and individual prices are not displayed.

## Results

Walk-forward backtest on 12,247 games (2017–18 to 2026–27, the last season in progress). Each season is forecast by a model trained only on earlier seasons, using inputs built only from games played on earlier days.

| Model | Winner accuracy | Log loss | Exact score |
|---|---|---|---|
| Home-ice baseline | 53.7% | 0.6905 | 6.8% |
| Elo → goals | 58.4% | 0.6705 | 7.4% |
| PUCKIQ without lineup inputs | 59.1% | 0.6662 | 7.5% |
| **PUCKIQ (current model, projected goalies)** | **59.5%** | **0.6647** | **7.5%** |

- The gain over both baselines is statistically significant (paired bootstrap, 95% interval).
- Lineup availability (who each team last dressed, weighted by ice time) was added after it significantly improved forecasts on the selection seasons.
- On the seasons held out from all modelling decisions (2024–25 onward, 3,169 games): 56.8% accuracy, 0.6717 log loss. There the lineup input improved the score distribution but not winner accuracy (56.9% without it).
- Gradient boosting (LightGBM), a blended model, separate five-on-five and special-teams ratings, and a spread calibration were all tested and rejected because they did not beat the simpler model.
- This is a simulation. The record of forecasts actually published before games is tracked separately on the Accuracy page and is still short.

The full report is in [`artifacts/backtest/summary.json`](artifacts/backtest/summary.json).

### Season simulation

Checked by re-running the simulation as of December 1 and February 1 in 7 past seasons, with everything after each date hidden (444 team-checkpoints):

| Measure | Result |
|---|---|
| Playoff odds, Brier score | 0.109 (0.185 for "whoever holds a spot today", 0.250 for a coin flip) |
| Teams given 70–90% to make the playoffs | made it 82% of the time (77 cases) |
| Final points, average miss | 6.3 points |
| Actual points inside the 80% range | 81% of the time |

- One setting is tuned on this history: the simulation's uncertainty about team strength is doubled, because the rating filter alone was too sure of itself over a full season. It was chosen on four seasons and checked on the other three.
- Cup odds rest on 14 champion-checkpoints, too few to validate.
- Trades, injuries and call-ups are not modelled.

The report is in [`artifacts/backtest/season_sim.json`](artifacts/backtest/season_sim.json).

## How it works

```
NHL APIs + MoneyPuck files
        │  ingest (retries, rate limits, conditional downloads, run log)
        ▼
  relational store (SQLite locally, PostgreSQL in production)
        │  features: one chronological pass, a day at a time
        ▼
  Kalman-filter team ratings · goalie ratings · starter projection · lineup availability
        │  Poisson rate model
        ▼
  20,000-game simulator (regulation → overtime → shootout)
        │  publish (versioned, append-only) · settle (exactly once)
        ▼
  FastAPI  ──►  Next.js site
```

| Path | Contents |
|---|---|
| `engine/puckiq/ingest` | NHL and MoneyPuck ingestion |
| `engine/puckiq/features` | Leak-free pre-game features and rating systems |
| `engine/puckiq/models` | Rate models, design matrices, production forecaster |
| `engine/puckiq/sim` | Final-score simulator (exact and Monte Carlo) and the season simulator |
| `engine/puckiq/backtest` | Walk-forward backtest and metrics |
| `engine/puckiq/pipeline` | Daily and refresh jobs, publishing, settlement |
| `engine/puckiq/api` | HTTP API, including the scenario lab |
| `engine/tests` | 73 tests: simulator rules, look-ahead leakage, lineups, season simulation, summaries, market benchmark, settlement, API |
| `web/` | Next.js 16 front end |
| `artifacts/` | Tuned rating parameters and the backtest report |

## Run it locally

Requires Python 3.12+ and Node 20+.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "engine[train,dev]"   # macOS/Linux: .venv/bin/python

# one-off: load history (about 3 minutes, ~350 MB of downloads), then backtest and publish
.venv/Scripts/puckiq ingest-history
.venv/Scripts/puckiq backtest
.venv/Scripts/puckiq daily --retrain

.venv/Scripts/puckiq serve                               # API on http://127.0.0.1:8000

cd web
cp .env.example .env.local
npm install
npm run dev                                              # site on http://localhost:3000
```

| Command | What it does |
|---|---|
| `puckiq daily` | Full run: results, advanced stats, ratings, weekly retrain, publish, settle |
| `puckiq refresh` | Light run: game states, goalie confirmations, forecast updates, settle |
| `puckiq backtest` | Walk-forward backtest of every candidate model |
| `puckiq season-sim` | Simulate the rest of the season and store the result (also part of `daily`) |
| `puckiq season-backtest` | Validate the season simulation against past seasons |
| `puckiq tune` | Re-tune rating parameters on pre-2017 seasons only |
| `puckiq status` | Data freshness for each feed |
| `puckiq copy-db --from <url>` | Copy every table from another database into `DATABASE_URL` |
| `pytest` (in `engine/`) | Test suite; uses a temporary database and no network |

Every job is idempotent. A failure in a critical feed aborts publishing; a stale secondary feed is recorded with the forecasts and shown on the site.

## Deployment

| Piece | Where | Notes |
|---|---|---|
| Site | Vercel project `puckiq` (root `web/`) | https://puckiq-dusky.vercel.app |
| API | Vercel project `puckiq-api` (root `engine/`) | https://puckiq-api.vercel.app, one Python function |
| Database | Neon Postgres `puckiq-db`, via the Vercel Marketplace | Free plan; attached to `puckiq-api`, which receives `DATABASE_URL` |
| Pipeline | GitHub Actions workflow `pipeline` | Full run daily at 13:00 UTC; light refresh every 30 minutes, 22:00–04:59 UTC |

- Both Vercel projects are connected to this repository: a push to `main` redeploys them.
- The site reads `PUCKIQ_API_URL`; the API reads `DATABASE_URL` and `PUCKIQ_CORS_ORIGINS`. All are set in Vercel.
- The pipeline reads the `DATABASE_URL` repository secret. Without it every run exits early.
- When a pipeline run fails, the workflow opens an issue titled "PUCKIQ pipeline run failed" (or comments on the open one), so GitHub emails the repository owner. The daily job also fails if any single step failed, even when forecasts were still published.
- Neon's free plan allows 5 GB of data transfer a month and suspends the database beyond that. To stay far below it, the pipeline caches completed seasons between runs, refresh runs skip the heavy work unless a game is about to start, the API serves precomputed summaries, and the site caches API reads for 20 seconds to a few minutes.
- The API installs only the runtime dependencies. Tuning, backtesting, training and the pipeline need `engine[train]`.
- To run a command against the hosted database from a workstation, set `DATABASE_URL` for that process (for example from `vercel env pull`) and never commit it.

Moving to another host: `engine/Dockerfile` builds the same API as a container, and `puckiq copy-db --from <url>` copies every table between databases while keeping forecast history linked.

## Data and licensing

| Source | Used for | Terms |
|---|---|---|
| [MoneyPuck](https://moneypuck.com/data.htm) | Expected goals and shot data | Free for non-commercial use with clear credit. Other uses need MoneyPuck's permission. |
| NHL public APIs | Schedule, results, goalie and skater game logs, live state, partner sportsbook odds (benchmark only) | Publicly accessible and undocumented; no published terms. Used read-only at low request rates. |

PUCKIQ must stay non-commercial (no ads, affiliate links or paid tiers) unless MoneyPuck grants permission. It is not affiliated with the NHL, its teams or MoneyPuck; team names and logos belong to their owners.

## Known limits

- **No injury reports or game-day lineups.** No free, licensed feed exists in the sources above. The model sees only who dressed in each team's last game, so a new absence is invisible until the first game missed.
- **Exact scores are mostly chance.** The most likely score is right about one game in thirteen.
- **Goalie confirmation is untested live.** The code that reads confirmed starters from the NHL boxscore has not yet been observed against a real pre-game window.
- **No in-game model.** Live scores are shown as observed; no live win probability is produced.
- **The market benchmark is new.** Odds are only available on the day of a game, so the comparison started on 23 November 2026 and needs a few hundred games before it means anything. Nothing here is betting advice.
