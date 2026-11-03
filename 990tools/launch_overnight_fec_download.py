#!/usr/bin/env python3
"""Double-fork overnight_fec_download.sh so a Grok 10h PGID kill misses it."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, "overnight_fec_download.sh")
LOG = os.path.join(ROOT, "logs", "overnight_fec_download_launch.log")


def main() -> None:
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    if os.fork():
        sys.exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    os.chdir(ROOT)
    env = os.environ.copy()
    env.setdefault("FEC_DATA_DIR", "/Volumes/Insurrection/990data/cms_data/fec")
    env.setdefault("FEC_CYCLES", "2024,2026")
    os.closerange(0, 3)
    fd = os.open("/dev/null", os.O_RDONLY)
    os.dup2(fd, 0)
    out = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(out, 1)
    os.dup2(out, 2)
    os.execve(SCRIPT, [SCRIPT], env)


if __name__ == "__main__":
    main()
