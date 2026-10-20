"""Heimdall characterisation tests (MAX78002/docs/CHARACTERISATION_PLAN.md), run through the GUI backend.

    python scripts/characterise.py n1     noise floor vs number of microphones (speaker off)
    python scripts/characterise.py n2     noise floor vs steering direction (speaker off)
    python scripts/characterise.py a3     beam-pattern cuts through the speaker, fitted with theory
    python scripts/characterise.py a4     pointing across speaker positions: slope, offset, residuals
    python scripts/characterise.py a8     front-to-back ratio
    python scripts/characterise.py l1     Live (bank) direction vs speaker position, against the firmware's centroid
    python scripts/characterise.py r1     room reverberation time per octave (interrupted noise)
    python scripts/characterise.py s1     system sensitivity and equivalent noise with an SPL meter
    python scripts/characterise.py a9     path gain, directivity index (diffuse field), signal and noise gain
    python scripts/characterise.py a10    spatial aliasing: grating lobes at 5 and 8 kHz (beam FIR swapped for a high-pass)

The speaker tests say where to put the speaker. a3 and l1 then find it with the steered beam (--truth array,
the default: rough placement is enough, no tape); with --truth measured they ask for the measured position.
a4 always needs measured positions (tape or a phone measuring app). Needs sim_server.py
connected to the board, the speaker as the Windows default output and a quiet room. Writes every capture
(captures.csv), summary.json and figures to --out, and restores gains, settings, Ch1 and the grid.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
import warnings
from contextlib import contextmanager
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from array_theory import band_frequencies, directivity_index_db, fit_cut, steered_response  # noqa: E402
from board_regression import WS_URL, Board, connect, http, play, record  # noqa: E402
from heimdall_acoustic import default_contract_path, load_beam_contract  # noqa: E402
from study_session import (FIELDS, MCU_BEAM, WINDOWS, Results, Session, make_signals, power,  # noqa: E402
                           power_mean_db, refine_peak_2d, signal_db, snr_db)

BAND_HZ = (500.0, 3500.0)
N1_COUNTS = (0, 1, 2, 4, 8, 11, 16, 22, 32, 44)
N2_ANGLES = (-60.0, -30.0, 0.0, 30.0, 60.0)


def l90(levels):
    """Level exceeded 90% of the time (dB): the usual measure of background noise under intermittent sounds."""
    return float(np.percentile(levels, 10))


def summarise(values):
    """Mean and standard error of per-cycle results."""
    values = np.asarray(values, float)
    se = float(np.std(values, ddof=1) / np.sqrt(len(values))) if len(values) > 1 else 0.0
    return float(values.mean()), se


def find(data, key):
    """First value stored under `key` anywhere in a nested contract."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = find(value, key)
            if found is not None:
                return found
    return None


class Array:
    """Deployed geometry and the three noise models, averaged over the 0.5-3.5 kHz measurement band."""

    def __init__(self):
        contract = load_beam_contract()
        self.positions = np.asarray(contract.microphone_positions_mm, float) / 1000.0
        self.c = float(contract.speed_of_sound_m_s)
        self.freqs = np.linspace(*BAND_HZ, 61)

    def diffuse_sum(self, members):
        """Band mean of sum_ij sinc(k d_ij): beam power from diffuse noise, one microphone = 1."""
        p = self.positions[list(members)]
        d = np.hypot(p[:, None, 0] - p[None, :, 0], p[:, None, 1] - p[None, :, 1])
        return float(np.mean([np.sinc(2 * f * d / self.c).sum() for f in self.freqs]))

    def steered(self, azimuth_deg, elevation_deg):
        """(common-mode, diffuse) beam power steered to a direction, relative to steering at 0,0, in dB."""
        a, e = np.radians(azimuth_deg), np.radians(elevation_deg)
        tau = (self.positions[:, 0] * np.cos(e) * np.sin(a) + self.positions[:, 1] * np.sin(e)) / self.c
        d = np.hypot(*(self.positions[:, None, :] - self.positions[None, :, :]).transpose(2, 0, 1))
        common, diffuse, diffuse0 = [], [], []
        for f in self.freqs:
            phase = np.exp(2j * np.pi * f * tau)
            gamma = np.sinc(2 * f * d / self.c)
            common.append(abs(phase.sum()) ** 2 / len(tau) ** 2)
            diffuse.append(float(np.real(np.conj(phase) @ gamma @ phase)))
            diffuse0.append(float(gamma.sum()))
        return 10 * np.log10(np.mean(common)), 10 * np.log10(np.mean(diffuse) / np.mean(diffuse0))


def write_words(words):
    """Write {address: word} through the backend (allow-listed, read back by the firmware)."""
    if not words:
        return
    status, body = http("POST", "/dsp/write", {"writes": [{"address": a, "word": w} for a, w in words.items()]})
    if status != 200:
        raise RuntimeError(f"DSP write refused ({status}): {body}")


def reload_dsp(board):
    """I reloads the taper and tables and puts the deployment grid back."""
    board.stop()
    board.command("I")
    board.pump(4.0, lambda s: (s.get("tables") or {}).get("verified") == (s.get("tables") or {}).get("words"))


def n1(session, args, out, array):
    """Noise floor (L90) vs number of unmuted microphones, beam at 0,0, speaker off, in interleaved cycles."""
    contract = json.loads(Path(default_contract_path()).read_text(encoding="utf-8"))
    addresses = [int(a) for a in find(contract, "gain_addresses")]
    unity = [int(w) for w in find(contract, "gain_fixpt_8_24")]
    order = [int(m) for m in np.random.default_rng(0).permutation(len(addresses))]
    sector = session.speaker_sector(0, 0)
    current = dict(zip(addresses, unity))
    cycles = {count: [] for count in N1_COUNTS}
    readings = {count: [] for count in N1_COUNTS}
    try:
        with session.settings(**MCU_BEAM):
            for cycle in range(args.cycles):
                for count in (N1_COUNTS if cycle % 2 == 0 else N1_COUNTS[::-1]):
                    on = set(order[:count])
                    wanted = {a: (unity[m] if m in on else 0) for m, a in enumerate(addresses)}
                    session.board.stop()
                    write_words({a: w for a, w in wanted.items() if current.get(a) != w})
                    current = wanted
                    capture = session.capture("n1", f"{count} microphones, cycle {cycle + 1}", sector,
                                              args.seconds or 4.0, value=count)
                    cycles[count].append(l90(capture["levels"]))
                    readings[count] += capture["levels"]
                    print(f"  cycle {cycle + 1}, {count:2d} microphones: L90 {cycles[count][-1]:7.2f} dBFS", flush=True)
    finally:
        session.board.stop()
        write_words({a: w for a, w in zip(addresses, unity) if current.get(a) != w})
        reload_dsp(session.board)

    rows = []
    for count in N1_COUNTS:
        level, se = summarise(cycles[count])
        rows.append({"count": count, "level_db": level, "se_db": se, "cycles_db": cycles[count],
                     "median_db": float(np.median(readings[count])), "mean_db": power_mean_db(readings[count]),
                     "diffuse_sum": array.diffuse_sum(order[:count]) if count else 0.0})
    floor = power(rows[0]["level_db"])
    full = rows[-1]
    full_excess = float(power(full["level_db"]) - floor)
    for row in rows[1:]:
        excess = float(power(row["level_db"]) - floor)
        row["excess_db"] = 10 * np.log10(excess) if excess > 0 else None
        row["usable"] = bool(excess > floor)
        n, n_all = row["count"], full["count"]
        row["independent_db"] = 10 * np.log10(full_excess * n / n_all)
        row["common_db"] = 10 * np.log10(full_excess * (n / n_all) ** 2)
        row["diffuse_db"] = 10 * np.log10(full_excess * row["diffuse_sum"] / full["diffuse_sum"])
    usable = [r for r in rows[1:] if r["usable"] and r["excess_db"] is not None]
    slope = None
    if len(usable) >= 3:
        x = 10 * np.log10([r["count"] for r in usable])
        slope = float(np.polyfit(x, [r["excess_db"] for r in usable], 1)[0])
    session.values["n1"] = {"statistic": "L90 per cycle, mean over cycles", "link_floor_dbfs": rows[0]["level_db"],
                            "rows": rows, "slope": slope, "order": order}
    session.report.info("N1 link floor (all muted)", f"{rows[0]['level_db']:.1f} dBFS")
    session.report.info("N1 slope of excess vs 10 log10 N", "not enough points above the link floor"
                        if slope is None else f"{slope:.2f} (1 = independent per microphone, 2 = common to all)")
    # No model lets a subset read above the full array; when one does, a source in the room has a direction.
    loud = [r["count"] for r in rows[1:-1] if r["level_db"] > full["level_db"] + 3.0]
    session.values["n1"]["valid"] = not loud
    if loud:
        session.report.info("N1 NOT VALID", f"{loud} microphones read more than 3 dB above all {full['count']}: "
                                            "something in the room is on; repeat when it is quiet")
    figure_n1(rows, out)


