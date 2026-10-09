from __future__ import annotations

import math
import time
import zlib

import pytest

from dsp_registry import DspParameterService
from heimdall_protocol import HeimdallState, ProtocolError, parse_line
from heimdall_transport import (
    DeployedProgram,
    EmulatorHeimdallTransport,
    HeimdallTransport,
    SerialHeimdallTransport,
    TRACK_FAILURE_LIMIT,
    validate_command,
)


class DeterministicAdaptiveEmulator(EmulatorHeimdallTransport):
    def __init__(self, rows=7, columns=7):
        registry = {"export": {"sha256": "0" * 64}, "map_hash": None, "parameters": []}
        service = DspParameterService(loader=lambda: registry)
        super().__init__(rows=rows, columns=columns, sector_time_s=0.0,
                         parameter_service=service)
        service.set(self, "measure", 1)
        self.test_levels = [100] * (rows * columns)
        self.measured_sectors = []
        self.running = True
        self.connected = True
        self._emit_configuration()

    def _level_for_sector(self, sector):
        self.measured_sectors.append(sector)
        return self.test_levels[sector]


def test_dynamic_configuration_and_measurement_state():
    state = HeimdallState()
    state.apply_line("READY,2D,2,3,6,44")
    state.apply_line("AZIMUTH,3,-30.0000,0.0000,30.0000")
    state.apply_line("ELEVATION,2,-10.0000,10.0000")
    state.apply_line("P,1,2,30.0000,10.0000,16777216")

    snapshot = state.snapshot()
    assert snapshot["configuration"]["sectors"] == 6
    assert snapshot["levels_raw"][1][2] == 16777216
    assert snapshot["levels_linear"][1][2] == 1.0
    assert snapshot["levels_db"][1][2] == 0.0


def test_transport_reports_target_update_rate_and_age():
    now = [10.0]
    transport = HeimdallTransport(clock=lambda: now[0])
    transport.apply_line("READY,2D,2,2,4,44")
    transport.apply_line("SCAN_STARTED,G")
    transport.apply_line("TARGET_ACQUIRED,0,0,0,-10.0000,-10.0000,1000")
    now[0] = 10.02
    transport.apply_line("TARGET_UPDATED,0,0,0,-10.0000,-10.0000,1100")
    now[0] = 10.04
    transport.apply_line("TARGET_UPDATED,1,0,1,10.0000,-10.0000,1200")

    snapshot = transport.snapshot()
    assert snapshot["target_update_rate_hz"] == pytest.approx(50.0)
    assert snapshot["target_update_age_s"] == pytest.approx(0.0)

    now[0] = 10.14
    assert transport.snapshot()["target_update_age_s"] == pytest.approx(0.1)
    transport.apply_line("SCAN_STOPPED")
    assert transport.snapshot()["target_update_rate_hz"] is None
    assert transport.snapshot()["target_update_age_s"] is None


@pytest.mark.parametrize(
    "line",
    [
        "",
        "READY,2D,2,3,5,44",
        "AZIMUTH,3,-30,0",
        "P,0,0,NaN,0,1",
        "P,-1,0,0,0,1",
        "STEER_ERR,1,2,nope",
        "UNKNOWN,1",
    ],
)
def test_malformed_records_are_rejected(line):
    with pytest.raises(ProtocolError):
        parse_line(line)


def test_out_of_bounds_measurement_is_rejected_after_ready():
    state = HeimdallState()
    state.apply_line("READY,2D,2,3,6,44")
    with pytest.raises(ProtocolError):
        state.apply_line("P,2,0,0,0,1")


def test_target_and_timing_records():
    state = HeimdallState()
    state.apply_line("READY,2D,7,7,49,44")
    state.apply_line("TARGET_ACQUIRED,24,3,3,0.0000,0.0000,1000")
    state.apply_line("TIMING,G,49,100000,110000,2000,2500")
    snapshot = state.snapshot()
    assert snapshot["target"]["sector"] == 24
    assert snapshot["last_timing"]["wall_us"] == 110000
    state.apply_line("TARGET_LOST,24,3,3,0.0000,0.0000,10")
    assert state.snapshot()["target"] is None


