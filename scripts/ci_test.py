"""Run the project test suite (used by CI).

On Windows the interpreter occasionally aborts with a fail-fast error (exit
code 0xC0000409) during C-extension finalization right after pytest has
already reported success -- a teardown race between PySide6/Qt and other
extension modules, not a test failure. It shows up on Python 3.11 and 3.13.
pytest's report is already fully flushed at that point, so on Windows we
leave via os._exit() and skip interpreter finalization entirely, giving CI
a deterministic exit code.
"""

from __future__ import annotations

import os
import sys

import pytest

# Match `python -m pytest` behaviour regardless of how this file is invoked:
# make the repository root importable for test modules and test helpers.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

code = pytest.main(sys.argv[1:])
sys.stdout.flush()
sys.stderr.flush()
if sys.platform == "win32":
    os._exit(code)
sys.exit(code)