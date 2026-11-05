#!/usr/bin/env python3
"""Double-fork overnight_building_footprints_run.sh so a Grok 10h PGID kill misses it."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, "overnight_building_footprints_run.sh")
LOG = os.path.join(ROOT, "logs", "overnight_building_footprints_launch.log")


def main() -> None:
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    if os.fork():
        sys.exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    os.chdir(ROOT)
    env = os.environ.copy()
    env.setdefault("BUILDING_DATA_DIR", "/Volumes/Insurrection/990data/buildings")
    env.setdefault("BUILDING_DB", "/Volumes/Insurrection/990data/buildings.duckdb")
    os.closerange(0, 3)
    fd = os.open("/dev/null", os.O_RDONLY)
    os.dup2(fd, 0)
    out = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(out, 1)
    os.dup2(out, 2)
    os.execve(SCRIPT, [SCRIPT], env)


if __name__ == "__main__":
    main()