def n2(session, args, out, array):
    """Noise floor (L90), all microphones on, steered along the azimuth and elevation axes, speaker off."""
    cycles = {}
    with session.settings(**MCU_BEAM):
        for cycle in range(args.cycles):
            for axis in ("azimuth", "elevation"):
                if axis == "azimuth":
                    spec = (N2_ANGLES[0], N2_ANGLES[-1], len(N2_ANGLES), 0.0, 0.0, 1)
                else:
                    spec = (0.0, 0.0, 1, N2_ANGLES[0], N2_ANGLES[-1], len(N2_ANGLES))
                azimuths, elevations = session.grid(*spec)
                sectors = range(len(N2_ANGLES)) if cycle % 2 == 0 else range(len(N2_ANGLES) - 1, -1, -1)
                for sector in sectors:
                    az = azimuths[sector] if axis == "azimuth" else azimuths[0]
                    el = elevations[0] if axis == "azimuth" else elevations[sector]
                    capture = session.capture("n2", f"{az:g}, {el:g}, cycle {cycle + 1}", sector, args.seconds or 4.0,
                                              value=az if axis == "azimuth" else el, azimuth_deg=az, elevation_deg=el)
                    cycles.setdefault((axis, az, el), []).append(l90(capture["levels"]))
                    print(f"  cycle {cycle + 1}, {axis} {az:6.1f}, {el:6.1f}: L90 {cycles[(axis, az, el)][-1]:7.2f} dBFS",
                          flush=True)
    rows = []
    for (axis, az, el), values in cycles.items():
        level, se = summarise(values)
        common, diffuse = array.steered(az, el)
        rows.append({"axis": axis, "azimuth_deg": az, "elevation_deg": el, "level_db": level, "se_db": se,
                     "cycles_db": values, "common_db": common, "diffuse_db": diffuse})
    rows.sort(key=lambda r: (r["axis"], r["azimuth_deg"] + r["elevation_deg"]))
    centre = float(np.mean([r["level_db"] for r in rows if r["azimuth_deg"] == 0 and r["elevation_deg"] == 0]))
    for row in rows:
        row["relative_db"] = row["level_db"] - centre
    away = [r for r in rows if abs(r["azimuth_deg"]) >= 30 or abs(r["elevation_deg"]) >= 30]
    measured, common = np.mean([r["relative_db"] for r in away]), np.mean([r["common_db"] for r in away])
    session.values["n2"] = {"statistic": "L90 per cycle, mean over cycles", "rows": rows, "centre_dbfs": centre,
                            "mean_off_axis_db": float(measured)}
    session.report.info("N2 floor at 0,0", f"{centre:.1f} dBFS")
    session.report.info("N2 mean change at 30-60 deg", f"{measured:+.1f} dB (independent or diffuse about 0, "
                                                        f"common to all channels {common:+.1f})")
    loudest = max(rows, key=lambda r: r["relative_db"])
    session.values["n2"]["valid"] = loudest["relative_db"] - loudest["diffuse_db"] <= 3.0
    if not session.values["n2"]["valid"]:
        session.report.info("N2 NOT VALID", f"{loudest['azimuth_deg']:g}, {loudest['elevation_deg']:g} reads "
                                            f"{loudest['relative_db']:+.1f} dB: a source in that direction is on")
    figure_n2(rows, out)


def figure_n1(rows, out):
    import matplotlib.pyplot as plt
    data = [r for r in rows[1:] if r["excess_db"] is not None]
    counts = [r["count"] for r in data]
    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.errorbar(counts, [r["excess_db"] for r in data], yerr=[r["se_db"] for r in data], fmt="o", color="k",
                label="measured (link floor subtracted)")
    for key, label, style in (("independent_db", "independent per microphone: 10 log N", "--"),
                              ("common_db", "common to all channels: 20 log N", ":"),
                              ("diffuse_db", "diffuse room noise (this geometry)", "-")):
        ax.plot(counts, [r[key] for r in data], style, label=label)
    ax.axhline(rows[0]["level_db"], color="gray", lw=0.8)
    ax.text(1, rows[0]["level_db"] + 0.5, f"link floor {rows[0]['level_db']:.1f} dBFS", color="gray", fontsize=9)
    ax.set_xscale("log")
    ax.set_xticks(counts, [str(c) for c in counts])
    ax.set_xlabel("microphones unmuted in the beam (beam at 0,0, speaker off)")
    ax.set_ylabel("beam noise above the link floor, L90 (dBFS, 0.5-3.5 kHz)")
    ax.set_title("N1: how the beam's noise floor grows with the number of microphones\n"
                 "models anchored at 44 microphones", fontsize=11)
    ax.legend(fontsize=9)
    fig.savefig(os.path.join(out, "n1_noise_vs_microphones.png"), dpi=150, bbox_inches="tight")


def figure_n2(rows, out):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, axis in zip(axes, ("azimuth", "elevation")):
        data = [r for r in rows if r["axis"] == axis]
        x = [r["azimuth_deg"] if axis == "azimuth" else r["elevation_deg"] for r in data]
        ax.errorbar(x, [r["relative_db"] for r in data], yerr=[r["se_db"] for r in data], fmt="o-", color="k",
                    label="measured")
        ax.plot(x, [0] * len(x), "--", label="independent per microphone")
        ax.plot(x, [r["diffuse_db"] for r in data], "-", label="diffuse room noise")
        ax.plot(x, [r["common_db"] for r in data], ":", label="common to all channels")
        ax.set_xlabel(f"{axis} the beam is steered to (deg)")
        ax.set_title(f"steered in {axis}")
    axes[0].set_ylabel("beam noise floor (L90) relative to 0,0 (dB)")
    axes[0].legend(fontsize=9)
    fig.suptitle("N2: beam noise floor vs steering, speaker off (0.5-3.5 kHz)")
    fig.savefig(os.path.join(out, "n2_noise_vs_steering.png"), dpi=150, bbox_inches="tight")


# Speaker tests ------------------------------------------------------------------------------------

BANDS = {"B1k": (1000.0, 2.0, 48.0), "B2k": (2000.0, 1.0, 24.0), "B3k": (3150.0, 1.0, 24.0)}
A_BAND = np.linspace(500.0, 3500.0, 31)
OCTAVES = {"500 Hz": 500.0, "1 kHz": 1000.0, "2 kHz": 2000.0, "4 kHz": 4000.0}
L1_POSITIONS = "-30,0;-22.5,0;-15,0;-7.5,0;0,0;7.5,0;15,0;22.5,0;30,0;0,-15;0,-7.5;0,7.5;0,15"


def place(nominal_az, nominal_el, distance_m):
    """Rough placement; the array then measures where the speaker is. Returns the nominal distance."""
    a, e = np.radians(nominal_az), np.radians(nominal_el)
    right, up = distance_m * np.cos(e) * np.sin(a), distance_m * np.sin(e)
    side = (f"{abs(100 * right):.0f} cm {'right' if right >= 0 else 'left'} of the centre line (seen from behind "
            "the array)" if abs(right) >= 0.05 else "straight ahead")
    height = (f", {abs(100 * up):.0f} cm {'above' if up >= 0 else 'below'} the array centre" if abs(up) >= 0.05
              else ", at the height of the array centre")
    input(f"\nSpeaker roughly {side}{height}, about {distance_m:g} m away, facing the array. Rest it on something "
          "rather than holding it; close is fine, the array measures where it is. Enter when ready.")
    return distance_m


