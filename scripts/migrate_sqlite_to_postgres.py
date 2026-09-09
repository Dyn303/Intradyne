#!/usr/bin/env python
"""Copy the three durable stores from SQLite into Postgres.

Run this *before* pointing ``DB_URL`` at Postgres, not after. Starting the
service against an empty Postgres works fine and is the dangerous outcome: the
drawdown guardrail reads an empty equity history, ``dd_30d([])`` is ``0.0``,
and a system that had just fallen 25% comes back believing its drawdown is zero
and resumes trading. That is the exact failure ``core/equity.py`` exists to
prevent, and switching backends is a new way to cause it.

The order-key table matters for the same kind of reason in the other direction:
an ``in_flight`` claim that does not make the trip stops being unreconciled, so
the restart check that should halt trading passes instead.

    python scripts/migrate_sqlite_to_postgres.py \\
        --source sqlite:////app/state/trades.sqlite \\
        --target postgresql://intradyne:...@postgres:5432/intradyne

Safe to run repeatedly against an empty target. It refuses a non-empty target
unless ``--replace`` is given, because these are append-only tables with no
natural key: re-running a plain copy would double every equity row and halve
every computed return. ``--replace`` truncates first and is the only
destructive path here; nothing touches the SQLite side, which stays exactly as
it was and remains the rollback.

Verify with ``--check`` after copying, or use ``--dry-run`` to see the counts
first without writing anything.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from typing import Any, Dict, List, Sequence, Tuple

# Import path for a script run from the repository root.
sys.path.insert(0, "src")

from intradyne.core.db import (  # noqa: E402
    backend_for,
    normalize_dsn,
    split_statements,
    sqlite_path_from_url,
)
from intradyne.core.equity import _SCHEMA as EQUITY_SCHEMA  # noqa: E402
from intradyne.core.idempotency import _SCHEMA as ORDER_KEY_SCHEMA  # noqa: E402
from intradyne.core.limits import _SCHEMA as NOTIONAL_SCHEMA  # noqa: E402

#: table -> (columns, schema). Column lists are explicit rather than
#: ``SELECT *`` so that a schema change on one side fails loudly here instead
#: of silently shifting values into the wrong columns.
TABLES: Dict[str, Tuple[Sequence[str], Dict[str, str]]] = {
    "equity_history": (("ts", "equity"), EQUITY_SCHEMA),
    "order_keys": (
        ("key", "symbol", "side", "qty", "ts", "status", "venue_id"),
        ORDER_KEY_SCHEMA,
    ),
    "traded_notional": (("ts", "symbol", "notional"), NOTIONAL_SCHEMA),
}

#: Rows per INSERT round trip. Large enough that a year of equity history is a
#: handful of statements, small enough not to build a multi-megabyte query.
BATCH = 500


def _sqlite_rows(
    path: str, table: str, columns: Sequence[str]
) -> List[Tuple[Any, ...]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = ", ".join(columns)
        try:
            return list(conn.execute(f"SELECT {names} FROM {table}"))
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise
    finally:
        conn.close()


def _count(conn: Any, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, help="sqlite:///... URL to read from")
    p.add_argument("--target", required=True, help="postgresql://... URL to write to")
    p.add_argument(
        "--replace",
        action="store_true",
        help="TRUNCATE the target tables first. Destructive; required to "
        "re-run against a non-empty target.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be copied and exit without writing.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Compare row counts on both sides and exit. No writes.",
    )
    args = p.parse_args(argv)

    if backend_for(args.source) != "sqlite":
        p.error(f"--source must be a sqlite URL, got {args.source!r}")
    if backend_for(args.target) != "postgres":
        p.error(f"--target must be a postgresql URL, got {args.target!r}")

    try:
        import psycopg
    except ImportError:
        print(
            "psycopg is not installed. pip install 'psycopg[binary,pool]'",
            file=sys.stderr,
        )
        return 2

    src_path = sqlite_path_from_url(args.source)
    source: Dict[str, List[Tuple[Any, ...]]] = {}
    for table, (columns, _schema) in TABLES.items():
        source[table] = _sqlite_rows(src_path, table, columns)

    with psycopg.connect(normalize_dsn(args.target)) as conn:
        # Create the tables if they are not there yet, so this can run against
        # a database the service has never started against.
        for _table, (_columns, schema) in TABLES.items():
            for statement in split_statements(schema["postgres"]):
                conn.execute(statement)
        conn.commit()

        existing = {table: _count(conn, table) for table in TABLES}

        if args.check:
            ok = True
            for table in TABLES:
                have, want = existing[table], len(source[table])
                mark = "ok " if have == want else "MISMATCH"
                if have != want:
                    ok = False
                print(f"{mark} {table}: sqlite={want} postgres={have}")
            return 0 if ok else 1

        for table in TABLES:
            print(
                f"{table}: {len(source[table])} rows in sqlite, {existing[table]} in postgres"
            )

        if args.dry_run:
            print("\n--dry-run: nothing written")
            return 0

        occupied = [t for t, n in existing.items() if n]
        if occupied and not args.replace:
            print(
                "\nRefusing to write: "
                + ", ".join(f"{t} already holds {existing[t]} rows" for t in occupied)
                + ".\nThese tables are append-only with no natural key, so copying "
                "again would\nduplicate every row rather than merge. Re-run with "
                "--replace to TRUNCATE\nthem first, or point --target at an empty "
                "database.",
                file=sys.stderr,
            )
            return 1

        if args.replace and occupied:
            for table in TABLES:
                conn.execute(f"TRUNCATE TABLE {table}")
            print("\ntruncated: " + ", ".join(TABLES))

        for table, (columns, _schema) in TABLES.items():
            rows = source[table]
            if not rows:
                continue
            names = ", ".join(columns)
            marks = ", ".join(["%s"] * len(columns))
            sql = f"INSERT INTO {table} ({names}) VALUES ({marks})"
            with conn.cursor() as cur:
                for i in range(0, len(rows), BATCH):
                    cur.executemany(sql, rows[i : i + BATCH])
            print(f"copied {len(rows)} rows into {table}")

        conn.commit()

        # Verify inside the same connection: a copy that reports success
        # without the rows being readable is the failure this whole script is
        # guarding against.
        failed = False
        for table in TABLES:
            have, want = _count(conn, table), len(source[table])
            if have != want:
                print(
                    f"VERIFY FAILED {table}: expected {want}, found {have}",
                    file=sys.stderr,
                )
                failed = True
        if failed:
            return 1
        print("\nverified: row counts match on both sides")

    print(
        "\nNow set DB_URL to the target and restart. The SQLite file is "
        "untouched;\nsetting DB_URL back to it is the rollback."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
