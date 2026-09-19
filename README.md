# Binance Observer

Read-only monitoring of public Binance Spot candles for BTCUSDT and ETHUSDT.
There are **no API keys, no account access, and no order execution endpoints**.
Prices shown are the latest **closed candle** prices, not live trade quotes.

## Run the server

Python 3.12 or newer; no third-party runtime dependencies:

```sh
python app.py
```

Open `http://localhost:8000`. `PORT` defaults to `8000`; the server binds to
`0.0.0.0`. The worker attempts collection every 15 seconds using one-minute
candles. The Hebrew dashboard refreshes every minute and has a manual refresh
button. Refreshing the page does not place an order or force a new collection.

```sh
docker build -t binance-observer .
docker run --rm -p 8000:8000 binance-observer
```

The Docker image includes both Python modules and the HTML and runs as a
non-root user. It has not been assumed to be deployed merely because it builds.

### Persistence

`DB_PATH` defaults to `/tmp/observer.sqlite3`. A restart on an ephemeral host can
lose history. For durable server history, point `DB_PATH` at a writable persistent
volume. The server retains at most 10,000 observation rows and 1,000 error events.
The latest 50 observations are exposed by `/api/log` and `/history.jsonl`.

### Health and read-only endpoints

`GET /api/health` separates process liveness (`ok`) from fresh, complete market
data (`data_ok`). A running web server does not by itself prove that collection
works. `execution` remains `NOT_IMPLEMENTED`.

`GET /data.json` supplies the dashboard. `/api/state` preserves the legacy state
interface and includes the detailed snapshot. Unknown paths return 404.
POST, PUT, PATCH and DELETE return 405, including requests to order-like paths.

## Scheduled collection and static dashboard

`python collector.py` collects five-minute candles and atomically writes
`docs/data.json` plus a bounded, deduplicated `docs/history.jsonl` (2,016 snapshots).
The existing ten-minute GitHub Actions schedule is preserved. Pushes of relevant
code to `main` and manual dispatch also trigger collection. Jobs run regression
tests first, serialize updates and publish an artifact, even when the upstream
request fails. Partial or failed collection marks the workflow as failed rather
than silently reporting success.

A failed symbol retains its last good values and original timestamp, with a
stale flag. A complete failure does not advance the last successful update time.
The UI separately marks ageing data even when the latest stored status was OK.
Transient connection/server failures have bounded retries; rate limits, bans,
authentication failures and geographic restrictions are not bypassed or retried.

### Website publishing is a separate step

The repository was private and GitHub Pages was disabled when this work began.
These changes do **not** enable Pages, alter repository visibility, buy a plan or
change an existing production deployment.

For an account/repository eligible for Pages, configure its publishing source as
**GitHub Actions**. `pages.yml` deploys `docs/` after the observation workflow
completes, but only if Pages is already enabled. It can also be run manually.
This is intentional: commits made with `GITHUB_TOKEN` do not themselves trigger
branch-based Pages builds. The published dashboard contains public market data;
repository privacy and website visibility are different settings.

A scheduled workflow is not a continuously running server and can be delayed.
Private-repository Actions runs use the account's available minutes; this project
does not make a guarantee of free or uninterrupted hosting.

## Tests and verification

```sh
python -m unittest discover -s tests -v
```

28 regression tests cover EMA calculation, candle validation, closed-candle
filtering, partial/total failures, stale timestamps, atomic output, history
limits/deduplication, HTTP routing and rejected write methods.

The CI workflow separately runs these tests, a real public-data connection check,
and browser checks. Browser checks use **synthetic candles**, clearly labelled
in screenshots; they are not proof of live market connectivity.

```sh
pip install playwright==1.57.0
python -m playwright install chromium
python tests/browser_smoke.py
```

Set `CHROMIUM_PATH` to use an existing browser. Browser artifacts record desktop
rendering, refresh behaviour, mobile layout, stale/error/empty states and
uncaught JavaScript errors. See the actual CI result before asserting these pass.

## API references

- [Binance market-data-only endpoints](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md)
- [GitHub Pages publishing](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
- [GitHub Actions hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
