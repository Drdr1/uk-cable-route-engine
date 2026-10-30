#!/usr/bin/env python3
"""Emit the tail of a log as GitHub error annotations (visible on the run summary)."""
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
interesting = [l for l in lines if any(k in l for k in ("Error", "error", "FAILED", "assert", "Traceback", "WARNING", "unavailable", "File \""))]
tail = (interesting[-60:] if interesting else []) + ["----- last lines -----"] + lines[-60:]
for n, start in enumerate(range(0, len(tail), 40), start=1):
    chunk = "\n".join(tail[start:start + 40]).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title=Log part {n}::{chunk}")
