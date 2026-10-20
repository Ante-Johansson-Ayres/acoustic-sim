"""Heimdall characterisation tests (MAX78002/docs/CHARACTERISATION_PLAN.md), run through the GUI backend.

    python scripts/characterise.py n1     noise floor vs number of microphones (speaker off)
    python scripts/characterise.py n2     noise floor vs steering direction (speaker off)

Each test compares the measurement with three models computed from the deployed microphone positions:
noise independent at every microphone (self-noise), noise common to all channels (electrical pickup),
and diffuse room noise. Needs sim_server.py connected to the board and a quiet room. Writes every capture
(captures.csv), summary.json and a figure to --out, and restores gains, settings, Ch1 and the grid.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board_regression import WS_URL, Board, connect, http  # noqa: E402
from heimdall_acoustic import default_contract_path, load_beam_contract  # noqa: E402
from study_session import FIELDS, MCU_BEAM, Results, Session, power, power_mean_db  # noqa: E402

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
                                              args.seconds, value=count)
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
                    capture = session.capture("n2", f"{az:g}, {el:g}, cycle {cycle + 1}", sector, args.seconds,
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


TESTS = {"n1": n1, "n2": n2}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("test", choices=sorted(TESTS))
    parser.add_argument("--seconds", type=float, default=4.0, help="capture per point and cycle")
    parser.add_argument("--cycles", type=int, default=3, help="interleaved repeats, alternating order")
    parser.add_argument("--out", help="output folder (default: temp)")
    args = parser.parse_args()
    out = args.out or os.path.join(tempfile.gettempdir(), time.strftime(f"heimdall_{args.test}_%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
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
        session = Session(board, {}, writer, report)
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            TESTS[args.test](session, args, out, array)
        finally:
            session.restore()
        summary = {"test": args.test, "started": started, "arguments": vars(args),
                   "map_hash": board.state.get("map_hash"), "values": session.values,
                   "notes": [detail for _, _, detail in report.rows]}
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, default=float)
    print(f"results: {out}")


if __name__ == "__main__":
    main()
