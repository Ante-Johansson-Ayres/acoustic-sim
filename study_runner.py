"""Runs the characterisation scripts for the Study screen: one at a time, as a child process of the backend.

The child gets the operator's answers on stdin (scripts/gui_run.py), a token for the backend's run lock
and, for a run with the Study's current DSP edits, a file of them. The results go to
MAX78002/docs/data/bench_<date>/<test id>, with the names of the characterisation plan, and the runner adds
run.json (what ran, and how it ended) and run_log.txt (everything the operator saw and typed).
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
DATA_ROOT = ROOT.parent / "MAX78002" / "docs" / "data"
PROMPT_MARK = "@@PROMPT "
TEXT_VALUE = re.compile(r"^[A-Za-z0-9 _.,;:+-]{0,200}$")
NUMBER_LIMIT = 1e7
ANSWER_MAX = 200
LINES_MAX = 20000
STOP_GRACE_S = 90.0
RESULT_FILES = {".png", ".json", ".csv", ".txt"}
CONFIGURATIONS = ("flashed", "edits")
LOG_MARKS = {"out": "", "ask": "? ", "answer": "> ", "system": "# "}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _number_text(value):
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def option_value(spec, raw):
    """The value of one option as the script takes it; None for no value. Raises ValueError."""
    kind, flag = spec["kind"], spec["flag"]
    if kind == "flag":
        if raw is None:
            return False
        if not isinstance(raw, bool):
            raise ValueError(f"{flag} is on or off")
        return raw
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if kind in ("int", "float"):
        if isinstance(raw, bool):
            raise ValueError(f"{flag} needs a number")
        try:
            number = float(raw.strip().replace(",", ".")) if isinstance(raw, str) else float(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{flag} needs a number") from error
        if not math.isfinite(number) or abs(number) > NUMBER_LIMIT:
            raise ValueError(f"{flag} is out of range")
        if kind == "int":
            if not number.is_integer():
                raise ValueError(f"{flag} needs a whole number")
            return int(number)
        return number
    text = str(raw).strip()
    if kind == "choice":
        if text not in (spec.get("choices") or []):
            raise ValueError(f"{flag} is one of {', '.join(spec.get('choices') or [])}")
        return text
    if not TEXT_VALUE.match(text):
        raise ValueError(f"{flag} takes letters, digits and , ; . : + - only")
    return text


def arguments(entry, options):
    """Command-line arguments of one run (without --out). Options at the script's default are left out."""
    options = dict(options or {})
    names = {spec["name"] for spec in entry["options"]}
    unknown = sorted(set(options) - names)
    if unknown:
        raise ValueError(f"{entry['id']} has no option {unknown[0]}")
    args = [entry["test"]] if entry.get("test") else []
    for spec in entry["options"]:
        value = option_value(spec, options.get(spec["name"], spec["default"]))
        if spec["kind"] == "flag":
            if value:
                args.append(spec["flag"])
            continue
        if value is None:
            if spec.get("required"):
                raise ValueError(f"{spec['flag']} needs a value")
            continue
        if not spec.get("required") and value == option_value(spec, spec.get("script_default")):
            continue
        text = _number_text(value)
        args += [f"{spec['flag']}={text}"] if text.startswith("-") else [spec["flag"], text]
    return args


def check_sound(path, seconds=2.0, rate=48000):
    """Sound A (noise from 0.5 to 3.5 kHz) at -18 dBFS RMS, with short fades, as a 16-bit WAV."""
    import numpy as np
    n = int(seconds * rate)
    rng = np.random.default_rng(7)
    freqs = np.fft.rfftfreq(n, 1 / rate)
    spectrum = np.zeros(freqs.size, complex)
    inside = (freqs >= 500) & (freqs <= 3500)
    spectrum[inside] = np.exp(2j * np.pi * rng.random(inside.sum()))
    x = np.fft.irfft(spectrum, n)
    x *= 10 ** (-18 / 20) / np.sqrt(np.mean(x ** 2))
    fade = int(0.05 * rate)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(np.round(x * 32767).astype("<i2").tobytes())
    return path