def ask_position(nominal_az, nominal_el, distance_m):
    """Tell the operator where the speaker goes; return the measured (azimuth, elevation, distance)."""
    a, e = np.radians(nominal_az), np.radians(nominal_el)
    x, z, h = distance_m * np.cos(e) * np.sin(a), distance_m * np.cos(e) * np.cos(a), distance_m * np.sin(e)
    print(f"\nSpeaker to az {nominal_az:g}, el {nominal_el:g}: driver {100 * x:.1f} cm right, {100 * z:.1f} cm "
          f"forward, {100 * h:.1f} cm up from the array centre, facing the array.", flush=True)
    text = input("Measured right, forward, up in cm (Enter = as above): ").strip()
    if text:
        x, z, h = (float(v) / 100.0 for v in text.replace(",", " ").split())
    return position_angles(x, z, h)


def position_angles(right_m, forward_m, up_m):
    """(azimuth, elevation, distance) of a point, in the firmware's convention."""
    r = float(np.sqrt(right_m ** 2 + forward_m ** 2 + up_m ** 2))
    return float(np.degrees(np.arctan2(right_m, forward_m))), float(np.degrees(np.arcsin(up_m / r))), r


def parse_positions(text):
    return [tuple(float(v) for v in item.split(",")) for item in text.split(";") if item.strip()]


def axis_spec(axis, centre_az, centre_el, step, half):
    """DG arguments for a one-axis direction set centred on (centre_az, centre_el)."""
    count = int(round(2 * half / step)) + 1
    if axis == "azimuth":
        return centre_az - half, centre_az + half, count, centre_el, centre_el, 1
    return centre_az, centre_az, 1, centre_el - half, centre_el + half, count


def template(contract, centre, axis, frequencies, distance_m):
    """Theory along one axis for a source at `centre`: relative angles and dB re the peak (peak at 0)."""
    rel = np.arange(-60.0, 60.001, 0.05)
    az = centre[0] + rel if axis == "azimuth" else np.full(rel.shape, centre[0])
    el = centre[1] + rel if axis == "elevation" else np.full(rel.shape, centre[1])
    p = steered_response(contract, centre[0], centre[1], az, el, frequencies, distance_m)
    return rel, 10 * np.log10(np.maximum(p / p.max(), 1e-12))


def measure_cut(session, axis, centre, step, half, signal, repeats, seconds, rng, label):
    """One silent pass, then shuffled repeats with the speaker on and the centre re-measured every 10
    points (its drift is corrected and reported). Signal = on minus off, in power."""
    azimuths, elevations = session.grid(*axis_spec(axis, centre[0], centre[1], step, half))
    angles = np.array(azimuths if axis == "azimuth" else elevations, float)
    n, middle = len(angles), len(angles) // 2

    def capture(s, text, duration):
        return session.capture(label, f"{axis} {angles[s]:g} {text}", s, duration, value=float(angles[s]),
                               azimuth_deg=azimuths[s] if axis == "azimuth" else azimuths[0],
                               elevation_deg=elevations[0] if axis == "azimuth" else elevations[s])

    session.sound(None)
    off = np.array([capture(s, "silent", 0.5)["mean_db"] for s in range(n)])
    session.sound(signal)
    levels, errors, drifts = [], [], []
    for repeat in range(repeats):
        order = [int(s) for s in rng.permutation(n)]
        on, refs = {}, [(0, capture(middle, "reference", seconds)["mean_db"])]
        for i, s in enumerate(order):
            on[s] = capture(s, f"repeat {repeat + 1}", seconds)
            if (i + 1) % 10 == 0 or i + 1 == n:
                refs.append((i + 1, capture(middle, "reference", seconds)["mean_db"]))
        steps, values = zip(*refs)
        drifts.append(float(max(values) - min(values)))
        place = {s: i + 0.5 for i, s in enumerate(order)}
        levels.append([on[s]["mean_db"] - (np.interp(place[s], steps, values) - np.mean(values)) for s in range(n)])
        errors.append([on[s]["std_db"] / np.sqrt(on[s]["n"]) for s in range(n)])
    session.sound(None)
    runs = np.array([[signal_db(level, off[s]) for s, level in enumerate(run)] for run in levels])
    runs[~np.isfinite(runs)] = np.nan
    within = np.sqrt(np.mean(np.square(errors), axis=0) / repeats)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        between = np.nanstd(runs, axis=0, ddof=1) / np.sqrt(repeats) if repeats > 1 else np.zeros(n)
        mean = np.nanmean(runs, axis=0)
    snr = np.array([snr_db(np.mean(np.array(levels)[:, s]), off[s]) for s in range(n)])
    return {"angles": angles, "signal_db": mean, "se_db": np.fmax(within, np.nan_to_num(between)),
            "snr_db": snr, "repeats_db": runs, "drift_db": drifts, "silent_db": off,
            "on_db": np.mean(np.array(levels), axis=0)}


def fit_axis(cut, centre_value, rel_model, db_model, shifts=None):
    """Fit the theory to the averaged cut; widen the errors to the spread of per-repeat fits if larger."""
    rel = cut["angles"] - centre_value
    ok = np.isfinite(cut["signal_db"]) & (cut["snr_db"] >= 10.0)
    if ok.sum() < 4:
        return None
    result = fit_cut(rel[ok], cut["signal_db"][ok], cut["se_db"][ok], rel_model, db_model, shifts=shifts)
    if result is None:
        return None
    shifts_found, widths = [], []
    for run in cut["repeats_db"]:
        good = ok & np.isfinite(run)
        single = fit_cut(rel[good], run[good], cut["se_db"][good] * np.sqrt(len(cut["repeats_db"])),
                         rel_model, db_model, shifts=shifts)
        if single:
            shifts_found.append(single["shift_deg"])
            widths.append(single["width_3db_deg"])
    if len(shifts_found) > 1:
        result["shift_sigma_deg"] = max(result["shift_sigma_deg"] or 0.0,
                                        float(np.std(shifts_found, ddof=1) / np.sqrt(len(shifts_found))))
        result["width_sigma_deg"] = max(result["width_sigma_deg"] or 0.0,
                                        float(np.std(widths, ddof=1) / np.sqrt(len(widths))))
    result["repeat_shifts_deg"], result["repeat_widths_deg"] = shifts_found, widths
    return result


def sidelobe_bound(cut, centre_value, rel_model, db_model):
    """Highest measured point outside the theoretical first nulls, dB re the peak (an upper bound indoors)."""
    minima = [i for i in range(1, len(db_model) - 1)
              if db_model[i] < db_model[i - 1] and db_model[i] <= db_model[i + 1] and db_model[i] < -6]
    left = max((rel_model[i] for i in minima if rel_model[i] < 0), default=None)
    right = min((rel_model[i] for i in minima if rel_model[i] > 0), default=None)
    rel = cut["angles"] - centre_value
    if left is None or right is None:
        return None
    outside = ((rel < left) | (rel > right)) & np.isfinite(cut["signal_db"])
    if not outside.any():
        return None
    return float(np.nanmax(cut["signal_db"][outside]) - np.nanmax(cut["signal_db"]))


def locate(session, contract, centre, distance_m, repeats, seconds, rng, label):
    """Find the speaker with the steered beam: 7x7 search at 4 deg around `centre`, then 1 deg cuts through its
    peak fitted with theory. Returns ((az, el), (sigma_az, sigma_el), fits, coarse peak)."""
    session.sound("B2k")
    azimuths, elevations = session.grid(centre[0] - 12, centre[0] + 12, 7, centre[1] - 12, centre[1] + 12, 7)
    grid = np.zeros((7, 7))
    for s in range(49):
        r, c = divmod(s, 7)
        grid[r, c] = session.capture(label, f"search {azimuths[c]:g}, {elevations[r]:g}", s, 0.5,
                                     azimuth_deg=azimuths[c], elevation_deg=elevations[r])["mean_db"]
    coarse = refine_peak_2d(grid, azimuths, elevations)
    found, sigma, fits = [], [], {}
    for i, axis in enumerate(("azimuth", "elevation")):
        cut = measure_cut(session, axis, coarse, 1.0, 12.0, "B2k", repeats, seconds, rng, label)
        rel_model, db_model = template(contract, coarse, axis, band_frequencies(2000.0), distance_m)
        fits[axis] = fit_axis(cut, coarse[i], rel_model, db_model)
        found.append(coarse[i] + fits[axis]["shift_deg"] if fits[axis] else float("nan"))
        sigma.append(fits[axis]["shift_sigma_deg"] if fits[axis] else float("nan"))
    session.restore_grid()
    return tuple(found), tuple(sigma), fits, coarse


