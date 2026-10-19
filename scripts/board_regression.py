"""Board regression through the live GUI backend (sim_server.py connected to the board).

Checks, first with the tonal detector off and then on: DSP reads of 8 to 11 bytes while idle,
in Stare and during a scan; settings and parameter writes; measurement windows; recordings and
their download; and that capture channel 0 carries the beam. Records over the device's
recording. Restores the settings and Ch1 routing it found and leaves the firmware idle.
Exit code 1 if any check fails.

    python scripts/board_regression.py [--reads 30] [--downloads 2] [--soak-min 0]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
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

    def pump(self, seconds, until=None):
        """Apply frames for up to `seconds`; True as soon as `until(state)` holds."""
        end = time.monotonic() + seconds
        while (remaining := end - time.monotonic()) > 0:
            try:
                message = json.loads(self.socket.recv(timeout=remaining))
            except TimeoutError:
                break
            if message.get("type") in ("init", "frame"):
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


def record(board, seconds):
    """Record, stop and download; the stop record's statistics and the stereo PCM, or a reason."""
    board.command("R,1")
    if not board.pump(3.0, lambda s: s.get("recording_state") == "recording"):
        return None, None, f"did not start ({board.state.get('last_error')})"
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


def stare_soak(board, report, minutes):
    maps = int((board.state.get("bank") or {}).get("maps", 0))
    board.command("B")
    board.pump(1.0)
    end = time.monotonic() + 60 * minutes
    errors = board.state.get("protocol_errors", 0)
    ok = rounds = 0
    while time.monotonic() < end:
        board.pump(10.0)
        status, _ = http("POST", "/dsp/read", {"address": 1030, "count": 16})
        ok += status == 200
        rounds += 1
    rate = (int((board.state.get("bank") or {}).get("maps", 0)) - maps) / (60 * minutes)
    board.stop()
    passed = ok == rounds and rate >= 18 and board.state.get("protocol_errors", 0) == errors
    report.add(f"Stare soak, {minutes} min", passed,
               f"{rate:.1f} maps/s, reads {ok}/{rounds}, protocol errors +{board.state.get('protocol_errors', 0) - errors}")


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
                stare_soak(board, report, args.soak_min)
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
