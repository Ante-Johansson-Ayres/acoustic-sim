"""scripts/study_session.py: its arithmetic must give what the Study screen gave for the same captures.

The numbers are captures from the 2026-10-19 session and the results the Study screen saved for them
(app/js/study/analysis.js and measure.js).
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import study_session as study  # noqa: E402

STEERING_AXIS = [-12, -8, -4, 0, 4, 8, 12]
STEERING_DB = [
    -24.6967, -24.1107, -22.8928, -22.7353, -22.5946, -22.9445, -24.8776,
    -23.6205, -24.1982, -22.5052, -21.576, -22.6579, -22.6014, -24.8979,
    -23.3946, -22.6997, -21.6871, -21.6769, -22.0725, -23.1967, -24.3116,
    -23.7094, -23.0559, -21.6851, -22.6765, -22.0125, -22.4607, -24.2231,
    -23.5696, -22.6649, -22.5155, -22.0012, -22.966, -24.0673, -24.312,
    -24.0824, -23.6268, -23.7524, -23.4857, -23.8018, -23.6865, -25.6615,
    -24.6736, -24.2699, -24.1401, -23.7944, -23.8178, -25.6947, -26.1556,
]
CUT_DEG = [-30 + 2.5 * i for i in range(25)]
CUT_DB = [-28.4875, -26.5343, -26.1689, -27.8363, -25.8288, -27.8671, -26.0251, -24.5231, -24.7492,
          -24.5641, -23.7189, -24.6634, -22.0115, -22.3675, -23.454, -25.4399, -26.0426, -26.2691,
          -26.1953, -27.91, -29.7947, -30.0125, -29.8458, -30.845, -30.4715]
LEVEL_STEPS = [(-36, -57.7839), (-30, -52.2669), (-24, -46.388), (-18, -39.6443), (-12, -33.449),
               (-6, -27.5319), (0, -21.5935)]


def test_snr_matches_the_study_screen():
    assert study.snr_db(-21.64387227112649, -65.66896128019766) == pytest.approx(44.024917105853625, abs=1e-9)
    assert study.snr_db(-70.0, -60.0) == float("-inf")


def test_steering_peak_matches_the_study_screen():
    grid = np.array(STEERING_DB).reshape(7, 7)
    azimuth, elevation = study.refine_peak_2d(grid, STEERING_AXIS, STEERING_AXIS)
    assert azimuth == pytest.approx(-0.15181007956421308, abs=1e-3)
    assert elevation == pytest.approx(-6.320233113934915, abs=1e-3)


def test_cut_peak_and_width_match_the_study_screen():
    assert study.refine_peak(CUT_DB, CUT_DEG) == pytest.approx(0.9541010566089204, abs=1e-3)
    assert study.width_at(CUT_DEG, CUT_DB) == pytest.approx(20.273482377426525, abs=1e-3)
    assert study.width_at([0, 1, 2], [-1.0, 0.0, -1.0]) is None


def test_dynamic_range_matches_the_study_screen():
    ceiling, span = study.dynamic_range(-65.7666, LEVEL_STEPS)
    assert ceiling == pytest.approx(-21.5935)
    assert span == pytest.approx(44.17, abs=0.01)


def test_grid_angles_follow_the_firmware():
    assert study.grid_angles(-12, 12, 7) == [-12, -8, -4, 0, 4, 8, 12]
    assert study.grid_angles(-6.3, -6.3, 1) == [-6.3]
    assert study.same_angles([-30.0, 0.0, 30.0], study.grid_angles(-30, 30, 3))


def test_signals_loop_cleanly_and_do_not_clip(tmp_path):
    paths = study.make_signals(str(tmp_path))
    assert set(paths) == {"A", "B1k", "B2k", "A-6", "A-12", "A-18", "A-24", "A-30", "A-36", "D150", "D200", "D310"}

    def read(name):
        with wave.open(paths[name], "rb") as handle:
            assert handle.getframerate() == study.RATE
            return np.frombuffer(handle.readframes(handle.getnframes()), "<i2") / 32767

    noise = read("A")
    assert 20 * np.log10(np.sqrt(np.mean(noise ** 2))) == pytest.approx(-18, abs=0.1)
    assert 20 * np.log10(np.sqrt(np.mean(read("A-12") ** 2))) == pytest.approx(-30, abs=0.1)
    # Periodic in the file length: the jump from the last sample to the first is an ordinary step.
    assert abs(noise[-1] - noise[0]) < 4 * np.std(np.diff(noise))
