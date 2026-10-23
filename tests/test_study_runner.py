"""The Study screen's test runner: catalogue against the plan, prompts, Stop, the run lock and results."""

from __future__ import annotations

import io
import json
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import catalogue  # noqa: E402
import characterise  # noqa: E402
import gui_run  # noqa: E402
import sim_server  # noqa: E402
import study_runner  # noqa: E402
from study_runner import StudyRunner, arguments, option_value  # noqa: E402

PLAN_TEXT = catalogue.PLAN.read_text(encoding="utf-8") if catalogue.PLAN.exists() else ""
needs_plan = pytest.mark.skipif(not PLAN_TEXT, reason="the MAX78002 repository is not next to acoustic-sim")


# ── Catalogue against the characterisation plan ─────────────────────────────

def parsed(script, args):
    """The arguments a script runs with, as its main() parses them (without --out)."""
    parser = catalogue.PARSERS[script]()
    args = [word for arg in args for word in (arg.split("=", 1) if arg.startswith("--") and "=" in arg else [arg])]
    namespace = parser.parse_args(args)
    if script == "characterise.py":
        for key, value in characterise.defaults_for(namespace.test).items():
            if getattr(namespace, key) == parser.get_default(key):
                setattr(namespace, key, value)
    values = vars(namespace)
    values.pop("out", None)
    return values


@pytest.fixture(scope="module")
def entries():
    return {entry["id"]: entry for entry in catalogue.catalogue()["entries"]}


@needs_plan
def test_every_plan_command_is_an_entry_that_runs_the_same_arguments(entries):
    commands = catalogue.plan_commands(PLAN_TEXT)
    assert set(commands) == {ident for ident, section, *_ in catalogue.ENTRIES if section}
    for ident, words in commands.items():
        entry = entries[ident]
        assert words[0] == entry["script"]
        plan_args = words[1:]
        if "$guard" in plan_args:  # the plan's variable: the guard that test 5.2 gives
            at = plan_args.index("--guard")
            del plan_args[at:at + 2]
            assert any(option["name"] == "guard" for option in entry["options"])
        assert parsed(entry["script"], arguments(entry, {})) == parsed(entry["script"], plan_args), ident


@needs_plan
def test_entries_carry_the_plan_text_and_duration(entries):
    health = entries["health"]
    assert (health["section"], health["title"], health["minutes"], health["speaker"]) == ("5.1", "Health check", 10, True)
    assert any(block["type"] == "table" and block["rows"][0] == ["Check", "Pass limit"] for block in health["text"])
    assert entries["noise-mics"]["note"] == "speaker silent" and not entries["noise-mics"]["speaker"]
    detection = [block.get("text", "") for block in entries["detection"]["text"]]
    live_log = [block.get("text", "") for block in entries["live-log"]["text"]]
    assert not any(text.startswith("**Live log") for text in detection)
    assert live_log and live_log[0].startswith("**Live log")
    steps = next(block for block in entries["sensitivity"]["text"] if block["type"] == "ol")["items"]
    assert len(steps) == 6 and steps[3].startswith("Look at the meter")


def test_every_characterise_option_reaches_some_test():
    for action in characterise.build_parser()._actions:
        if action.dest in catalogue.HIDDEN or not action.option_strings:
            continue
        match = catalogue.PREFIX.match(action.help or "")
        assert match or action.dest in catalogue.GENERAL, action.dest
        if match:
            assert set(match.group(1).split("/")) <= set(characterise.TESTS), action.dest


def test_option_scope_follows_the_help_prefix(entries):
    names = {ident: {option["name"] for option in entry["options"]} for ident, entry in entries.items()}
    assert names["noise-mics"] == {"seconds", "cycles"}
    assert {"guard", "steps", "trials", "listen"} <= names["detection"]
    assert "guard" not in names["live-log"] and "burst_at" in names["live-log"]
    assert "out" not in names["health"] and "seed" not in names["delay"]
    assert "soak_sound" not in names["regression"] and "soak_log" not in names["regression"]


