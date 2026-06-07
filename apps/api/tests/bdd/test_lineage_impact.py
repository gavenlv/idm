"""M3 Knowledge Graph BDD — lineage + impact analysis."""
from pytest_bdd import scenarios

from .steps import common_steps  # noqa: F401

scenarios("lineage_impact.feature")
