"""Prepare a v1-era data directory for the legacy rollback drill.

The real drill (old 1.1.x program reading a v1-era data dir) needs an
old program build, which must come from the controller.  This script
creates the OTHER half: a data directory whose todo store is built by
the SAME ``initialize_v1`` routine the pre-migration app used, so an
old EXE can be pointed at it via ``--data-dir``.

Usage:
    python scripts/prepare_v1_drill.py --output DIR [--tasks 5]

Contains ONLY synthetic tasks; never touches a daily-use data dir.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from retirement_pet.todo.migrations import initialize_v1  # noqa: E402


def build_v1_dir(output: Path, *, tasks: int = 5) -> Path:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        # OVR-03: the drill owns its directory outright - refuse any
        # pre-existing content (settings.json included) instead of
        # partially overwriting a directory that may hold real data
        raise SystemExit(
            f"refusing to write into non-empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    tasks_db = output / "tasks.db"
    conn = None
    created: list[Path] = []
    try:
        conn = sqlite3.connect(tasks_db)
        created.append(tasks_db)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        initialize_v1(conn)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for index in range(tasks):
            conn.execute(
                "INSERT INTO tasks (id, parent_id, title, horizon, status,"
                " sort_key, due_date, created_at, updated_at, completed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"v1-drill-{index:04d}", None,
                 f"回滚演练任务 {index}", "short", "open",
                 float(index), None, now, now, None),
            )
        conn.commit()
        conn.close()
        conn = None
        logs_dir = output / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        created.append(logs_dir)
        settings = output / "settings.json"
        # register BEFORE writing: a partial write that raises must
        # still be cleaned up (OVR-03 second round)
        created.append(settings)
        settings.write_text("{}", encoding="utf-8")
    except BaseException:
        # OVR-03: cover the ENTIRE build — a failure at any stage must
        # clean up every artifact this call created
        if conn is not None:
            conn.close()
        for path in created:
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        for suffix in ("-wal", "-shm"):
            Path(str(tasks_db) + suffix).unlink(missing_ok=True)
        raise
    print(f"v1-era data dir prepared: {output} ({tasks} synthetic tasks)")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tasks", type=int, default=5)
    args = parser.parse_args()
    build_v1_dir(args.output, tasks=args.tasks)
    print("drill steps:")
    print("  1. point the OLD program at this dir; it lists the tasks")
    print("  2. run the 1.2.0 candidate once (migrates v1->v2)")
    print("  3. point the OLD program back; confirm the rollback path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