def a3(session, args, out, array):
    """Azimuth and elevation cuts through the speaker per band, fitted with the theory."""
    contract = load_beam_contract()
    rng = np.random.default_rng(args.seed)
    if args.truth == "array":
        distance = place(0.0, 0.0, args.distance)
        with session.settings(**MCU_BEAM):
            (az, el), _, _, _ = locate(session, contract, (0.0, 0.0), distance, 1, 1.0, rng, "a3 locate")
        if not (np.isfinite(az) and np.isfinite(el)):
            raise RuntimeError("the steered beam did not find the speaker; is it on and roughly in front?")
        print(f"  speaker found at {az:+.2f}, {el:+.2f} deg", flush=True)
    else:
        az, el, distance = ask_position(0.0, 0.0, args.distance)
    results, plots = {}, []
    with session.settings(**MCU_BEAM):
        for band in args.bands.split(","):
            centre_hz, step, half = BANDS[band]
            for axis in ("azimuth", "elevation"):
                cut = measure_cut(session, axis, (az, el), step, half, band, args.repeats, args.seconds or 1.0, rng,
                                  "a3")
                centre_value = az if axis == "azimuth" else el
                rel_model, db_model = template(contract, (az, el), axis, band_frequencies(centre_hz), distance)
                fit = fit_axis(cut, centre_value, rel_model, db_model, shifts=np.arange(-10.0, 10.001, 0.02))
                psl = sidelobe_bound(cut, centre_value, rel_model, db_model)
                name = f"{band} {axis}"
                results[name] = {"fit": fit, "sidelobe_bound_db": psl, "drift_db": cut["drift_db"],
                                 "angles": cut["angles"], "signal_db": cut["signal_db"], "se_db": cut["se_db"],
                                 "snr_db": cut["snr_db"]}
                plots.append((name, cut, centre_value, rel_model, db_model, fit))
                if fit is None:
                    session.report.add(f"A3 {name} width", False, "not enough points 10 dB above the silent floor")
                    continue
                ok = abs(fit["width_3db_deg"] - fit["theory_width_3db_deg"]) <= 3 * (fit["width_sigma_deg"] or 0.0)
                session.report.add(f"A3 {name} width", ok and not fit["edge"],
                                   f"{fit['width_3db_deg']:.1f} +- {fit['width_sigma_deg']:.1f} deg, theory "
                                   f"{fit['theory_width_3db_deg']:.1f}; reduced chi2 {fit['reduced_chi2']:.1f}")
                session.report.info(f"A3 {name} peak", f"{fit['shift_deg']:+.2f} +- {fit['shift_sigma_deg']:.2f} deg "
                                    f"from the speaker; drift {max(cut['drift_db']):.2f} dB"
                                    + ("" if psl is None else f"; sidelobes <= {psl:.1f} dB (room-limited)"))
                if max(cut["drift_db"]) > 0.5:
                    session.report.info(f"A3 {name} drift", "over 0.5 dB: repeat this cut")
    session.values["a3"] = {"speaker": {"azimuth_deg": az, "elevation_deg": el, "distance_m": distance},
                            "cuts": results}
    figure_cuts(plots, out)


def a4(session, args, out, array):
    """Pointing at several speaker positions: 7x7 search, then 1 deg cuts through its peak, fitted."""
    contract = load_beam_contract()
    rng = np.random.default_rng(args.seed)
    rows = []
    print("A4 needs each speaker position measured (tape, or a phone measuring app): here the array cannot be "
          "its own reference.", flush=True)
    with session.settings(**MCU_BEAM):
        for nominal in parse_positions(args.positions):
            az, el, distance = ask_position(nominal[0], nominal[1], args.distance)
            found, sigma, fits, coarse = locate(session, contract, (az, el), distance, args.repeats,
                                                args.seconds or 1.0, rng, "a4")
            rows.append({"true_az": az, "true_el": el, "distance_m": distance, "measured_az": found[0],
                         "measured_el": found[1], "sigma_az": sigma[0], "sigma_el": sigma[1],
                         "coarse": list(coarse), "fits": fits})
            print(f"  true {az:+.2f}, {el:+.2f}: measured {found[0]:+.2f} +- {sigma[0]:.2f}, "
                  f"{found[1]:+.2f} +- {sigma[1]:.2f}", flush=True)
    lines = {}
    for axis, true, key, err, other in (("azimuth", "true_az", "measured_az", "sigma_az", "true_el"),
                                        ("elevation", "true_el", "measured_el", "sigma_el", "true_az")):
        pts = [r for r in rows if np.isfinite(r[key]) and abs(r[other]) < 5.0]
        if len(pts) < 3:
            continue
        x = np.array([r[true] for r in pts])
        y = np.array([r[key] for r in pts])
        s = np.sqrt(np.array([r[err] for r in pts]) ** 2 + args.alignment_sigma ** 2)
        (slope, offset), cov = np.polyfit(x, y, 1, w=1 / s, cov="unscaled")
        residual = y - (offset + slope * x)
        lines[axis] = {"slope": float(slope), "slope_sigma": float(np.sqrt(cov[0, 0])), "offset_deg": float(offset),
                       "offset_sigma_deg": float(np.sqrt(cov[1, 1])), "residuals_deg": residual.tolist(),
                       "sigma_deg": s.tolist()}
        session.report.add(f"A4 {axis} slope", abs(slope - 1) <= 3 * np.sqrt(cov[0, 0]),
                           f"{slope:.4f} +- {np.sqrt(cov[0, 0]):.4f} (1 = right scale and sign)")
        session.report.add(f"A4 {axis} offset", abs(offset) <= 0.5,
                           f"{offset:+.2f} +- {np.sqrt(cov[1, 1]):.2f} deg (alignment)")
        session.report.add(f"A4 {axis} residuals", bool(np.all(np.abs(residual) <= 3 * s)),
                           "largest " + f"{np.max(np.abs(residual)):.2f} deg, 3 sigma {3 * s[np.argmax(np.abs(residual))]:.2f}")
    session.values["a4"] = {"positions": rows, "lines": lines, "alignment_sigma_deg": args.alignment_sigma}
    figure_pointing(rows, lines, out)


def a8(session, args, out, array):
    """Beam at 0,0 with the speaker on the axis in front, then behind at the same distance."""
    sector = session.speaker_sector(0, 0)
    seconds = args.seconds or 3.0
    levels = {}
    with session.settings(**MCU_BEAM):
        for side in ("front", "behind"):
            input(f"\nA8: speaker on the array axis {side} the array, {args.distance:g} m from its centre, facing it. "
                  "Enter when ready.")
            session.sound(None)
            silent = session.capture("a8", f"{side} silent", sector, seconds)["mean_db"]
            for signal in ("B1k", "B2k", "A"):
                session.sound(signal)
                values = [signal_db(session.capture("a8", f"{side} {signal}", sector, seconds)["mean_db"], silent)
                          for _ in range(3)]
                levels[(side, signal)] = values
            session.sound(None)
    result = {}
    for signal in ("B1k", "B2k", "A"):
        front, behind = np.array(levels[("front", signal)]), np.array(levels[("behind", signal)])
        ratio = float(front.mean() - behind.mean())
        se = float(np.sqrt(front.var(ddof=1) / 3 + behind.var(ddof=1) / 3))
        result[signal] = {"front_to_back_db": ratio, "se_db": se}
        text = f"{ratio:+.1f} +- {se:.1f} dB (theory 0 for a flat array)"
        if signal == "B1k":
            session.report.add("A8 front to back, 1 kHz", abs(ratio) <= 3.0, text)
        else:
            session.report.info(f"A8 front to back, {signal}", text)
    session.values["a8"] = result


