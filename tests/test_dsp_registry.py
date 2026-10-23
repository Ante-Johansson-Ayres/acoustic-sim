from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

import dsp_registry
import sim_server
from heimdall_transport import EmulatorHeimdallTransport


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


def test_registry_reads_the_contract_map_hash(monkeypatch):
    monkeypatch.setenv("HEIMDALL_DSP_EXPORT", "SigmaStudioBank")
    contract = json.loads(
        (dsp_registry.selected_export_dir() / "beam_table_2d.json").read_text(encoding="utf-8"))
    assert dsp_registry.load_registry()["map_hash"] == contract["study"]["map_hash"]


def test_bank_program_scan_detector_is_the_contracts(monkeypatch):
    monkeypatch.setenv("HEIMDALL_DSP_EXPORT", "SigmaStudioBank")
    contract = json.loads(
        (dsp_registry.selected_export_dir() / "beam_table_2d.json").read_text(encoding="utf-8"))
    registry = dsp_registry.load_registry()
    detector = [p for p in registry["parameters"] if p["group"] == "detector"]
    assert {p["block"] for p in detector} == {contract["level_detector"]}
    assert {p["name"] for p in detector} >= {"TCONST", "hold", "decay"}
    assert all(p["writable"] for p in detector)
    assert registry["level_readback_address"] == contract["level_readback_address"]


def test_bank_detectors_never_count_as_the_scan_detector(tmp_path):
    detectors = {"SingleBandwNumericalDisplay_3": 20, "Bank_Beam_0_0": 30, "Bank_Beam_0_1": 40}
    parameters = {label: [("TCONST", address, 1, "FixPoint8d24", [0])] for label, address in detectors.items()}
    address_map = {}
    for index, address in enumerate(detectors.values()):
        address_map[f"Det{index}TCONST"] = {"Address": address}
        address_map[f"Det{index}rms"] = {"Address": address + 3}
    registry = dsp_registry.load_registry(_export(tmp_path, parameters, address_map))
    by_block = {p["block"]: p for p in registry["parameters"]}
    assert by_block["SingleBandwNumericalDisplay_3"]["group"] == "detector"
    assert by_block["Bank_Beam_0_0"]["group"] == "bank_detector"
    assert registry["level_readback_address"] == 23

    (tmp_path / "beam_table_2d.json").write_text(json.dumps({"level_detector": "Bank_Beam_0_1"}))
    registry = dsp_registry.load_registry(tmp_path / "export.json")
    assert registry["level_readback_address"] == 43


def test_fir_stages_after_the_beam_sum_are_writable(tmp_path):
    registry = dsp_registry.load_registry(_export(tmp_path, {
        "FIR_Stage1_Beam": [("fircoeff", 300, 11, "FixPoint8d24", [_word(0.1)] * 11)],
        "FIR_Stage2_Beam": [("fircoeff", 311, 11, "FixPoint8d24", [_word(0.1)] * 11)],
    }))
    stages = {p["group"]: p for p in registry["parameters"]}
    assert stages["fir1"]["mic"] is None and stages["fir1"]["writable"]
    assert stages["fir2"]["address"] == 311 and stages["fir2"]["writable"]


@pytest.mark.parametrize("folder", ["SigmaStudioBank7x7", "SigmaStudioBank", "SigmaStudioCharacterisation",
                                    "SigmaStudioExperimental", "SigmaStudioExport"])
def test_study_writes_exactly_what_the_firmware_accepts(monkeypatch, folder):
    monkeypatch.setenv("HEIMDALL_DSP_EXPORT", folder)
    contract = json.loads(
        (dsp_registry.selected_export_dir() / "beam_table_2d.json").read_text(encoding="utf-8"))
    firmware = {address for start, words in contract["study"]["writable_ranges"]
                for address in range(start, start + words)}
    registry = dsp_registry.load_registry()
    study = {p["address"] + k for p in registry["parameters"] if p["writable"] for k in range(p["words"])}
    assert study == firmware


