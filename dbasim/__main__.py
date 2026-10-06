"""Lets `python -m dbasim` work when the `dbasim` script is not on PATH."""
import sys

from .cli import main

sys.exit(main())