def test_taper_record_reports_whether_every_gain_was_verified():
    state = HeimdallState()
    state.apply_line("READY,2D,7,7,49,44")
    state.apply_line("TAPER,1500,44,44,0")
    assert state.snapshot()["taper"] == {"sigma": 1.5, "written": 44, "verified": 44, "code": 0, "applied": True}
    state.apply_line("TAPER,1500,44,40,0")
    assert not state.snapshot()["taper"]["applied"]
    state.apply_line("TAPER,0,5,0,-5")
    assert state.snapshot()["taper"] == {"sigma": 0.0, "written": 5, "verified": 0, "code": -5, "applied": False}
    for line in ("TAPER,1,44,44", "TAPER,-1,44,44,0", "TAPER,0,44,x,0"):
        with pytest.raises(ProtocolError):
            parse_line(line)


def test_dsp_check_and_table_load_records():
    state = HeimdallState()
    state.apply_line("READY,2D,7,7,49,44")
    state.apply_line("DSP,MICCANVAS_SS+_BEAMFORM_Golden_Image_LUT,lut,1,44,44,0")
    state.apply_line("TAPER,0,44,44,0")
    state.apply_line("TABLES,2156,2156,2156,0")
    snapshot = state.snapshot()
    assert snapshot["dsp"] == {"program": "MICCANVAS_SS+_BEAMFORM_Golden_Image_LUT", "style": "lut",
                                "steer_words": 1, "read": 44, "matched": 44, "code": 0, "matches": True}
    assert snapshot["tables"] == {"words": 2156, "written": 2156, "verified": 2156, "code": 0, "loaded": True}
    # On a mismatch the firmware writes nothing, so the previous taper and tables no longer hold.
    state.apply_line("DSP,MICCANVAS_SS+_BEAMFORM_Golden_Image_LUT,lut,1,44,3,0")
    snapshot = state.snapshot()
    assert not snapshot["dsp"]["matches"]
    assert snapshot["taper"] is None and snapshot["tables"] is None
    state.apply_line("DSP,MICCANVAS_SS+_BEAMFORM_Multi_Beam,per_mic,44,0,0,-5")
    assert not state.snapshot()["dsp"]["matches"]
    state.apply_line("TABLES,2156,2156,2100,0")
    assert not state.snapshot()["tables"]["loaded"]
    for line in ("DSP,,lut,1,44,44,0", "DSP,x,beam,1,44,44,0", "DSP,x,lut,0,44,44,0", "TABLES,1,1,1"):
        with pytest.raises(ProtocolError):
            parse_line(line)


def test_emulator_reports_the_deployed_program():
    program = DeployedProgram(name="Golden_LUT", style="lut", steer_words=1, table_words=2156,
                              taper_sigma=0.75)
    emulator = EmulatorHeimdallTransport(rows=2, columns=2, program=program)
    emulator._emit_configuration()
    snapshot = emulator.snapshot()
    assert snapshot["taper"]["sigma"] == 0.75 and snapshot["taper"]["applied"]
    assert snapshot["dsp"]["program"] == "Golden_LUT" and snapshot["dsp"]["matches"]
    assert snapshot["tables"] == {"words": 2156, "written": 2156, "verified": 2156, "code": 0, "loaded": True}
    assert emulator.protocol_errors == 0


def test_one_shot_scan_returns_to_idle_on_completion():
    state = HeimdallState()
    state.apply_line("READY,2D,2,2,4,44")
    state.apply_line("SCAN_STARTED,F")
    assert state.snapshot()["mode"] == "F"
    state.apply_line("SCAN_DONE")
    assert state.snapshot()["mode"] == "IDLE"


def test_information_refresh_preserves_active_mode():
    state = HeimdallState()
    state.apply_line("READY,2D,2,2,4,44")
    state.apply_line("SCAN_STARTED,G")
    state.apply_line("READY,2D,2,2,4,44")
    assert state.snapshot()["mode"] == "G"