def test_setting_limits_and_defaults_match_firmware():
    config = (dsp_registry.FIRMWARE_DIR / "heimdall_config.h").read_text(encoding="utf-8")
    names = {name: int(value) for name, value in
             re.findall(r"#define\s+(HEIMDALL_\w+)\s+(-?\d+)[UL]*\b", config)}
    names.update(MEASURE_DSP=0, MEASURE_WINDOW=1)

    def resolve(token):
        token = token.replace("(int32_t)", "").strip()
        return int(token) if re.fullmatch(r"-?\d+", token) else names[token]

    source = (dsp_registry.FIRMWARE_DIR / "heimdall_settings.c").read_text(encoding="utf-8")
    firmware = {name: tuple(resolve(field) for field in fields) for name, *fields in
                re.findall(r'\{\s*"(\w+)",\s*([^,]+),\s*([^,]+),\s*([^}]+?)\s*\}', source)}
    assert firmware == dsp_registry.DspParameterService.SETTINGS


def test_serial_status_reports_the_firmware_settings(small_registry):
    small_registry["map_hash"] = "0123456789abcdef"

    class SerialTransport(_Transport):
        def snapshot(self):
            return {"mode": "IDLE", "map_hash": "0123456789abcdef", "settings": {"guard_us": 700}}

    service = dsp_registry.DspParameterService(loader=lambda: small_registry)
    assert service.status(SerialTransport(name="serial:COM7"))["settings"]["guard_us"] == 700


def test_emulator_setting_change_reaches_the_displayed_state(small_registry):
    service = dsp_registry.DspParameterService(loader=lambda: small_registry)
    emulator = EmulatorHeimdallTransport(rows=1, columns=1, parameter_service=service)
    service.set(emulator, "measure", 1)
    assert emulator.snapshot()["settings"].get("measure") == 1


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
        # Steering belongs to the firmware in every program (delay words or lookup tables).
        steering = next(p for p in registry["parameters"] if p["group"] in {"steering", "delays"})
        assert not steering["writable"]
        assert client.post("/dsp/write", json={"writes": [{"address": steering["address"], "word": 0}]}).status_code == 400
        client.post("/hw_disconnect")


# ── Bank filters and detectors (Study Filters and Level detector tabs) ──────

def _bank_export(tmp_path, contract=None):
    path = _export(tmp_path, {
        "Gain_DSP00": [("Gain", 100, 1, "FixPoint8d24", [_word(1.0)])],
        "Bank_BandPass_0_0": [("fircoeff", 400, 3, "FixPoint8d24", [_word(0.5)] * 3)],
        "Bank_Beam_0_0": [("TCONST", 403, 1, "FixPoint8d24", [_word(0.002)])],
        "Bank_LowPass_DSP07": [("fircoeff", 500, 3, "FixPoint8d24", [_word(0.3)] * 3)],
        "Bank_Ch1_LowPass": [("fircoeff", 503, 3, "FixPoint8d24", [_word(0.3)] * 3)],
        "Bank_Stage1_R0_0": [("delay", 600, 1, "Integer32", [3])],
    })
    if contract is not None:
        (tmp_path / "beam_table_2d.json").write_text(json.dumps(contract))
    return dsp_registry.load_registry(path)


def test_bank_filters_and_detectors_are_writable_groups(tmp_path):
    by_block = {p["block"]: p for p in _bank_export(tmp_path)["parameters"]}
    assert by_block["Bank_BandPass_0_0"]["group"] == "bank_band"
    assert by_block["Bank_LowPass_DSP07"]["group"] == "bank_alias" and by_block["Bank_LowPass_DSP07"]["mic"] == 7
    assert by_block["Bank_Ch1_LowPass"]["group"] == "bank_ch1"
    assert by_block["Bank_Beam_0_0"]["group"] == "bank_detector"
    assert all(by_block[name]["writable"] for name in ("Bank_BandPass_0_0", "Bank_LowPass_DSP07",
                                                         "Bank_Ch1_LowPass", "Bank_Beam_0_0"))
    assert not by_block["Bank_Stage1_R0_0"]["writable"]


def test_flashed_means_what_the_firmware_loads_and_the_allow_list_limits_writes(tmp_path):
    taps = [0.25, -0.5, 0.25]
    registry = _bank_export(tmp_path, {
        "gain_addresses": [100], "gain_fixpt_8_24": [_word(0.75)],
        "bank": {"band_pass": taps, "level_time_constant_ms": 34.66},
        "study": {"map_hash": "0123456789abcdef", "writable_ranges": [[100, 1], [400, 4]]}})
    by_block = {p["block"]: p for p in registry["parameters"]}
    band = by_block["Bank_BandPass_0_0"]
    assert band["default_words"] == [_word(v) for v in taps] and band["boot"] == "tables"
    assert band["compiled_words"] == [_word(0.5)] * 3
    assert by_block["Gain_DSP00"]["default_words"] == [_word(0.75)] and by_block["Gain_DSP00"]["boot"] == "taper"
    assert by_block["Bank_Beam_0_0"]["writable"]
    assert not by_block["Bank_LowPass_DSP07"]["writable"]
    assert "allow-list" in by_block["Bank_LowPass_DSP07"]["reason"]
    assert registry["bank"]["level_time_constant_ms"] == 34.66


