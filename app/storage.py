"""SQLite persistence: sessions, full-information hand records, Claude call log.

Hands are stored as JSON (players + hole cards, actions, result) so the stats and
mistake-finder steps can replay them without a schema migration.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hands.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    hand_no INTEGER NOT NULL,
    started_at REAL NOT NULL,
    n_players INTEGER NOT NULL,
    hero_position TEXT,
    hero_net INTEGER,
    pot INTEGER,
    showdown INTEGER,
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS hands_session ON hands(session_id);
CREATE TABLE IF NOT EXISTS claude_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    hand_no INTEGER,
    seat INTEGER,
    created_at REAL NOT NULL,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read INTEGER,
    cache_write INTEGER,
    cost_usd REAL,
    error TEXT,
    reason TEXT
);
"""


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def new_session(self, config: dict) -> int:
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT INTO sessions (started_at, config) VALUES (?, ?)",
                                    (time.time(), json.dumps(config)))
            return cur.lastrowid

    def save_hand(self, session_id: int, record: dict) -> None:
        hero = next((p for p in record["players"] if p["seat"] == 0), None)
        result = record["result"]
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO hands (session_id, hand_no, started_at, n_players, hero_position, hero_net, pot, showdown, record)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, record["number"], record["started_at"], len(record["players"]),
                 hero["position"] if hero else None, result["net"].get(0) if hero else None,
                 result["pot"], int(result["showdown"]), json.dumps(record)),
            )

    def save_claude_call(self, session_id: int, hand_no: int, seat: int, usage: dict, meta: dict) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO claude_calls (session_id, hand_no, seat, created_at, model, input_tokens, output_tokens,"
                " cache_read, cache_write, cost_usd, error, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, hand_no, seat, time.time(), usage.get("model"), usage.get("input_tokens", 0),
                 usage.get("output_tokens", 0), usage.get("cache_read", 0), usage.get("cache_write", 0),
                 usage.get("cost_usd", 0.0), usage.get("error"), meta.get("reason")),
            )

    def hand_rows(self, session_id: int | None = None) -> list[tuple[int, int, dict]]:
        """(hand id, session id, record) oldest first; all sessions when session_id is None."""
        sql = "SELECT id, session_id, record FROM hands"
        args: tuple = ()
        if session_id is not None:
            sql += " WHERE session_id = ?"
            args = (session_id,)
        with self.lock:
            rows = self.conn.execute(sql + " ORDER BY id", args).fetchall()
        return [(r[0], r[1], json.loads(r[2])) for r in rows]

    def hand_records(self, heads_up: bool) -> list[dict]:
        """Every stored hand of one format (heads-up or 3+ players), oldest first."""
        sql = "SELECT record FROM hands WHERE n_players " + ("= 2" if heads_up else "> 2") + " ORDER BY id"
        with self.lock:
            rows = self.conn.execute(sql).fetchall()
        return [json.loads(r[0]) for r in rows]

    def hand(self, hand_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT record FROM hands WHERE id = ?", (hand_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest_session(self) -> int | None:
        with self.lock:
            row = self.conn.execute("SELECT MAX(session_id) FROM hands").fetchone()
        return row[0]

    def recent_hands(self, session_id: int, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT record FROM hands WHERE session_id = ? ORDER BY id DESC LIMIT ?", (session_id, limit)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]