class StudyRunner:
    """One test at a time. All public methods are thread-safe."""

    def __init__(self, python=sys.executable, scripts=SCRIPTS, data_root=DATA_ROOT, catalogue=None, today=None):
        self.python = python
        self.scripts = Path(scripts)
        self.data_root = Path(data_root)
        self._catalogue_source = catalogue
        self._catalogue = None
        self._catalogue_stamp = None
        self._catalogue_lock = threading.Lock()
        self._today = today or (lambda: datetime.now().strftime("%Y-%m-%d"))
        self._lock = threading.RLock()
        self._process = None
        self._run = None
        self._run_id = 0
        self._lines = []
        self._offset = 0
        self._prompt = None
        self._token = None
        self._context = None
        self._finish = None
        self._briefs = {}
        self.revision = 0

    # ── Catalogue ─────────────────────────────────────────────────────────

    def _env(self):
        env = dict(os.environ)
        env.update({"PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg"})
        for key in ("HEIMDALL_GUI_PROMPTS", "HEIMDALL_RUN_TOKEN", "HEIMDALL_DSP_EDITS"):
            env.pop(key, None)
        return env

    def catalogue(self):
        """The test list (scripts/catalogue.py), read again when a script or the plan changes."""
        if self._catalogue_source is not None:
            return self._catalogue_source()
        files = sorted(self.scripts.glob("*.py")) + [DATA_ROOT.parent / "CHARACTERISATION_PLAN.md"]
        stamp = tuple((str(path), path.stat().st_mtime_ns) for path in files if path.exists())
        with self._catalogue_lock:
            if self._catalogue is None or stamp != self._catalogue_stamp:
                result = subprocess.run([self.python, str(self.scripts / "catalogue.py")], cwd=ROOT, env=self._env(),
                                        capture_output=True, timeout=120)
                if result.returncode != 0:
                    tail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
                    raise RuntimeError(f"the test list could not be read: {' '.join(tail)}")
                self._catalogue, self._catalogue_stamp = json.loads(result.stdout), stamp
            return self._catalogue

    def _entry(self, entry_id):
        for entry in self.catalogue()["entries"]:
            if entry["id"] == entry_id:
                return entry
        raise ValueError(f"unknown test {entry_id!r}")

    # ── Runs ──────────────────────────────────────────────────────────────

    @property
    def running(self):
        with self._lock:
            return self._process is not None

    def allows(self, token):
        """Board commands are open, or come with the running test's token."""
        with self._lock:
            return self._token is None or (isinstance(token, str) and secrets.compare_digest(token, self._token))

    def lock_text(self):
        with self._lock:
            title = self._run["title"] if self._run else "a test"
        return (f"A Study test is running ({title}). The board takes commands from that test only, until it ends "
                "or you stop it on the Study screen.")

    def brief(self):
        """The running test for the hardware frames, or None."""
        with self._lock:
            if self._process is None:
                return None
            return {"entry": self._run["entry"], "title": self._run["title"], "state": self._run["state"]}

    def _folder(self, entry_id):
        day = self.data_root / f"bench_{self._today()}"
        name, count = entry_id, 1
        while (day / name).exists():
            count += 1
            name = f"{entry_id}_{count}"
        return day / name

    def start(self, entry_id, options=None, configuration="flashed", prepare=None, finish=None):
        """Start a test. prepare(configuration) -> context runs first (it may set the board to flashed);
        finish(context) -> [messages] runs after the test, before the lock opens."""
        if configuration not in CONFIGURATIONS:
            raise ValueError(f"configuration is one of {', '.join(CONFIGURATIONS)}")
        entry = self._entry(entry_id)
        args = arguments(entry, options)
        with self._lock:
            if self._process is not None:
                raise RuntimeError(f"{self._run['title']} is running. Stop it first.")
            folder = self._folder(entry_id)
            folder.mkdir(parents=True)
            try:
                context = prepare(configuration) if prepare else {}
            except Exception:
                folder.rmdir()
                raise
            context = {"configuration": configuration, "edits": [], "messages": [], **context}
            token = secrets.token_urlsafe(16)
            env = self._env()
            env.update({"HEIMDALL_GUI_PROMPTS": "1", "HEIMDALL_RUN_TOKEN": token})
            if configuration == "edits" and context["edits"]:
                edits_path = folder / "dsp_edits.json"
                edits_path.write_text(json.dumps({"configuration": configuration, "edits": context["edits"]}, indent=2),
                                      encoding="utf-8")
                env["HEIMDALL_DSP_EDITS"] = str(edits_path)
            relative = folder.relative_to(self.data_root).as_posix()
            command = [self.python, "-u", str(self.scripts / entry["script"]), *args, "--out", str(folder)]
            shown = " ".join(["python", f"scripts/{entry['script']}", *args, "--out", relative])
            flags = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                     else {"start_new_session": True})
            try:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, **flags)
            except OSError:
                if finish:
                    finish(context)
                raise
            self._run_id += 1
            self._run = {"id": self._run_id, "entry": entry_id, "title": entry["title"], "section": entry.get("section"),
                         "folder": relative, "command": shown, "configuration": configuration,
                         "edits": len(context["edits"]), "state": "running", "started": _now(), "finished": None,
                         "exit_code": None}
            self._process, self._token, self._prompt = process, token, None
            self._context, self._finish = context, finish
            self._lines, self._offset = [], 0
            self._log("system", f"Started: {shown}")
            for message in context["messages"]:
                self._log("system", message)
            self.revision += 1
            self._write_run(folder)
            threading.Thread(target=self._read, args=(process, folder), daemon=True).start()
            return self.status()

    def _log(self, kind, text):
        self._lines.append({"kind": kind, "text": text})
        if len(self._lines) > LINES_MAX:
            drop = len(self._lines) - LINES_MAX + 1000
            del self._lines[:drop]
            self._offset += drop

    def _read(self, process, folder):
        for raw in iter(process.stdout.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            prompt = None
            if text.startswith(PROMPT_MARK):
                try:
                    prompt = json.loads(text[len(PROMPT_MARK):])
                except ValueError:
                    prompt = None
            with self._lock:
                if isinstance(prompt, dict):
                    self._prompt = {"text": str(prompt.get("text", "")), "numbers": int(prompt.get("numbers") or 0),
                                    "optional": bool(prompt.get("optional"))}
                    self._log("ask", self._prompt["text"])
                else:
                    self._log("out", text)
        code = process.wait()
        with self._lock:
            self._prompt = None
            self._run["state"] = "finishing"
            context, finish = self._context, self._finish
        messages = []
        if finish is not None:
            try:
                messages = list(finish(context) or [])
            except Exception as error:  # the run has ended; report, then open the lock
                messages = [f"The Study's edits and settings were not put back: {error}"]
        with self._lock:
            for message in messages:
                self._log("system", message)
            run = self._run
            run["exit_code"], run["finished"] = code, _now()
            run["state"] = ("stopped" if run.get("stopping") else
                            "done" if (folder / "summary.json").exists() else "error")
            self._log("system", {"done": "Finished.", "stopped": "Stopped.",
                                 "error": f"Ended without results (exit code {code})."}[run["state"]])
            self._process = self._token = self._context = self._finish = None
            self.revision += 1
            self._write_run(folder, context)
            self._write_log(folder)

    def _write_run(self, folder, context=None):
        record = {key: value for key, value in self._run.items() if key not in ("id", "stopping")}
        context = context if context is not None else (self._context or {})
        record.update({"edits": context.get("edits", []), "settings": context.get("settings", {}),
                       "changed_settings": context.get("changed_settings", {})})
        try:
            (folder / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _write_log(self, folder):
        text = "\n".join(LOG_MARKS.get(line["kind"], "") + line["text"] for line in self._lines)
        try:
            (folder / "run_log.txt").write_text(text + "\n", encoding="utf-8")
        except OSError:
            pass

    def status(self, since=0):
        with self._lock:
            run = {key: value for key, value in self._run.items() if key != "stopping"} if self._run else None
            start = max(0, int(since) - self._offset)
            return {"state": run["state"] if run else "idle", "run": run, "prompt": self._prompt,
                    "lines": self._lines[start:], "next": self._offset + len(self._lines)}

    def answer(self, text):
        text = str(text or "").replace("\r", " ").replace("\n", " ")
        if len(text) > ANSWER_MAX:
            raise ValueError("the answer is too long")
        with self._lock:
            if self._process is None or self._prompt is None:
                raise RuntimeError("no question is waiting")
            try:
                self._process.stdin.write((text + "\n").encode("utf-8"))
                self._process.stdin.flush()
            except OSError as error:
                raise RuntimeError(f"the test takes no answers now ({error})") from error
            self._log("answer", text or "(Enter)")
            self._prompt = None
            return self.status()

    def stop(self, force=False):
        """Stop the test. Without force, it gets CTRL_BREAK (Ctrl+C) and runs its restore steps first."""
        with self._lock:
            process = self._process
            if process is None:
                raise RuntimeError("no test is running")
            self._run["stopping"] = True
            if self._run["state"] == "running":
                self._run["state"] = "stopping"
            self._prompt = None
            self.revision += 1
            if force:
                self._log("system", "Force stop: the test ends now, without its restore steps.")
                process.kill()
                return self.status()
            self._log("system", "Stop: the test ends after its restore steps.")
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        except (OSError, ValueError):
            process.terminate()
        try:
            process.stdin.close()
        except OSError:
            pass
        timer = threading.Timer(STOP_GRACE_S, self._kill, args=(process,))
        timer.daemon = True
        timer.start()
        return self.status()

    def _kill(self, process):
        if process.poll() is None:
            with self._lock:
                self._log("system", f"The test did not end {STOP_GRACE_S:.0f} s after Stop; it was killed.")
            process.kill()

    # ── Results ───────────────────────────────────────────────────────────

    def _resolve(self, relative):
        """A path inside a bench_* folder of the data root; FileNotFoundError for anything else."""
        root = self.data_root.resolve()
        path = (root / str(relative)).resolve()
        if path == root or not path.is_relative_to(root) or not path.relative_to(root).parts[0].startswith("bench_"):
            raise FileNotFoundError(f"no result {relative}")
        return path

    @staticmethod
    def _read_json(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _brief(self, folder):
        summary_path, run_path = folder / "summary.json", folder / "run.json"
        stamps = tuple(path.stat().st_mtime_ns if path.exists() else 0 for path in (summary_path, run_path))
        cached = self._briefs.get(folder)
        if cached and cached[0] == stamps:
            return cached[1]
        summary, run = self._read_json(summary_path) or {}, self._read_json(run_path) or {}
        counts = {"pass": 0, "fail": 0, "info": 0}
        for check in summary.get("checks") or []:
            if check.get("result") in counts:
                counts[check["result"]] += 1
        brief = {"path": folder.relative_to(self.data_root.resolve()).as_posix(), "day": folder.parent.name[6:],
                 "folder": folder.name, "entry": run.get("entry"), "title": run.get("title"), "test": summary.get("test"),
                 "state": run.get("state") or ("done" if summary else None), "configuration": run.get("configuration"),
                 "started": run.get("started") or summary.get("started"), "finished": run.get("finished"),
                 "time": max(stamps) / 1e9, "counts": counts,
                 "figures": sorted(path.name for path in folder.glob("*.png"))}
        self._briefs[folder] = (stamps, brief)
        return brief

    def results(self, limit=300):
        """Result folders (with summary.json or run.json), the newest first."""
        root = self.data_root.resolve()
        items = []
        if root.is_dir():
            for day in root.glob("bench_*"):
                if day.is_dir():
                    items += [self._brief(folder) for folder in day.iterdir() if folder.is_dir() and (
                        (folder / "summary.json").exists() or (folder / "run.json").exists())]
        items.sort(key=lambda item: item["time"], reverse=True)
        return items[:limit]

    def result(self, relative):
        folder = self._resolve(relative)
        if not folder.is_dir():
            raise FileNotFoundError(f"no result {relative}")
        summary = self._read_json(folder / "summary.json") or {}
        log = folder / "run_log.txt"
        return {**self._brief(folder), "run": self._read_json(folder / "run.json"),
                "summary": {key: summary.get(key) for key in ("test", "started", "arguments", "map_hash", "settings",
                                                              "dsp_edits", "checks")},
                "log": log.read_text(encoding="utf-8", errors="replace") if log.exists() else None,
                "files": sorted(path.name for path in folder.iterdir()
                                if path.is_file() and path.suffix.lower() in RESULT_FILES)}

    def result_file(self, relative):
        path = self._resolve(relative)
        if not path.is_file() or path.suffix.lower() not in RESULT_FILES:
            raise FileNotFoundError(f"no file {relative}")
        return path

    # ── Speaker check ─────────────────────────────────────────────────────

    def speaker_check(self):
        """Play 2 s of sound A on the Windows default output, as the tests do."""
        if self.running:
            raise RuntimeError("a test is running")
        if os.name != "nt":
            raise RuntimeError("the check sound plays on Windows only")
        import winsound
        path = Path(tempfile.gettempdir()) / "heimdall_check_sound.wav"
        if not path.exists():
            check_sound(path)
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return {"played_s": 2.0, "time": time.time()}
