"""The tests that the Study screen offers, in the order of the characterisation plan, printed as JSON.

Each entry is one command of MAX78002/docs/CHARACTERISATION_PLAN.md section 5, or the board regression:
the script, its test, the options the operator can change (taken from the scripts' own parsers) and the
plan's text for the test. tests/test_study_runner.py checks the entries against the plan's commands.

    python scripts/catalogue.py
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import board_regression  # noqa: E402
import characterise  # noqa: E402
import study_session  # noqa: E402

PLAN = HERE.parent.parent / "MAX78002" / "docs" / "CHARACTERISATION_PLAN.md"

# (id = result folder name in the plan, plan section, script, characterise test, values other than the defaults)
ENTRIES = [
    ("health", "5.1", "study_session.py", None, {"speaker_az": 0.0, "speaker_el": 0.0, "distance": 2.0}),
    ("delay", "5.2", "characterise.py", "p2", {}),
    ("linearity", "5.3", "characterise.py", "s3", {}),
    ("sensitivity", "5.4", "characterise.py", "s1", {}),
    ("noise-mics", "5.5", "characterise.py", "n1", {}),
    ("noise-direction", "5.6", "characterise.py", "n2", {}),
    ("room", "5.7", "characterise.py", "r1", {}),
    ("beam-pattern", "5.8", "characterise.py", "a3", {}),
    ("detection", "5.9", "characterise.py", "l5", {}),
    ("live-log", "5.9", "characterise.py", "l7", {"trials": 5, "listen": 60.0, "burst": 2.0, "burst_at": 20.0}),
    ("drone-tones", "5.10", "characterise.py", "t1", {}),
    ("aliasing", "5.11", "characterise.py", "a10", {}),
    ("directivity", "5.12", "characterise.py", "a9", {}),
    ("front-back", "5.13", "characterise.py", "a8", {}),
    ("two-sources", "5.14", "characterise.py", "l6", {}),
    ("live-accuracy", "5.15", "characterise.py", "l1",
     {"positions": "-30,0;-22.5,0;-15,0;-7.5,0;0,0;7.5,0;15,0;22.5,0;30,0"}),
    ("live-drone", "5.16", "characterise.py", "l1",
     {"sound": "drone", "distance": 1.5, "positions": "0,0;15,0;-15,0;30,0;-30,0"}),
    ("pointing", "5.17", "characterise.py", "a4", {}),
    ("regression", None, "board_regression.py", None, {}),
]
# Entries that share a plan section take part of its text: from the paragraph that starts with the first
# marker, up to the paragraph that starts with the second.
TEXT_SPLIT = {"detection": (None, "**Live log"), "live-log": ("**Live log", None)}
TITLES = {"live-log": "Live log", "regression": "Board regression"}
PARSERS = {"characterise.py": characterise.build_parser, "study_session.py": study_session.build_parser,
           "board_regression.py": board_regression.build_parser}
# The runner sets --out; paths and the seed keep their defaults.
HIDDEN = {"help", "test", "out", "seed", "soak_sound", "soak_log"}
# characterise.py options whose help has no "test/test:" prefix: for every test, or for the sound tests.
GENERAL = {"seconds": "all", "distance": "sound", "alignment_sigma": "sound"}
PREFIX = re.compile(r"^((?:[a-z]\d+/)*[a-z]\d+): (.*)$", re.S)
HEADING = re.compile(r"^### (5\.\d+) (.+?)(?: \((\d+) minutes?(?:, ([^)]*))?\))?$")
COMMAND = re.compile(r"^& \$py scripts\\(\w+\.py)(.*)$")


def option_kind(action):
    if isinstance(action, argparse._StoreTrueAction):
        return "flag"
    if action.choices:
        return "choice"
    return {int: "int", float: "float"}.get(action.type, "text")


def options_for(script, test, values):
    """The options of one entry: kind, default (the entry's value), script default and help."""
    script_defaults = characterise.defaults_for(test) if script == "characterise.py" else {}
    result = []
    for action in PARSERS[script]()._actions:
        if action.dest in HIDDEN or not action.option_strings:
            continue
        text = action.help or ""
        if script == "characterise.py":
            match = PREFIX.match(text)
            if match:
                if test not in match.group(1).split("/"):
                    continue
                text = match.group(2)
            elif GENERAL.get(action.dest) != "all" and not (
                    GENERAL.get(action.dest) == "sound" and test in characterise.SOUND_TESTS):
                continue
        script_default = script_defaults.get(action.dest, action.default)
        result.append({"name": action.dest, "flag": max(action.option_strings, key=len), "kind": option_kind(action),
                       "choices": list(action.choices) if action.choices else None,
                       "default": values.get(action.dest, script_default), "script_default": script_default,
                       "required": bool(action.required), "help": text})
    return result


def plan_sections(text):
    """{section: {title, minutes, note, lines}} for the tests of section 5."""
    sections, current = {}, None
    for line in text.splitlines():
        if line.startswith("#"):
            match = HEADING.match(line)
            current = None
            if match:
                current = sections[match.group(1)] = {
                    "title": match.group(2), "minutes": int(match.group(3)) if match.group(3) else None,
                    "note": match.group(4), "lines": []}
            continue
        if current is not None:
            current["lines"].append(line)
    return sections


def plan_commands(text):
    """{result folder: [script, arguments...]} for every test command of the plan."""
    commands = {}
    for line in text.splitlines():
        match = COMMAND.match(line.strip())
        if not match:
            continue
        words = [word.strip('"') for word in shlex.split(match.group(2), posix=False)]
        if "--out" in words:
            at = words.index("--out")
            folder = words[at + 1].split("\\")[-1]
            commands[folder] = [match.group(1)] + words[:at] + words[at + 2:]
    return commands


def blocks(lines):
    """Paragraphs, lists and tables of a plan section. Code blocks (the commands) are left out."""
    out, paragraph, fence = [], [], False

    def close():
        if paragraph:
            out.append({"type": "p", "text": " ".join(paragraph)})
            paragraph.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = not fence
            close()
            continue
        if fence:
            continue
        if not stripped:
            close()
            continue
        last = out[-1] if out else None
        item = re.match(r"^(\d+\.|-) (.*)$", stripped)
        if item and not line.startswith(" "):
            close()
            kind = "ul" if item.group(1) == "-" else "ol"
            if last is not None and last["type"] == kind:
                last["items"].append(item.group(2))
            else:
                out.append({"type": kind, "items": [item.group(2)]})
            continue
        if stripped.startswith("|"):
            close()
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            if last is not None and last["type"] == "table":
                last["rows"].append(cells)
            else:
                out.append({"type": "table", "rows": [cells]})
            continue
        if line.startswith(" ") and not paragraph and last is not None and last["type"] in ("ol", "ul"):
            last["items"][-1] += " " + stripped
            continue
        paragraph.append(stripped)
    close()
    return out


def split_text(items, first, stop):
    def starts(block, marker):
        return block["type"] == "p" and block["text"].startswith(marker)

    begin = next((i for i, block in enumerate(items) if starts(block, first)), len(items)) if first else 0
    end = next((i for i, block in enumerate(items) if i >= begin and starts(block, stop)), len(items)) if stop else len(items)
    return items[begin:end]


def script_text(script, test):
    """The docstring of the test, or of the script, when the plan has no text."""
    if script == "characterise.py":
        doc = characterise.TESTS[test].__doc__
    else:
        doc = {"study_session.py": study_session, "board_regression.py": board_regression}[script].__doc__
    paragraph = inspect.cleandoc(doc or "").split("\n\n")[0]
    return [{"type": "p", "text": " ".join(paragraph.split())}] if paragraph else []


def catalogue(plan=PLAN):
    text = plan.read_text(encoding="utf-8") if plan.exists() else ""
    sections = plan_sections(text)
    entries = []
    for ident, section, script, test, values in ENTRIES:
        info = sections.get(section) if section else None
        items = blocks(info["lines"]) if info else []
        if ident in TEXT_SPLIT:
            items = split_text(items, *TEXT_SPLIT[ident])
        speaker = test in characterise.SOUND_TESTS if script == "characterise.py" else script == "study_session.py"
        entries.append({
            "id": ident, "section": section, "title": TITLES.get(ident) or (info["title"] if info else ident),
            "minutes": info["minutes"] if info and ident not in TITLES else None,
            "note": info["note"] if info else None, "speaker": speaker, "script": script, "test": test,
            "options": options_for(script, test, values), "text": items or script_text(script, test)})
    return {"plan": str(plan) if text else None, "entries": entries}


if __name__ == "__main__":
    json.dump(catalogue(), sys.stdout)
