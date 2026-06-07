"""Asset browsing BDD scenarios — auto-collected by pytest-bdd."""
from pytest_bdd import scenarios

# Import step definitions (side-effect: registers @given/@when/@then)
from .steps import common_steps  # noqa: F401

# This auto-creates a test function per Scenario in asset_browsing.feature
scenarios("asset_browsing.feature")
