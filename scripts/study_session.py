"""Repeatable acoustic check of Heimdall with one loudspeaker, through the GUI backend.

Plays test signals on the default Windows output and measures with the commands the Study
screen uses (S, M, DG, O, SET): where Live finds the speaker, clipping, linearity and SNR,
every microphone, the beam against one microphone, a steering search and two beam cuts at
2 kHz, and the tonal detector. Needs sim_server.py connected to the board, the speaker as the
default output, a quiet room, and the speaker's direction from the array centre (azimuth
+ = right, elevation + = up, as seen from behind the array). Stops after the first three
checks if one fails, since nothing later would be valid. Puts back the settings, Ch1, the
grid and tone. Writes the signals, every capture (captures.csv) and summary.json to --out.
Exit code 1 if a check fails.

    python scripts/study_session.py --speaker-az 0 --speaker-el 0 --distance 1.7
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import tempfile
import time
import wave
from contextlib import contextmanager

import numpy as np

from board_regression import WS_URL, Board, Report, connect, http, play, record

RATE = 48000
LOOP_S = 20
# MCU windows of 100 ms (the maximum): one reading of 1/3-octave noise scatters 0.6 dB, not 2.7 as at 5 ms.
WINDOWS = {"measure": 1, "window_us": 100000}
MCU_BEAM = {**WINDOWS, "channel": 0}
TONES_HZ = (150, 200, 310)
FIELDS = ["step", "label", "sector", "value", "azimuth_deg", "elevation_deg", "n", "mean_db", "std_db",
          "min_db", "max_db", "settings"]


# The Study screen's arithmetic (app/js/study/analysis.js and measure.js) -------------------------

def power(db):
    return 10 ** (np.asarray(db, float) / 10)


def power_mean_db(levels):
    return float(10 * np.log10(np.mean(power(levels))))


def snr_db(on_db, off_db):
    signal = power(on_db) - power(off_db)
    return float(10 * np.log10(signal / power(off_db))) if signal > 0 else float("-inf")


def signal_db(on_db, off_db):
    signal = power(on_db) - power(off_db)
    return float(10 * np.log10(signal)) if signal > 0 else float("-inf")


def refine_peak(levels, angles):
    """Angle of the strongest level, refined by a parabola through it and its neighbours."""
    levels, angles = [float(v) for v in levels], [float(a) for a in angles]
    i = int(np.argmax(levels))
    if i in (0, len(levels) - 1):
        return angles[i]
    (x0, x1, x2), (y0, y1, y2) = angles[i - 1:i + 2], levels[i - 1:i + 2]
    denominator = (x1 - x0) * (y1 - y2) - (x1 - x2) * (y1 - y0)
    if denominator == 0:
        return x1
    return x1 - 0.5 * ((x1 - x0) ** 2 * (y1 - y2) - (x1 - x2) ** 2 * (y1 - y0)) / denominator


def refine_peak_2d(grid, azimuths, elevations):
    """(azimuth, elevation) of the strongest cell of grid[row = elevation][column = azimuth], refined."""
    grid = np.asarray(grid, float)
    row, column = np.unravel_index(int(np.argmax(grid)), grid.shape)
    return refine_peak(grid[row], azimuths), refine_peak(grid[:, column], elevations)


def width_at(angles, levels, drop=3.0):
    """Interpolated width where the level is `drop` dB below the strongest one; None if an edge is missing."""
    peak = int(np.argmax(levels))
    target = levels[peak] - drop

    def edge(step):
        i = peak
        while 0 <= i + step < len(levels):
            a, b = i, i + step
            if levels[b] <= target:
                return angles[a] + (angles[b] - angles[a]) * (levels[a] - target) / (levels[a] - levels[b])
            i = b
        return None

    left, right = edge(-1), edge(1)
    return None if left is None or right is None else abs(right - left)


def dynamic_range(floor_db, steps):
    """(ceiling, range): the highest level whose step from the one below moves the reading by half the step."""
    steps = sorted(steps)
    for (v0, l0), (v1, l1) in reversed(list(zip(steps, steps[1:]))):
        if (l1 - l0) / (v1 - v0) >= 0.5:
            return l1, l1 - floor_db
    return None, None


def grid_angles(minimum, maximum, count):
    """The angles the firmware reports for one axis of a direction set (main.c grid_angle)."""
    lo, hi = int(np.floor(minimum * 100 + 0.5)), int(np.floor(maximum * 100 + 0.5))
    if count < 2:
        return [(lo + hi) / 200]
    return [(lo + (hi - lo) * i / (count - 1)) / 100 for i in range(count)]


def same_angles(actual, expected):
    return isinstance(actual, list) and len(actual) == len(expected) and all(
        abs(a - b) < 1e-3 for a, b in zip(actual, expected))


# Uncertainty of a result computed from noisy means ------------------------------------------------

def spread(estimate, means, errors, runs=1000):
    """1-sigma of estimate(means) when every mean moves by its standard error; None if it mostly fails."""
    rng = np.random.default_rng(1)
    values = [estimate(np.asarray(means, float) + rng.normal(0, errors)) for _ in range(runs)]
    values = [v for v in values if v is not None]
    return float(np.std(values)) if len(values) >= runs // 2 else None


# Test signals: periodic in LOOP_S, so a looping WAV has no seam ----------------------------------

def make_signals(folder):
    n = RATE * LOOP_S
    rng = np.random.default_rng(7)

    def rms(x, db):
        return x * (10 ** (db / 20) / np.sqrt(np.mean(x ** 2)))

    def band(lo, hi):
        freqs = np.fft.rfftfreq(n, 1 / RATE)
        spectrum = np.zeros(freqs.size, complex)
        inside = (freqs >= lo) & (freqs <= hi)
        spectrum[inside] = np.exp(2j * np.pi * rng.random(inside.sum()))
        return rms(np.fft.irfft(spectrum, n), -18)

    def harmonics(f0):
        t = np.arange(n) / RATE
        return rms(sum(np.sin(2 * np.pi * f0 * h * t + 2 * np.pi * rng.random()) / h for h in range(1, 9)), -18)

    base = band(500, 3500)
    signals = {"A": base, "B1k": band(891, 1122), "B2k": band(1782, 2245)}
    signals.update({f"A-{step}": base * 10 ** (-step / 20) for step in range(6, 37, 6)})
    signals.update({f"D{f0}": harmonics(f0) for f0 in TONES_HZ})
    paths = {}
    for name, x in signals.items():
        if np.max(np.abs(x)) >= 0.99:
            raise ValueError(f"signal {name} would clip")
        paths[name] = os.path.join(folder, f"{name}.wav")
        with wave.open(paths[name], "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RATE)
            handle.writeframes(np.round(x * 32767).astype("<i2").tobytes())
    return paths


# Session: the board, the sounds and the record of every capture ---------------------------------

class Results(Report):
    def info(self, name, detail):
        self.rows.append((name, None, detail))
        print(f"INFO  {name:38s} {detail}", flush=True)


class Session:
    def __init__(self, board, signals, writer, report):
        self.board, self.signals, self.writer, self.report = board, signals, writer, report
        state = board.state
        self.settings_before = dict(state.get("settings") or {})
        self.ch1_before = dict(state.get("ch1") or {"source": 0, "index": 0})
        self.home = (dict(state["configuration"]), list(state["azimuth_deg"]), list(state["elevation_deg"]))
        self.values = {}

    def sound(self, name):
        """Loop a test signal (None stops it); waits 2 s for the Bluetooth delay and the speaker to wake."""
        play(self.signals[name] if name else None, loop=True)
        self.board.pump(2.0 if name else 0.5)

    def read(self, command):
        """Send S,<sector> or M and wait for its measurement; None if none came."""
        board = self.board
        count = int((board.state.get("last_measurement") or {}).get("count", 0))
        error = board.state.get("last_error")
        board.command(command)
        board.pump(2.0, lambda s: int((s.get("last_measurement") or {}).get("count", 0)) > count
                   or s.get("last_error") != error)
        measurement = board.state.get("last_measurement") or {}
        return measurement if int(measurement.get("count", 0)) > count else None

    def capture(self, step, label, sector, seconds, **extra):
        """Hold `sector` and read it with M, one window after the other, for `seconds`."""
        board = self.board
        board.stop()
        columns = board.state["configuration"]["columns"]
        # S's own reading starts 0.5 ms after the steer, inside the audio path's latency.
        if self.read(f"S,{sector}") is None:
            raise RuntimeError(f"S,{sector} gave no reading ({board.state.get('last_error')})")
        levels, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            measurement = self.read("M")
            if measurement and measurement["row"] * columns + measurement["column"] == sector:
                levels.append(measurement["level_db"])
        if not levels:
            raise RuntimeError(f"no level readings for {label}")
        row = {"step": step, "label": label, "sector": sector, "n": len(levels), "mean_db": power_mean_db(levels),
               "std_db": float(np.std(levels, ddof=1)) if len(levels) > 1 else 0.0,
               "min_db": min(levels), "max_db": max(levels),
               "settings": json.dumps(board.state.get("settings") or {}, sort_keys=True), **extra}
        self.writer.writerow(row)
        return row

    @contextmanager
    def settings(self, **changes):
        """Apply settings and put the previous values back."""
        board = self.board
        board.stop()
        before = dict(board.state.get("settings") or {})
        try:
            for key, value in changes.items():
                self.set(key, value)
            yield
        finally:
            board.stop()
            for key in changes:
                if key in before:
                    board.set(key, before[key])

    def set(self, key, value):
        """The firmware takes settings only while idle."""
        self.board.stop()
        if not self.board.set(key, value):
            raise RuntimeError(f"SET,{key},{value} was refused")

    def route(self, source, index):
        before = self.board.state.get("sequence", 0)
        self.board.command(f"O,{source},{index}")
        replied = self.board.pump(2.0, lambda s: s.get("sequence", 0) > before and (s.get("ch1") or {}).get(
            "source") == source and (s.get("ch1") or {}).get("index") == index)
        code = (self.board.state.get("ch1") or {}).get("code")
        if not replied or code != 0:
            raise RuntimeError(f"Ch1 = {source},{index} was refused (code {code})")

    def grid(self, az_min, az_max, columns, el_min, el_max, rows):
        """Load a direction set; returns its azimuth and elevation axes."""
        board = self.board
        board.stop()
        before = board.state.get("sequence", 0)
        centi = [int(np.floor(v * 100 + 0.5)) for v in (az_min, az_max, el_min, el_max)]
        board.command(f"DG,{centi[0]},{centi[1]},{columns},{centi[2]},{centi[3]},{rows}")
        azimuths, elevations = grid_angles(az_min, az_max, columns), grid_angles(el_min, el_max, rows)

        def ready(state):
            configuration, loaded = state.get("configuration") or {}, state.get("direction_set") or {}
            return (state.get("sequence", 0) > before and loaded.get("code") == 0
                    and configuration.get("rows") == rows and configuration.get("columns") == columns
                    and same_angles(state.get("azimuth_deg"), azimuths)
                    and same_angles(state.get("elevation_deg"), elevations))

        if not board.pump(5.0, ready):
            raise RuntimeError(f"direction set not loaded ({board.state.get('last_error')})")
        return azimuths, elevations

    def restore_grid(self):
        """I puts the deployment grid back (the firmware reloads its tables)."""
        configuration, azimuths, elevations = self.home
        state = self.board.state
        if (state.get("configuration") or {}).get("rows") == configuration["rows"] and same_angles(
                state.get("azimuth_deg"), azimuths) and same_angles(state.get("elevation_deg"), elevations):
            return
        self.board.stop()
        self.board.command("I")
        if not self.board.pump(8.0, lambda s: same_angles(s.get("azimuth_deg"), azimuths)
                               and same_angles(s.get("elevation_deg"), elevations)):
            raise RuntimeError("the deployment grid did not come back after I")

    def speaker_sector(self, azimuth, elevation):
        configuration, azimuths, elevations = self.home
        column = int(np.argmin(np.abs(np.array(azimuths) - azimuth)))
        row = int(np.argmin(np.abs(np.array(elevations) - elevation)))
        return row * configuration["columns"] + column

    def restore(self):
        play(None)
        board = self.board
        board.stop()
        try:
            self.restore_grid()
        except RuntimeError as error:
            print(f"warning: {error}")
        for key in ("measure", "channel", "settle_us", "guard_us", "window_us", "band", "tone"):
            if key in self.settings_before and (board.state.get("settings") or {}).get(key) != self.settings_before[key]:
                board.set(key, self.settings_before[key])
        board.command(f"O,{self.ch1_before.get('source', 0)},{self.ch1_before.get('index', 0)}")
        board.pump(0.5)


# Checks ------------------------------------------------------------------------------------------

def live_check(session, args):
    """Stare starts in silence (its first map is the background), then the speaker plays for 8 s."""
    board, report = session.board, session.report
    for attempt in range(2):
        found = []

        def collect(state):
            target = (state.get("bank") or {}).get("target")
            if state.get("firmware_mode") == "B" and target:
                found.append((target["azimuth_deg"], target["elevation_deg"]))
            return False

        board.command("B")
        board.pump(1.5)
        session.sound("A")
        board.pump(8.0, collect)
        board.stop()
        session.sound(None)
        if found:
            azimuth = statistics.median(a for a, _ in found[-40:])
            elevation = statistics.median(e for _, e in found[-40:])
            session.values["live"] = {"azimuth_deg": azimuth, "elevation_deg": elevation}
            if abs(azimuth - args.speaker_az) <= 10 and abs(elevation - args.speaker_el) <= 15:
                report.add("Live finds the speaker", True, f"{azimuth:.1f}, {elevation:.1f} deg")
                return True
    detail = (f"Live put it at {azimuth:.1f}, {elevation:.1f} deg" if found else "Live found no target") + \
        "; check the output device, the volume and the speaker direction"
    report.add("Live finds the speaker", False, detail)
    return False


def clipping_check(session):
    session.sound("A")
    stats, _, reason = record(session.board, 2.0)
    session.sound(None)
    if not stats or "minimum" not in stats:
        session.report.add("no clipping", False, f"recording failed: {reason}")
        return False
    peak = max(abs(stats["minimum"]), abs(stats["maximum"])) / 32768
    session.values["recording_peak"] = peak
    ok = peak < 0.5 and stats.get("overruns") == 0
    session.report.add("no clipping", ok, f"recording peak {100 * peak:.0f}% of full scale, overruns "
                                          f"{stats.get('overruns')}" + ("" if ok else "; turn the speaker down"))
    return ok


def level_check(session, sector):
    """Floor, then A in 6 dB steps: SNR, linearity and dynamic range of the beam."""
    report = session.report
    with session.settings(**MCU_BEAM):
        session.sound(None)
        floor = session.capture("level", "floor, speaker silent", sector, 3, value="")["mean_db"]
        steps = []
        for attenuation in (36, 30, 24, 18, 12, 6, 0):
            session.sound(f"A-{attenuation}" if attenuation else "A")
            level = session.capture("level", f"A at -{attenuation} dB", sector, 3, value=-attenuation)["mean_db"]
            steps.append((-attenuation, level))
        session.sound(None)
    snr = snr_db(steps[-1][1], floor)
    usable = [(value, signal_db(level, floor)) for value, level in steps if level - floor >= 10]
    deltas = [l1 - l0 for (_, l0), (_, l1) in zip(usable, usable[1:])]
    linear = len(deltas) >= 3 and all(abs(delta - 6) <= 1 for delta in deltas)
    ceiling, span = dynamic_range(floor, steps)
    session.values["level"] = {"floor_dbfs": floor, "snr_db": snr, "steps": steps, "step_db": deltas,
                               "dynamic_range_db": span}
    report.add("SNR on the speaker", snr >= 25, f"{snr:.1f} dB (beam, sector {sector})" +
               ("" if snr >= 25 else "; turn the speaker up or bring it closer"))
    report.add("linear in 6 dB steps", linear, "steps " + ", ".join(f"{d:.1f}" for d in deltas) +
               ("" if linear else "; the speaker compresses or the path clips"))
    report.info("dynamic range", f"at least {span:.1f} dB (floor {floor:.1f} dBFS)" if span else "not found")
    return snr >= 25 and linear


def survey(session, sector, microphones):
    on, off = {}, {}
    with session.settings(**WINDOWS, channel=1):
        for phase, levels, signal in (("on", on, "A"), ("off", off, None)):
            session.sound(signal)
            for microphone in range(microphones):
                session.route(1, microphone)
                levels[microphone] = session.capture("survey", f"microphone {microphone}, speaker {phase}", sector,
                                                     1, value=microphone)["mean_db"]
        session.sound(None)
    snr = {m: snr_db(on[m], off[m]) for m in on}
    level = {m: signal_db(on[m], off[m]) for m in on}
    median = statistics.median(level.values())
    relative = {m: level[m] - median for m in level}
    quiet = [m for m in snr if snr[m] < 10]
    weak = [m for m in relative if relative[m] < -10]
    session.values["survey"] = {str(m): {"snr_db": snr[m], "relative_db": relative[m]} for m in on}
    session.report.add("every microphone hears the speaker", not quiet,
                       f"SNR {min(snr.values()):.1f}-{max(snr.values()):.1f} dB" +
                       (f"; below 10 dB: {quiet}" if quiet else ""))
    session.report.add("no weak microphone", not weak, "none more than 10 dB below the median" if not weak
                       else f"more than 10 dB below the median: {weak}")
    loudest = sorted(relative, key=relative.get, reverse=True)[:5]
    session.report.info("loudest microphones", ", ".join(f"{m} +{relative[m]:.1f}" for m in loudest) +
                        " dB (the inner rings read louder; acoustic)")


def beam_against_microphone(session, sector, microphone, microphones):
    in_step, as_noise = 20 * np.log10(microphones), 10 * np.log10(microphones)
    gaps = {}
    with session.settings(**MCU_BEAM):
        for signal in ("B1k", "B2k", "A"):
            session.sound(signal)
            beam = session.capture("beam vs microphone", f"beam, {signal}", sector, 4)["mean_db"]
            session.route(1, microphone)
            session.set("channel", 1)
            single = session.capture("beam vs microphone", f"microphone {microphone}, {signal}", sector, 4,
                                     value=microphone)["mean_db"]
            session.set("channel", 0)
            gaps[signal] = beam - single
        session.sound(None)
    session.values["beam_minus_microphone_db"] = gaps
    session.report.info(f"beam minus microphone {microphone}",
                        ", ".join(f"{name} {gap:.1f}" for name, gap in gaps.items()) +
                        f" dB (direct sound in step {in_step:.1f}, independent noise {as_noise:.1f})")


def steering(session, args):
    """7 x 7 search over +-12 deg with 2 kHz noise; re-centred once if the peak lands on the edge."""
    report, size, span = session.report, 7, 12.0
    centre = (args.speaker_az, args.speaker_el)
    for attempt in range(2):
        with session.settings(**MCU_BEAM):
            session.sound("B2k")
            azimuths, elevations = session.grid(centre[0] - span, centre[0] + span, size,
                                                centre[1] - span, centre[1] + span, size)
            grid = np.zeros((size, size))
            for sector in range(size * size):
                row, column = divmod(sector, size)
                grid[row, column] = session.capture(
                    "steering", f"{azimuths[column]:.1f}, {elevations[row]:.1f}", sector, 0.5,
                    azimuth_deg=azimuths[column], elevation_deg=elevations[row])["mean_db"]
            session.sound(None)
        row, column = np.unravel_index(int(np.argmax(grid)), grid.shape)
        on_edge = row in (0, size - 1) or column in (0, size - 1)
        if not on_edge:
            break
        centre = (azimuths[column], elevations[row])
    azimuth, elevation = refine_peak_2d(grid, azimuths, elevations)
    errors = (azimuth - args.speaker_az, elevation - args.speaker_el)
    session.values["steering"] = {"azimuth_deg": azimuth, "elevation_deg": elevation, "on_edge": bool(on_edge),
                                  "contrast_db": float(grid.max() - grid.min())}
    report.add("steering peak in azimuth", not on_edge and abs(errors[0]) <= 3,
               f"{azimuth:.1f} deg, {errors[0]:+.1f} from the speaker" + (", peak on the grid edge" if on_edge else ""))
    report.add("steering peak in elevation", not on_edge and abs(errors[1]) <= 8,
               f"{elevation:.1f} deg, {errors[1]:+.1f} from the speaker (tape elevation is rough)")
    return azimuth, elevation


def cuts(session, peak, seconds):
    """Azimuth and elevation cuts through the steering peak, 2 kHz noise, against theory at 2 kHz."""
    session.values["cuts"] = {}
    for axis in ("azimuth", "elevation"):
        along = 0 if axis == "azimuth" else 1
        if axis == "azimuth":
            spec = (peak[0] - 30, peak[0] + 30, 25, peak[1], peak[1], 1)
        else:
            spec = (peak[0], peak[0], 1, peak[1] - 30, peak[1] + 30, 25)
        with session.settings(**MCU_BEAM):
            session.sound("B2k")
            azimuths, elevations = session.grid(*spec)
            angles = azimuths if axis == "azimuth" else elevations
            levels = [session.capture("cut", f"{axis} {angle:.1f}", sector, seconds, value=angle)["mean_db"]
                      for sector, angle in enumerate(angles)]
            session.sound(None)
        status, theory = http("GET", f"/study/theory/cut?axis={axis}&steer_azimuth_deg={peak[0]}"
                                     f"&steer_elevation_deg={peak[1]}&frequency_hz=2000&minimum_deg={angles[0]}"
                                     f"&maximum_deg={angles[-1]}&step_deg={(angles[-1] - angles[0]) / 240}")
        theory_width = (theory.get("beamwidth_3db") or {}).get("width_deg") if status == 200 else None
        measured_peak, width = refine_peak(levels, angles), width_at(angles, levels)
        session.values["cuts"][axis] = {"peak_deg": measured_peak, "width_3db_deg": width,
                                        "theory_width_3db_deg": theory_width}
        session.report.add(f"{axis} cut peak", abs(measured_peak - peak[along]) <= 3,
                           f"{measured_peak:.1f} deg, steering peak {peak[along]:.1f}")
        session.report.info(f"{axis} -3 dB width, 2 kHz", (f"{width:.1f} deg" if width else "not bracketed") +
                            (f", theory {theory_width:.1f}" if theory_width else ""))


def tonal_detector(session, sector):
    board, report = session.board, session.report
    try:
        session.set("tone", 100)
    except RuntimeError as error:
        report.add("tonal detector", False, str(error))
        return
    medians = {}
    try:
        board.command(f"S,{sector}")
        board.pump(1.0)
        for f0 in TONES_HZ:
            session.sound(f"D{f0}")
            reports, last = [], [None]

            def collect(state):
                tone = (state.get("tones") or {}).get("0")
                key = json.dumps(tone, sort_keys=True)
                if tone and key != last[0]:
                    last[0] = key
                    reports.append(tone["f0_hz"])
                return False

            board.pump(12.0, collect)
            medians[f0] = statistics.median(reports) if reports else None
            report.add(f"tonal detector at {f0} Hz", medians[f0] is not None and abs(medians[f0] - f0) <= 2,
                       f"median {medians[f0]} Hz from {len(reports)} reports")
    finally:
        session.sound(None)
        board.stop()
        if not board.set("tone", 0):
            report.add("tonal detector off again", False, "SET,tone,0 was refused; reset the board")
    session.values["tonal_median_hz"] = medians


def run(session, args):
    state = session.board.state
    sector = session.speaker_sector(args.speaker_az, args.speaker_el)
    microphones = int(state["configuration"]["microphones"])
    print(f"speaker sector {sector}; gates: Live, clipping, level", flush=True)
    if not (live_check(session, args) and clipping_check(session) and level_check(session, sector)):
        print("stopped: fix the setup and run again", flush=True)
        return
    steps = []
    if (state.get("outputs") or {}).get("microphones", 0) > 0:
        if not args.skip_survey:
            steps.append(("microphone survey", lambda: survey(session, sector, microphones)))
        steps.append(("beam against one microphone",
                      lambda: beam_against_microphone(session, sector, args.microphone, microphones)))
    else:
        session.report.info("microphone checks", "skipped: this DSP program has no output selector")
    for name, step in steps:
        print(f"-- {name}", flush=True)
        try:
            step()
        except RuntimeError as error:
            session.report.add(name, False, str(error))
    print("-- steering search and cuts", flush=True)
    try:
        peak = steering(session, args)
        cuts(session, peak, args.cut_seconds)
    except RuntimeError as error:
        session.report.add("steering and cuts", False, str(error))
    finally:
        session.restore_grid()
    print("-- tonal detector", flush=True)
    try:
        tonal_detector(session, sector)
    except RuntimeError as error:
        session.report.add("tonal detector", False, str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--speaker-az", type=float, required=True, help="degrees from the array centre, + = right")
    parser.add_argument("--speaker-el", type=float, required=True, help="degrees from the array centre, + = up")
    parser.add_argument("--distance", type=float, help="speaker distance in metres (recorded in the summary)")
    parser.add_argument("--microphone", type=int, default=4, help="microphone compared with the beam")
    parser.add_argument("--cut-seconds", type=float, default=1.0, help="capture per cut direction")
    parser.add_argument("--skip-survey", action="store_true", help="skip the 44-microphone survey")
    parser.add_argument("--out", help="folder for the signals, captures.csv and summary.json (default: temp)")
    args = parser.parse_args()
    out = args.out or os.path.join(tempfile.gettempdir(), time.strftime("heimdall_study_%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    signals = make_signals(out)
    report = Results()
    with connect(WS_URL, open_timeout=5, max_size=None) as socket, \
            open(os.path.join(out, "captures.csv"), "w", newline="") as handle:
        board = Board(socket)
        board.pump(1.0)
        if not board.state.get("connected"):
            sys.exit("the backend is not connected to the board")
        if board.state.get("recording_state") in ("recording", "ready", "downloading"):
            sys.exit("the device holds a recording that is not downloaded; download it first")
        board.stop()
        board.command("I")
        board.pump(3.0)
        dsp, tables = board.state.get("dsp") or {}, board.state.get("tables") or {}
        booted = bool(dsp.get("matches")) and tables.get("verified") == tables.get("words")
        report.add("boot checks (I)", booted, f"DSP {dsp.get('matched')}/{dsp.get('read')}, "
                                             f"tables {tables.get('verified')}/{tables.get('words')}")
        writer = csv.DictWriter(handle, FIELDS, extrasaction="ignore")
        writer.writeheader()
        session = Session(board, signals, writer, report)
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            if booted:
                run(session, args)
        finally:
            session.restore()
        summary = {"started": started, "arguments": vars(args), "map_hash": board.state.get("map_hash"),
                   "dsp": board.state.get("dsp"), "values": session.values,
                   "checks": [{"name": name, "result": "info" if passed is None else ("pass" if passed else "fail"),
                               "detail": detail} for name, passed, detail in report.rows]}
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, default=float)
    failed = [name for name, passed, _ in report.rows if passed is False]
    checks = sum(passed is not None for _, passed, _ in report.rows)
    print(f"\n{checks - len(failed)}/{checks} checks passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    print(f"results: {out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
