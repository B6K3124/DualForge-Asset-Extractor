"""Support ``python -m dualforge`` as an alias for the CLI entry point."""

from __future__ import annotations

import sys

from dualforge.cli import main


if __name__ == "__main__":
    sys.exit(main())