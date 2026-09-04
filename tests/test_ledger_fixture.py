"""Mechanical contract for the captured live-v1 ledger fixture."""

import hashlib
import sqlite3

from tests.ledger_fixture import build


LIVE_V1_SCHEMA_SHA256 = "f444ed8cb82db2e011ec6d76299d4f7ae4d2bc3153935c6bfceec6fefdf619d2"


def schema_rows(conn):
    return conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()


def schema_digest(rows):
    payload = "\n".join(
        "|".join((kind, name, " ".join(sql.split())))
        for kind, name, sql in rows
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def test_build_matches_captured_live_v1_schema(tmp_path):
    conn = sqlite3.connect(build(tmp_path / "ledger.sqlite"))
    rows = schema_rows(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert schema_digest(rows) == LIVE_V1_SCHEMA_SHA256
    assert {kind: sum(row[0] == kind for row in rows) for kind in ("table", "index", "trigger")} == {
        "table": 11,
        "index": 9,
        "trigger": 9,
    }
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_build_seeds_required_v1_rows(tmp_path):
    conn = sqlite3.connect(build(tmp_path / "ledger.sqlite"))
    counts = {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "objectives", "plans", "observations", "tasks", "flights", "artifacts",
            "landings", "messages", "gates", "events", "leases",
        )
    }

    assert counts == {
        "objectives": 1,
        "plans": 1,
        "observations": 1,
        "tasks": 1,
        "flights": 1,
        "artifacts": 0,
        "landings": 0,
        "messages": 2,
        "gates": 1,
        "events": 0,
        "leases": 0,
    }
    assert conn.execute(
        "SELECT count(*) FROM messages WHERE delivered_at IS NOT NULL"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM messages WHERE delivered_at IS NULL"
    ).fetchone()[0] == 1
    conn.close()