def test_stop_and_direct_steer_clear_tracking_state():
    state = HeimdallState()
    state.apply_line("READY,2D,7,7,49,44")
    state.apply_line("TARGET_ACQUIRED,24,3,3,0.0000,0.0000,1000")
    state.apply_line("SCAN_STOPPED")
    assert state.snapshot()["target"] is None
    state.apply_line("TARGET_ACQUIRED,24,3,3,0.0000,0.0000,1000")
    record = state.apply_line("STEER_OK,48,6,6,60.0000,51.4286")
    assert record["sector"] == 48
    state.apply_line("TIMING,S,2800")
    assert state.snapshot()["last_steer"]["azimuth_deg"] == 60.0
    assert state.snapshot()["target"] is None


def test_last_measurement_survives_later_records():
    state = HeimdallState()
    state.apply_line("READY,2D,1,2,2,44")
    state.apply_line("P,0,1,10.0000,0.0000,8388608")
    state.apply_line("TONE,0,182.0,20.0,6,-10.0")
    measurement = state.snapshot()["last_measurement"]
    assert measurement["count"] == 1 and (measurement["row"], measurement["column"]) == (0, 1)
    assert measurement["level_db"] == pytest.approx(-6.0206, abs=1e-3)
    state.apply_line("P,0,0,-10.0000,0.0000,8388608")
    assert state.snapshot()["last_measurement"]["count"] == 2


def test_command_validation():
    assert validate_command("S,399", sectors=400) == "S,399"
    assert validate_command("I") == "I"
    assert validate_command("M") == "M"
    assert validate_command("R,1") == "R,1"
    assert validate_command("R,0") == "R,0"
    assert validate_command("D") == "D"
    assert validate_command("SET,window_us,5000") == "SET,window_us,5000"
    pw_body = "PW,1134,01000000,FFFFFFFF"
    checked_pw = validate_command(pw_body)
    assert checked_pw.startswith(f"{pw_body}*")
    assert validate_command(checked_pw) == checked_pw
    assert validate_command("PR,1134,16") == "PR,1134,16"
    assert validate_command("DG,-4000,4000,7,-3000,3000,7", sectors=49) == \
        "DG,-4000,4000,7,-3000,3000,7"
    with pytest.raises(ValueError):
        validate_command("S,400", sectors=400)
    with pytest.raises(ValueError):
        validate_command("s,1")
    with pytest.raises(ValueError):
        validate_command("PW,1134,01000000*00")
    with pytest.raises(ValueError):
        validate_command("PR,1134,17")
    with pytest.raises(ValueError):
        validate_command("DG,-4000,4000,8,-3000,3000,7", sectors=49)


def test_recording_and_audio_transfer_records():
    state = HeimdallState()
    state.apply_line("AUDIO_READY,48000,16,1")
    state.apply_line("REC_STARTED,48000,16,1")
    assert state.snapshot()["recording_state"] == "recording"

    state.apply_line("REC_STOPPED,96000,192000,-123,456,47,0,987654321")
    snapshot = state.snapshot()
    assert snapshot["recording_state"] == "ready"
    assert snapshot["last_recording"]["bytes"] == 192000
    assert snapshot["last_recording"]["overruns"] == 0

    state.apply_line("AUDIO_BEGIN,1,192000,48000,16,1,89ABCDEF")
    assert state.snapshot()["recording_state"] == "downloading"
    state.apply_line("AUDIO_END,89ABCDEF")
    snapshot = state.snapshot()
    assert snapshot["recording_state"] == "downloaded"
    assert snapshot["audio_transfer"]["crc32"] == 0x89ABCDEF
    assert snapshot["audio_transfer"]["end_crc32"] == 0x89ABCDEF


def test_new_recording_invalidates_previous_audio_download():
    transport = HeimdallTransport()
    transport.latest_audio = {
        "sample_rate_hz": 48000,
        "bits_per_sample": 16,
        "channels": 1,
        "pcm": b"old",
    }
    transport.audio_download_expected = 3
    transport.audio_download_received = 3

    transport.apply_line("REC_STARTED,48000,16,1")

    snapshot = transport.snapshot()
    assert transport.get_audio_download() is None
    assert not snapshot["audio_download_ready"]
    assert snapshot["audio_download_expected"] == 0
    assert snapshot["audio_download_received"] == 0


