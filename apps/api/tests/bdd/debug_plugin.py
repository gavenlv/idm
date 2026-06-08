"""Debug plugin for BDD step discovery."""
import os
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ["APP_NAME"] = "idm-api"

import tests.bdd.conftest  # noqa: F401  registers step fixtures
import tests.bdd.steps.common_steps  # noqa: F401  same


def pytest_collection_finish(session):
    """After collection, inspect the fixture manager for BDD step fixtures."""
    fm = getattr(session, "_fixturemanager", None)
    if fm is None:
        fm = session.config.pluginmanager.get_plugin("funcmanage")
    if fm is None:
        print("[BDD DEBUG] no fixture manager found")
        return
    step_count = 0
    sample = []
    bdd_step_count = 0
    for name, defs in fm._arg2fixturedefs.items():
        for fd in defs:
            if hasattr(fd.func, "_pytest_bdd_step_context"):
                bdd_step_count += 1
            if name.startswith("pytestbdd_stepdef"):
                step_count += 1
                if len(sample) < 5:
                    sample.append(name)
    print(f"\n[BDD DEBUG] pytestbdd_stepdef fixtures: {step_count}")
    print(f"[BDD DEBUG] bdd step context: {bdd_step_count}")
    print(f"[BDD DEBUG] sample: {sample}\n")

    # Now check the common_steps module
    from tests.bdd.steps import common_steps
    module_fixtures = [k for k in common_steps.__dict__ if k.startswith("pytestbdd_stepdef")]
    print(f"[BDD DEBUG] common_steps module has {len(module_fixtures)} step fixtures")
    print(f"[BDD DEBUG] module sample: {module_fixtures[:3]}")

    # Check the first fixture
    if module_fixtures:
        k = module_fixtures[0]
        v = common_steps.__dict__[k]
        print(f"[BDD DEBUG] fixture[{k}] = {v!r}")
        print(f"[BDD DEBUG] type: {type(v).__name__}")
        print(f"[BDD DEBUG] attrs: {[a for a in dir(v) if not a.startswith('__')][:10]}")
