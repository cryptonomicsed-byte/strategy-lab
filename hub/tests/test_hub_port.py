"""P1-07: hub/score.py keeps HUB_BIND_HOST and gains HUB_PORT."""
import importlib.util
import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("hub_score", HERE / "score.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_port_is_8010(monkeypatch):
    monkeypatch.delenv("HUB_PORT", raising=False)
    assert _load().hub_port() == 8010


def test_hub_port_from_env(monkeypatch):
    monkeypatch.setenv("HUB_PORT", "8012")
    assert _load().hub_port() == 8012


@pytest.mark.parametrize("bad", ["abc", "0", "70000"])
def test_bad_hub_port_is_an_error(monkeypatch, bad):
    monkeypatch.setenv("HUB_PORT", bad)
    with pytest.raises(ValueError):
        _load().hub_port()


def test_cli_port_default_follows_env(monkeypatch):
    monkeypatch.setenv("HUB_PORT", "8099")
    mod = _load()
    seen = {}
    monkeypatch.setattr(mod, "serve", lambda port: seen.setdefault("port", port))
    monkeypatch.setattr(sys, "argv", ["score.py", "--serve"])
    mod.main()
    assert seen["port"] == 8099
    monkeypatch.setattr(sys, "argv", ["score.py", "--serve", "--port", "8123"])
    seen.clear(); mod.main()
    assert seen["port"] == 8123