def test_serial_audio_payload_uses_exact_length_not_newline_framing():
    class FakeSerial:
        def __init__(self):
            self.writes = []

        def write(self, data):
            self.writes.append(data)
            return len(data)

        def flush(self):
            pass

    transport = SerialHeimdallTransport.__new__(SerialHeimdallTransport)
    HeimdallTransport.__init__(transport)
    transport.rx_buffer = bytearray()
    transport.audio_header = None
    transport.audio_payload = bytearray()
    transport.audio_acknowledged = 0
    transport.write_lock = __import__("threading").Lock()
    transport.serial = FakeSerial()

    pcm = b"\x00\x01\n\r\xff\x7f\x00\x80"
    crc = zlib.crc32(pcm) & 0xFFFFFFFF
    packet = (
        f"AUDIO_BEGIN,2,{len(pcm)},48000,16,1,4,{crc:08X}\r\n".encode("ascii")
        + pcm
        + f"\r\nAUDIO_END,{crc:08X}\r\n".encode("ascii")
    )
    for split in (packet[:5], packet[5:23], packet[23:31], packet[31:]):
        transport._process_received_data(split)

    captured = transport.get_audio_download()
    assert captured is not None
    assert captured["pcm"] == pcm
    assert captured["sample_rate_hz"] == 48000
    assert captured["bits_per_sample"] == 16
    assert transport.snapshot()["audio_download_progress"] == 1.0
    assert transport.serial.writes == [b"\x06", b"\x06"]


def test_transport_request_waits_for_matching_reply_and_reports_firmware_errors():
    class ReplyTransport(HeimdallTransport):
        def send_command(self, command):
            if command == "PR,100,2":
                self.apply_line("PV,100,01000000,FFFFFFFF")
            else:
                self.apply_line("ERR,PW,ADDR")

    transport = ReplyTransport()
    record = transport.request("PR,100,2", {"pv"})
    assert record == {"type": "pv", "address": 100, "words": [1 << 24, -1]}
    with pytest.raises(RuntimeError, match="PW: ADDR"):
        transport.request("PW,100,01000000", {"pw_ok"})


def test_emulator_uses_the_same_protocol_state_path():
    emulator = EmulatorHeimdallTransport(
        rows=3,
        columns=4,
        azimuth_min_deg=-40.0,
        azimuth_max_deg=40.0,
        elevation_min_deg=-30.0,
        elevation_max_deg=30.0,
        sector_time_s=0.0,
    )
    emulator.start()
    try:
        emulator.send_command("F")
        deadline = time.monotonic() + 2.0
        while emulator.snapshot()["scan_count"] < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        snapshot = emulator.snapshot()
        assert snapshot["connected"]
        assert snapshot["configuration"]["rows"] == 3
        assert snapshot["configuration"]["columns"] == 4
        assert snapshot["azimuth_deg"] == [-30.0, -10.0, 10.0, 30.0]
        assert snapshot["elevation_deg"] == [-20.0, 0.0, 20.0]
        assert snapshot["scan_count"] == 1
        assert all(value is not None for row in snapshot["levels_raw"] for value in row)
    finally:
        emulator.stop()
        emulator.join(timeout=1.0)


def test_emulator_study_commands_round_trip_through_protocol():
    registry = {
        "export": {"sha256": "0" * 64},
        "map_hash": "0123456789abcdef",
        "parameters": [{
            "block": "Gain_DSP00", "name": "Gain", "address": 100, "words": 1,
            "default_words": [1 << 24], "writable": True, "reason": "",
        }],
    }
    service = DspParameterService(loader=lambda: registry)
    emulator = EmulatorHeimdallTransport(
        rows=3,
        columns=3,
        sector_time_s=0.0,
        parameter_service=service,
        program=DeployedProgram(map_hash="0123456789abcdef"),
    )
    emulator.start()
    try:
        deadline = time.monotonic() + 1.0
        while emulator.snapshot()["map_hash"] is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert emulator.snapshot()["map_hash"] == "0123456789abcdef"
        assert emulator.request("SET,window_us,8000", {"set_ok"})["value"] == 8000
        assert emulator.request("PW,100,00800000", {"pw_ok"})["count"] == 1
        assert emulator.request("PR,100,1", {"pv"})["words"] == [1 << 23]
        direction_set = emulator.request("DG,-4000,4000,3,-3000,3000,2", {"dirset"})
        assert (direction_set["rows"], direction_set["columns"]) == (2, 3)
        assert emulator.snapshot()["configuration"]["sectors"] == 6
        direction_set = emulator.request("DG,-4000,4000,3,-3000,3000,3", {"dirset"})
        assert (direction_set["rows"], direction_set["columns"]) == (3, 3)
    finally:
        emulator.stop()
        emulator.join(timeout=1.0)


