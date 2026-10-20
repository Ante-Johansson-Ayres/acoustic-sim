"""scripts/characterise.py: the noise models behind N1 and N2 (no hardware)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import characterise  # noqa: E402


@pytest.fixture(scope="module")
def array():
    return characterise.Array()


def test_diffuse_noise_lies_between_independent_and_common(array):
    assert array.diffuse_sum([0]) == pytest.approx(1.0)
    everyone = list(range(len(array.positions)))
    total = array.diffuse_sum(everyone)
    assert len(everyone) < total < len(everyone) ** 2


def test_steering_does_not_change_noise_at_broadside(array):
    common, diffuse = array.steered(0.0, 0.0)
    assert common == pytest.approx(0.0, abs=1e-9)
    assert diffuse == pytest.approx(0.0, abs=1e-9)


def test_common_noise_drops_when_steered_away(array):
    common, diffuse = array.steered(30.0, 0.0)
    assert common < -6.0
    assert abs(diffuse) < 3.0


def test_find_reaches_nested_keys():
    assert characterise.find({"a": {"b": {"gain_addresses": [1, 2]}}}, "gain_addresses") == [1, 2]
    assert characterise.find({"a": 1}, "missing") is None
