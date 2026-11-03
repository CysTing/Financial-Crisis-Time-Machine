# Acceptance evidence

Final validation date: **3 November 2026 (Asia/Hong_Kong)**. Tested locally on Windows with **CPython 3.12.14**. This records observed work; it does not assert remote CI or live hosting.

## Definition of Done

| Requirement | Evidence / status |
|---|---|
| Essential project files | Root `app.py`, pinned `requirements.txt`, README, MIT license, engine/data/UI modules, sourced events, tests, Streamlit configuration, workflow, and methodology/architecture documentation are present. |
| Application launches | Actual Streamlit server launched on `http://127.0.0.1:8501`; the running app was operated in the in-app browser. No startup/import exceptions. |
| Three historical scenarios | Financial crisis 2007–2010, COVID 2019–2021, inflation/rates 2021–2023. All seven ETFs validated on genuine provider downloads for every window. |
| Functional Survival Mode | Actual browser launched an explicit synthetic replay, executed the initial allocation, queued a manual cash target, advanced daily/weekly/monthly, and reached 31 December 2010. |
| No future presentation | Engine snapshots copy prices only through the clock; chart builders enforce date boundaries; event disclosures are session-aware; exports independently filter and recompute metrics. Perturbation and export tests pass. |
| Allocation changes | Persistent allocation desk, single-asset target ticket, cash target, pending replacement/cancellation, allocation comparison, and ledger. AppTest changes weights, executes, and verifies duplicate-free reruns. Zero/unfunded tickets preserve the existing order and show explicit errors. |
| Correct portfolio accounting | Hand-calculated next-close and transaction-cost examples, post-cost full-allocation feasibility, cash reconciliation, daily asset/cash/cost P&L reconciliation, and terminal-order tests pass. |
| All six strategies | AppTest runs all six. `scripts/validate_scenarios.py --historical` completed all 18 genuine-data strategy/scenario combinations with matching windows, nonnegative cash, and reconciled costs/P&L. |
| Trading delay / costs | Initial cash baseline; next-session fills; old holdings earn the move into execution close; cost-aware targets; both trade sides charged. A specific test proves the new position cannot earn the return preceding execution. |
| Matched benchmarks | SPY buy-and-hold and monthly 60/40 share capital, data, calendar, initial delay, costs, and evaluation index; incompatible strategies are excluded. Benchmark/index tests pass. |
| Financial metrics | Total return, CAGR, volatility, Sharpe, Sortino, drawdown, Calmar, best/worst day, excess return, turnover, costs, rolling risk/correlation, VaR/ES, realized P&L and covariance risk attribution. Formula and edge-case tests pass. |
| Critical tests | `python -m pytest -q` from the actual repository: **183 passed, 3 skipped in 34.39s**. Skips are the deliberately opt-in live-provider cases, not missing core tests. |
| Five polished responsive pages | All five pages exercised in AppTest and browser. Desktop 1440×1000 and phone 390×844 inspected. Genuine running-app screenshots are in `docs/screenshots/`, with synthetic results labeled. |
| Complete README | Installation, walkthrough, sources/rights, financial rules, six strategies, architecture/tree, testing, GitHub publishing, Cloud deployment, biases, roadmap, education disclaimer. |
| GitHub Actions | `.github/workflows/tests.yml` runs the offline suite on push/PR for Python 3.12/3.13. Uses current official checkout/setup-python v7 examples and read-only repository permissions. **No remote run executed: repository was not published.** |
| Streamlit deployment preparation | Root `app.py` and `requirements.txt`, tested pinned dependencies, Cloud-compatible relative paths, no secrets, graceful provider fallback. Select `main`, `app.py`, Python 3.12. **No hosted deployment performed.** |
| Creative replay layer | Actual browser verified a −3.04% and +3.07% newly revealed synthetic daily move; bounded pulse emphasis and disabling motion work. Pure pulse/debrief tests include intermediate sessions, future-row perturbations, recovery, and missing inputs. |
| Decision journal | Browser-saved observation appeared in actual CSV/HTML downloads. AppTest verifies dated notes, replacement-order linkage, no trading when saving an observation, persistence, empty/invalid input safety, reset, and a fresh mission. |
| Export provenance | Every figure carries its source inside native PNG output; formal CSV/HTML exports label synthetic data. Laboratory CSV round-trips through a standard parser with rectangular rows and numeric metrics. The unlabeled grid CSV shortcut is hidden. |

## Commands and observed results

Commands are run from the project root using the active local environment.

```powershell
python -m pip install -r requirements.txt
python -m pip check
python -m pytest -q
python -m compileall -q app.py core ui scripts tests
```

