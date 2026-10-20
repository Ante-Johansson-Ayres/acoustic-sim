"""Board regression through the live GUI backend (sim_server.py connected to the board).

Checks, first with the tonal detector off and then on: DSP reads of 8 to 11 bytes while idle,
in Stare and during a scan; settings and parameter writes; measurement windows; recordings and
their download; and that capture channel 0 carries the beam. Records over the device's
recording. Restores the settings and Ch1 routing it found and leaves the firmware idle.
Exit code 1 if any check fails.

--soak-min runs Stare in the demo configuration afterwards (or alone with --soak-only): a read
every 10 s, optionally a WAV played 20 s of every minute through the default output
(--soak-sound), and every --soak-cycle-min minutes a 5 s recording during the sound, then its
download, an I check and a SET in the quiet. One CSV row every 10 s (--soak-log).

    python scripts/board_regression.py [--reads 30] [--downloads 2]
    python scripts/board_regression.py --soak-only --soak-min 15 --soak-sound audio/drone.wav
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

import numpy as np
from websockets.sync.client import connect


API = "http://127.0.0.1:8766"
WS_URL = "ws://127.0.0.1:8766/realtime_hw"
# Read commands of 8, 9, 10 and 11 bytes including the newline.
READS = [(32, 7), (32, 16), (1030, 7), (1030, 16)]


def http(method, path, body=None, timeout=15):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get_content_type() == "application/json":
                return response.status, json.loads(raw)
            return response.status, raw
    except urllib.error.HTTPError as error:
        return error.code, error.read()[:200].decode(errors="replace")


class Board:
    def __init__(self, socket):
        self.socket = socket
        self.state = {}
        self.last_frame = time.monotonic()

    def pump(self, seconds, until=None):
        """Apply frames for up to `seconds`; True as soon as `until(state)` holds."""
        end = time.monotonic() + seconds
        while (remaining := end - time.monotonic()) > 0:
            try:
                message = json.loads(self.socket.recv(timeout=remaining))
            except TimeoutError:
                break
            if message.get("type") in ("init", "frame"):
                self.last_frame = time.monotonic()
                self.state.update(message)
                if until is not None and until(self.state):
                    return True
        return False

    def command(self, text):
        self.socket.send(json.dumps({"type": "command", "command": text}))

    def mode(self):
        return self.state.get("firmware_mode")

    def stop(self):
        if self.mode() != "IDLE":
            self.command("X")
            self.pump(3.0, lambda s: s.get("firmware_mode") == "IDLE")
        return self.mode() == "IDLE"

    def set(self, key, value):
        status, _ = http("POST", "/dsp/set", {"key": key, "value": value})
        self.pump(0.2)
        return status == 200


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, passed, detail):
        self.rows.append((name, passed, detail))
        print(f"{'PASS' if passed else 'FAIL'}  {name:38s} {detail}", flush=True)


def reads(board, report, label, rounds):
    ok = timeouts = errors = 0
    for i in range(rounds):
        address, count = READS[i % len(READS)]
        status, body = http("POST", "/dsp/read", {"address": address, "count": count})
        if status == 200 and len(body.get("words", [])) == count:
            ok += 1
        elif status == 500:
            timeouts += 1
        else:
            errors += 1
        board.pump(0.02)
    report.add(f"reads, {label}", ok == rounds,
               f"{ok}/{rounds} answered, {timeouts} timed out, {errors} refused")


def settings_and_writes(board, report, rounds):
    guard = int((board.state.get("settings") or {}).get("guard_us", 500))
    ok = sum(board.set("guard_us", guard + 100 * (1 + i % 5)) for i in range(rounds))
    restored = board.set("guard_us", guard)
    report.add("SET, idle", ok == rounds and restored, f"{ok}/{rounds} applied, restored {restored}")
    status, body = http("POST", "/dsp/read", {"address": 32, "count": 1})
    word = body["words"][0] if status == 200 else 1 << 24
    ok = 0
    for _ in range(rounds):
        status, _ = http("POST", "/dsp/write", {"writes": [{"address": 32, "word": word}]})
        ok += status == 200
        board.pump(0.02)
    report.add("PW (same value back), idle", ok == rounds, f"{ok}/{rounds} written and read back")


def windows(board, report, rounds):
    if not board.set("measure", 1):
        report.add("measurement windows, idle", False, "SET,measure,1 was not applied")
        return
    count = int((board.state.get("last_measurement") or {}).get("count", 0))
    board.command("S,24")
    board.pump(1.0, lambda s: int((s.get("last_measurement") or {}).get("count", 0)) > count)
    measured = errors = 0
    for _ in range(rounds):
        count = int((board.state.get("last_measurement") or {}).get("count", 0))
        error = board.state.get("last_error")
        board.command("M")
        board.pump(1.0, lambda s: int((s.get("last_measurement") or {}).get("count", 0)) > count
                   or s.get("last_error") != error)
        if int((board.state.get("last_measurement") or {}).get("count", 0)) > count:
            measured += 1
        else:
            errors += 1
    report.add("measurement windows, idle", measured == rounds,
               f"{measured}/{rounds} measured, {errors} failed (last error {board.state.get('last_error')})")


def record(board, seconds, at=None, action=None):
    """Record, stop and download; the stop record's statistics and the stereo PCM, or a reason.
    `action()` runs `at` seconds into the recording (for example to stop a sound)."""
    board.command("R,1")
    if not board.pump(3.0, lambda s: s.get("recording_state") == "recording"):
        return None, None, f"did not start ({board.state.get('last_error')})"
    if action is not None:
        board.pump(at)
        action()
        board.pump(seconds - at)
    else:
        board.pump(seconds)
    board.command("R,0")
    if not board.pump(5.0, lambda s: s.get("recording_state") in ("ready", "error")):
        return None, None, "did not stop"
    stats = board.state.get("last_recording") or {}
    if board.state.get("recording_state") != "ready":
        return stats, None, f"stop failed ({board.state.get('last_error')})"
    if not board.stop():
        return stats, None, "firmware did not go idle for the download"
    board.command("D")
    board.pump(60.0, lambda s: s.get("recording_state") in ("downloaded", "error"))
    if board.state.get("recording_state") != "downloaded":
        return stats, None, (f"download {board.state.get('recording_state')}: "
                             f"{board.state.get('audio_download_error') or board.state.get('last_error')}")
    status, data = http("GET", "/hw_recording.wav", timeout=30)
    if status != 200:
        return stats, None, f"WAV fetch returned {status}"
    with wave.open(io.BytesIO(data)) as wav:
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").reshape(-1, wav.getnchannels())
    return stats, pcm / 32768.0, ""


def recordings(board, report, label, rounds, stare):
    ok = 0
    reasons = []
    for _ in range(rounds):
        if stare:
            board.command("B")
            board.pump(1.0, lambda s: s.get("firmware_mode") == "B")
        stats, pcm, reason = record(board, 2.0)
        overruns = (stats or {}).get("overruns")
        if pcm is not None and overruns == 0:
            ok += 1
        else:
            reasons.append(reason or f"overruns {overruns}")
    report.add(f"recordings, {label}", ok == rounds,
               f"{ok}/{rounds} recorded and downloaded intact" + (f"; {reasons[0]}" if reasons else ""))


def channel_order(board, report):
    ch1 = dict(board.state.get("ch1") or {"source": 0, "index": 0})
    board.command("O,1,0")
    board.pump(0.5)
    _, pcm, reason = record(board, 1.0)
    board.command(f"O,{ch1.get('source', 0)},{ch1.get('index', 0)}")
    board.pump(0.5)
    if pcm is None:
        report.add("channel order (beam on channel 0)", False, reason)
        return
    # The beam sums 44 microphones, so it is far louder than microphone 0 below 3.5 kHz.
    spectrum = np.abs(np.fft.rfft((pcm - pcm.mean(axis=0)) * np.hanning(len(pcm))[:, None], axis=0)) ** 2
    freqs = np.fft.rfftfreq(len(pcm), 1 / 48000)
    band = spectrum[(freqs >= 20) & (freqs < 3500)].sum(axis=0)
    difference = 10 * np.log10(band[0] / band[1])
    report.add("channel order (beam on channel 0)", difference >= 10, f"beam {difference:+.1f} dB over mic 0")


def play(path, loop=False):
    """Start a WAV on the default output, or stop it with None."""
    import winsound
    if path:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | (winsound.SND_LOOP if loop else 0))
    else:
        winsound.PlaySound(None, 0)


class Soak:
    SOUND_S = 20.0
    MINUTE_S = 60.0
    # A target this long after the sound stopped counts as one in the quiet.
    QUIET_GRACE_S = 5.0

    def __init__(self, board, args, writer):
        self.board, self.args, self.writer = board, args, writer
        self.start = self.window_start = time.monotonic()
        self.window_maps = self.maps()
        self.window_stare = True
        self.window_tones = 0
        self.reads = self.reads_ok = 0
        self.rates, self.tone_rates, self.errors, self.cycles, self.phases = [], [], [], [], []
        self.left_stare = self.quiet_targets = self.tone_updates = self.stalls = 0
        self.last_error = board.state.get("last_error")
        self.last_tones = None
        self.protocol_errors = board.state.get("protocol_errors", 0)

    def maps(self):
        return int((self.board.state.get("bank") or {}).get("maps", 0))

    def row(self, event, sound, rate="", tone_rate="", detail=""):
        target = self.board.state.get("target") or {}
        self.writer.writerow([f"{time.monotonic() - self.start:.1f}", event, self.board.mode(), int(sound),
                              rate, target.get("azimuth_deg", ""), target.get("elevation_deg", ""),
                              self.reads_ok, self.reads,
                              self.board.state.get("protocol_errors", 0) - self.protocol_errors, tone_rate, detail])

    def watch(self, seconds, sound, phase=None):
        """Pump frames, tracking targets, errors and tone reports; a read and a row every 10 s."""
        end = time.monotonic() + seconds
        while (remaining := end - time.monotonic()) > 0:
            self.board.pump(min(0.25, remaining))
            state = self.board.state
            now = time.monotonic()
            if now - self.board.last_frame > 3.0:
                self.stalls += 1
                self.row("no frames", sound, detail=f"none for {now - self.board.last_frame:.1f} s")
                self.board.last_frame = now
            if state.get("last_error") != self.last_error:
                self.last_error = state.get("last_error")
                self.errors.append((round(now - self.start, 1), self.last_error))
            tones = json.dumps(state.get("tones"), sort_keys=True)
            if tones != self.last_tones:
                self.last_tones = tones
                self.window_tones += 1
            if self.board.mode() != "B":
                self.left_stare += 1
                self.window_stare = False
                self.row("left Stare", sound, detail=json.dumps(state.get("last_error")))
                self.board.command("B")
                self.board.pump(2.0, lambda s: s.get("firmware_mode") == "B")
            target = state.get("target")
            if phase is not None and target:
                if sound and phase.get("detected_s") is None:
                    bank = (state.get("bank") or {}).get("target") or {}
                    phase.update(detected_s=round(now - phase["start"], 1),
                                 azimuth=bank.get("azimuth_deg", target.get("azimuth_deg")),
                                 elevation=bank.get("elevation_deg", target.get("elevation_deg")))
                if not sound and now - phase["start"] > self.QUIET_GRACE_S and not phase.get("quiet_target"):
                    phase["quiet_target"] = True
                    self.quiet_targets += 1
                    bank = (state.get("bank") or {}).get("target") or {}
                    self.row("quiet target", False, detail=f"bank direction {bank.get('azimuth_deg')}, "
                                                           f"{bank.get('elevation_deg')} deg")
            if now - self.window_start >= 10.0:
                self.close_window(sound)

    def close_window(self, sound):
        span = time.monotonic() - self.window_start
        rate = (self.maps() - self.window_maps) / span if self.window_stare else None
        tone_rate = self.window_tones / span if self.window_stare else None
        if rate is not None:
            self.rates.append(rate)
            self.tone_rates.append(tone_rate)
        status, _ = http("POST", "/dsp/read", {"address": 1030, "count": 16})
        self.reads += 1
        self.reads_ok += status == 200
        self.row("window", sound, "" if rate is None else f"{rate:.1f}",
                 "" if tone_rate is None else f"{tone_rate:.1f}")
        self.window_start, self.window_maps = time.monotonic(), self.maps()
        self.window_stare, self.window_tones = self.board.mode() == "B", 0

    def cycle(self):
        """In the quiet after a recording: download it, check the DSP, apply a SET, restart Stare."""
        self.window_stare = False
        stats = self.board.state.get("last_recording") or {}
        recorded = self.board.state.get("recording_state") == "ready" and stats.get("overruns") == 0
        self.board.stop()
        self.board.command("D")
        self.board.pump(60.0, lambda s: s.get("recording_state") in ("downloaded", "error"))
        downloaded = self.board.state.get("recording_state") == "downloaded"
        why = "" if downloaded else f" ({self.board.state.get('audio_download_error') or self.board.state.get('last_error')})"
        self.board.command("I")
        self.board.pump(3.0)
        dsp, tables = self.board.state.get("dsp") or {}, self.board.state.get("tables") or {}
        checked = bool(dsp.get("matches")) and tables.get("verified") == tables.get("words")
        applied = self.board.set("guard_us", int((self.board.state.get("settings") or {}).get("guard_us", 500)))
        self.board.command("B")
        restarted = self.board.pump(2.0, lambda s: s.get("firmware_mode") == "B")
        ok = recorded and downloaded and checked and applied and restarted
        self.cycles.append(ok)
        self.row("cycle", False, detail=(f"recorded {recorded} ({stats.get('bytes')} B, overruns "
                                         f"{stats.get('overruns')}), downloaded {downloaded}{why}, I {checked}, "
                                         f"SET {applied}, Stare {restarted}"))

    def minute(self, index):
        sound = self.args.soak_sound
        cycle = self.args.soak_cycle_min > 0 and index % self.args.soak_cycle_min == self.args.soak_cycle_min - 1
        start = time.monotonic()
        phase = {"minute": index, "start": start}
        if sound:
            play(sound)
        if cycle:
            self.watch(10.0, bool(sound), phase)
            self.board.command("R,1")
            self.watch(5.0, bool(sound), phase)
            self.board.command("R,0")
            self.board.pump(5.0, lambda s: s.get("recording_state") in ("ready", "error"))
        self.watch(max(0.0, start + self.SOUND_S - time.monotonic()), bool(sound), phase)
        if sound:
            bank = (self.board.state.get("bank") or {}).get("target") or {}
            if self.board.state.get("target") and bank:
                phase.update(end_azimuth=bank.get("azimuth_deg"), end_elevation=bank.get("elevation_deg"))
            play(None)
            self.phases.append(phase)
            self.row("sound phase", True, detail=f"first target after {phase.get('detected_s')} s; at the end "
                                                 f"{phase.get('end_azimuth')}, {phase.get('end_elevation')} deg")
        quiet = {"start": time.monotonic()}
        if cycle:
            self.cycle()
        self.watch(max(0.0, start + self.MINUTE_S - time.monotonic()), False, quiet if sound else None)


def soak(board, report, args):
    label = f"soak {args.soak_min:g} min, detector {'on' if args.soak_tone else 'off'}"
    if args.soak_sound and not os.path.isfile(args.soak_sound):
        report.add(label, False, f"no such WAV: {args.soak_sound}")
        return
    if args.soak_tone and not board.set("tone", args.soak_tone):
        report.add(label, False, f"SET,tone,{args.soak_tone} was not applied")
        return
    if not board.set("measure", 1):
        report.add(label, False, "SET,measure,1 was not applied")
        return
    path = args.soak_log or os.path.join(tempfile.gettempdir(),
                                         time.strftime("heimdall_soak_%Y%m%d_%H%M%S.csv"))
    board.command("B")
    board.pump(2.0, lambda s: s.get("firmware_mode") == "B")
    try:
        with open(path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["elapsed_s", "event", "mode", "sound", "maps_per_s", "target_az", "target_el",
                             "reads_ok", "reads", "protocol_errors_added", "tone_updates_per_s", "detail"])
            run = Soak(board, args, writer)
            for index in range(int(round(args.soak_min))):
                run.minute(index)
                handle.flush()
    finally:
        if args.soak_sound:
            play(None)
        board.stop()
        if args.soak_tone:
            board.set("tone", 0)
    print(f"soak log: {path}")

    report.add(f"{label}: reads", run.reads_ok == run.reads, f"{run.reads_ok}/{run.reads} answered")
    low = min(run.rates, default=0.0)
    report.add(f"{label}: map rate", low >= 18, f"lowest 10 s window {low:.1f} maps/s over {len(run.rates)} windows")
    added = board.state.get("protocol_errors", 0) - run.protocol_errors
    report.add(f"{label}: errors", added == 0 and not run.errors and run.left_stare == 0 and run.stalls == 0,
               f"protocol errors +{added}, error records {run.errors[:3]}, left Stare {run.left_stare}, "
               f"frame stalls {run.stalls}")
    if run.cycles:
        report.add(f"{label}: record, download, I, SET", all(run.cycles),
                   f"{sum(run.cycles)}/{len(run.cycles)} cycles clean")
    if args.soak_tone:
        mean = sum(run.tone_rates) / max(1, len(run.tone_rates))
        report.add(f"{label}: tone reports", mean >= 5, f"{mean:.1f} updates/s")
    if run.phases:
        ends = [(p["end_azimuth"], p["end_elevation"]) for p in run.phases if p.get("end_azimuth") is not None]
        middle = (sorted(a for a, _ in ends)[len(ends) // 2], sorted(e for _, e in ends)[len(ends) // 2]) if ends else None
        found = [d for d in ends if abs(d[0] - middle[0]) <= 15 and abs(d[1] - middle[1]) <= 15] if ends else []
        report.add(f"{label}: drone clip detected", len(found) >= 0.9 * len(run.phases),
                   f"{len(found)}/{len(run.phases)} sound phases on the speaker's direction "
                   f"({'none' if middle is None else f'{middle[0]:.0f}, {middle[1]:.0f} deg'}), "
                   f"targets in the quiet {run.quiet_targets}")


def suite(board, report, label, args):
    board.stop()
    reads(board, report, f"idle, {label}", args.reads)
    settings_and_writes(board, report, max(5, args.reads // 3))
    windows(board, report, args.reads)
    board.command("B")
    board.pump(1.0, lambda s: s.get("firmware_mode") == "B")
    reads(board, report, f"Stare, {label}", args.reads)
    board.stop()
    board.command("C")
    board.pump(1.0, lambda s: s.get("firmware_mode") == "C")
    reads(board, report, f"scan with windows, {label}", args.reads)
    board.stop()
    recordings(board, report, f"in Stare, {label}", args.downloads, stare=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reads", type=int, default=30, help="commands per mode")
    parser.add_argument("--downloads", type=int, default=2, help="recordings per configuration")
    parser.add_argument("--soak-min", type=float, default=0.0, help="minutes of Stare at the end")
    parser.add_argument("--soak-only", action="store_true", help="skip the regular checks")
    parser.add_argument("--soak-tone", type=int, default=0, help="tone report period during the soak (0 = off)")
    parser.add_argument("--soak-sound", help="WAV to play for 20 s of every soak minute")
    parser.add_argument("--soak-cycle-min", type=int, default=2, help="minutes between record/I/SET cycles (0 = none)")
    parser.add_argument("--soak-log", help="CSV path (default: the temp folder)")
    args = parser.parse_args()

    report = Report()
    with connect(WS_URL, open_timeout=5, max_size=None) as socket:
        board = Board(socket)
        board.pump(1.0)
        if not board.state.get("connected"):
            sys.exit("the backend is not connected to the board")
        if board.state.get("recording_state") in ("recording", "ready", "downloading"):
            sys.exit("the device holds a recording that is not downloaded; download it first")
        settings = dict(board.state.get("settings") or {})
        ch1 = dict(board.state.get("ch1") or {"source": 0, "index": 0})
        board.stop()
        board.command("I")
        board.pump(3.0)
        dsp, tables = board.state.get("dsp") or {}, board.state.get("tables") or {}
        report.add("boot checks (I)", bool(dsp.get("matches")) and tables.get("verified") == tables.get("words"),
                   f"DSP {dsp.get('matched')}/{dsp.get('read')}, tables {tables.get('verified')}/{tables.get('words')}")
        try:
            if not args.soak_only:
                channel_order(board, report)
                suite(board, report, "detector off", args)
                if board.set("tone", 100):
                    suite(board, report, "detector on", args)
                else:
                    report.add("tonal detector on", False, "SET,tone,100 was not applied")
                board.stop()
                off = board.set("tone", 0)
                report.add("tonal detector off again", off, "applied" if off else "not applied; reset the board")
            if args.soak_min > 0:
                soak(board, report, args)
        finally:
            board.stop()
            for key in ("measure", "band", "channel", "guard_us", "tone"):
                if key in settings:
                    board.set(key, settings[key])
            board.command(f"O,{ch1.get('source', 0)},{ch1.get('index', 0)}")
            board.pump(0.5)

    failed = [name for name, passed, _ in report.rows if not passed]
    print(f"\n{len(report.rows) - len(failed)}/{len(report.rows)} checks passed" +
          (f"; failed: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
