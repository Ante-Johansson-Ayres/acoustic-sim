"""What a script needs when the Study screen runs it (study_runner.py starts it from the backend).

The backend sets HEIMDALL_GUI_PROMPTS=1, HEIMDALL_RUN_TOKEN and, for a run with the Study's current DSP
edits, HEIMDALL_DSP_EDITS. From a terminal none of them is set and the prompts read the keyboard.
"""

from __future__ import annotations

import json
import os
import signal
import sys

GUI = os.environ.get("HEIMDALL_GUI_PROMPTS") == "1"
RUN_TOKEN = os.environ.get("HEIMDALL_RUN_TOKEN", "")
PROMPT_MARK = "@@PROMPT "

if GUI and hasattr(signal, "SIGBREAK"):
    # Stop on the Study screen sends CTRL_BREAK; like Ctrl+C, it lets the restore steps run.
    signal.signal(signal.SIGBREAK, signal.default_int_handler)


def ask_operator(text, numbers=0, optional=False):
    """Wait for the operator and return the typed line; `numbers` > 0 asks for that many numbers."""
    if not GUI:
        return input(text)
    print(PROMPT_MARK + json.dumps({"text": text.strip(), "numbers": numbers, "optional": optional}), flush=True)
    line = sys.stdin.readline()
    if not line:
        raise EOFError("the Study screen closed the prompt")
    return line.rstrip("\r\n")


def run_edits():
    """{address: word}: the Study's DSP edits that this run writes again after each I."""
    path = os.environ.get("HEIMDALL_DSP_EDITS")
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {int(item["address"]): int(item["word"]) for item in data.get("edits", [])}
