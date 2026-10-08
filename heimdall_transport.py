"""Real and emulated transports for the Heimdall firmware protocol."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import queue
import random
import re
import threading
import time
from typing import Any
import zlib

from dsp_registry import DspParameterService, checksum
from heimdall_bank import BankTracker, LOST_MAPS as BANK_LOST_MAPS, PERIOD_S as BANK_PERIOD_S
from heimdall_protocol import HeimdallState, ProtocolError


ACQUIRE_RATIO = 3.0 / 2.0
RETAIN_RATIO = 5.0 / 4.0
LEVEL_ABSOLUTE_FLOOR = 0
CONFIRM_PASSES = 2
TRACK_FAILURE_LIMIT = 3
TRACK_MOVE_CONFIRMATIONS = 2
GLOBAL_RESCAN_INTERVAL = 50
FLIGHT_PROFILES = frozenset({"crossing", "approach", "orbit", "patrol", "evasive", "legacy"})


@dataclass(frozen=True)
class DeployedProgram:
    """The DSP program the firmware is built for, as its beam table contract describes it."""
    name: str = "emulated"
    style: str = "per_mic"
    steer_words: int = 44
    signature_words: int = 44
    table_words: int = 0
    taper_sigma: float = 0.0
    microphones: int = 44
    selectors: bool = False
    bank_rows: int = 0
    bank_columns: int = 0
    bank_rate_hz: int = 0
    bank_azimuth_deg: tuple[float, ...] = ()
    bank_elevation_deg: tuple[float, ...] = ()
    map_hash: str | None = None
    level_time_constant_ms: float = 8.665

    @classmethod
    def from_contract(cls, contract: dict[str, Any]) -> "DeployedProgram":
        steering = contract["steering"]
        bank = contract.get("bank") or {}
        return cls(
            name=str(contract["dsp_export"]["name"]),
            style=str(steering["style"]),
            steer_words=len(steering["steer_addresses"]),
            signature_words=len(contract["signature"]),
            table_words=int(steering["table_words"]),
            taper_sigma=float(contract["taper_sigma"]),
            microphones=int(contract.get("microphones", 44)),
            selectors=bool(contract.get("selectors")),
            bank_rows=int(bank.get("rows", 0)),
            bank_columns=int(bank.get("columns", 0)),
            bank_rate_hz=int(bank.get("sample_rate_hz", 0)),
            bank_azimuth_deg=tuple(float(value) for value in bank.get("azimuth_deg", ())),
            bank_elevation_deg=tuple(float(value) for value in bank.get("elevation_deg", ())),
            map_hash=(contract.get("study") or {}).get("map_hash"),
            level_time_constant_ms=float(contract.get("level_time_constant_ms", 8.665)),
        )

    @property
    def bank_beams(self) -> int:
        return self.bank_rows * self.bank_columns

    def configuration_lines(self) -> list[str]:
        """What the firmware prints after AZIMUTH/ELEVATION for this program."""
        lines = []
        if self.bank_beams:
            lines.append(f"BANK,{self.bank_rows},{self.bank_columns},{self.bank_rate_hz}")
        if self.selectors:
            lines.append(f"OUTPUTS,{self.microphones},{self.bank_beams}")
        return lines

    def boot_lines(self) -> list[str]:
        """What the firmware prints after INFO when the EEPROM holds this program."""
        words = self.signature_words
        return [
            f"DSP,{self.name},{self.style},{self.steer_words},{words},{words},0",
            f"TAPER,{int(round(self.taper_sigma * 1000.0))},44,44,0",
            f"TABLES,{self.table_words},{self.table_words},{self.table_words},0",
        ]


class HeimdallTransport:
    """Thread-safe shared state exposed by all Heimdall transports."""

    def __init__(self, clock=time.monotonic) -> None:
        self.state = HeimdallState()
        self.lock = threading.Lock()
        self.running = False
        self.connected = False
        self.transport_name = "disconnected"
        self.protocol_errors = 0
        self.clock = clock
        self.target_update_times: deque[float] = deque(maxlen=32)
        self.audio_download_expected = 0
        self.audio_download_received = 0
        self.audio_download_error: str | None = None
        self.latest_audio: dict[str, Any] | None = None
        self.response_condition = threading.Condition(self.lock)
        self.response_sequence = 0
        self.response_records: deque[tuple[int, dict[str, Any]]] = deque(maxlen=64)

    def apply_line(self, line: str) -> dict[str, Any] | None:
        try:
            with self.response_condition:
                record = self.state.apply_line(line)
                record_type = record["type"]
                if record_type in {"scan_started", "scan_stopped", "steer_ok", "target_lost"}:
                    self.target_update_times.clear()
                elif record_type == "target_acquired":
                    self.target_update_times.clear()
                    self.target_update_times.append(self.clock())
                elif record_type == "target_updated":
                    self.target_update_times.append(self.clock())
                elif record_type == "recording_started":
                    self.latest_audio = None
                    self.audio_download_expected = 0
                    self.audio_download_received = 0
                    self.audio_download_error = None
                self.response_sequence += 1
                self.response_records.append((self.response_sequence, record))
                self.response_condition.notify_all()
                return record
        except ProtocolError:
            self.protocol_errors += 1
            return None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            snapshot = self.state.snapshot()
            update_times = tuple(self.target_update_times)
            now = self.clock()
        update_rate_hz = None
        if len(update_times) >= 2 and update_times[-1] > update_times[0]:
            update_rate_hz = (len(update_times) - 1) / (update_times[-1] - update_times[0])
        snapshot.update({
            "connected": self.connected,
            "transport": self.transport_name,
            "protocol_errors": self.protocol_errors,
            "target_update_rate_hz": update_rate_hz,
            "target_update_age_s": None if not update_times else max(0.0, now - update_times[-1]),
            "audio_download_expected": self.audio_download_expected,
            "audio_download_received": self.audio_download_received,
            "audio_download_progress": (
                0.0 if self.audio_download_expected == 0
                else self.audio_download_received / self.audio_download_expected
            ),
            "audio_download_ready": self.latest_audio is not None,
            "audio_download_error": self.audio_download_error,
        })
        return snapshot

    def get_audio_download(self) -> dict[str, Any] | None:
        with self.lock:
            if self.latest_audio is None:
                return None
            return {**self.latest_audio, "pcm": bytes(self.latest_audio["pcm"])}

    def send_command(self, command: str) -> None:
        raise NotImplementedError

    def request(self, command: str, response_types: set[str], timeout: float = 2.0) -> dict[str, Any]:
        with self.response_condition:
            baseline = self.response_sequence
        self.send_command(command)
        deadline = self.clock() + timeout
        with self.response_condition:
            while True:
                for sequence, record in self.response_records:
                    if sequence <= baseline:
                        continue
                    if record["type"] == "error":
                        detail = ": ".join([record["reason"], *record["details"]])
                        raise RuntimeError(detail)
                    if record["type"] in response_types:
                        return record
                remaining = deadline - self.clock()
                if remaining <= 0.0:
                    expected = ", ".join(sorted(response_types))
                    raise TimeoutError(f"timed out waiting for {expected}")
                self.response_condition.wait(remaining)

    def stop(self) -> None:
        self.running = False


class SerialHeimdallTransport(HeimdallTransport, threading.Thread):
    def __init__(self, port: str, baud: int = 921600) -> None:
        HeimdallTransport.__init__(self)
        threading.Thread.__init__(self, daemon=True)
        import serial

        self.serial = serial.Serial(port, baud, timeout=0.05, write_timeout=0.5)
        try:
            self.serial.set_buffer_size(rx_size=4 * 1024 * 1024)
        except (AttributeError, OSError):
            pass
        self.port = port
        self.baud = baud
        self.transport_name = f"serial:{port}"
        self.write_lock = threading.Lock()
        self.rx_buffer = bytearray()
        self.audio_header: dict[str, Any] | None = None
        self.audio_payload = bytearray()
        self.audio_acknowledged = 0

    def run(self) -> None:
        self.running = True
        self.connected = True
        self.send_command("I")
        try:
            while self.running:
                data = self.serial.read(max(1, self.serial.in_waiting))
                if not data:
                    continue
                self._process_received_data(data)
        except OSError:
            self.protocol_errors += 1
        finally:
            self.connected = False
            self.running = False
            with self.lock:
                self.state.sequence += 1

    def send_command(self, command: str) -> None:
        command = validate_command(command)
        with self.write_lock:
            self.serial.write((command + "\n").encode("ascii"))
            self.serial.flush()

    def _process_received_data(self, data: bytes) -> None:
        self.rx_buffer.extend(data)
        while True:
            if self.audio_header is not None and len(self.audio_payload) < self.audio_download_expected:
                remaining = self.audio_download_expected - len(self.audio_payload)
                take = min(remaining, len(self.rx_buffer))
                if take == 0:
                    return
                self.audio_payload.extend(self.rx_buffer[:take])
                del self.rx_buffer[:take]
                with self.lock:
                    self.audio_download_received = len(self.audio_payload)
                self._acknowledge_audio_blocks()
                if len(self.audio_payload) < self.audio_download_expected:
                    return
                continue

            newline = self.rx_buffer.find(b"\n")
            if newline < 0:
                if len(self.rx_buffer) > 65536:
                    self.rx_buffer.clear()
                    self.protocol_errors += 1
                return

            raw_line = bytes(self.rx_buffer[:newline])
            del self.rx_buffer[:newline + 1]
            try:
                line = raw_line.decode("ascii", errors="strict").rstrip("\r")
            except UnicodeError:
                self.protocol_errors += 1
                continue

            record = self.apply_line(line)
            if record is None:
                continue
            if record["type"] == "audio_begin":
                self.audio_header = record
                self.audio_payload = bytearray()
                self.audio_acknowledged = 0
                with self.lock:
                    self.latest_audio = None
                    self.audio_download_expected = record["bytes"]
                    self.audio_download_received = 0
                    self.audio_download_error = None
            elif record["type"] == "audio_end":
                self._finish_audio_download(record)

    def _acknowledge_audio_blocks(self) -> None:
        if self.audio_header is None:
            return
        interval = int(self.audio_header.get("ack_interval", 0))
        if interval <= 0:
            return
        received = len(self.audio_payload)
        target = min(
            self.audio_download_expected,
            ((received // interval) * interval),
        )
        if received == self.audio_download_expected:
            target = received
        while self.audio_acknowledged < target:
            with self.write_lock:
                self.serial.write(b"\x06")
                self.serial.flush()
            self.audio_acknowledged = min(
                self.audio_acknowledged + interval,
                self.audio_download_expected,
            )

    def _finish_audio_download(self, footer: dict[str, Any]) -> None:
        header = self.audio_header
        payload = bytes(self.audio_payload)
        error = None
        if header is None:
            error = "audio footer received without a header"
        elif len(payload) != header["bytes"]:
            error = "audio payload length does not match header"
        else:
            actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
            if footer["crc32"] != actual_crc:
                error = (
                    f"audio CRC32 mismatch: expected {footer['crc32']:08X}, "
                    f"received {actual_crc:08X}"
                )
            elif header["crc32"] not in {0, actual_crc}:
                error = "audio header CRC32 does not match payload"

        with self.lock:
            if error is None:
                self.latest_audio = {**header, "pcm": payload}
                self.audio_download_received = len(payload)
            else:
                self.audio_download_error = error
                self.state.recording_state = "error"
                self.protocol_errors += 1
            self.state.sequence += 1
        self.audio_header = None
        self.audio_payload = bytearray()
        self.audio_acknowledged = 0

    def stop(self) -> None:
        self.running = False
        self.serial.close()


class EmulatorHeimdallTransport(HeimdallTransport, threading.Thread):
    """Protocol-faithful moving-target emulator using the real parsed state path."""

    def __init__(self, rows: int = 7, columns: int = 7,
                 azimuth_min_deg: float = -70.0, azimuth_max_deg: float = 70.0,
                 elevation_min_deg: float = -60.0, elevation_max_deg: float = 60.0,
                 sector_time_s: float = 0.0028, flight_profile: str = "crossing",
                 flight_speed: float = 1.0, clock=time.monotonic,
                 level_provider=None, program: DeployedProgram = DeployedProgram(),
                 parameter_service: DspParameterService | None = None) -> None:
        HeimdallTransport.__init__(self, clock=clock)
        threading.Thread.__init__(self, daemon=True)
        if not 1 <= rows <= 20 or not 1 <= columns <= 20:
            raise ValueError("emulator grid must be between 1x1 and 20x20")
        if not -90.0 <= azimuth_min_deg < azimuth_max_deg <= 90.0:
            raise ValueError("azimuth limits must satisfy -90 <= minimum < maximum <= 90")
        if not -90.0 <= elevation_min_deg < elevation_max_deg <= 90.0:
            raise ValueError("elevation limits must satisfy -90 <= minimum < maximum <= 90")
        if flight_profile not in FLIGHT_PROFILES:
            raise ValueError(f"unknown emulator flight profile: {flight_profile}")
        if not 0.25 <= flight_speed <= 3.0:
            raise ValueError("emulator flight speed must be between 0.25 and 3.0")
        self.rows = rows
        self.columns = columns
        self.azimuth_deg = _cell_centers(azimuth_min_deg, azimuth_max_deg, columns)
        self.elevation_deg = _cell_centers(elevation_min_deg, elevation_max_deg, rows)
        self.default_grid = (rows, columns, list(self.azimuth_deg), list(self.elevation_deg))
        self.sector_time_s = sector_time_s
        self.flight_profile = flight_profile
        self.flight_speed = flight_speed
        self.level_provider = level_provider
        self.program = program
        self.parameter_service = parameter_service or DspParameterService()
        self.commands: queue.Queue[str] = queue.Queue()
        self.mode = "IDLE"
        self.transport_name = "acoustic-emulator" if level_provider is not None else "emulator"
        self.random = random.Random(78002)
        self.started_at = self.clock()
        self.current_sector: int | None = None
        self.adaptive_state = "IDLE"
        self.background_level = 0
        self.candidate_sector: int | None = None
        self.target_sector: int | None = None
        self.pending_move_sector: int | None = None
        self.move_confirmations = 0
        self.failed_track_passes = 0
        self.tracking_passes = 0
        self.confirm_passes = 0
        self.bank_tracker: BankTracker | None = None
        self.bank_has_target = False
        self.bank_lost_maps = 0
        self.bank_target_sector = 0
        self.ch1_bank_beam: int | None = None
        self.detector_level = 0.0

    def snapshot(self) -> dict[str, Any]:
        snapshot = super().snapshot()
        if self.level_provider is None:
            snapshot.update({
                "emulator_flight_profile": self.flight_profile,
                "emulator_flight_speed": self.flight_speed,
            })
        else:
            snapshot.update(self.level_provider.metadata())
        return snapshot

    def run(self) -> None:
        self.running = True
        self.connected = True
        self._emit_configuration()
        try:
            while self.running:
                try:
                    timeout = 0.01 if self.mode == "IDLE" else BANK_PERIOD_S if self.mode == "B" else 0.0
                    command = self.commands.get(timeout=timeout)
                    self._handle_command(command)
                except queue.Empty:
                    if self.mode == "C":
                        self._scan_pass("C")
                    elif self.mode == "G":
                        self._adaptive_cycle()
                    elif self.mode == "B":
                        self._stare_cycle()
        finally:
            self.connected = False
            self.running = False

    def send_command(self, command: str) -> None:
        self.commands.put(validate_command(command, self.rows * self.columns))

    def _handle_command(self, command: str) -> None:
        if command == "I":
            self.rows, self.columns, azimuth, elevation = self.default_grid
            self.azimuth_deg = list(azimuth)
            self.elevation_deg = list(elevation)
            self._emit_configuration()
        elif command == "F":
            self.mode = "F"
            self.apply_line("SCAN_STARTED,F")
            self._scan_pass("F")
            self.mode = "IDLE"
        elif command == "C":
            self.mode = "C"
            self.apply_line("SCAN_STARTED,C")
        elif command == "G":
            self.mode = "G"
            self._reset_adaptive_search()
            self.apply_line("SCAN_STARTED,G")
        elif command == "B":
            if not self.program.bank_beams:
                self.apply_line("ERR,MALFORMED_COMMAND")
                return
            self.mode = "B"
            self.bank_tracker = BankTracker(
                self.program.bank_rows, self.program.bank_columns,
                list(self.program.bank_azimuth_deg), list(self.program.bank_elevation_deg),
                self.azimuth_deg, self.elevation_deg,
            )
            self.bank_has_target = False
            self.bank_lost_maps = 0
            self.apply_line("SCAN_STARTED,B")
        elif command.startswith("O,"):
            source, index = (int(value) for value in command[2:].split(","))
            limit = {0: 1, 1: self.program.microphones, 2: self.program.bank_beams}[source]
            valid = self.program.selectors and index < limit
            if valid and source == 2:
                self.ch1_bank_beam = index
            self.apply_line(f"CH1,{source},{index},{0 if valid else -1}")
        elif command == "X":
            self.mode = "IDLE"
            self.adaptive_state = "IDLE"
            self.apply_line("SCAN_STOPPED")
        elif command == "M":
            if self.current_sector is None:
                self.apply_line("ERR,NO_ACTIVE_BEAM")
            else:
                self._emit_measurement(self.current_sector, delay=False)
        elif command.startswith("S,"):
            self.mode = "IDLE"
            self.adaptive_state = "IDLE"
            sector = int(command[2:])
            self.current_sector = sector
            self._emit_steer_ok(sector)
            self._emit_measurement(sector, delay=False)
        elif command.startswith("SET,"):
            _, key, value = command.split(",")
            try:
                settings = self.parameter_service.set(self, key, int(value))
                self.apply_line(f"SET_OK,{key},{settings[key]}")
            except RuntimeError:
                self.apply_line("ERR,SET,BUSY")
            except (PermissionError, ValueError):
                self.apply_line("ERR,SET,RANGE")
        elif command.startswith("PW,"):
            body = command.split("*", 1)[0]
            fields = body.split(",")
            address = int(fields[1])
            words = [int(value, 16) for value in fields[2:]]
            writes = [{"address": address + offset, "word": word}
                      for offset, word in enumerate(words)]
            try:
                self.parameter_service.write(self, writes)
                self.apply_line(f"PW_OK,{address},{len(words)}")
            except RuntimeError:
                self.apply_line("ERR,PW,BUSY")
            except (PermissionError, ValueError):
                self.apply_line("ERR,PW,ADDR")
        elif command.startswith("PR,"):
            _, address_text, count_text = command.split(",")
            address = int(address_text)
            count = int(count_text)
            try:
                words = self.parameter_service.read(self, address, count)
                values = ",".join(f"{word & 0xFFFFFFFF:08X}" for word in words)
                self.apply_line(f"PV,{address},{values}")
            except (PermissionError, ValueError):
                self.apply_line("ERR,PR,ADDR")
        elif command.startswith("DG,"):
            azimuth_min, azimuth_max, columns, elevation_min, elevation_max, rows = (
                int(value) for value in command[3:].split(",")
            )
            self.columns = columns
            self.rows = rows
            self.azimuth_deg = _grid_axis(azimuth_min / 100.0, azimuth_max / 100.0, columns)
            self.elevation_deg = _grid_axis(elevation_min / 100.0, elevation_max / 100.0, rows)
            words = rows * columns * self.program.steer_words
            self.apply_line(f"DIRSET,{rows},{columns},{words},{words},0,0")
            self._emit_configuration(include_boot=False)

    def _emit_configuration(self, include_boot: bool = True) -> None:
        sectors = self.rows * self.columns
        self.apply_line(f"READY,2D,{self.rows},{self.columns},{sectors},44")
        self.apply_line("AZIMUTH," + str(self.columns) + "," + ",".join(_format_angle(v) for v in self.azimuth_deg))
        self.apply_line("ELEVATION," + str(self.rows) + "," + ",".join(_format_angle(v) for v in self.elevation_deg))
        for line in self.program.configuration_lines():
            self.apply_line(line)
        if include_boot:
            for line in self.program.boot_lines():
                self.apply_line(line)
            if self.program.map_hash:
                self.apply_line(f"MAP,{self.program.map_hash}")
            settings = self.parameter_service.status(self)["settings"]
            self.apply_line("SETTINGS," + ",".join(f"{key}={value}"
                                                    for key, value in settings.items()))

    def _stare_cycle(self) -> None:
        """One bank map, as the firmware's step_stare does it."""
        program = self.program
        levels = [self._level_for_direction(azimuth, elevation)
                  for azimuth, elevation in zip(program.bank_azimuth_deg, program.bank_elevation_deg)]
        self.apply_line(f"LEVELS,B,{program.bank_rows},{program.bank_columns}," + ",".join(map(str, levels)))
        result = self.bank_tracker.update(levels)
        if not result.beams:
            if self.bank_has_target:
                self.bank_lost_maps += 1
                if self.bank_lost_maps >= BANK_LOST_MAPS:
                    self._emit_target("TARGET_LOST", self.bank_target_sector, 0)
                    self.bank_has_target = False
            return
        self.bank_lost_maps = 0
        listen = result.beams[1] if len(result.beams) > 1 else result.beams[0]
        self.apply_line(
            f"BANK_TARGET,{result.beams[0]},{listen},{_format_angle(result.azimuth_deg)},"
            f"{_format_angle(result.elevation_deg)},{result.sector},{result.levels[0]}"
        )
        if program.selectors and self.ch1_bank_beam != listen:
            self.ch1_bank_beam = listen
            self.apply_line(f"CH1,2,{listen},0")
        self.current_sector = result.sector
        self.bank_target_sector = result.sector
        level = self._level_for_sector(result.sector)
        self._emit_target("TARGET_UPDATED" if self.bank_has_target else "TARGET_ACQUIRED", result.sector, level)
        self.bank_has_target = True

    def _scan_pass(self, mode: str) -> None:
        started = time.monotonic()
        measured = 0
        for sector in range(self.rows * self.columns):
            if self._consume_interrupt():
                return
            self._emit_measurement(sector)
            measured += 1
        elapsed_us = max(1, int((time.monotonic() - started) * 1_000_000))
        sector_us = elapsed_us // max(1, measured)
        self.apply_line("SCAN_DONE")
        self.apply_line(f"TIMING,{mode},{measured},{elapsed_us},{elapsed_us},{sector_us},{sector_us}")

    def _adaptive_cycle(self) -> None:
        if self.adaptive_state == "SEARCH":
            self._adaptive_search()
        elif self.adaptive_state == "CONFIRM":
            self._adaptive_confirm()
        elif self.adaptive_state == "TRACK":
            self._adaptive_track()
        elif self.adaptive_state == "REACQUIRE":
            self._adaptive_reacquire()

    def _reset_adaptive_search(self) -> None:
        self.adaptive_state = "SEARCH"
        self.candidate_sector = None
        self.target_sector = None
        self.pending_move_sector = None
        self.move_confirmations = 0
        self.failed_track_passes = 0
        self.tracking_passes = 0
        self.confirm_passes = 0

    def _adaptive_search(self) -> None:
        result = self._measure_sector_set(range(self.rows * self.columns))
        if result is None:
            return
        measured, elapsed_us = result
        self._emit_pass_timing("G", measured, elapsed_us)
        strongest_sector, strongest_level = self._strongest_measurement(measured)
        sorted_levels = sorted(measured.values())
        self.background_level = sorted_levels[len(sorted_levels) // 2]
        if not self._level_above(strongest_level, self.background_level, ACQUIRE_RATIO):
            return
        self.candidate_sector = strongest_sector
        self.confirm_passes = 0
        self.adaptive_state = "CONFIRM"

    def _adaptive_confirm(self) -> None:
        assert self.candidate_sector is not None
        result = self._measure_sector_set(self._cross(self.candidate_sector))
        if result is None:
            return
        measured, _ = result
        strongest_sector, strongest_level = self._strongest_measurement(measured)
        if not self._level_above(strongest_level, self.background_level, ACQUIRE_RATIO):
            self._reset_adaptive_search()
            return
        self.candidate_sector = strongest_sector
        self.confirm_passes += 1
        if self.confirm_passes < CONFIRM_PASSES:
            return
        self.target_sector = self.candidate_sector
        self.pending_move_sector = self.target_sector
        self.failed_track_passes = 0
        self.tracking_passes = 0
        self.move_confirmations = 0
        self._emit_target("TARGET_ACQUIRED", self.target_sector, strongest_level)
        self.adaptive_state = "TRACK"

    def _adaptive_track(self) -> None:
        assert self.target_sector is not None
        result = self._measure_sector_set(self._cross(self.target_sector))
        if result is None:
            return
        measured, _ = result
        strongest_sector, strongest_level = self._strongest_measurement(measured)
        if not self._level_above(strongest_level, self.background_level, RETAIN_RATIO):
            self.failed_track_passes += 1
            if self.failed_track_passes >= TRACK_FAILURE_LIMIT:
                self.adaptive_state = "REACQUIRE"
                return
        else:
            self.failed_track_passes = 0
            if strongest_sector == self.target_sector:
                self.move_confirmations = 0
            elif strongest_sector == self.pending_move_sector:
                self.move_confirmations += 1
            else:
                self.pending_move_sector = strongest_sector
                self.move_confirmations = 1
            if self.move_confirmations >= TRACK_MOVE_CONFIRMATIONS:
                self.target_sector = strongest_sector
                self.move_confirmations = 0
            self._emit_target("TARGET_UPDATED", self.target_sector, strongest_level)

        self.tracking_passes += 1
        if self.tracking_passes >= GLOBAL_RESCAN_INTERVAL:
            self._reset_adaptive_search()

    def _adaptive_reacquire(self) -> None:
        assert self.target_sector is not None
        result = self._measure_sector_set(self._neighborhood(self.target_sector))
        if result is None:
            return
        measured, _ = result
        strongest_sector, strongest_level = self._strongest_measurement(measured)
        if self._level_above(strongest_level, self.background_level, RETAIN_RATIO):
            self.target_sector = strongest_sector
            self.pending_move_sector = strongest_sector
            self.failed_track_passes = 0
            self.move_confirmations = 0
            self._emit_target("TARGET_UPDATED", strongest_sector, strongest_level)
            self.adaptive_state = "TRACK"
            return
        lost_sector = self.target_sector
        self._emit_target("TARGET_LOST", lost_sector, strongest_level)
        self._reset_adaptive_search()

    def _measure_sector_set(self, sectors) -> tuple[dict[int, int], int] | None:
        started = time.monotonic()
        measured: dict[int, int] = {}
        for sector in sectors:
            if self._consume_interrupt():
                return None
            measured[sector] = self._emit_measurement(sector)
        elapsed_us = max(1, int((time.monotonic() - started) * 1_000_000))
        return measured, elapsed_us

    def _emit_pass_timing(self, mode: str, measured: dict[int, int], elapsed_us: int) -> None:
        count = len(measured)
        sector_us = elapsed_us // max(1, count)
        self.apply_line("SCAN_DONE")
        self.apply_line(f"TIMING,{mode},{count},{elapsed_us},{elapsed_us},{sector_us},{sector_us}")

    @staticmethod
    def _strongest_measurement(measured: dict[int, int]) -> tuple[int, int]:
        return max(measured.items(), key=lambda item: item[1])

    @staticmethod
    def _level_above(level: int, background: int, ratio: float) -> bool:
        if level <= LEVEL_ABSOLUTE_FLOOR:
            return False
        return background == 0 or level >= background * ratio

    def _emit_target(self, record_type: str, sector: int, level: int) -> None:
        row, column = divmod(sector, self.columns)
        self.apply_line(
            f"{record_type},{sector},{row},{column},"
            f"{_format_angle(self.azimuth_deg[column])},{_format_angle(self.elevation_deg[row])},{level}"
        )

    def _consume_interrupt(self) -> bool:
        if not self.running:
            return True
        try:
            command = self.commands.get_nowait()
        except queue.Empty:
            return False
        self._handle_command(command)
        return command != "I"

    def _emit_measurement(self, sector: int, delay: bool = True) -> int:
        self.current_sector = sector
        row, column = divmod(sector, self.columns)
        level = self._measurement_level(sector)
        self.apply_line(
            f"P,{row},{column},{_format_angle(self.azimuth_deg[column])},"
            f"{_format_angle(self.elevation_deg[row])},{level}"
        )
        if delay:
            duration = self._measurement_duration()
            if duration > 0.0:
                time.sleep(duration)
        return level

    def _measurement_duration(self) -> float:
        settings = self.parameter_service.status(self)["settings"]
        if settings["measure"] == 1:
            return (settings["guard_us"] + settings["window_us"]) / 1_000_000.0
        return max(self.sector_time_s, settings["settle_us"] / 1_000_000.0)

    def _measurement_level(self, sector: int) -> int:
        settings = self.parameter_service.status(self)["settings"]
        instantaneous = self._level_for_sector(sector)
        duration = self._measurement_duration()
        tau = max(1e-6, self.program.level_time_constant_ms / 1000.0)
        alpha = -math.expm1(-duration / tau)
        self.detector_level += alpha * (instantaneous - self.detector_level)
        if settings["measure"] == 0:
            return max(0, min(0x7FFFFFFF, round(self.detector_level)))
        if self.level_provider is not None:
            return instantaneous
        window_s = max(0.001, settings["window_us"] / 1_000_000.0)
        scale = 1.0 + self.random.uniform(-0.002, 0.002) * math.sqrt(0.005 / window_s)
        return max(0, min(0x7FFFFFFF, round(instantaneous * scale)))

    def _emit_steer_ok(self, sector: int) -> None:
        row, column = divmod(sector, self.columns)
        self.apply_line(
            f"STEER_OK,{sector},{row},{column},{_format_angle(self.azimuth_deg[column])},"
            f"{_format_angle(self.elevation_deg[row])}"
        )
        self.apply_line(f"TIMING,S,{int(self.sector_time_s * 1_000_000)}")

    def _target_state(self, elapsed: float | None = None) -> tuple[float, float, float, float]:
        elapsed = (self.clock() - self.started_at) if elapsed is None else elapsed
        t = elapsed * self.flight_speed

        if self.flight_profile == "legacy":
            azimuth = 48.0 * math.sin(t * 0.22)
            elevation = 34.0 * math.sin(t * 0.15 + 0.8)
            target_range = 30.0
            attenuation = 1.0
            return azimuth, elevation, target_range, attenuation

        if self.flight_profile == "crossing":
            forward = 38.0 + 4.0 * math.sin(t * 0.13)
            lateral = 42.0 * math.sin(t * 0.34)
            vertical = 9.0 + 2.0 * math.sin(t * 0.21 + 0.7)
            attenuation = 1.0
        elif self.flight_profile == "approach":
            forward = 47.0 + 35.0 * math.cos(t * 0.38)
            lateral = 8.0 + 4.0 * math.sin(t * 0.19)
            vertical = 10.0 + 2.5 * math.sin(t * 0.24 + 0.4)
            attenuation = 1.0
        elif self.flight_profile == "orbit":
            forward = 38.0 + 13.0 * math.cos(t * 0.36)
            lateral = 24.0 * math.sin(t * 0.36)
            vertical = 11.0 + 2.0 * math.sin(t * 0.18)
            attenuation = 1.0
        elif self.flight_profile == "patrol":
            forward = 45.0 + 14.0 * math.sin(t * 0.16 + 0.5)
            lateral = 31.0 * math.sin(t * 0.24)
            vertical = 8.0 + 4.0 * math.sin(t * 0.11 + 1.2)
            attenuation = 1.0
        else:
            forward = 34.0 + 12.0 * math.sin(t * 0.43) + 5.0 * math.sin(t * 0.91)
            lateral = 29.0 * math.sin(t * 0.57) + 9.0 * math.sin(t * 1.17 + 0.8)
            vertical = 10.0 + 5.0 * math.sin(t * 0.49 + 0.3)
            dropout_phase = t % 17.0
            attenuation = 0.0 if 11.5 <= dropout_phase < 12.4 else 1.0

        horizontal_range = math.hypot(forward, lateral)
        target_range = math.hypot(horizontal_range, vertical)
        azimuth = math.degrees(math.atan2(lateral, forward))
        elevation = math.degrees(math.atan2(vertical, horizontal_range))
        return azimuth, elevation, target_range, attenuation

    def _level_for_sector(self, sector: int) -> int:
        if self.level_provider is not None:
            return self.level_provider.level_for_sector(sector)
        row, column = divmod(sector, self.columns)
        return self._level_for_direction(self.azimuth_deg[column], self.elevation_deg[row])

    def _level_for_direction(self, azimuth_deg: float, elevation_deg: float) -> int:
        if self.level_provider is not None:
            # The acoustic cache holds the steered sectors only; a bank beam reads its nearest one.
            return self.level_provider.level_for_sector(self.bank_tracker.nearest_sector(azimuth_deg, elevation_deg))
        target_azimuth, target_elevation, target_range, attenuation = self._target_state()
        azimuth_error = azimuth_deg - target_azimuth
        elevation_error = elevation_deg - target_elevation
        distance_squared = azimuth_error * azimuth_error + elevation_error * elevation_error
        # Far-field pressure amplitude is proportional to 1/r; 30 m is the reference.
        range_gain = min(1.4, 30.0 / max(8.0, target_range))
        linear = 0.015 + 0.85 * range_gain * attenuation * math.exp(
            -distance_squared / (2.0 * 18.0 * 18.0))
        linear += self.random.uniform(-0.002, 0.002)
        return max(0, min(0x7FFFFFFF, round(linear * 16777216.0)))

    def _cross(self, center: int) -> list[int]:
        row, column = divmod(center, self.columns)
        sectors = [center]
        if column > 0:
            sectors.append(center - 1)
        if column + 1 < self.columns:
            sectors.append(center + 1)
        if row > 0:
            sectors.append(center - self.columns)
        if row + 1 < self.rows:
            sectors.append(center + self.columns)
        return sectors

    def _neighborhood(self, center: int) -> list[int]:
        center_row, center_column = divmod(center, self.columns)
        sectors = []
        for row_offset in (-1, 0, 1):
            row = center_row + row_offset
            if not 0 <= row < self.rows:
                continue
            for column_offset in (-1, 0, 1):
                column = center_column + column_offset
                if 0 <= column < self.columns:
                    sectors.append(row * self.columns + column)
        return sectors


def validate_command(command: str, sectors: int = 65535) -> str:
    command = command.strip()
    if command in {"F", "C", "G", "B", "X", "I", "M", "R,1", "R,0", "D"}:
        return command
    match = re.fullmatch(r"S,(\d+)", command)
    if match and int(match.group(1)) < sectors:
        return command
    if re.fullmatch(r"O,[0-2],\d{1,3}", command):
        return command
    if re.fullmatch(
        r"SET,(?:settle_us|guard_us|window_us|measure|band|channel|map_record|tone),-?\d+",
        command,
    ):
        return command
    match = re.fullmatch(r"(PW,(\d+)(?:,[0-9A-Fa-f]{8}){1,5})(?:\*([0-9A-Fa-f]{2}))?", command)
    if match and int(match.group(2)) <= 0xFFFF:
        body = match.group(1)
        expected = checksum(body)
        if match.group(3) is not None and int(match.group(3), 16) != expected:
            raise ValueError("invalid PW checksum")
        return f"{body}*{expected:02X}"
    match = re.fullmatch(r"PR,(\d+),(\d+)", command)
    if match and int(match.group(1)) <= 0xFFFF and 1 <= int(match.group(2)) <= 16:
        return command
    match = re.fullmatch(r"DG,(-?\d+),(-?\d+),(\d+),(-?\d+),(-?\d+),(\d+)", command)
    if match:
        azimuth_min, azimuth_max, columns, elevation_min, elevation_max, rows = map(
            int, match.groups()
        )
        if (
            all(-9000 <= angle <= 9000 for angle in
                (azimuth_min, azimuth_max, elevation_min, elevation_max))
            and columns >= 1
            and rows >= 1
            and columns * rows <= sectors
        ):
            return command
    raise ValueError("invalid Heimdall command")


def _cell_centers(minimum: float, maximum: float, count: int) -> list[float]:
    step = (maximum - minimum) / count
    return [minimum + (index + 0.5) * step for index in range(count)]


def _grid_axis(minimum: float, maximum: float, count: int) -> list[float]:
    if count == 1:
        return [(minimum + maximum) / 2.0]
    return [minimum + (maximum - minimum) * index / (count - 1) for index in range(count)]


def _format_angle(value: float) -> str:
    return f"{value:.4f}"