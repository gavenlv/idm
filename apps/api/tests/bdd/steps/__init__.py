"""Step definitions package.

Each module exports @given/@when/@then functions, all auto-loaded by
`tests/bdd/__init__.py`. Keeping them split by domain makes them easier
to find.
"""
from . import common_steps  # noqa: F401
