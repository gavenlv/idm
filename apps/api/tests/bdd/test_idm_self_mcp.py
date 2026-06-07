"""M5 idm-self MCP BDD — External Agent access."""
from pytest_bdd import scenarios

from .steps import common_steps  # noqa: F401

scenarios("idm_self_mcp.feature")