def test_blocks_parse_lists_tables_and_skip_commands():
    lines = textwrap.dedent("""\
        **What it measures.** One
        line.

        | A | B |
        |---|---|
        | 1 | 2 |

        1. First
           continued.
        2. Second:

           ```powershell
           & $py scripts\\characterise.py n1 --out $out\\noise-mics
           ```

        3. Third.

        - bullet
        NOTE: a note.
        """).splitlines()
    assert catalogue.blocks(lines) == [
        {"type": "p", "text": "**What it measures.** One line."},
        {"type": "table", "rows": [["A", "B"], ["1", "2"]]},
        {"type": "ol", "items": ["First continued.", "Second:", "Third."]},
        {"type": "ul", "items": ["bullet"]},
        {"type": "p", "text": "NOTE: a note."},
    ]


# ── Option values and arguments ─────────────────────────────────────────────

ENTRY = {"id": "fake", "test": "x1", "options": [
    {"name": "count", "flag": "--count", "kind": "int", "default": 3, "script_default": 3, "required": False},
    {"name": "level", "flag": "--level", "kind": "float", "default": None, "script_default": None, "required": False},
    {"name": "where", "flag": "--where", "kind": "text", "default": "0,0", "script_default": "0,0", "required": False},
    {"name": "truth", "flag": "--truth", "kind": "choice", "choices": ["array", "measured"], "default": "array",
     "script_default": "array", "required": False},
    {"name": "loud", "flag": "--loud", "kind": "flag", "default": False, "script_default": False, "required": False},
]}


def test_arguments_leave_out_defaults_and_keep_changes():
    assert arguments(ENTRY, {}) == ["x1"]
    assert arguments(ENTRY, {"count": 5, "level": 1.5, "truth": "measured", "loud": True}) == [
        "x1", "--count", "5", "--level", "1.5", "--truth", "measured", "--loud"]
    assert arguments(ENTRY, {"level": 2.0}) == ["x1", "--level", "2"]
    assert arguments(ENTRY, {"where": "-30,0;-15,0"}) == ["x1", "--where=-30,0;-15,0"]
    assert arguments(ENTRY, {"level": "2,5"}) == ["x1", "--level", "2.5"]


@pytest.mark.parametrize("options", [
    {"nope": 1}, {"count": 2.5}, {"count": "two"}, {"count": True}, {"level": float("inf")}, {"level": 1e9},
    {"where": "..\\secret"}, {"where": "a/b"}, {"where": "x" * 201}, {"truth": "other"}, {"loud": "yes"},
])
def test_arguments_refuse_bad_values(options):
    with pytest.raises(ValueError):
        arguments(ENTRY, options)


def test_required_option_needs_a_value():
    spec = {"name": "az", "flag": "--speaker-az", "kind": "float", "default": None, "script_default": None,
            "required": True}
    with pytest.raises(ValueError):
        arguments({"id": "h", "test": None, "options": [spec]}, {})
    assert arguments({"id": "h", "test": None, "options": [spec]}, {"az": 0}) == ["--speaker-az", "0"]
    assert option_value(spec, " ") is None


# ── The prompt helper ───────────────────────────────────────────────────────

def test_ask_operator_prints_one_prompt_line_and_reads_the_answer(monkeypatch, capsys):
    monkeypatch.setattr(gui_run, "GUI", True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("72,5 3\n"))
    assert gui_run.ask_operator("\nMeasured right, forward: ", numbers=2, optional=True) == "72,5 3"
    line = capsys.readouterr().out.strip()
    assert line.startswith(gui_run.PROMPT_MARK)
    assert json.loads(line[len(gui_run.PROMPT_MARK):]) == {"text": "Measured right, forward:", "numbers": 2,
                                                           "optional": True}
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(EOFError):
        gui_run.ask_operator("Enter.")