Dependency installation succeeded; `pip check` reported **No broken requirements found**. The final suite executed from the actual repository reported **183 passed, 3 skipped in 34.39s**. Compilation completed without errors. The Windows convenience launcher was also executed successfully to create the project environment, install its dependencies, and run checks.

The seven pinned top-level versions tested together are Streamlit 1.60.0, pandas 2.3.3, NumPy 2.3.5, Plotly 6.9.0, yfinance 1.7.0, exchange-calendars 4.13.2, and pytest 9.2.0. NumPy was pinned to a version that avoids upstream pandas/calendar deprecation warnings observed during initial setup.

```powershell
$env:FTM_INTEGRATION = '1'
python -m pytest -q -m integration tests/test_provider_integration.py
Remove-Item Env:FTM_INTEGRATION
```

Observed **3 passed in 4.84s** during the final review. These were genuine Yahoo Finance downloads, one per scenario; all seven tickers had valid observations. A separate provider probe also succeeded for all three windows with zero validation warnings. No genuine price observations are included in the repository or deliverable archive.

```powershell
python scripts/validate_scenarios.py --historical
```

Observed **PASS: 18 runs; matching windows, nonnegative cash, cost and P&L reconciliation**, rerun from the actual repository with the final engine. Evaluation counts: 1,008 sessions for the financial-crisis window; 757 for COVID; 753 for inflation. Full warm-up frames contained respectively 1,288, 1,037, and 1,033 sessions. The initial observation is cash; these results do not include pre-start performance.

## Actual browser checks

- Initialized and reviewed a synthetic preflight, then launched on the first exchange close, 3 January 2007.
- Advanced one day to execute the initial 100% SPY target at the next close. Displayed USD 99,950.02 after the 5 bps buy cost.
- Queued 100% cash manually, advanced five sessions, then advanced to the end of the next calendar month. Intervening valuations and the sale cost were reflected.
- Opened an interim report through 28 February 2007. Downloaded actual HTML and CSV files through the browser. CSV contained 39 recorded rows, maximum date `2007-02-28`, identical `as_of`, and explicit synthetic labels. HTML carried the synthetic and partial-session labels. No future price rows were present.
- Ran all six Laboratory strategies in the browser; Survival clock stayed on 28 February 2007.
- Completed the replay through 46 further month-end button actions to 31 December 2010. Final report and Crisis Analysis gates unlocked.
- Inspected all five pages, numeric units, chart labels, paired risk charts, and eight-series legends at desktop and phone sizes. Reset behavior is also exercised by AppTest and browser.
- Fixed the original title/legend collision, long event headline collisions, header clipping, overwritten icon fonts, dollar-markdown interpretation, misleading normalized-price units, and over-rounded small risk-axis labels. Phone legends have an actual visible scroll region; chart tests verify reserved space and boundaries.
- Repeated the complete replay with an invested SPY portfolio, inspected non-flat drawdown/volatility curves and the computed three-observation debrief, and confirmed all six shortened legend names remain visible at 390×844.
- Verified the actual multiline SVG title boxes were approximately 12 pixels inside their figures after correcting clipping. KPI amounts remain readable at 1440×1000 and in a compact two-column phone layout.
- Recorded a journal observation at 3 January 2007. The actual downloaded interim decision CSV contained two rows through 28 February 2007, including the note, and the actual HTML contained that note, selected benchmark, partial label, and strategy parameters.
- Fixed and regression-tested stale preflight launches after configuration changes, shared strategy lookback inputs, excluded zero-weight assets, discarded allocation drafts, and zero-dollar tickets replacing valid pending orders.
- Replaced the engine's today-relative calendar defaults with explicit historical bounds. An independent January 2000 hand example produces equity `[100, 100, 110]`, with the first allocation executing on the second session; advancing the host date cannot age the historical window out of the calendar.

Screenshots show genuine UI captures of **synthetic demonstrations**. They must not be interpreted as historical performance evidence.

## Remaining external verification

- **GitHub publishing, remote CI, and Streamlit live deployment were not executed.** The local repository initially had no remote. These actions were outside the authorized local implementation scope.
- **Python 3.13 and Linux have not been executed locally.** They are configured for GitHub Actions; the verified local runtime is Windows CPython 3.12.14.
- Provider integration succeeded during this session; external service availability, future revisions, rate limits, and data-use terms remain outside the application's control.
- Optional macroeconomic vintages, PDF export, persistent snapshots, Monte Carlo, and AI-selected strategies are not implemented or advertised as existing features.

The simplified adjusted-unit execution model, surviving ETF universe, zero-interest cash, and finite-sample risk estimates remain research limitations, documented in the README and methodology.
