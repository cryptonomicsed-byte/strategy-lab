"""P4-07: the hub reads the watchlist shape radar/build_watchlist.py writes.
Run: python -m pytest hub/test_radar_watchlist_shape.py"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import score  # noqa: E402


def _load(tmp_path, monkeypatch, data):
    p = tmp_path / "watchlist.json"
    p.write_text(json.dumps(data))
    monkeypatch.setattr(score, "RADAR_WATCHLIST", str(p))
    return score.load_watchlist()


def test_dict_shape_from_build_watchlist(tmp_path, monkeypatch):
    wl, src = _load(tmp_path, monkeypatch, {"schema_version": 1, "generated_at": "x", "meta": {},
                                            "watchlist": [{"address": "AbC", "conviction": 0.8}]})
    assert wl == {"abc": 0.8} and src.startswith("repo:")


def test_bare_list_still_works(tmp_path, monkeypatch):
    wl, _ = _load(tmp_path, monkeypatch, [{"address": "Z", "conviction": 0.6}])
    assert wl == {"z": 0.6}


def test_real_box_file_now_loads():
    path = "/opt/ares/strategy-lab/radar/watchlist.json"
    if not os.path.exists(path):
        import pytest
        pytest.skip("box file not present")
    old = score.RADAR_WATCHLIST
    score.RADAR_WATCHLIST = path
    try:
        wl, _ = score.load_watchlist()
    finally:
        score.RADAR_WATCHLIST = old
    assert len(wl) > 0
