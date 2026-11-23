# PUCKIQ

**Know the numbers. Before the puck drops.**

PUCKIQ forecasts the final score of every NHL game with a statistical model, shows why, and keeps a public record of how the forecasts turned out. Predictions come from a fitted model and a simulator, never from a language model and never from hand-entered numbers.

## What it does

- Projects a final score, win probabilities, expected goals and the full exact-score distribution for each game.
- Models regulation, overtime and shootouts separately, so a final score is never a tie and an OT/SO result always has a one-goal margin.
- Treats an unconfirmed starting goalie as a probability-weighted mix of the likely starters.
- Explains each forecast with the model's own factor contributions.
- Lets you re-run a matchup in the Prediction Lab with a different goalie or schedule situation.
- Publishes forecasts as append-only versions and scores every one automatically after the game.

## Results

Walk-forward backtest on 12,228 games (2017–18 to 2026–27, the last season in progress). Each season is forecast by a model trained only on earlier seasons, using inputs built only from games played on earlier days.

| Model | Winner accuracy | Log loss | Exact score |
|---|---|---|---|
| Home-ice baseline | 53.7% | 0.6905 | 6.8% |
| Elo → goals | 58.4% | 0.6704 | 7.4% |
| **PUCKIQ (projected goalies)** | **59.1%** | **0.6661** | **7.5%** |

- The gain over both baselines is statistically significant (paired bootstrap, 95% interval).
- On the three seasons held out from all modelling decisions (2024–25 onward, 3,150 games): 56.9% accuracy, 0.6721 log loss.
- Gradient boosting (LightGBM), a blended model, separate five-on-five and special-teams ratings, and a spread calibration were all tested and rejected because they did not beat the simpler model.
- This is a simulation. The record of forecasts actually published before games is tracked separately on the Accuracy page and is still short.

The full report is in [`artifacts/backtest/summary.json`](artifacts/backtest/summary.json).

## How it works

```
NHL APIs + MoneyPuck files
        │  ingest (retries, rate limits, conditional downloads, run log)
        ▼
  relational store (SQLite locally, PostgreSQL in production)
        │  features: one chronological pass, a day at a time
        ▼
  Kalman-filter team ratings · shrunk goalie ratings · starter projection
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
| `engine/puckiq/sim` | Final-score simulator (exact and Monte Carlo) |
| `engine/puckiq/backtest` | Walk-forward backtest and metrics |
| `engine/puckiq/pipeline` | Daily and refresh jobs, publishing, settlement |
| `engine/puckiq/api` | HTTP API, including the scenario lab |
| `engine/tests` | 53 tests: simulator rules, look-ahead leakage, settlement, API |
| `web/` | Next.js 16 front end |
| `artifacts/` | Tuned rating parameters and the backtest report |

## Run it locally

Requires Python 3.11+ and Node 20+.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "engine[dev]"     # macOS/Linux: .venv/bin/python

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
| `puckiq tune` | Re-tune rating parameters on pre-2017 seasons only |
| `puckiq status` | Data freshness for each feed |
| `pytest` (in `engine/`) | Test suite; uses a temporary database and no network |

Every job is idempotent. A failure in a critical feed aborts publishing; a stale secondary feed is recorded with the forecasts and shown on the site.

## Deploying

Nothing is hosted yet. The pieces are ready:

1. **Database**: create a PostgreSQL database and set `DATABASE_URL`. PostgreSQL support is implemented but has only been exercised on SQLite so far.
2. **Pipeline**: add `DATABASE_URL` as a repository secret, then run the `pipeline` workflow once with `bootstrap`. After that it runs daily and every 20 minutes through the game window.
3. **API**: build `engine/Dockerfile` from the repository root and run it anywhere that hosts containers, with `DATABASE_URL` and `PUCKIQ_CORS_ORIGINS` set.
4. **Site**: deploy `web/` (for example on Vercel) with `PUCKIQ_API_URL` pointing at the API.

## Data and licensing

| Source | Used for | Terms |
|---|---|---|
| [MoneyPuck](https://moneypuck.com/data.htm) | Expected goals and shot data | Free for non-commercial use with clear credit. Other uses need MoneyPuck's permission. |
| NHL public APIs | Schedule, results, goalie logs, live state | Publicly accessible and undocumented; no published terms. Used read-only at low request rates. |

PUCKIQ must stay non-commercial (no ads, affiliate links or paid tiers) unless MoneyPuck grants permission. It is not affiliated with the NHL, its teams or MoneyPuck; team names and logos belong to their owners.

## Known limits

- **No injuries or lineups.** No free, licensed feed exists in the sources above, so these are marked unavailable rather than guessed.
- **Exact scores are mostly chance.** The most likely score is right about one game in thirteen.
- **Goalie confirmation is untested live.** The code that reads confirmed starters from the NHL boxscore has not yet been observed against a real pre-game window.
- **No in-game model.** Live scores are shown as observed; no live win probability is produced.
- **Not benchmarked against betting markets**, and not betting advice.
