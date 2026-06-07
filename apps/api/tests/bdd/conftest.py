"""Local conftest for BDD tests.

The step definitions live under `tests/bdd/steps/`. We import them here
so that pytest-bdd registers all `@given` / `@when` / `@then` handlers
**before** it walks the auto-generated `test_*` functions from
`tests/bdd/test_*.py`.

Without this conftest, pytest-bdd tries to resolve steps at collection
time but the `from .steps import common_steps` line in the test
modules runs **after** step resolution, leading to:

    StepDefinitionNotFoundError: Given "the IDM API is running"
"""
from __future__ import annotations

# Side-effect import: register all step handlers
from .steps import common_steps  # noqa: F401
