"""Unit tests for backend-agnostic radio selection (majordom_zigbee.radio)."""

import importlib.util
import sys
import types
from unittest.mock import AsyncMock

import pytest
from majordom_integration_sdk import Endpoint

from majordom_zigbee import radio as radio_mod
from majordom_zigbee.controller import ZigBeeController


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


def test_the_dialect_names_its_radio_library(monkeypatch):
    ezsp = next(r for r in radio_mod.RADIOS if r.key == "ezsp")
    app = _fake_radio_module(monkeypatch, ezsp.application, probe_result=True)
    assert radio_mod.application_class("ezsp") is app


def test_a_radio_library_missing_says_what_to_install(monkeypatch):
    deconz = next(r for r in radio_mod.RADIOS if r.key == "deconz")
    monkeypatch.setitem(sys.modules, deconz.application.rpartition(".")[0], None)
    with pytest.raises(RuntimeError, match=r"majordom-zigbee\[deconz\]"):
        radio_mod.application_class("deconz")


@pytest.mark.asyncio
async def test_the_driver_names_the_first_radio_that_answers(monkeypatch):
    # deconz probe fails (raises), znp probe matches -> znp is chosen.
    deconz = next(r for r in radio_mod.RADIOS if r.key == "deconz")
    znp = next(r for r in radio_mod.RADIOS if r.key == "znp")
    deconz_app = _fake_radio_module(monkeypatch, deconz.application, probe_result=False)
    deconz_app.probe = AsyncMock(side_effect=RuntimeError("wrong protocol"))
    _fake_radio_module(monkeypatch, znp.application, probe_result=True)
    # make sure no other radio libs are importable so the loop only sees our two fakes
    for r in radio_mod.RADIOS:
        if r.key not in ("deconz", "znp"):
            monkeypatch.setitem(sys.modules, r.application.rpartition(".")[0], None)
    driver = radio_mod.ZigpyDriver()
    assert await driver.identify("/dev/x") == "znp"
    assert driver.serves("znp", "/dev/x") == [Endpoint("zigbee", "znp", "/dev/x")]


@pytest.mark.asyncio
async def test_the_driver_probes_the_cheapest_radios_first_and_ezsp_last(monkeypatch):
    probed: list[str] = []
    for radio in radio_mod.RADIOS:
        app = _fake_radio_module(monkeypatch, radio.application, probe_result=False)
        app.probe = AsyncMock(side_effect=lambda _config, key=radio.key: probed.append(key))
    driver = radio_mod.ZigpyDriver()
    assert await driver.identify("/dev/x") is None
    # ziggurat is a server's WebSocket, never a stick; ezsp last: the SDK's Silabs driver tried the stick first
    assert probed == ["deconz", "zigate", "xbee", "blz", "znp", "ezsp"]
    assert list(driver.firmwares) == probed


def test_the_need_takes_every_zigpy_dialect_only():
    (need,) = ZigBeeController.needs
    assert all(need.accepts(Endpoint("zigbee", r.key, "/dev/x")) for r in radio_mod.RADIOS)
    assert not need.accepts(Endpoint("zigbee", "zigbee2mqtt", "mqtt://127.0.0.1"))


def test_the_need_takes_what_the_usb_driver_serves():
    (need,) = ZigBeeController.needs
    served = [e for r in radio_mod.PROBED for e in radio_mod.ZigpyDriver().serves(r.key, "/dev/x")]
    assert [e.dialect for e in served] == [r.key for r in radio_mod.PROBED]
    assert all(need.accepts(e) for e in served)


@pytest.mark.parametrize("radio", radio_mod.RADIOS, ids=lambda r: r.key)
def test_installed_radio_libraries_load(radio):
    if importlib.util.find_spec(radio.application.partition(".")[0]) is None:
        pytest.skip(f"{radio.package} is not installed")
    assert radio_mod._load(radio) is not None
