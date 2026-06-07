"""AI 1.0 (M2) BDD scenarios — glossary mapping, owner verify."""
from pytest_bdd import scenarios

from .steps import common_steps  # noqa: F401

scenarios("ai_governance.feature")
