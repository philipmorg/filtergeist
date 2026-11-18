#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Executable entrypoint for Filtergeist CLI and local Studio web GUI."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from filtergeist.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