def bank_prediction(bank, contract, az, el, distance_m):
    """Where Live should report a source: the firmware's 3x3 centroid (beam_bank.c interpolate) applied to
    theory bank levels (RMS amplitudes, quiet background), 0.5-3.5 kHz."""
    baz, bel = np.asarray(bank["azimuth_deg"], float), np.asarray(bank["elevation_deg"], float)
    rows, columns = int(bank["rows"]), int(bank["columns"])
    amplitude = np.sqrt(steered_response(contract, az, el, baz, bel, A_BAND, distance_m))
    r0, c0 = divmod(int(np.argmax(amplitude)), columns)
    sx = sy = total = 0.0
    for r in range(max(0, r0 - 1), min(rows, r0 + 2)):
        for c in range(max(0, c0 - 1), min(columns, c0 + 2)):
            b = r * columns + c
            e = np.radians(bel[b])
            sx += amplitude[b] * np.cos(e) * np.sin(np.radians(baz[b]))
            sy += amplitude[b] * np.sin(e)
            total += amplitude[b]
    e = np.arcsin(sy / total)
    return float(np.degrees(np.arcsin(np.clip(sx / total / np.cos(e), -1, 1)))), float(np.degrees(e))


def live_trial(session, sound, listen):
    """Stare starts in silence (its background), the sound plays; median target over the last 4 s."""
    board, found = session.board, []

    def collect(state):
        target = (state.get("bank") or {}).get("target")
        if state.get("firmware_mode") == "B" and target:
            found.append((time.monotonic(), float(target["azimuth_deg"]), float(target["elevation_deg"])))
        return False

    board.stop()
    board.command("B")
    board.pump(2.0)
    session.sound(sound)
    end = time.monotonic() + listen
    board.pump(listen, collect)
    board.stop()
    session.sound(None)
    late = [(a, e) for t, a, e in found if t >= end - 4.0]
    return (float(np.median([a for a, _ in late])), float(np.median([e for _, e in late]))) if late else None


def l1(session, args, out, array):
    """Live direction vs speaker position, against the firmware centroid applied to theory bank levels."""
    contract = load_beam_contract()
    bank = find(json.loads(Path(default_contract_path()).read_text(encoding="utf-8")), "bank")
    rng = np.random.default_rng(args.seed)
    rows = []
    for nominal in parse_positions(args.positions or L1_POSITIONS):
        if args.truth == "array":
            distance = place(nominal[0], nominal[1], args.distance)
        else:
            az, el, distance = ask_position(nominal[0], nominal[1], args.distance)
        trials = [live_trial(session, args.sound, args.listen) for _ in range(args.trials)]
        hits = np.array([t for t in trials if t])
        if args.truth == "array":
            centre = (float(np.median(hits[:, 0])), float(np.median(hits[:, 1]))) if len(hits) else nominal
            with session.settings(**MCU_BEAM):
                (az, el), truth_sigma, _, _ = locate(session, contract, centre, distance, 1, 1.0, rng, "l1 locate")
        else:
            truth_sigma = (args.alignment_sigma, args.alignment_sigma)
        predicted = bank_prediction(bank, contract, az, el, distance) if np.isfinite([az, el]).all() else (
            float("nan"), float("nan"))
        row = {"true_az": az, "true_el": el, "distance_m": distance, "truth": args.truth,
               "truth_sigma_deg": list(truth_sigma), "predicted_az": predicted[0],
               "predicted_el": predicted[1], "trials": trials, "detected": len(hits) / len(trials)}
        if len(hits):
            row.update({"median_az": float(np.median(hits[:, 0])), "median_el": float(np.median(hits[:, 1])),
                        "spread_az": float(np.subtract(*np.percentile(hits[:, 0], [75, 25]))),
                        "spread_el": float(np.subtract(*np.percentile(hits[:, 1], [75, 25])))})
            for axis, key in (("az", 0), ("el", 1)):
                se = 1.253 * np.std(hits[:, key], ddof=1) / np.sqrt(len(hits)) if len(hits) > 1 else 0.0
                row[f"sigma_{axis}"] = float(np.hypot(se, truth_sigma[key]))
            print(f"  true {az:+.1f}, {el:+.1f}: Live {row['median_az']:+.1f}, {row['median_el']:+.1f}; "
                  f"predicted {predicted[0]:+.1f}, {predicted[1]:+.1f}; detected {row['detected']:.0%}", flush=True)
        rows.append(row)
    detected = [r for r in rows if "median_az" in r and np.isfinite([r["true_az"], r["true_el"]]).all()]
    lost = [r for r in rows if not np.isfinite([r["true_az"], r["true_el"]]).all()]
    if lost:
        session.report.add("L1 speaker found by the steered beam", False,
                           f"not found at {len(lost)} position(s): speaker off, too quiet or outside the search")
    near = all(abs(r["median_az"] - r["predicted_az"]) <= 2 * r["sigma_az"] and
               abs(r["median_el"] - r["predicted_el"]) <= 2 * r["sigma_el"] for r in detected)
    session.report.add("L1 every position within 2 sigma of the prediction", bool(detected) and near,
                       f"{len(detected)} of {len(rows)} positions detected; speaker direction from "
                       + ("the steered beam's fit (Live vs the array's own fine beam)" if args.truth == "array"
                          else "measured positions"))
    session.report.add("L1 detection", all(r["detected"] >= 0.95 for r in rows),
                       f"lowest {min(r['detected'] for r in rows):.0%} of trials")
    errors = [np.hypot(r["median_az"] - r["true_az"], r["median_el"] - r["true_el"]) for r in detected]
    if errors:
        session.report.info("L1 error vs true position", f"median {np.median(errors):.1f} deg, largest "
                                                          f"{np.max(errors):.1f} deg")
    session.values["l1"] = {"sound": args.sound, "positions": rows}
    figure_live(rows, out)


def decay_time(x, centre_hz, rate=48000, stop_s=1.5):
    """Reverberation time of an octave band from an interrupted-noise recording (ISO 3382-2 style)."""
    spectrum = np.fft.rfft(x - np.mean(x))
    f = np.fft.rfftfreq(len(x), 1 / rate)
    y = np.fft.irfft(spectrum * ((f >= centre_hz / np.sqrt(2)) & (f <= centre_hz * np.sqrt(2))), len(x))
    window = int(0.01 * rate)
    envelope = 10 * np.log10(np.convolve(y ** 2, np.ones(window) / window, mode="same") + 1e-20)
    t = np.arange(len(x)) / rate
    steady = float(np.median(envelope[(t > 0.2) & (t < stop_s - 0.1)]))
    tail = float(np.median(envelope[t > t[-1] - 0.5]))
    start = np.nonzero((t > stop_s - 0.2) & (envelope < steady - 5))[0]
    result = {"rt60_s": None, "method": None, "range_db": steady - tail}
    if not start.size:
        return result
    for depth, method in ((25, "T20"), (20, "T15"), (15, "T10")):
        if steady - tail < depth + 10:
            continue
        below = np.nonzero((t > t[start[0]]) & (envelope < steady - depth))[0]
        if below.size:
            span = slice(start[0], below[0])
            slope = np.polyfit(t[span], envelope[span], 1)[0]
            result.update({"rt60_s": float(-60 / slope) if slope < 0 else None, "method": method})
            break
    return result


