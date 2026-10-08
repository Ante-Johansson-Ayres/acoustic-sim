from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import dsp_registry
import sim_server


def _word(value: float) -> int:
    return int(round(value * (1 << 24)))


def _export(tmp_path, parameters, address_map=None):
    modules = []
    for algo, params in parameters.items():
        module_params = []
        for name, address, words, dtype, defaults in params:
            data = ", ".join(f"0x{byte:02X}" for w in defaults for byte in (w & 0xFFFFFFFF).to_bytes(4, "big"))
            module_params.append({"Name": name, "Address": str(address), "Size": str(words * 4),
                                  "DataType": dtype, "Data": data})
        modules.append({"AlgoName": algo, "Caption": algo, "ModuleParameter": module_params})
    body = {"Processor": {"Core": [{"Schematic": [{"ModuleList": modules, "AddressMap": address_map or {}}]}]}}
    path = tmp_path / "export.json"
    path.write_text(json.dumps(body))
    return path


@pytest.fixture()
def small_registry(tmp_path):
    path = _export(tmp_path, {
        # Gains deliberately not contiguous: a delay sits between DSP01 and DSP02.
        "Gain_DSP00": [("Gain", 100, 1, "FixPoint8d24", [_word(1.0)])],
        "Gain_DSP01": [("Gain", 101, 1, "FixPoint8d24", [_word(1.0)])],
        "Delay_DSP00": [("DelayPercentage", 103, 1, "FixPoint8d24", [0]), ("MaxDelay", 102, 1, "Integer32", [68])],
        "Gain_DSP02": [("Gain", 104, 1, "FixPoint8d24", [_word(1.0)])],
        "FIR_Stage1_DSP00": [("fircoeff", 200, 11, "FixPoint8d24", [_word(0.1)] * 11)],
        "MultipleControlMute_3": [("Mute_Channel1", 221, 1, "FixPoint8d24", [_word(1.0)])],
        "Safeload": [("Data_SafeLoad0", 24576, 1, "Integer32", [0])],
    }, {"State_rms": {"Address": 1123}, "Gain_DSP00Gain": {"Address": 100}})
    return dsp_registry.load_registry(path)


def test_registry_groups_types_and_allow_list(small_registry):
    by_block = {(p["block"], p["name"]): p for p in small_registry["parameters"]}
    assert by_block[("Gain_DSP01", "Gain")]["group"] == "gains"
    assert by_block[("Gain_DSP01", "Gain")]["mic"] == 1
    assert by_block[("Gain_DSP01", "Gain")]["type"] == "8.24"
    assert by_block[("MultipleControlMute_3", "Mute_Channel1")]["mic"] == 17
    assert not by_block[("Delay_DSP00", "DelayPercentage")]["writable"]
    assert not by_block[("Delay_DSP00", "MaxDelay")]["writable"]
    assert not by_block[("Safeload", "Data_SafeLoad0")]["writable"]
    assert by_block[("FIR_Stage1_DSP00", "fircoeff")]["words"] == 11
    assert small_registry["states"] == [{"name": "State_rms", "address": 1123}]
    gains = next(g for g in small_registry["groups"] if g["key"] == "gains")
    assert not gains["contiguous"]


def test_planner_batches_only_truly_consecutive_addresses(small_registry):
    plan = dsp_registry.plan_writes(
        [{"address": a, "word": _word(0.5)} for a in (100, 101, 104)], small_registry)
    assert plan["command_count"] == 2
    assert plan["runs"] == [{"address": 100, "words": 2}, {"address": 104, "words": 1}]


def test_planner_splits_runs_at_five_words(small_registry):
    plan = dsp_registry.plan_writes(
        [{"address": 200 + i, "word": _word(0.2)} for i in range(11)], small_registry)
    assert [run["words"] for run in plan["runs"]] == [5, 5, 1]


def test_planner_rejects_read_only_and_unknown_addresses(small_registry):
    with pytest.raises(ValueError, match="read-only"):
        dsp_registry.plan_writes([{"address": 103, "word": 0}], small_registry)
    with pytest.raises(ValueError, match="not a parameter"):
        dsp_registry.plan_writes([{"address": 5000, "word": 0}], small_registry)


def test_pw_command_format_and_checksum():
    command = dsp_registry.format_pw(1134, [_word(1.0), -1])
    body, cs = command.split("*")
    assert body == "PW,1134,01000000,FFFFFFFF"
    assert int(cs, 16) == dsp_registry.checksum(body)


class _Transport:
    def __init__(self, name="emulator", mode="IDLE"):
        self.transport_name = name
        self.mode = mode

    def snapshot(self):
        return {"mode": self.mode}


