#!/usr/bin/env python3
"""Windows entry point: use the native Windows runtime instead of the macOS socket one."""
from __future__ import annotations

import sys

import pet_runtime_win32

sys.modules.setdefault("pet_runtime", pet_runtime_win32)

from pet_app import main

if __name__ == "__main__":
    raise SystemExit(main())
