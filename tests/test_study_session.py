"""scripts/study_session.py: its arithmetic must give what the Study screen gave for the same captures.

The numbers are captures from the 2026-10-19 session and the results the Study screen saved for them
(app/js/study/analysis.js and measure.js).
"""
from __future__ import annotations

import sys
import wave
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

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


def test_spread_is_the_scatter_of_the_estimate():
    # The mean of 100 values each with sigma 1 scatters by 1 / sqrt(100).
    assert study.spread(np.mean, np.zeros(100), np.ones(100)) == pytest.approx(0.1, rel=0.1)
    assert study.spread(np.mean, np.zeros(100), np.zeros(100)) == 0.0
    assert study.spread(lambda values: None, np.zeros(3), np.ones(3)) is None


class FakeBoard:
    """Answers S,<n> with a reading of -90 dB (inside the latency) and M with -20 dB at the held sector."""

    def __init__(self, refuse_steer=False, wrong_sector=False):
        self.state = {"configuration": {"rows": 7, "columns": 7, "microphones": 44}, "settings": {},
                      "azimuth_deg": [], "elevation_deg": [], "firmware_mode": "IDLE"}
        self.refuse_steer, self.wrong_sector = refuse_steer, wrong_sector
        self.replies, self.commands, self.count, self.sector = [], [], 0, None

    def command(self, text):
        self.commands.append(text)
        if text.startswith("S,"):
            if self.refuse_steer:
                self.replies.append({"last_error": {"code": "BUSY", "n": len(self.commands)}})
                return
            self.sector = int(text[2:])
            self.reply(-90.0)
        elif text == "M" and self.sector is not None:
            self.reply(-20.0)

    def reply(self, level_db):
        self.count += 1
        sector = (self.sector + 1) % 49 if self.wrong_sector and level_db > -90 else self.sector
        row, column = divmod(sector, 7)
        self.replies.append({"last_measurement": {"count": self.count, "row": row, "column": column,
                                                  "level_db": level_db}})

    def pump(self, seconds, until=None):
        while self.replies:
            self.state.update(self.replies.pop(0))
            if until is not None and until(self.state):
                return True
        return False

    def stop(self):
        return True


class Rows(list):
    writerow = list.append


def test_capture_drops_the_steer_reading_and_reads_one_window_at_a_time():
    board = FakeBoard()
    rows = Rows()
    session = study.Session(board, {}, rows, study.Results())
    row = session.capture("test", "label", 24, 0.02)
    assert board.commands[0] == "S,24" and set(board.commands[1:]) == {"M"}
    assert row["n"] == len(board.commands) - 1 and row["mean_db"] == pytest.approx(-20.0)
    assert rows == [row]


def test_capture_refuses_without_a_reading_of_its_sector():
    with pytest.raises(RuntimeError, match="S,24 gave no reading"):
        study.Session(FakeBoard(refuse_steer=True), {}, Rows(), study.Results()).capture("t", "l", 24, 0.01)
    with pytest.raises(RuntimeError, match="no level readings"):
        study.Session(FakeBoard(wrong_sector=True), {}, Rows(), study.Results()).capture("t", "l", 24, 0.01)


class SteeringSession:
    """Answers captures from a paraboloid peaked at (1, -1) deg, 0.48 dB down at 4 deg, with a set scatter."""

    def __init__(self, std_db, n):
        self.report, self.values, self.std_db, self.n = study.Results(), {}, std_db, n

    @contextmanager
    def settings(self, **changes):
        yield

    def sound(self, name):
        pass

    def grid(self, az_min, az_max, columns, el_min, el_max, rows):
        self.axes = study.grid_angles(az_min, az_max, columns), study.grid_angles(el_min, el_max, rows)
        return self.axes

    def capture(self, step, label, sector, seconds, azimuth_deg, elevation_deg):
        level = -20 - 0.03 * ((azimuth_deg - 1) ** 2 + (elevation_deg + 1) ** 2)
        return {"mean_db": level, "std_db": self.std_db, "n": self.n}


def test_steering_passes_only_with_a_small_sigma():
    args = SimpleNamespace(speaker_az=0.0, speaker_el=0.0, steer_seconds=2.0)
    quiet = SteeringSession(std_db=0.6, n=18)
    azimuth, elevation = study.steering(quiet, args)
    assert azimuth == pytest.approx(1.0, abs=0.05) and elevation == pytest.approx(-1.0, abs=0.05)
    assert all(s < 1.0 for s in quiet.values["steering"]["sigma_deg"])
    assert [passed for _, passed, _ in quiet.report.rows] == [True, True]

    noisy = SteeringSession(std_db=3.0, n=9)
    study.steering(noisy, args)
    assert all(s > 1.0 for s in noisy.values["steering"]["sigma_deg"])
    assert [passed for _, passed, _ in noisy.report.rows] == [False, False]
    assert all("sigma above 1 deg" in detail for _, _, detail in noisy.report.rows)


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