def r1(session, args, out, array):
    """Interrupted broadband noise recorded on one microphone; T20 (or T15/T10) per octave."""
    session.route(1, args.microphone)
    found = {name: [] for name in OCTAVES}
    for repeat in range(args.repeats):
        session.sound("W")
        session.board.pump(2.0)
        stats, pcm, reason = record(session.board, args.record, at=1.5, action=lambda: play(None))
        session.sound(None)
        if pcm is None:
            session.report.info(f"R1 recording {repeat + 1}", f"failed: {reason}")
            continue
        for name, centre in OCTAVES.items():
            found[name].append(decay_time(pcm[:, 1], centre))
    text = input("\nRoom volume in m^3 for the critical distance (Enter to skip): ").strip()
    volume = float(text) if text else None
    result = {}
    for name, values in found.items():
        times = [v["rt60_s"] for v in values if v["rt60_s"]]
        rt = float(np.mean(times)) if times else None
        result[name] = {"rt60_s": rt, "sd_s": float(np.std(times, ddof=1)) if len(times) > 1 else None,
                        "methods": [v["method"] for v in values], "range_db": [v["range_db"] for v in values],
                        "critical_distance_m": 0.057 * np.sqrt(volume / rt) if rt and volume else None}
        session.report.info(f"R1 RT60 {name}", "not measurable (decay range too small)" if rt is None else
                            f"{rt:.2f} s ({', '.join(m for m in result[name]['methods'] if m)})"
                            + (f", critical distance {result[name]['critical_distance_m']:.2f} m (Q = 1)"
                               if result[name]["critical_distance_m"] else ""))
    session.values["r1"] = {"microphone": args.microphone, "volume_m3": volume, "octaves": result}


def s1(session, args, out, array):
    """Sensitivity of microphone channels and the beam at 1 kHz from an SPL meter, then their silent levels
    in dB SPL. The scale is at the MCU (through the DSP and the analog link), not at the PDM output."""
    microphones = [int(m) for m in args.microphones.split(",")]
    sector = session.speaker_sector(0, 0)
    seconds = args.seconds or 3.0
    input("\nS1: speaker on the array axis; SPL meter beside the array centre, same distance from the speaker, "
          "Z or C weighting, slow. Enter to start the 1 kHz tone.")
    session.sound("T1k")
    spl = float(input("SPL meter reading with the tone (dB SPL): "))
    on, off = {}, {}
    with session.settings(**WINDOWS, channel=1):
        for m in microphones:
            session.route(1, m)
            on[m] = session.capture("s1", f"microphone {m}, tone", sector, seconds, value=m)["mean_db"]
    with session.settings(**MCU_BEAM):
        on["beam"] = session.capture("s1", "beam, tone", sector, seconds)["mean_db"]
    session.sound(None)
    with session.settings(**WINDOWS, channel=1):
        for m in microphones:
            session.route(1, m)
            off[m] = session.capture("s1", f"microphone {m}, silent", sector, seconds, value=m)["mean_db"]
    with session.settings(**MCU_BEAM):
        off["beam"] = session.capture("s1", "beam, silent", sector, seconds)["mean_db"]
    result = {}
    for key in [*microphones, "beam"]:
        sensitivity = signal_db(on[key], off[key]) - (spl - 94.0)
        result[str(key)] = {"sensitivity_dbfs_at_94": sensitivity, "silent_dbfs": off[key],
                            "silent_db_spl": off[key] - sensitivity + 94.0}
        name = "beam" if key == "beam" else f"microphone {key}"
        session.report.info(f"S1 {name}", f"{sensitivity:.1f} dBFS at 94 dB SPL, 1 kHz; silent level "
                                          f"{off[key] - sensitivity + 94.0:.1f} dB SPL, so SNR re 94 dB SPL "
                                          f"{sensitivity - off[key]:.1f} dB (0.5-3.5 kHz, unweighted, includes the room)")
    session.values["s1"] = {"meter_db_spl": spl, "channels": result}


def figure_cuts(plots, out):
    import matplotlib.pyplot as plt
    if not plots:
        return
    fig, axes = plt.subplots(len(plots) // 2, 2, figsize=(12, 4.2 * (len(plots) // 2)), squeeze=False)
    for ax, (name, cut, centre_value, rel_model, db_model, fit) in zip(axes.flat, plots):
        rel = cut["angles"] - centre_value
        peak = np.nanmax(cut["signal_db"])
        ax.errorbar(rel, cut["signal_db"] - peak, yerr=cut["se_db"], fmt="o", ms=3, color="k", label="measured")
        if fit:
            shown = np.interp((rel_model - fit["shift_deg"]) * fit["scale"], rel_model, db_model)
            ax.plot(rel_model, shown + fit["level_db"] - peak, "-", label=f"fit: width {fit['width_3db_deg']:.1f} "
                    f"+- {fit['width_sigma_deg']:.1f}, peak {fit['shift_deg']:+.1f} deg")
            ax.plot(rel_model, db_model + fit["level_db"] - peak, "--", lw=1,
                    label=f"theory: width {fit['theory_width_3db_deg']:.1f} deg")
        ax.set_xlim(rel.min() - 1, rel.max() + 1)
        ax.set_ylim(-30, 3)
        ax.set_xlabel("steering angle from the speaker (deg)")
        ax.set_ylabel("dB re peak")
        ax.set_title(name)
        ax.legend(fontsize=8, loc="lower center")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "a3_cuts.png"), dpi=150)


def figure_pointing(rows, lines, out):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for ax, (axis, true, key, err) in zip(axes, (("azimuth", "true_az", "measured_az", "sigma_az"),
                                                 ("elevation", "true_el", "measured_el", "sigma_el"))):
        x = np.array([r[true] for r in rows])
        ax.errorbar(x, np.array([r[key] for r in rows]) - x, yerr=[r[err] for r in rows], fmt="o", color="k")
        if axis in lines:
            line = lines[axis]
            grid = np.linspace(x.min(), x.max(), 50)
            ax.plot(grid, line["offset_deg"] + (line["slope"] - 1) * grid, "-",
                    label=f"slope {line['slope']:.3f}, offset {line['offset_deg']:+.2f} deg")
            ax.legend(fontsize=9)
        ax.axhline(0, color="gray", lw=0.8)
        ax.set_xlabel(f"true {axis} (deg)")
        ax.set_ylabel("measured minus true (deg)")
        ax.set_title(f"Pointing error, {axis}")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "a4_pointing.png"), dpi=150)


def figure_live(rows, out):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for ax, (axis, true, other) in zip(axes, (("az", "true_az", "true_el"), ("el", "true_el", "true_az"))):
        data = [r for r in rows if abs(r[other]) < 5 and f"median_{axis}" in r]
        if not data:
            continue
        x = np.array([r[true] for r in data])
        ax.errorbar(x, [r[f"median_{axis}"] - r[true] for r in data], yerr=[r[f"sigma_{axis}"] for r in data],
                    fmt="o", color="k", label="Live, median of trials")
        ax.plot(x, [r[f"predicted_{axis}"] - r[true] for r in data], "s--", mfc="none",
                label="predicted: firmware centroid on theory levels")
        ax.axhline(0, color="gray", lw=0.8)
        ax.set_xlabel(f"true {'azimuth' if axis == 'az' else 'elevation'} (deg)")
        ax.set_ylabel("reported minus true (deg)")
        ax.legend(fontsize=9)
    fig.suptitle("Live (bank) direction error")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "l1_live.png"), dpi=150)


A9_BANDS = ("B1k", "B2k", "B3k")
A10_BANDS = {"B5k": 5000.0, "B8k": 8000.0}


def registry_parameter(group):
    """(address, words) of a DSP parameter group from the backend's register map."""
    status, registry = http("GET", "/dsp/registry")
    if status != 200:
        raise RuntimeError(f"no DSP register map ({status})")
    for parameter in registry["parameters"]:
        if parameter.get("group") == group:
            return int(parameter["address"]), int(parameter["words"])
    raise RuntimeError(f"no {group} in the DSP register map")


def read_words(address, count):
    status, body = http("POST", "/dsp/read", {"address": address, "count": count})
    if status != 200:
        raise RuntimeError(f"DSP read refused ({status}): {body}")
    return [int(word) for word in body["words"]]


