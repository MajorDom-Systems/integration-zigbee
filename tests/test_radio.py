"""Unit tests for backend-agnostic radio selection (majordom_zigbee.radio)."""

import sys
import types
from unittest.mock import AsyncMock

import pytest

from majordom_zigbee import radio as radio_mod


def _fake_radio_module(monkeypatch, dotted_path: str, probe_result):
    """Install a fake module exposing a ControllerApplication with a stubbed async probe()."""
    module_path, _, cls_name = dotted_path.rpartition(".")

    app_cls = type(cls_name, (), {"probe": AsyncMock(return_value=probe_result)})
    module = types.ModuleType(module_path)
    setattr(module, cls_name, app_cls)
    # register the (possibly dotted) module so importlib.import_module finds it
    parts = module_path.split(".")
    for i in range(1, len(parts) + 1):
        name = ".".join(parts[:i])
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name) or types.ModuleType(name))
    monkeypatch.setitem(sys.modules, module_path, module)
    return app_cls


@pytest.mark.asyncio
async def test_pinned_radio_returns_that_backend(monkeypatch):
    ezsp = next(r for r in radio_mod.RADIOS if r.key == "ezsp")
    app = _fake_radio_module(monkeypatch, ezsp.application, probe_result=True)
    resolved = await radio_mod.resolve_application({"path": "/dev/ttyUSB0"}, requested="ezsp")
    assert resolved is app


@pytest.mark.asyncio
async def test_alias_resolves(monkeypatch):
    ezsp = next(r for r in radio_mod.RADIOS if r.key == "ezsp")
    app = _fake_radio_module(monkeypatch, ezsp.application, probe_result=True)
    resolved = await radio_mod.resolve_application({"path": "/dev/x"}, requested="SkyConnect")
    assert resolved is app


@pytest.mark.asyncio
async def test_unknown_radio_raises(monkeypatch):
    with pytest.raises(ValueError, match="Unknown zigbee radio"):
        await radio_mod.resolve_application({"path": "/dev/x"}, requested="nope")


@pytest.mark.asyncio
async def test_env_var_pins_radio(monkeypatch):
    znp = next(r for r in radio_mod.RADIOS if r.key == "znp")
    app = _fake_radio_module(monkeypatch, znp.application, probe_result=True)
    monkeypatch.setenv(radio_mod.RADIO_ENV, "znp")
    resolved = await radio_mod.resolve_application({"path": "/dev/x"}, requested=None)
    assert resolved is app


@pytest.mark.asyncio
async def test_auto_probe_picks_first_matching(monkeypatch):
    # ezsp probe fails (raises), znp probe matches -> znp is chosen.
    ezsp = next(r for r in radio_mod.RADIOS if r.key == "ezsp")
    znp = next(r for r in radio_mod.RADIOS if r.key == "znp")
    ezsp_app = _fake_radio_module(monkeypatch, ezsp.application, probe_result=False)
    ezsp_app.probe = AsyncMock(side_effect=RuntimeError("wrong protocol"))
    znp_app = _fake_radio_module(monkeypatch, znp.application, probe_result=True)
    # make sure no other radio libs are importable so the loop only sees our two fakes
    for r in radio_mod.RADIOS:
        if r.key not in ("ezsp", "znp"):
            monkeypatch.setitem(sys.modules, r.application.rpartition(".")[0], None)
    monkeypatch.delenv(radio_mod.RADIO_ENV, raising=False)
    resolved = await radio_mod.resolve_application({"path": "/dev/x"}, requested="auto")
    assert resolved is znp_app