def test_run_edits_reads_the_file_of_the_run(monkeypatch, tmp_path):
    path = tmp_path / "dsp_edits.json"
    path.write_text(json.dumps({"edits": [{"address": 3714, "word": -5, "label": "FIR"}]}), encoding="utf-8")
    monkeypatch.setenv("HEIMDALL_DSP_EDITS", str(path))
    assert gui_run.run_edits() == {3714: -5}
    monkeypatch.delenv("HEIMDALL_DSP_EDITS")
    assert gui_run.run_edits() == {}


# ── Runs with a fake script ─────────────────────────────────────────────────

FAKE = """\
import argparse, json, os, sys, time
sys.path.insert(0, {scripts!r})
from gui_run import RUN_TOKEN, ask_operator, run_edits

parser = argparse.ArgumentParser()
parser.add_argument("--mode", default="ask")
parser.add_argument("--out")
args = parser.parse_args()
print("hello", flush=True)
try:
    if args.mode == "ask":
        print("got " + ask_operator("\\nType two numbers: ", numbers=2), flush=True)
    elif args.mode == "wait":
        print("waiting", flush=True)
        while True:
            time.sleep(0.05)
    with open(os.path.join(args.out, "summary.json"), "w") as handle:
        json.dump({{"test": "fake", "token": RUN_TOKEN, "dsp_edits": {{str(a): w for a, w in run_edits().items()}},
                   "checks": [{{"name": "one", "result": "pass", "detail": ""}},
                              {{"name": "two", "result": "info", "detail": ""}}]}}, handle)
finally:
    open(os.path.join(args.out, "restored.txt"), "w").close()
"""


@pytest.fixture()
def runner(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "fake.py").write_text(FAKE.format(scripts=str(ROOT / "scripts")), encoding="utf-8")
    entry = {"id": "fake", "title": "Fake test", "section": None, "script": "fake.py", "test": None, "options": [
        {"name": "mode", "flag": "--mode", "kind": "choice", "choices": ["ask", "wait", "quick"], "default": "ask",
         "script_default": "ask", "required": False}]}
    return StudyRunner(scripts=scripts, data_root=tmp_path / "data", catalogue=lambda: {"entries": [entry]},
                       today=lambda: "2026-10-23")


