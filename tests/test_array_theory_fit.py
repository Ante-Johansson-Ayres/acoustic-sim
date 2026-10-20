"""array_theory: band-averaged steered response, directivity index and the cut fit."""
from __future__ import annotations

import numpy as np
import pytest

from array_theory import band_frequencies, beamwidth, directivity_index_db, fit_cut, steered_response
from heimdall_acoustic import load_beam_contract


@pytest.fixture(scope="module")
def contract():
    return load_beam_contract()


@pytest.fixture(scope="module")
def cut_2k(contract):
    angles = np.arange(-60.0, 60.001, 0.05)
    power = steered_response(contract, 0.0, 0.0, angles, 0.0, band_frequencies(2000.0))
    return angles, 10 * np.log10(power / power.max())


def test_third_octave_band_edges():
    f = band_frequencies(2000.0)
    assert f[0] == pytest.approx(1781.8, abs=0.5)
    assert f[-1] == pytest.approx(2244.9, abs=0.5)


def test_steered_response_on_axis_and_near_field(contract):
    assert steered_response(contract, 0.0, 0.0, 0.0, 0.0, [2000.0])[0] == pytest.approx(1.0)
    near = steered_response(contract, 0.0, 0.0, 0.0, 0.0, [2000.0], distance_m=2.0)[0]
    assert -0.2 < 10 * np.log10(near) < -0.05


def test_band_averaged_width_and_directivity(contract, cut_2k):
    angles, db = cut_2k
    width = beamwidth([{"angle_deg": a, "gain_db": g} for a, g in zip(angles, db)])["width_deg"]
    assert width == pytest.approx(21.1, abs=0.2)
    assert directivity_index_db(contract, 2000.0) == pytest.approx(14.7, abs=0.1)
    assert directivity_index_db(contract, 1000.0) == pytest.approx(10.05, abs=0.1)


def test_fit_recovers_shift_and_width(cut_2k):
    angles, db = cut_2k
    x = np.arange(-24.0, 25.0)
    noisy = np.interp((x - 1.3) * 1.05, angles, db) - 20 + np.random.default_rng(3).normal(0, 0.2, x.size)
    result = fit_cut(x, noisy, np.full(x.size, 0.2), angles, db)
    assert abs(result["shift_deg"] - 1.3) < 3 * result["shift_sigma_deg"]
    assert abs(result["scale"] - 1.05) < 3 * result["scale_sigma"]
    assert result["level_db"] == pytest.approx(-20, abs=0.3)
    assert 0.5 < result["reduced_chi2"] < 2.0
    assert not result["edge"]


def test_fit_needs_four_points(cut_2k):
    angles, db = cut_2k
    assert fit_cut([0, 1, 2], [0, -1, -2], [0.2] * 3, angles, db) is None
