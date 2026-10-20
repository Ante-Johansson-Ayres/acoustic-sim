"""scripts/characterise.py: the noise models behind N1 and N2 (no hardware)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
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


def test_positions_and_direction_sets():
    az, el, r = characterise.position_angles(1.0, 1.0, 0.0)
    assert (az, el, r) == pytest.approx((45.0, 0.0, 2 ** 0.5))
    assert characterise.position_angles(0.0, 2.0, 2.0)[1] == pytest.approx(45.0)
    assert characterise.parse_positions("-30,0; 0,7.5") == [(-30.0, 0.0), (0.0, 7.5)]
    assert characterise.axis_spec("azimuth", 2.0, -1.0, 1.0, 24.0) == (-22.0, 26.0, 49, -1.0, -1.0, 1)
    assert characterise.axis_spec("elevation", 0.0, 0.0, 2.0, 48.0)[3:] == (-48.0, 48.0, 49)


def test_theory_template_peaks_at_the_speaker():
    contract = characterise.load_beam_contract()
    rel, db = characterise.template(contract, (10.0, 0.0), "azimuth", [2000.0], None)
    assert rel[int(np.argmax(db))] == pytest.approx(0.0, abs=0.05)


def test_live_prediction_is_exact_on_a_bank_beam_and_symmetric():
    contract = characterise.load_beam_contract()
    bank = characterise.find(json.loads(Path(characterise.default_contract_path()).read_text(encoding="utf-8")), "bank")
    az, el = characterise.bank_prediction(bank, contract, 0.0, 0.0, None)
    assert (az, el) == pytest.approx((0.0, 0.0), abs=1e-6)
    left = characterise.bank_prediction(bank, contract, -7.5, 0.0, None)
    right = characterise.bank_prediction(bank, contract, 7.5, 0.0, None)
    assert left[0] == pytest.approx(-right[0], abs=1e-6)
    assert 0.0 < right[0] < 15.0


def test_decay_time_of_a_synthetic_room():
    rate, rt60, stop = 48000, 0.5, 1.5
    t = np.arange(int(4.0 * rate)) / rate
    noise = np.random.default_rng(1).normal(size=t.size)
    envelope = np.where(t < stop, 1.0, 10 ** (-3 * (t - stop) / rt60))
    x = noise * envelope + 1e-4 * np.random.default_rng(2).normal(size=t.size)
    result = characterise.decay_time(x, 1000.0, rate, stop)
    assert result["method"] == "T20"
    assert result["rt60_s"] == pytest.approx(rt60, rel=0.1)


def test_l90_and_summaries():
    assert characterise.l90(list(range(101))) == pytest.approx(10.0)
    mean, se = characterise.summarise([1.0, 3.0])
    assert (mean, se) == pytest.approx((2.0, 1.0))
