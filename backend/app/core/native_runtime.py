"""Bootstrap native Windows runtime DLL discovery before scientific imports."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_DLL_DIRECTORY = None

if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    environment_root = Path(sys.prefix)
    if (environment_root / "msvcp140.dll").is_file():
        # The msvc-runtime wheel installs here. Keep the handle alive for the
        # full process lifetime; closing it removes the directory immediately.
        _DLL_DIRECTORY = os.add_dll_directory(str(environment_root))
