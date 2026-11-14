"""Shared pytest setup (owned by An).

Contract tests receive the module under test through a fixture:

    dispatch_impl  -> the real ``dispatch`` package     (skipped until it exists)
    cabin_impl     -> the real ``cabin`` package
    cc_impl        -> the real ``control_center`` package

Each package must export ``create`` (contracts/interfaces.py).

Options:
    pytest --fakes      run only the tests marked ``fake_ok``, against fakes/ instead
                        (checks that the test harness itself works)
    pytest --fakes-all  run every contract test against fakes/; many are EXPECTED to fail
                        because the fakes skip behaviour. Used by An to find broken tests.
"""

import importlib
from types import ModuleType

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("contract")
    group.addoption(
        "--fakes",
        action="store_true",
        help="run the fake_ok contract tests against fakes/ instead of the real modules",
    )
    group.addoption(
        "--fakes-all",
        action="store_true",
        help="run every contract test against fakes/ (harness check; failures expected)",
    )


def _impl(request: pytest.FixtureRequest, real: str, fake: str) -> ModuleType:
    config = request.config
    if config.getoption("--fakes-all"):
        return importlib.import_module(fake)
    if config.getoption("--fakes"):
        if request.node.get_closest_marker("fake_ok") is None:
            pytest.skip("not checkable against fakes/")
        return importlib.import_module(fake)
    module = pytest.importorskip(real, reason=f"{real}/ does not exist yet")
    assert hasattr(module, "create"), (
        f"{real}/__init__.py must export create() (see contracts/interfaces.py)"
    )
    return module


@pytest.fixture
def dispatch_impl(request: pytest.FixtureRequest) -> ModuleType:
    return _impl(request, "dispatch", "fakes.dispatch")


@pytest.fixture
def cabin_impl(request: pytest.FixtureRequest) -> ModuleType:
    return _impl(request, "cabin", "fakes.cabin")


@pytest.fixture
def cc_impl(request: pytest.FixtureRequest) -> ModuleType:
    return _impl(request, "control_center", "fakes.control_center")