def wait_for(runner, condition, seconds=30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = runner.status()
        if condition(status):
            return status
        time.sleep(0.05)
    raise AssertionError(f"timed out: {runner.status()}")


def texts(status):
    return [line["text"] for line in status["lines"]]


def test_a_run_asks_takes_the_answer_and_records_everything(runner, tmp_path):
    status = runner.start("fake")
    assert status["state"] == "running" and status["run"]["folder"] == "bench_2026-10-23/fake"
    assert runner.brief() == {"entry": "fake", "title": "Fake test", "state": "running"}
    status = wait_for(runner, lambda s: s["prompt"] is not None)
    assert status["prompt"] == {"text": "Type two numbers:", "numbers": 2, "optional": False}
    assert not runner.allows(None) and not runner.allows("guess") and runner.allows(runner._token)
    with pytest.raises(RuntimeError):
        runner.start("fake")
    runner.answer("1 2")
    status = wait_for(runner, lambda s: s["state"] == "done")
    assert "got 1 2" in texts(status) and "hello" in texts(status)
    assert runner.allows(None) and runner.brief() is None
    folder = tmp_path / "data" / "bench_2026-10-23" / "fake"
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["token"] and summary["dsp_edits"] == {}
    run = json.loads((folder / "run.json").read_text())
    assert run["state"] == "done" and run["exit_code"] == 0 and run["configuration"] == "flashed"
    log = (folder / "run_log.txt").read_text(encoding="utf-8")
    assert "? Type two numbers:" in log and "> 1 2" in log
    [result] = runner.results()
    assert result["path"] == "bench_2026-10-23/fake" and result["counts"] == {"pass": 1, "fail": 0, "info": 1}
    detail = runner.result("bench_2026-10-23/fake")
    assert [check["name"] for check in detail["summary"]["checks"]] == ["one", "two"]
    assert "summary.json" in detail["files"] and detail["log"].startswith("# Started")
    assert runner.result_file("bench_2026-10-23/fake/run.json").name == "run.json"
    assert runner.start("fake", {"mode": "quick"})["run"]["folder"] == "bench_2026-10-23/fake_2"
    wait_for(runner, lambda s: s["state"] == "done")


def test_stop_lets_the_restore_steps_run(runner, tmp_path):
    runner.start("fake", {"mode": "wait"})
    wait_for(runner, lambda s: "waiting" in texts(s))
    assert runner.stop()["state"] == "stopping"
    status = wait_for(runner, lambda s: s["state"] == "stopped")
    folder = tmp_path / "data" / "bench_2026-10-23" / "fake"
    assert (folder / "restored.txt").exists() and not (folder / "summary.json").exists()
    assert status["run"]["exit_code"] != 0 and runner.allows(None)
    with pytest.raises(RuntimeError):
        runner.stop()


def test_stop_at_a_prompt_and_force_stop(runner):
    runner.start("fake")
    wait_for(runner, lambda s: s["prompt"] is not None)
    runner.stop()
    wait_for(runner, lambda s: s["state"] == "stopped")
    runner.start("fake", {"mode": "wait"})
    wait_for(runner, lambda s: "waiting" in texts(s))
    runner.stop(force=True)
    assert wait_for(runner, lambda s: s["state"] == "stopped")["run"]["exit_code"] != 0


def test_edits_run_passes_the_edits_and_finish_puts_them_back(runner, tmp_path):
    calls = []
    edits = [{"address": 3714, "word": 7, "default": 1, "label": "FIR stage 1[0]"}]

    def prepare(configuration):
        calls.append(("prepare", configuration))
        return {"edits": edits, "messages": ["prepared"]}

    def finish(context):
        calls.append(("finish", context["configuration"], len(context["edits"])))
        return ["put back"]

    runner.start("fake", {"mode": "quick"}, "edits", prepare=prepare, finish=finish)
    status = wait_for(runner, lambda s: s["state"] == "done")
    assert calls == [("prepare", "edits"), ("finish", "edits", 1)]
    assert "prepared" in texts(status) and "put back" in texts(status)
    folder = tmp_path / "data" / "bench_2026-10-23" / "fake"
    assert json.loads((folder / "summary.json").read_text())["dsp_edits"] == {"3714": 7}
    assert json.loads((folder / "run.json").read_text())["edits"] == edits


def test_a_failed_prepare_starts_nothing(runner, tmp_path):
    def prepare(configuration):
        raise PermissionError("Connect the board on the Hardware screen first.")

    with pytest.raises(PermissionError):
        runner.start("fake", prepare=prepare)
    assert not runner.running and not (tmp_path / "data" / "bench_2026-10-23" / "fake").exists()
    with pytest.raises(ValueError):
        runner.start("fake", configuration="other")
    with pytest.raises(ValueError):
        runner.start("nothing")


@pytest.mark.parametrize("path", ["..", "../x", "bench_2026-10-23/../../outside", "other/x", "", "/etc/passwd",
                                  "C:/Windows/win.ini"])
def test_results_stay_inside_the_bench_folders(runner, path):
    with pytest.raises(FileNotFoundError):
        runner.result(path)
    with pytest.raises(FileNotFoundError):
        runner.result_file(path)


def test_result_files_are_figures_and_records_only(runner, tmp_path):
    folder = tmp_path / "data" / "bench_2026-10-23" / "x"
    folder.mkdir(parents=True)
    (folder / "script.py").write_text("")
    (folder / "a.png").write_bytes(b"png")
    with pytest.raises(FileNotFoundError):
        runner.result_file("bench_2026-10-23/x/script.py")
    assert runner.result_file("bench_2026-10-23/x/a.png").name == "a.png"


# ── Backend routes ──────────────────────────────────────────────────────────

class LockedRunner:
    revision = 0

    def allows(self, token):
        return token == "secret"

    def lock_text(self):
        return "A Study test is running (Fake)."

    def brief(self):
        return {"entry": "fake", "title": "Fake", "state": "running"}

    def status(self, since=0):
        return {"state": "running"}


def test_board_routes_refuse_other_clients_while_a_test_runs(monkeypatch):
    monkeypatch.setattr(sim_server, "_study_runner", LockedRunner())
    with TestClient(sim_server.app) as client:
        response = client.post("/dsp/set", json={"key": "guard_us", "value": 2000})
        assert response.status_code == 423 and "Fake" in response.json()["detail"]
        assert client.post("/hw_disconnect").status_code == 423
        assert client.post("/dsp/set", json={"key": "guard_us", "value": 2000},
                           headers={"X-Heimdall-Run": "secret"}).status_code != 423


def test_websocket_commands_need_the_run_token_while_a_test_runs(monkeypatch):
    with TestClient(sim_server.app) as client:
        assert client.post("/hw_connect", json={"transport": "emulator"}).status_code == 200
        monkeypatch.setattr(sim_server, "_study_runner", LockedRunner())
        try:
            with client.websocket_connect("/realtime_hw") as websocket:
                assert websocket.receive_json()["study_run"]["entry"] == "fake"
                websocket.send_json({"type": "command", "command": "F"})
                for _ in range(200):
                    message = websocket.receive_json()
                    if message["type"] == "command_error":
                        assert "Fake" in message["detail"]
                        break
                else:
                    raise AssertionError("no command_error")
        finally:
            monkeypatch.undo()
            client.post("/hw_disconnect")


def test_runner_routes_answer_local_pages_only(monkeypatch):
    monkeypatch.setattr(sim_server, "_study_runner", LockedRunner())
    with TestClient(sim_server.app) as client:
        assert client.get("/study/run").status_code == 200
        assert client.get("/study/run", headers={"Origin": "http://localhost:5173"}).status_code == 200
        assert client.get("/study/run", headers={"Origin": "http://127.0.0.1:4173"}).status_code == 200
        assert client.get("/study/run", headers={"Origin": "https://example.com"}).status_code == 403
        assert client.get("/study/run", headers={"Origin": "http://localhost.example.com"}).status_code == 403


def test_run_start_needs_a_connected_board():
    sim_server._replace_heimdall_transport(None)
    with TestClient(sim_server.app) as client:
        response = client.post("/study/run", json={"entry": "noise-direction"})
        assert response.status_code == 409 and "Connect the board" in response.json()["detail"]
    assert not sim_server._study_runner.running


@needs_plan
def test_the_backend_serves_the_catalogue():
    with TestClient(sim_server.app) as client:
        response = client.get("/study/tests")
        assert response.status_code == 200
        ids = [entry["id"] for entry in response.json()["entries"]]
        assert ids[0] == "health" and ids[-1] == "regression" and len(ids) == len(catalogue.ENTRIES)


def test_check_sound_is_sound_a(tmp_path):
    import wave
    import numpy as np
    path = study_runner.check_sound(tmp_path / "check.wav", seconds=1.0)
    with wave.open(str(path)) as handle:
        x = np.frombuffer(handle.readframes(handle.getnframes()), "<i2") / 32767
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(x.size, 1 / 48000)
    inside = spectrum[(freqs >= 500) & (freqs <= 3500)].sum() / spectrum.sum()
    assert inside > 0.99 and abs(20 * np.log10(np.sqrt(np.mean(x ** 2))) + 18.2) < 0.5
