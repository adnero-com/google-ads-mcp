"""Every test runs with a plan key (writes are refused without one) and a fresh record of applied plans.

Tests about where the key comes from opt out with @pytest.mark.real_plan_key.
"""

import pytest

from ads_mcp import plans


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_plan_key: read the plan key from the environment"
    )


@pytest.fixture(autouse=True)
def _plan_key(request, monkeypatch):
    plans.reset()
    if request.node.get_closest_marker("real_plan_key") is None:
        monkeypatch.setattr(plans, "_secret", lambda: b"t" * 40)
    yield
    plans.reset()