def _quiet_registry_service():
    return DspParameterService(loader=lambda: {"export": {"sha256": "0" * 64},
                                               "map_hash": None, "parameters": []})


class _TwoLevelProvider:
    def level_for_sector(self, sector):
        return (1000, 0)[sector]

    def metadata(self):
        return {}


def test_emulator_detector_decays_only_for_the_settle_time_after_a_steer():
    now = [0.0]
    service = _quiet_registry_service()
    emulator = EmulatorHeimdallTransport(
        rows=1, columns=2, sector_time_s=0.0, level_provider=_TwoLevelProvider(),
        parameter_service=service, program=DeployedProgram(level_time_constant_ms=10.0),
        clock=lambda: now[0],
    )
    service.set(emulator, "settle_us", 5000)
    emulator._measurement_level(0)
    now[0] = 1.0
    assert emulator._measurement_level(0) == 1000
    # A long host round trip keeps the detector on the loud beam until the steer.
    now[0] = 1.2
    assert emulator._measurement_level(1) == pytest.approx(1000 * math.exp(-0.5), abs=1)

    service.set(emulator, "measure", 1)
    assert emulator._measurement_level(0) == 1000
    assert emulator._measurement_level(1) == 0


def test_acoustic_emulator_refuses_direction_sets():
    emulator = EmulatorHeimdallTransport(rows=1, columns=2, sector_time_s=0.0,
                                         level_provider=_TwoLevelProvider(),
                                         parameter_service=_quiet_registry_service())
    emulator.start()
    try:
        with pytest.raises(RuntimeError, match="DG: UNSUPPORTED"):
            emulator.request("DG,-1000,1000,2,0,0,1", {"dirset"})
    finally:
        emulator.stop()
        emulator.join(timeout=1.0)


def test_emulator_reports_tones_once_enabled():
    service = _quiet_registry_service()
    emulator = EmulatorHeimdallTransport(rows=1, columns=1, sector_time_s=0.0,
                                         parameter_service=service)
    emulator.current_sector = 0
    emulator._report_tones()
    assert emulator.snapshot()["tones"] == {}
    service.set(emulator, "tone", 100)
    emulator._report_tones()
    tone = emulator.snapshot()["tones"]["0"]
    assert tone["f0_hz"] > 0 and tone["harmonics"] >= 0


def test_acoustic_level_provider_uses_the_same_emulator_policy():
    class Provider:
        def level_for_sector(self, sector):
            return 400 if sector == 5 else 100

        def metadata(self):
            return {"acoustic_cache_id": "test-cache", "acoustic_calibrated": False}

    emulator = EmulatorHeimdallTransport(
        rows=2, columns=3, sector_time_s=0.0, level_provider=Provider()
    )
    emulator.start()
    try:
        emulator.send_command("G")
        deadline = time.monotonic() + 1.0
        while emulator.snapshot()["target"] is None and time.monotonic() < deadline:
            time.sleep(0.005)
        snapshot = emulator.snapshot()
        assert snapshot["transport"] == "acoustic-emulator"
        assert snapshot["target"]["sector"] == 5
        assert snapshot["acoustic_cache_id"] == "test-cache"
        assert not snapshot["acoustic_calibrated"]
        assert "emulator_flight_profile" not in snapshot
    finally:
        emulator.stop()
        emulator.join(timeout=1.0)