def test_service_writes_only_when_idle_and_tracks_modifications(small_registry):
    service = dsp_registry.DspParameterService(loader=lambda: small_registry)
    transport = _Transport(mode="C")
    with pytest.raises(RuntimeError):
        service.write(transport, [{"address": 100, "word": _word(0.5)}])
    transport.mode = "IDLE"
    result = service.write(transport, [{"address": 100, "word": _word(0.5)}])
    assert result["readback"] == {100: _word(0.5)}
    assert service.status(transport)["modified_words"] == 1
    assert service.read(transport, 100, 2) == [_word(0.5), _word(1.0)]


def test_service_reports_serial_as_unsupported(small_registry):
    service = dsp_registry.DspParameterService(loader=lambda: small_registry)
    status = service.status(_Transport(name="serial:COM7"))
    assert not status["supported"]
    with pytest.raises(PermissionError):
        service.write(_Transport(name="serial:COM7"), [{"address": 100, "word": 0}])


def test_service_uses_serial_protocol_when_register_map_matches(small_registry):
    class SerialTransport(_Transport):
        def __init__(self, map_hash):
            super().__init__(name="serial:COM7")
            self.map_hash = map_hash
            self.commands = []

        def snapshot(self):
            return {"mode": self.mode, "map_hash": self.map_hash}

        def request(self, command, response_types):
            self.commands.append(command)
            if command.startswith("PW,"):
                body = command.split("*")[0].split(",")
                return {"type": "pw_ok", "address": int(body[1]), "count": len(body) - 2}
            if command.startswith("PR,"):
                _, address, count = command.split(",")
                return {"type": "pv", "address": int(address),
                        "words": [_word(0.5)] * int(count)}
            key, value = command.split(",")[1:]
            return {"type": "set_ok", "key": key, "value": int(value)}

    small_registry["map_hash"] = "0123456789abcdef"
    service = dsp_registry.DspParameterService(loader=lambda: small_registry)
    mismatch = SerialTransport("fedcba9876543210")
    assert not service.status(mismatch)["supported"]
    with pytest.raises(PermissionError, match="does not match"):
        service.write(mismatch, [{"address": 100, "word": _word(0.5)}])

    transport = SerialTransport("0123456789abcdef")
    result = service.write(transport, [{"address": 100, "word": _word(0.5)}])
    assert result["readback"] == {100: _word(0.5)}
    assert transport.commands[0].startswith("PW,100,00800000*")
    assert transport.commands[1] == "PR,100,1"
    assert service.read(transport, 100, 1) == [_word(0.5)]
    assert service.set(transport, "window_us", 8000)["window_us"] == 8000
    assert transport.commands[-1] == "SET,window_us,8000"


def test_service_memory_resets_when_the_transport_changes(small_registry):
    service = dsp_registry.DspParameterService(loader=lambda: small_registry)
    first = _Transport()
    service.write(first, [{"address": 100, "word": 0}])
    assert service.status(_Transport())["modified_words"] == 0


def test_multibeam_delays_detectors_and_outputs(tmp_path):
    registry = dsp_registry.load_registry(_export(tmp_path, {
        "Delay_DSP05": [("DelayPercentage", 10, 1, "FixPoint8d24", [0])],
        "Delay_DSP05_2": [("DelayPercentage", 11, 1, "FixPoint8d24", [0])],
        "SingleBandwNumericalDisplay_Beam1": [("TCONST", 20, 1, "FixPoint8d24", [0])],
        "SingleBandwNumericalDisplay_Beam2": [("TCONST", 21, 1, "FixPoint8d24", [0])],
        "Beamformer_Output_Gain_3": [("Gain", 30, 1, "FixPoint8d24", [_word(1.0)])],
    }, {"SingleBandwNumericalDisplay_3TCONST": {"Address": 20}, "SingleBandwNumericalDisplay_3rms": {"Address": 23},
        "SingleBandwNumericalDisplay_4TCONST": {"Address": 21}, "SingleBandwNumericalDisplay_4rms": {"Address": 27}}))
    assert registry["level_readback_address"] == 23
    by_block = {p["block"]: p for p in registry["parameters"]}
    assert (by_block["Delay_DSP05"]["group"], by_block["Delay_DSP05"]["mic"], by_block["Delay_DSP05"]["beam"]) == ("delays", 5, 1)
    assert (by_block["Delay_DSP05_2"]["group"], by_block["Delay_DSP05_2"]["mic"], by_block["Delay_DSP05_2"]["beam"]) == ("delays", 5, 3)
    assert "beam 3" in by_block["Delay_DSP05_2"]["reason"]
    # Only the detector the firmware scans with is editable as "the" level detector.
    assert by_block["SingleBandwNumericalDisplay_Beam1"]["group"] == "detector"
    assert by_block["SingleBandwNumericalDisplay_Beam2"]["group"] == "other"
    assert by_block["Beamformer_Output_Gain_3"]["group"] == "output"


