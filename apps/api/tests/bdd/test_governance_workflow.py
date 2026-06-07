"""Governance workflow BDD scenarios — auto-collected by pytest-bdd."""
from pytest_bdd import scenarios

from .steps import common_steps  # noqa: F401

scenarios("governance_workflow.feature")