def test_large_emulator_stops_during_an_active_scan():
    emulator = EmulatorHeimdallTransport(rows=20, columns=20, sector_time_s=0.01)
    emulator.start()
    emulator.send_command("F")
    time.sleep(0.03)
    emulator.stop()
    emulator.join(timeout=0.5)
    assert not emulator.is_alive()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rows": 0},
        {"columns": 21},
        {"azimuth_min_deg": 10.0, "azimuth_max_deg": 10.0},
        {"elevation_min_deg": -91.0},
    ],
)
def test_emulator_rejects_invalid_grid_configuration(kwargs):
    with pytest.raises(ValueError):
        EmulatorHeimdallTransport(**kwargs)


@pytest.mark.parametrize("flight_profile", ["crossing", "approach", "orbit", "patrol", "evasive", "legacy"])
def test_emulator_flight_profiles_produce_finite_target_states(flight_profile):
    emulator = EmulatorHeimdallTransport(flight_profile=flight_profile)
    for elapsed in (0.0, 3.0, 10.0, 25.0):
        azimuth, elevation, target_range, attenuation = emulator._target_state(elapsed)
        assert all(math.isfinite(value) for value in (azimuth, elevation, target_range, attenuation))
        assert -90.0 < azimuth < 90.0
        assert -90.0 < elevation < 90.0
        assert target_range > 0.0
        assert 0.0 <= attenuation <= 1.0


def test_emulator_flight_speed_scales_trajectory_time():
    normal = EmulatorHeimdallTransport(flight_profile="crossing", flight_speed=1.0)
    fast = EmulatorHeimdallTransport(flight_profile="crossing", flight_speed=2.0)
    assert fast._target_state(4.0) == pytest.approx(normal._target_state(8.0))


def test_emulator_flight_clock_and_snapshot_metadata_are_deterministic():
    now = [100.0]
    emulator = EmulatorHeimdallTransport(
        flight_profile="orbit", flight_speed=1.5, clock=lambda: now[0]
    )
    initial = emulator._target_state()
    now[0] = 104.0
    assert emulator._target_state() == pytest.approx(emulator._target_state(4.0))
    assert emulator._target_state() != pytest.approx(initial)
    snapshot = emulator.snapshot()
    assert snapshot["emulator_flight_profile"] == "orbit"
    assert snapshot["emulator_flight_speed"] == 1.5


@pytest.mark.parametrize("flight_profile", ["crossing", "approach", "orbit", "patrol", "evasive"])
def test_emulator_flight_trajectory_has_no_position_jumps(flight_profile):
    emulator = EmulatorHeimdallTransport(flight_profile=flight_profile)

    def cartesian(state):
        azimuth, elevation, target_range, _ = state
        azimuth_rad = math.radians(azimuth)
        elevation_rad = math.radians(elevation)
        horizontal = target_range * math.cos(elevation_rad)
        return (
            horizontal * math.cos(azimuth_rad),
            horizontal * math.sin(azimuth_rad),
            target_range * math.sin(elevation_rad),
        )

    previous = cartesian(emulator._target_state(0.0))
    for step in range(1, 6001):
        current = cartesian(emulator._target_state(step * 0.01))
        assert math.dist(previous, current) < 0.35
        previous = current


def test_emulator_approach_range_changes_detector_level():
    emulator = EmulatorHeimdallTransport(rows=1, columns=1, flight_profile="approach")
    emulator.random.uniform = lambda _low, _high: 0.0
    far_azimuth, far_elevation, far_range, _ = emulator._target_state(0.0)
    near_time = math.pi / 0.38
    near_azimuth, near_elevation, near_range, _ = emulator._target_state(near_time)
    assert near_range < far_range
    emulator.started_at = time.monotonic()
    emulator.azimuth_deg = [far_azimuth]
    emulator.elevation_deg = [far_elevation]
    far_level = emulator._level_for_sector(0)
    emulator.started_at = time.monotonic() - near_time
    emulator.azimuth_deg = [near_azimuth]
    emulator.elevation_deg = [near_elevation]
    near_level = emulator._level_for_sector(0)
    assert near_level > far_level


