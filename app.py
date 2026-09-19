#!/usr/bin/env python3
"""Small read-only dashboard server; importing this module starts no services."""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from collector import collect_snapshot, error_text

ROOT = Path(__file__).resolve().parent


class Observer:
    def __init__(self, database: str = "/tmp/observer.sqlite3", *, fetcher: Callable | None = None):
        if database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.fetcher = fetcher
        self.snapshot: dict[str, Any] = {
            "mode": "OBSERVE_ONLY", "execution": "NOT_IMPLEMENTED", "interval": "1m",
            "status": "waiting", "updated_at": None, "last_attempt_at": None,
            "stale_after_seconds": 180, "symbols": {}, "errors": {},
        }
        self.db.execute("create table if not exists ticks(ts integer,symbol text,price real,signal text,reason text,primary key(ts,symbol))")
        self.db.execute("create table if not exists events(ts integer,level text,message text)")
        self.db.commit()

    def refresh(self, now_ms: int | None = None) -> dict:
        snapshot = collect_snapshot(self.read(), interval="1m", now_ms=now_ms, fetcher=self.fetcher)
        with self.lock:
            for symbol, value in snapshot["symbols"].items():
                if value.get("stale"):
                    continue
                reason = f"EMA9={value['ema9']}, EMA21={value['ema21']} — observation only"
                self.db.execute("insert or ignore into ticks values(?,?,?,?,?)",
                                (value["candle_close"], symbol, value["price"], "מעקב", reason))
            if snapshot["errors"]:
                self.db.execute("insert into events values(?,?,?)", (int(time.time()), "ERROR",
                                json.dumps(snapshot["errors"])))
            self.db.execute("delete from ticks where rowid not in (select rowid from ticks order by ts desc limit 10000)")
            self.db.execute("delete from events where rowid not in (select rowid from events order by rowid desc limit 1000)")
            self.db.commit()
            self.snapshot = snapshot
        return self.read()

    def read(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.snapshot)

    def rows(self) -> list:
        with self.lock:
            return self.db.execute("select ts,symbol,price,signal from ticks order by ts desc,symbol limit 50").fetchall()

    def state(self) -> dict:
        snapshot = self.read()
        updated_at = snapshot.get("updated_at")
        last_update = int(dt.datetime.fromisoformat(updated_at).timestamp()) if updated_at else 0
        return {
            "running": bool(self.thread and self.thread.is_alive()),
            "last_update": last_update,
            "error": "; ".join(snapshot["errors"].values()) or None,
            "prices": {s: v["price"] for s, v in snapshot["symbols"].items()},
            "snapshot": snapshot,
        }

    def health(self) -> dict:
        snapshot = self.read()
        cutoff = int(time.time() * 1000) - snapshot["stale_after_seconds"] * 1000
        data_ok = snapshot["status"] == "ok" and all(
            int(value["candle_close"]) > cutoff and not value.get("stale")
            for value in snapshot["symbols"].values()
        )
        return {"ok": True, "data_ok": data_ok, "status": snapshot["status"],
                "mode": "OBSERVE_ONLY", "execution": "NOT_IMPLEMENTED",
                "account_access": False, "last_attempt_at": snapshot["last_attempt_at"]}

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        def work() -> None:
            while not self.stop.is_set():
                try:
                    self.refresh()
                except Exception as exc:
                    with self.lock:
                        self.snapshot["status"] = "error"
                        self.snapshot["errors"]["runtime"] = error_text(exc)
                self.stop.wait(15)
        self.thread = threading.Thread(target=work, name="market-observer", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=75)
        with self.lock:
            self.db.close()


def make_handler(observer: Observer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def respond(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            for name, value in {
                "Content-Type": content_type, "Content-Length": str(len(body)),
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            }.items():
                self.send_header(name, value)
            if status == 405:
                self.send_header("Allow", "GET, HEAD")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/favicon.ico":
                self.respond(204, b"", "image/x-icon")
                return
            if path == "/":
                self.respond(200, (ROOT / "docs" / "index.html").read_bytes(), "text/html; charset=utf-8")
                return
            routes = {
                "/data.json": observer.read, "/api/state": observer.state,
                "/api/health": observer.health, "/api/log": lambda: {"rows": observer.rows()},
            }
            if path == "/history.jsonl":
                body = "".join(json.dumps({"candle_close": row[0], "symbol": row[1],
                                         "price": row[2], "mode": "OBSERVE_ONLY"}) + "\n"
                               for row in observer.rows()).encode()
                self.respond(200, body, "application/x-ndjson; charset=utf-8")
                return
            if path not in routes:
                self.respond(404, b'{"error":"not found"}')
                return
            self.respond(200, json.dumps(routes[path](), ensure_ascii=False, allow_nan=False).encode())

        do_HEAD = do_GET

        def do_POST(self) -> None:
            self.respond(405, b'{"error":"observe only"}')

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST
    return Handler


def main() -> None:
    observer = Observer(os.getenv("DB_PATH", "/tmp/observer.sqlite3"))
    server = ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8000"))), make_handler(observer))
    observer.start()
    try:
        print(f"Binance Observer listening on port {server.server_port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        observer.close()


if __name__ == "__main__":
    main()