def highpass_taps(cutoff_hz, taps=11, rate=48000.0):
    """Linear-phase FIR high-pass: centre tap minus a Hamming-windowed sinc low-pass with unity DC gain."""
    n = np.arange(taps) - (taps - 1) / 2
    low = 2 * cutoff_hz / rate * np.sinc(2 * cutoff_hz / rate * n) * np.hamming(taps)
    high = -low / low.sum()
    high[(taps - 1) // 2] += 1.0
    return high


def to_word(value):
    """8.24 fixed point as the 32-bit two's-complement word the DSP stores."""
    return int(round(value * (1 << 24))) & 0xFFFFFFFF


@contextmanager
def fir_replaced(board, highpass_hz=None):
    """Beam FIR stage 1 as a centre tap of 1 (Study's Bypass), stage 2 the same or a high-pass at highpass_hz;
    the flashed taps are put back and checked."""
    board.stop()
    stages = [registry_parameter(group) for group in ("fir1", "fir2")]
    originals = [read_words(address, words) for address, words in stages]
    try:
        for index, (address, words) in enumerate(stages):
            taps = np.zeros(words)
            taps[words // 2] = 1.0
            if index == 1 and highpass_hz:
                taps = highpass_taps(highpass_hz, words)
            write_words({address + k: to_word(t) for k, t in enumerate(taps)})
        yield
    finally:
        board.stop()
        for (address, words), taps in zip(stages, originals):
            write_words({address + k: tap for k, tap in enumerate(taps)})
        if [read_words(address, words) for address, words in stages] != originals:
            raise RuntimeError("FIR taps not restored: reset the MCU (its boot reloads the DSP program)")


def excess(on_db, off_db):
    """Signal power: on minus off, in power; nan when not above the floor."""
    value = float(power(on_db) - power(off_db))
    return value if value > 0 else float("nan")


def band_directivity_db(contract, centre_hz):
    """Directivity index at 0,0 for diffuse noise over a 1/3-octave band (power average of 1/DF)."""
    inverse = np.mean([10 ** (-directivity_index_db(contract, f) / 10) for f in band_frequencies(centre_hz)])
    return float(-10 * np.log10(inverse))


def directivity_from_levels(microphones, path_gain, microphone_power, beam_power):
    """DF = N^2 * G * P_mic / P_beam: the diffuse-field beam/microphone ratio corrected by the path gain G."""
    return float(microphones ** 2 * path_gain * microphone_power / beam_power)


def grating_lobes(rel_deg, db, main_half_deg, threshold_db=-10.0):
    """Local maxima at or above threshold_db (re the main peak) outside +-main_half_deg: [(angle, level)]."""
    return [(float(rel_deg[i]), float(db[i])) for i in range(1, len(db) - 1)
            if abs(rel_deg[i]) > main_half_deg and db[i] >= threshold_db and db[i] >= db[i - 1] and db[i] >= db[i + 1]]


def first_null_half_width(rel_deg, db):
    minima = [abs(rel_deg[i]) for i in range(1, len(db) - 1)
              if db[i] < db[i - 1] and db[i] <= db[i + 1] and db[i] < -6]
    return min(minima) if minima else 10.0


def a9(session, args, out, array):
    """(1) Path gain G: one microphone through the beam (others muted) vs the same microphone on Ch1.
    (2) Directivity index by the diffuse-field method: speaker facing away, beam/microphone power ratio / G.
    (3) Speaker close and in front: signal gain and noise gain of the full beam (/ G); their difference is the
    array gain here. WNG proper (16.4 dB) needs a free field and a self-noise-limited floor."""
    contract = load_beam_contract()
    data = json.loads(Path(default_contract_path()).read_text(encoding="utf-8"))
    addresses = [int(a) for a in find(data, "gain_addresses")]
    unity = [int(w) for w in find(data, "gain_fixpt_8_24")]
    count = len(addresses)
    references = [int(m) for m in np.random.default_rng(1).permutation(count)[:args.references]]
    seconds = args.seconds or 3.0
    centre = session.speaker_sector(0, 0)
    rng = np.random.default_rng(args.seed)

    def beam(label, sector=centre):
        return session.capture("a9", label, sector, seconds)["mean_db"]

    def mics(label, which, source=1):
        values = {}
        with session.settings(**WINDOWS, channel=1):
            for m in which:
                session.route(source, m)
                values[m] = session.capture("a9", f"{label}, Ch1 {source},{m}", centre, seconds, value=m)["mean_db"]
        return values

    def gains_only(on):
        session.board.stop()
        write_words({a: (unity[k] if k in on else 0) for k, a in enumerate(addresses)})

    place(0.0, 0.0, args.distance)
    path, links = {}, {}
    try:
        with session.settings(**MCU_BEAM):
            gains_only(set())
            links["ch0"] = beam("link floor, all muted")
            links["ch1"] = mics("link floor, all muted", [0], source=0)[0]
            for m in references[:2]:
                gains_only({m})
                for band in A9_BANDS:
                    session.sound(None)
                    off_b, off_m = beam(f"path {band} silent, microphone {m} alone"), mics(f"path {band} silent", [m])[m]
                    session.sound(band)
                    on_b, on_m = beam(f"path {band}, microphone {m} alone"), mics(f"path {band}", [m])[m]
                    path.setdefault(band, []).append(excess(on_b, off_b) / excess(on_m, off_m))
                session.sound(None)
    finally:
        gains_only(set(range(count)))
        reload_dsp(session.board)
    g = {band: float(np.nanmean(values)) for band, values in path.items()}
    result = {"references": references, "link_floor_dbfs": links, "path_gain_db": {}, "bands": {}}
    for band in A9_BANDS:
        result["path_gain_db"][band] = [10 * np.log10(v) if np.isfinite(v) else None for v in path[band]]
        session.report.info(f"A9 path gain {band}", f"{10 * np.log10(g[band]):+.1f} dB, beam path vs microphone path "
                            f"(microphones {references[:2]}: " + ", ".join(
                                f"{v:+.1f}" for v in result["path_gain_db"][band] if v is not None) + ")")

    input("\nA9 diffuse field: turn the speaker to face a far wall or corner, as far from the array as the room "
          "allows, ideally with no direct line to the array. Enter when ready.")
    directions = [(0.0, 0.0), (34.29, 0.0), (-34.29, 0.0), (0.0, 34.29), (0.0, -34.29)]
    sectors = [session.speaker_sector(az, el) for az, el in directions]
    with session.settings(**MCU_BEAM):
        for band in A9_BANDS:
            session.sound(None)
            off_b, off_m = [beam(f"diffuse {band} silent", s) for s in sectors], mics(f"diffuse {band} silent", references)
            session.sound(band)
            on_b, on_m = [beam(f"diffuse {band}", s) for s in sectors], mics(f"diffuse {band}", references)
            session.sound(None)
            beam_power = np.array([excess(a, b) for a, b in zip(on_b, off_b)])
            mic_power = np.array([excess(on_m[m], off_m[m]) for m in references])
            df = directivity_from_levels(count, g[band], np.nanmean(mic_power), np.nanmean(beam_power))
            theory = band_directivity_db(contract, BANDS[band][0])
            spread = float(10 * np.log10(np.nanmax(beam_power) / np.nanmin(beam_power)))
            measured = 10 * np.log10(df) if df > 0 else float("nan")
            result["bands"].setdefault(band, {}).update({"di_db": measured, "di_theory_db": theory,
                                                         "beam_spread_db": spread})
            session.report.info(f"A9 directivity index {band}", f"{measured:.1f} dB, theory {theory:.1f} "
                                f"(diffuse-field method, approximate in a room); beam spread over 5 directions "
                                f"{spread:.1f} dB (large = direct sound still present)")

    near = place(0.0, 0.0, args.near_distance)
    with session.settings(**MCU_BEAM):
        (az, el), _, _, _ = locate(session, contract, (0.0, 0.0), near, 1, 1.0, rng, "a9 locate")
        if not np.isfinite([az, el]).all():
            az, el = 0.0, 0.0
        session.grid(az, az, 1, el, el, 1)
        for band in A9_BANDS:
            session.sound(None)
            off_b, off_m = beam(f"near {band} silent", 0), mics(f"near {band} silent", references)
            session.sound(band)
            on_b, on_m = beam(f"near {band}", 0), mics(f"near {band}", references)
            session.sound(None)
            signal = excess(on_b, off_b) / np.nanmean([excess(on_m[m], off_m[m]) for m in references]) / g[band]
            noise = (excess(off_b, links["ch0"]) /
                     np.nanmean([excess(off_m[m], links["ch1"]) for m in references]) / g[band])
            direct = count ** 2 * float(steered_response(contract, az, el, az, el,
                                                         band_frequencies(BANDS[band][0]), near)[0])
            values = {"signal_gain_db": 10 * np.log10(signal), "signal_gain_direct_theory_db": 10 * np.log10(direct),
                      "noise_gain_db": 10 * np.log10(noise), "noise_gain_independent_db": 10 * np.log10(count)}
            values["array_gain_db"] = values["signal_gain_db"] - values["noise_gain_db"]
            result["bands"].setdefault(band, {}).update(values)
            session.report.info(f"A9 gains {band}", f"signal {values['signal_gain_db']:.1f} dB (direct sound only "
                                f"{values['signal_gain_direct_theory_db']:.1f}), noise {values['noise_gain_db']:.1f} dB "
                                f"(independent noise {10 * np.log10(count):.1f}); array gain here "
                                f"{values['array_gain_db']:+.1f} dB, WNG theory {10 * np.log10(count):.1f}")
    result["speaker_near"] = {"azimuth_deg": az, "elevation_deg": el, "distance_m": near}
    session.values["a9"] = result


def a10(session, args, out, array):
    """Spatial aliasing: speaker straight ahead, the beam steered across +-60 deg with the FIR pair replaced by
    a 3.5 kHz high-pass (removes the low-frequency floor) and the MCU window unfiltered (band 0), at 5 kHz
    (theory: no grating lobe in view) and 8 kHz (lobes near +-50 deg). Measured false peaks against theory."""
    contract = load_beam_contract()
    distance = place(0.0, 0.0, args.distance)
    seconds = args.seconds or 1.0
    rng = np.random.default_rng(args.seed)
    results, plots = {}, []
    with fir_replaced(session.board, highpass_hz=3500.0), session.settings(**{**MCU_BEAM, "band": 0}):
        for band, centre_hz in A10_BANDS.items():
            for axis in ("azimuth", "elevation"):
                cut = measure_cut(session, axis, (0.0, 0.0), 2.5, 60.0, band, 1, seconds, rng, "a10")
                db = cut["signal_db"] - np.nanmax(cut["signal_db"])
                peak = float(cut["angles"][int(np.nanargmax(cut["signal_db"]))])
                source = (peak, 0.0) if axis == "azimuth" else (0.0, peak)
                rel_model, db_model = template(contract, source, axis, band_frequencies(centre_hz), distance)
                half = first_null_half_width(rel_model, db_model)
                limit = 60.0 - abs(peak)
                expected = [lobe for lobe in grating_lobes(rel_model, db_model, half, -6.0) if abs(lobe[0]) <= limit]
                found = grating_lobes(cut["angles"] - peak, np.nan_to_num(db, nan=-99.0), half, -10.0)
                matched = all(any(abs(f[0] - e[0]) <= 5.0 and abs(f[1] - e[1]) <= 6.0 for f in found) for e in expected)
                clean = expected or not any(level >= -6.0 for _, level in found)
                name = f"{band} {axis}"
                results[name] = {"peak_deg": peak, "expected": expected, "found": found,
                                 "max_on_dbfs": float(np.nanmax(cut["on_db"])), "angles": cut["angles"],
                                 "signal_db": cut["signal_db"]}
                plots.append((name, cut["angles"] - peak, db, rel_model, db_model))
                session.report.add(f"A10 {name}", matched and bool(clean),
                                   "theory lobes " + (", ".join(f"{a:+.0f} deg {l:.0f} dB" for a, l in expected) or "none")
                                   + "; measured " + (", ".join(f"{a:+.0f} deg {l:.0f} dB" for a, l in found
                                                               if l >= -6.0) or "none above -6 dB"))
                if results[name]["max_on_dbfs"] > -12.0:
                    session.report.add(f"A10 {name} level", False, f"{results[name]['max_on_dbfs']:.1f} dBFS RMS: band "
                                       "noise peaks about 10 dB higher and may clip; turn the speaker down, repeat")
    session.values["a10"] = {"distance_m": distance, "cuts": results}
    figure_a10(plots, out)


def figure_a10(plots, out):
    import matplotlib.pyplot as plt
    if not plots:
        return
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), squeeze=False)
    for ax, (name, rel, db, rel_model, db_model) in zip(axes.flat, plots):
        ax.plot(rel, db, "o-", ms=3, color="k", label="measured")
        ax.plot(rel_model, db_model, "--", lw=1, label="theory")
        ax.set_xlim(-62, 62)
        ax.set_ylim(-30, 3)
        ax.set_xlabel("steering angle from the speaker (deg)")
        ax.set_ylabel("dB re main lobe")
        ax.set_title(name)
        ax.legend(fontsize=8, loc="lower center")
    fig.suptitle("A10: grating lobes (false beams) above the spatial aliasing frequency")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "a10_aliasing.png"), dpi=150)