def test_edits_put_back_by_a_boot_check_are_reported(tmp_path):
    registry = _bank_export(tmp_path, {"bank": {"band_pass": [0.25, -0.5, 0.25]}})

    class Emulator(_Transport):
        boot_checks = dsp_loads = 0

        def snapshot(self):
            return {"mode": "IDLE", "boot_checks": self.boot_checks, "dsp_loads": self.dsp_loads}

    service = dsp_registry.DspParameterService(loader=lambda: registry)
    transport = Emulator()
    service.write(transport, [{"address": 400, "word": _word(0.1)}, {"address": 500, "word": _word(0.2)}])
    assert service.status(transport)["modified_words"] == 2
    transport.boot_checks = 1  # I: the tables come back, the anti-alias low-pass stays
    status = service.status(transport)
    assert status["lost"] == [{"address": 400, "word": _word(0.1)}]
    assert status["modified_words"] == 1 and service.read(transport, 400, 1) == [_word(0.25)]
    service.write(transport, [{"address": 400, "word": _word(0.1)}])
    assert service.lost() == []
    transport.dsp_loads = 1  # a program load puts back every word
    assert {item["address"] for item in service.status(transport)["lost"]} == {400, 500}
    service.forget_lost()
    assert service.lost() == [] and service.status(transport)["modified_words"] == 0


def test_band_corners_keep_low_below_high(small_registry):
    service = dsp_registry.DspParameterService(loader=lambda: small_registry)
    transport = _Transport()
    with pytest.raises(ValueError, match="below"):
        service.set(transport, "band_low_hz", 4000)
    assert service.set(transport, "band_high_hz", 8000)["band_high_hz"] == 8000
    assert service.set(transport, "band_low_hz", 4000)["band_low_hz"] == 4000
    with pytest.raises(ValueError, match="below"):
        service.set(transport, "band_high_hz", 3000)
    with pytest.raises(ValueError, match="between"):
        service.set(transport, "band_low_hz", 50)


@pytest.mark.parametrize("current, target", [
    ((300, 500), (500, 3500)), ((4000, 6000), (500, 3500)), ((500, 3500), (4000, 6000)), ((1000, 3500), (300, 800))])
def test_setting_order_keeps_every_step_valid(small_registry, current, target):
    service = dsp_registry.DspParameterService(loader=lambda: small_registry)
    transport = _Transport()
    for key, value in dsp_registry.setting_order({"band_high_hz": 3500}, {"band_high_hz": 8000, "band_low_hz": current[0]}):
        service.set(transport, key, value)
    service.set(transport, "band_high_hz", current[1])
    now = service.status(transport)["settings"]
    changes = {"measure": 0, "band_high_hz": target[1], "band_low_hz": target[0]}
    for key, value in dsp_registry.setting_order(now, changes):
        service.set(transport, key, value)
    settings = service.status(transport)["settings"]
    assert (settings["band_low_hz"], settings["band_high_hz"], settings["measure"]) == (*target, 0)


def test_deployed_bank_registry_matches_the_boot_tables(monkeypatch):
    monkeypatch.setenv("HEIMDALL_DSP_EXPORT", "SigmaStudioBank7x7")
    contract = json.loads((dsp_registry.selected_export_dir() / "beam_table_2d.json").read_text(encoding="utf-8"))
    registry = dsp_registry.load_registry()
    groups = {}
    for p in registry["parameters"]:
        groups.setdefault(p["group"], []).append(p)
    boot = [dsp_registry._signed_8_24(v) for v in contract["bank"]["band_pass"]]
    assert len(groups["bank_band"]) == 49 and all(p["default_words"] == boot for p in groups["bank_band"])
    assert len(groups["bank_alias"]) == 44 and {p["words"] for p in groups["bank_alias"]} == {31}
    assert len(groups["bank_ch1"]) == 1 and groups["bank_ch1"][0]["words"] == 31
    assert {p["name"] for p in groups["bank_detector"]} == {"TCONST", "hold", "decay"}
    assert len(groups["bank_detector"]) == 3 * 49