def test_emulator_evasive_profile_contains_short_attenuation_event():
    emulator = EmulatorHeimdallTransport(flight_profile="evasive")
    assert emulator._target_state(10.0)[3] == 1.0
    assert emulator._target_state(12.0)[3] == 0.0
    assert emulator._target_state(13.0)[3] == 1.0


def test_emulator_evasive_dropout_forces_loss_and_reacquisition():
    now = [0.0]
    emulator = EmulatorHeimdallTransport(
        flight_profile="evasive", sector_time_s=0.0, clock=lambda: now[0]
    )
    emulator.random.uniform = lambda _low, _high: 0.0
    emulator.running = True
    emulator.connected = True
    emulator._emit_configuration()
    emulator.parameter_service.set(emulator, "measure", 1)
    emulator._handle_command("G")
    for _ in range(3):
        emulator._adaptive_cycle()
    assert emulator.adaptive_state == "TRACK"
    assert emulator.snapshot()["target"] is not None

    now[0] = 12.0
    for _ in range(TRACK_FAILURE_LIMIT):
        emulator._adaptive_cycle()
    assert emulator.adaptive_state == "REACQUIRE"
    emulator._adaptive_cycle()
    assert emulator.adaptive_state == "SEARCH"
    assert emulator.snapshot()["target"] is None

    now[0] = 13.0
    for _ in range(3):
        emulator._adaptive_cycle()
    assert emulator.adaptive_state == "TRACK"
    assert emulator.snapshot()["target"] is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"flight_profile": "unknown"},
        {"flight_speed": 0.1},
        {"flight_speed": 3.1},
    ],
)
def test_emulator_rejects_invalid_flight_configuration(kwargs):
    with pytest.raises(ValueError):
        EmulatorHeimdallTransport(**kwargs)


def test_emulator_steer_measures_and_repeat_measure_does_not_change_sector():
    emulator = EmulatorHeimdallTransport(rows=7, columns=7, sector_time_s=0.0)
    emulator.start()
    try:
        emulator.send_command("S,48")
        deadline = time.monotonic() + 1.0
        while emulator.snapshot()["levels_raw"][6][6] is None and time.monotonic() < deadline:
            time.sleep(0.005)
        first = emulator.snapshot()
        assert first["last_steer"]["sector"] == 48
        assert first["levels_raw"][6][6] is not None
        emulator.send_command("M")
        first_sequence = first["sequence"]
        while emulator.snapshot()["sequence"] == first_sequence and time.monotonic() < deadline:
            time.sleep(0.005)
        assert emulator.snapshot()["last_steer"]["sector"] == 48
    finally:
        emulator.stop()
        emulator.join(timeout=1.0)


def test_emulator_adaptive_uses_confirmation_and_fifty_local_tracking_passes():
    emulator = DeterministicAdaptiveEmulator()
    center = 24
    emulator.test_levels[center] = 400
    emulator._handle_command("G")

    emulator._adaptive_cycle()
    assert emulator.adaptive_state == "CONFIRM"
    assert emulator.snapshot()["scan_count"] == 1
    assert emulator.measured_sectors == list(range(49))

    emulator._adaptive_cycle()
    assert emulator.adaptive_state == "CONFIRM"
    emulator._adaptive_cycle()
    assert emulator.adaptive_state == "TRACK"
    assert emulator.target_sector == center
    assert emulator.snapshot()["target"]["sector"] == center
    assert emulator.measured_sectors[49:] == emulator._cross(center) * 2

    emulator.measured_sectors.clear()
    for _ in range(49):
        emulator._adaptive_cycle()
    assert emulator.adaptive_state == "TRACK"
    assert emulator.tracking_passes == 49
    assert emulator.snapshot()["scan_count"] == 1
    assert emulator.measured_sectors == emulator._cross(center) * 49

    emulator._adaptive_cycle()
    assert emulator.adaptive_state == "SEARCH"
    assert emulator.snapshot()["scan_count"] == 1
    emulator._adaptive_cycle()
    assert emulator.snapshot()["scan_count"] == 2


