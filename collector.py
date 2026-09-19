"""Public Binance Spot observations. Never authenticates or places orders."""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

SYMBOLS = ("BTCUSDT", "ETHUSDT")
PUBLIC_API = "https://data-api.binance.vision"
INTERVAL_SECONDS = {"1m": 60, "5m": 300}
HISTORY_LIMIT = 2016


def iso_time(now_ms: int) -> str:
    return dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc).isoformat()


def ema(values: list[float], period: int) -> float:
    if not values or period < 1:
        raise ValueError("EMA requires values and a positive period")
    value = values[0]
    alpha = 2 / (period + 1)
    for item in values[1:]:
        value = item * alpha + value * (1 - alpha)
    return value


def fetch_klines(symbol: str, interval: str) -> list:
    if symbol not in SYMBOLS or interval not in INTERVAL_SECONDS:
        raise ValueError("Unsupported symbol or interval")
    query = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": 60})
    request = urllib.request.Request(
        f"{PUBLIC_API}/api/v3/klines?{query}",
        headers={"User-Agent": "BinanceObserver/1.1", "Accept": "application/json"},
        method="GET",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Do not retry bans, rate limits, or regional/access restrictions.
            if exc.code not in (500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("Request attempts exhausted")


def observation(rows: Any, now_ms: int, interval: str) -> dict:
    if not isinstance(rows, list):
        raise ValueError("Expected an array of candles")
    closed: list[tuple[int, float]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            raise ValueError("Malformed candle")
        close_time = int(row[6])
        if close_time >= now_ms:
            continue
        price = float(row[4])
        if close_time <= 0 or not math.isfinite(price) or price <= 0:
            raise ValueError("Invalid candle values")
        if closed and close_time <= closed[-1][0]:
            raise ValueError("Candles must be strictly chronological")
        closed.append((close_time, price))
    if len(closed) < 40:
        raise ValueError("Need at least 40 closed candles")
    if now_ms - closed[-1][0] > INTERVAL_SECONDS[interval] * 3_000:
        raise ValueError("Market data is stale")
    prices = [price for _, price in closed]
    return {
        "price": prices[-1],
        "ema9": round(ema(prices[-30:], 9), 4),
        "ema21": round(ema(prices[-40:], 21), 4),
        "candle_close": closed[-1][0],
        "updated_at": iso_time(now_ms),
        "stale": False,
    }


def error_text(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"Market data HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "Market data connection failed"
    if isinstance(exc, (TimeoutError, OSError)):
        return "Market data request failed"
    if isinstance(exc, (ValueError, TypeError, KeyError, IndexError, OverflowError)):
        return "Invalid, insufficient, or stale market data"
    return "Observation failed"


def collect_snapshot(
    previous: dict | None = None,
    *,
    interval: str = "5m",
    now_ms: int | None = None,
    fetcher: Callable[[str, str], Any] | None = None,
) -> dict:
    if interval not in INTERVAL_SECONDS:
        raise ValueError("Unsupported interval")
    previous = previous if isinstance(previous, dict) else {}
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    fetcher = fetch_klines if fetcher is None else fetcher
    symbols: dict[str, Any] = {}
    errors: dict[str, str] = {}
    successes = 0
    old_symbols = previous.get("symbols")
    old_symbols = old_symbols if isinstance(old_symbols, dict) else {}
    for symbol in SYMBOLS:
        try:
            symbols[symbol] = observation(fetcher(symbol, interval), now_ms, interval)
            successes += 1
        except Exception as exc:
            errors[symbol] = error_text(exc)
            old = old_symbols.get(symbol)
            if isinstance(old, dict) and previous.get("interval", "5m") == interval:
                symbols[symbol] = copy.deepcopy(old)
                symbols[symbol]["stale"] = True
    return {
        "updated_at": iso_time(now_ms) if successes else previous.get("updated_at"),
        "last_attempt_at": iso_time(now_ms),
        "status": "ok" if successes == len(SYMBOLS) else ("partial" if successes else "error"),
        "mode": "OBSERVE_ONLY",
        "execution": "NOT_IMPLEMENTED",
        "source": PUBLIC_API,
        "interval": interval,
        "stale_after_seconds": 1200 if interval == "5m" else 180,
        "symbols": symbols,
        "errors": errors,
    }


def load_snapshot(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, UnicodeError):
        return {}


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def fingerprint(snapshot: dict) -> tuple:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("symbols", {}), dict):
        return ()
    return (snapshot.get("interval"), tuple(
        (symbol, value.get("candle_close"))
        for symbol, value in sorted(snapshot.get("symbols", {}).items())
        if isinstance(value, dict) and not value.get("stale")
    ))


def write_outputs(snapshot: dict, directory: Path, history_limit: int = HISTORY_LIMIT) -> None:
    if history_limit < 1:
        raise ValueError("history_limit must be positive")
    history = directory / "history.jsonl"
    lines = history.read_text(encoding="utf-8").splitlines() if history.exists() else []
    last = {}
    if lines:
        try:
            last = json.loads(lines[-1])
        except json.JSONDecodeError:
            pass
    if snapshot["status"] in ("ok", "partial") and fingerprint(last) != fingerprint(snapshot):
        lines.append(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
    # Create the file even on first-run failure so the workflow can stage it safely.
    atomic_write(history, "\n".join(lines[-history_limit:]) + ("\n" if lines else ""))
    atomic_write(directory / "data.json", json.dumps(snapshot, ensure_ascii=False,
                 allow_nan=False, indent=2) + "\n")


def main() -> int:
    directory = Path(__file__).resolve().parent / "docs"
    snapshot = collect_snapshot(load_snapshot(directory / "data.json"))
    write_outputs(snapshot, directory)
    print(json.dumps({"status": snapshot["status"], "updated_at": snapshot["updated_at"],
                      "errors": snapshot["errors"]}))
    return 0 if snapshot["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
