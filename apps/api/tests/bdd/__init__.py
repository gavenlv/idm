"""BDD test runner: bridges pytest-bdd scenarios to the test framework.

Each scenario from `tests/bdd/features/*.feature` is bound to step
definitions in `tests/bdd/steps/*.py`. pytest-bdd auto-generates a
`test_<scenario_name>` function for each scenario.

Usage:
    pytest apps/api/tests/bdd/        # run all BDD scenarios
    pytest apps/api/tests/bdd/ -k asset   # filter by scenario name
"""
from __future__ import annotations

# Auto-import all step modules so pytest-bdd can collect @given/@when/@then
from .steps import common_steps  # noqa: F401
