# Binance Observer — Cloud

Observe-only Binance Spot market monitor. No API keys, no account access, and no order execution endpoints.

## Deploy
Dockerfile deployment. The server binds to 0.0.0.0 and reads PORT from the host.

## Health
GET /api/health returns execution=NOT_IMPLEMENTED.

## Persistence
The default DB is /tmp/observer.sqlite3. On hosts without persistent volumes, history resets after a redeploy/restart. Live monitoring resumes automatically.