def test_emulator_adaptive_flat_levels_repeat_search_without_locking():
    emulator = DeterministicAdaptiveEmulator(rows=3, columns=4)
    emulator._handle_command("G")

    emulator._adaptive_cycle()
    assert emulator.adaptive_state == "SEARCH"
    assert emulator.snapshot()["target"] is None
    assert emulator.snapshot()["scan_count"] == 1

    emulator.measured_sectors.clear()
    emulator._adaptive_cycle()
    assert emulator.adaptive_state == "SEARCH"
    assert emulator.snapshot()["target"] is None
    assert emulator.snapshot()["scan_count"] == 2
    assert emulator.measured_sectors == list(range(12))


def test_emulator_adaptive_matches_move_hysteresis_and_reacquisition():
    emulator = DeterministicAdaptiveEmulator()
    center = 24
    neighbor = 25
    emulator.test_levels[center] = 400
    emulator._handle_command("G")
    for _ in range(3):
        emulator._adaptive_cycle()
    assert emulator.target_sector == center

    emulator.test_levels[center] = 100
    emulator.test_levels[neighbor] = 500
    emulator._adaptive_cycle()
    assert emulator.target_sector == center
    emulator._adaptive_cycle()
    assert emulator.target_sector == neighbor

    emulator.test_levels = [0] * 49
    for _ in range(3):
        emulator._adaptive_cycle()
    assert emulator.adaptive_state == "REACQUIRE"
    emulator.measured_sectors.clear()
    emulator._adaptive_cycle()
    assert emulator.adaptive_state == "SEARCH"
    assert emulator.snapshot()["target"] is None
    assert emulator.measured_sectors == emulator._neighborhood(neighbor)

def _configured_state(rows=2, columns=3):
    state = HeimdallState()
    state.apply_line(f"READY,2D,{rows},{columns},{rows * columns},44")
    state.apply_line(f"AZIMUTH,{columns}," + ",".join(f"{c * 10.0:.4f}" for c in range(columns)))
    state.apply_line(f"ELEVATION,{rows}," + ",".join(f"{r * 10.0:.4f}" for r in range(rows)))
    return state


def test_study_protocol_records_reach_the_snapshot():
    state = _configured_state()
    state.apply_line("MAP,0123456789abcdef")
    state.apply_line("SETTINGS,settle_us=1250,guard_us=500,window_us=5000,measure=1,band=1,channel=0,map_record=1,tone=0")
    state.apply_line("SET_OK,window_us,8000")
    state.apply_line("PW_OK,2338,2")
    state.apply_line("PV,2338,00000001,FFFFFFFF")
    snapshot = state.snapshot()
    assert snapshot["map_hash"] == "0123456789abcdef"
    assert snapshot["settings"]["measure"] == 1 and snapshot["settings"]["window_us"] == 8000
    assert parse_line("PV,2338,00000001,FFFFFFFF")["words"] == [1, -1]  # signed, like the register map
    assert parse_line("PW_OK,2338,2") == {"type": "pw_ok", "address": 2338, "count": 2}


def test_compact_scan_map_fills_every_sector():
    state = _configured_state()
    state.apply_line("LEVELS,S,2,3,1,2,3,4,5,6")
    assert state.snapshot()["levels_raw"] == [[1, 2, 3], [4, 5, 6]]
    with pytest.raises(ProtocolError):
        state.apply_line("LEVELS,S,3,3,1,2,3,4,5,6,7,8,9")


def test_tone_class_dirset_and_recording_metadata():
    state = _configured_state()
    state.apply_line("TONE,0,1875,123,6,-213")
    state.apply_line("CLASS,1,drone,0.87")
    state.apply_line("DIRSET,2,3,264,264,0,61234")
    state.apply_line("REC_META,4,2,12")
    snapshot = state.snapshot()
    assert snapshot["tones"]["0"]["harmonics"] == 6
    assert snapshot["classes"]["1"]["label"] == "drone"
    assert snapshot["direction_set"]["verified"] == 264
    assert snapshot["recording_meta"] is not None
    for line in ("TONE,2,1,1,1,1", "DIRSET,2,3,1,1,0", "PW_OK,1,6", "MAP"):
        with pytest.raises(ProtocolError):
            parse_line(line)
