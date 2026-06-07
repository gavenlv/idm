"""M4 ChatBI BDD — NL2SQL."""
from pytest_bdd import scenarios

from .steps import common_steps  # noqa: F401

scenarios("chatbi.feature")
