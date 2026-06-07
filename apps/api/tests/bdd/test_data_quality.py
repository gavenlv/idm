"""M4 Data Quality BDD — dashboard, rules, profiler, insight."""
from pytest_bdd import scenarios

from .steps import common_steps  # noqa: F401

scenarios("data_quality.feature")