def test_lookup_table_steering_is_read_only_and_the_sole_detector_is_scanned(tmp_path):
    registry = dsp_registry.load_registry(_export(tmp_path, {
        "LUT_DSP07": [("TableValues", 40, 49, "FixPoint8d24", [0] * 49)],
        "Delay_DSP07": [("MaxDelay", 89, 1, "Integer32", [68])],
        "Sector_Index": [("DCValue", 90, 1, "Integer32", [0])],
        "SingleBandwNumericalDisplay_3": [("TCONST", 20, 1, "FixPoint8d24", [0])],
    }, {"SingleBandwNumericalDisplay_3TCONST": {"Address": 20}, "SingleBandwNumericalDisplay_3rms": {"Address": 23}}))
    by_block = {p["block"]: p for p in registry["parameters"]}
    table = by_block["LUT_DSP07"]
    assert (table["group"], table["mic"], table["beam"], table["words"]) == ("steering", 7, 1, 49)
    assert not table["writable"] and "boot" in table["reason"]
    index = by_block["Sector_Index"]
    assert index["group"] == "steering" and not index["writable"] and "every steer" in index["reason"]
    assert by_block["SingleBandwNumericalDisplay_3"]["group"] == "detector"
    assert registry["level_readback_address"] == 23
    steering = next(g for g in registry["groups"] if g["key"] == "steering")
    assert steering["words"] == 50 and steering["writable_words"] == 0


def test_export_discovery_requires_exactly_one_export(tmp_path, monkeypatch):
    monkeypatch.setenv("HEIMDALL_DSP_EXPORT", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="none"):
        dsp_registry.export_path()
    (tmp_path / "A_ADAU1467_0.json").write_text("{}")
    assert dsp_registry.export_path().name == "A_ADAU1467_0.json"
    (tmp_path / "B_ADAU1467_0.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="exactly one"):
        dsp_registry.export_path()


def test_export_selection_matches_make(tmp_path, monkeypatch):
    monkeypatch.setattr(dsp_registry, "FIRMWARE_DIR", tmp_path)
    monkeypatch.delenv("HEIMDALL_DSP_EXPORT", raising=False)
    assert dsp_registry.selected_export_dir() == tmp_path / "SigmaStudioExport"
    (tmp_path / "project.mk").write_text("# HEIMDALL_DSP_EXPORT ?= Comment\nHEIMDALL_DSP_EXPORT ?= SigmaStudioExperimental\n")
    assert dsp_registry.selected_export_dir() == tmp_path / "SigmaStudioExperimental"
    # The environment wins, as it does for make.
    monkeypatch.setenv("HEIMDALL_DSP_EXPORT", "SigmaStudioExport")
    assert dsp_registry.selected_export_dir() == tmp_path / "SigmaStudioExport"
    monkeypatch.setenv("HEIMDALL_DSP_EXPORT", str(tmp_path / "elsewhere"))
    assert dsp_registry.selected_export_dir() == tmp_path / "elsewhere"


def test_dsp_api_against_the_emulator():
    try:
        dsp_registry.export_path()
    except FileNotFoundError:
        pytest.skip("SigmaStudio export not present next to acoustic-sim")
    with TestClient(sim_server.app) as client:
        registry = client.get("/dsp/registry").json()
        assert registry["schema"] == "heimdall-dsp-registry-v1"
        assert sum(1 for p in registry["parameters"] if p["group"] == "gains") == 44
        assert client.post("/hw_connect", json={"transport": "emulator", "rows": 6, "columns": 6,
                                                "azimuth_min_deg": -40, "azimuth_max_deg": 40,
                                                "elevation_min_deg": -40, "elevation_max_deg": 40}).status_code == 200
        status = client.get("/dsp/status").json()
        assert status["supported"]
        gain = next(p for p in registry["parameters"] if p["block"] == "Gain_DSP00")
        written = client.post("/dsp/write", json={"writes": [{"address": gain["address"], "word": _word(0.5)}]})
        assert written.status_code == 200, written.text
        assert client.post("/dsp/read", json={"address": gain["address"]}).json()["words"] == [_word(0.5)]
        assert client.get("/dsp/status").json()["modified_words"] == 1
        delay = next(p for p in registry["parameters"] if p["block"] == "Delay_DSP00" and p["name"] == "DelayPercentage")
        assert client.post("/dsp/write", json={"writes": [{"address": delay["address"], "word": 0}]}).status_code == 400
        client.post("/hw_disconnect")