TESTS = {"n1": n1, "n2": n2, "a3": a3, "a4": a4, "a8": a8, "a9": a9, "a10": a10, "l1": l1, "r1": r1, "s1": s1}
SOUND_TESTS = {"a3", "a4", "a8", "a9", "a10", "l1", "r1", "s1"}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("test", choices=sorted(TESTS))
    parser.add_argument("--seconds", type=float, help="capture per point (default per test)")
    parser.add_argument("--cycles", type=int, default=3, help="n1/n2: interleaved repeats, alternating order")
    parser.add_argument("--repeats", type=int, default=3, help="a3/a4/r1: repeats per set")
    parser.add_argument("--distance", type=float, default=2.0, help="nominal speaker distance (m)")
    parser.add_argument("--bands", default="B2k", help="a3: comma list of B1k, B2k, B3k")
    parser.add_argument("--positions", default="-30,0;-15,0;0,0;15,0;30,0",
                        help="a4/l1: 'az,el;az,el;...' nominal positions (l1 default: 13 positions)")
    parser.add_argument("--alignment-sigma", type=float, default=0.4, help="speaker placement 1-sigma (deg)")
    parser.add_argument("--truth", choices=("array", "measured"), default="array",
                        help="a3/l1: find the speaker with the steered beam, or type in measured positions")
    parser.add_argument("--trials", type=int, default=5, help="l1: trials per position")
    parser.add_argument("--listen", type=float, default=8.0, help="l1: seconds of sound per trial")
    parser.add_argument("--sound", default="A", help="l1: signal name or WAV path (e.g. audio/drone.wav)")
    parser.add_argument("--microphone", type=int, default=4, help="r1: microphone on Ch1")
    parser.add_argument("--microphones", default="0,4,20,24", help="s1: microphones to calibrate")
    parser.add_argument("--references", type=int, default=8, help="a9: reference microphones (seeded, spread)")
    parser.add_argument("--near-distance", type=float, default=1.0, help="a9: speaker distance for the gains (m)")
    parser.add_argument("--record", type=float, default=5.0, help="r1: recording length (s)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", help="output folder (default: temp)")
    args = parser.parse_args()
    if args.test == "l1" and args.positions == parser.get_default("positions"):
        args.positions = L1_POSITIONS
    out = args.out or os.path.join(tempfile.gettempdir(), time.strftime(f"heimdall_{args.test}_%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    signals = make_signals(out) if args.test in SOUND_TESTS else {}
    if args.test == "l1" and args.sound not in signals:
        if not os.path.exists(args.sound):
            sys.exit(f"no signal or file {args.sound}")
        signals[args.sound] = args.sound
    report = Results()
    array = Array()
    with connect(WS_URL, open_timeout=5, max_size=None) as socket, \
            open(os.path.join(out, "captures.csv"), "w", newline="") as handle:
        board = Board(socket)
        board.pump(1.0)
        if not board.state.get("connected"):
            sys.exit("the backend is not connected to the board")
        reload_dsp(board)
        writer = csv.DictWriter(handle, FIELDS, extrasaction="ignore")
        writer.writeheader()
        session = Session(board, signals, writer, report)
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            TESTS[args.test](session, args, out, array)
        finally:
            session.restore()
        summary = {"test": args.test, "started": started, "arguments": vars(args),
                   "map_hash": board.state.get("map_hash"), "values": session.values,
                   "checks": [{"name": name, "result": "info" if passed is None else ("pass" if passed else "fail"),
                               "detail": detail} for name, passed, detail in report.rows]}
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o))
    print(f"results: {out}")


if __name__ == "__main__":
    main()
