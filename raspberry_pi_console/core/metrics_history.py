# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sqlite3
import time

from raspberry_pi_console.core.paths import app_base_dir

_DB_PATH = "metrics_history.db"
_MAX_ROWS = 2000


def _db_path() -> str:
    return os.path.join(app_base_dir(), "metrics_history.db")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host TEXT NOT NULL,
            ts REAL NOT NULL,
            cpu REAL,
            mem REAL,
            disk REAL,
            temp REAL,
            swap REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_host_ts ON metrics(host, ts)")
    return conn


def record_snapshot(host: str, data: dict) -> None:
    if not host:
        return
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO metrics(host, ts, cpu, mem, disk, temp, swap)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                host,
                time.time(),
                float(data.get("cpu_percent") or 0),
                float(data.get("mem_percent") or 0),
                float(data.get("disk_percent") or 0),
                float(data.get("temp_value") or 0),
                float(data.get("swap_percent") or 0),
            ),
        )
        conn.execute(
            """
            DELETE FROM metrics
            WHERE host = ? AND id NOT IN (
                SELECT id FROM metrics WHERE host = ? ORDER BY ts DESC LIMIT ?
            )
            """,
            (host, host, _MAX_ROWS),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_series(host: str, limit: int = 120) -> list[dict]:
    if not host:
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT ts, cpu, mem, disk, temp, swap
            FROM metrics
            WHERE host = ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (host, limit),
        ).fetchall()
    finally:
        conn.close()
    rows.reverse()
    return [
        {
            "ts": row[0],
            "cpu": row[1],
            "mem": row[2],
            "disk": row[3],
            "temp": row[4],
            "swap": row[5],
        }
        for row in rows
    ]
