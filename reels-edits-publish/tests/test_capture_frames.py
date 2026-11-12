"""Offline tests of the frame helpers: time parsing, spacing, the zone filter."""

from __future__ import annotations

import pytest

from capture_frames import evenly_spaced, parse_times, zones_filter


def test_parse_times() -> None:
    assert parse_times("0, 1.5,10") == [0.0, 1.5, 10.0]
    with pytest.raises(ValueError):
        parse_times(" , ")
    with pytest.raises(ValueError):
        parse_times("-1")
    with pytest.raises(ValueError):
        parse_times("a")


def test_evenly_spaced() -> None:
    assert evenly_spaced(10.1, 3) == [0.0, 5.0, 10.0]
    assert evenly_spaced(10.0, 1) == [0.0]


def test_zones_filter_uses_fractions_of_1080x1920() -> None:
    text = zones_filter()
    assert text.count("drawbox") == 3
    assert "h=ih*0.0599" in text  # 115 / 1920
    assert "y=ih*0.7917" in text  # 1520 / 1920
    assert "x=iw*0.8796" in text  # 950 / 1080
    assert "y=ih*0.5625" in text and "h=ih*0.3333" in text  # 1080..1720
