"""Far-field theory for the deployed Heimdall microphone array."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from heimdall_acoustic import BeamContract, load_beam_contract


def direction_components(azimuth_deg: float, elevation_deg: float) -> np.ndarray:
    """Direction projected onto the array's right/up plane."""
    azimuth = math.radians(float(azimuth_deg))
    elevation = math.radians(float(elevation_deg))
    return np.asarray((math.cos(elevation) * math.sin(azimuth), math.sin(elevation)))


def array_factor(contract: BeamContract, azimuth_deg: float, elevation_deg: float,
                 steer_azimuth_deg: float, steer_elevation_deg: float,
                 frequency_hz: float) -> float:
    """Normalized pressure gain for an ideal far-field delay-and-sum beam."""
    positions = contract.microphone_positions_mm / 1000.0
    positions -= positions.mean(axis=0, keepdims=True)
    delta = (direction_components(azimuth_deg, elevation_deg)
             - direction_components(steer_azimuth_deg, steer_elevation_deg))
    phase = 2.0 * math.pi * float(frequency_hz) / contract.speed_of_sound_m_s * (positions @ delta)
    weights = np.asarray(contract.gains, dtype=np.float64)
    response = np.sum(weights * np.exp(1j * phase))
    return float(abs(response) / max(np.sum(abs(weights)), np.finfo(float).eps))


def beam_cut(contract: BeamContract | None = None, *, axis: str = "azimuth",
             steer_azimuth_deg: float = 0.0, steer_elevation_deg: float = 0.0,
             frequency_hz: float = 2000.0, minimum_deg: float = -60.0,
             maximum_deg: float = 60.0, step_deg: float = 1.0) -> list[dict[str, float]]:
    """Return a horizontal or vertical normalized array-factor cut in dB."""
    if axis not in {"azimuth", "elevation"}:
        raise ValueError("axis must be 'azimuth' or 'elevation'")
    if frequency_hz <= 0.0 or step_deg <= 0.0 or minimum_deg >= maximum_deg:
        raise ValueError("frequency, range and step must be positive and ordered")
    contract = contract or load_beam_contract()
    count = int(math.floor((maximum_deg - minimum_deg) / step_deg + 1e-9)) + 1
    angles = [minimum_deg + index * step_deg for index in range(count)]
    gains = []
    for angle in angles:
        azimuth = angle if axis == "azimuth" else steer_azimuth_deg
        elevation = angle if axis == "elevation" else steer_elevation_deg
        gains.append(array_factor(contract, azimuth, elevation, steer_azimuth_deg,
                                  steer_elevation_deg, frequency_hz))
    peak = max(gains)
    return [{"angle_deg": float(angle),
             "gain_db": 20.0 * math.log10(max(gain / peak, 1e-6))}
            for angle, gain in zip(angles, gains)]


def beamwidth(points: list[dict[str, float]], drop_db: float = 3.0) -> dict[str, float] | None:
    """Interpolated width around the strongest point at ``drop_db`` below peak."""
    if len(points) < 3 or drop_db <= 0.0:
        return None
    peak_index = max(range(len(points)), key=lambda index: points[index]["gain_db"])
    threshold = points[peak_index]["gain_db"] - drop_db

    def crossing(direction: int) -> float | None:
        index = peak_index
        while 0 <= index + direction < len(points):
            first = points[index]
            second = points[index + direction]
            if second["gain_db"] <= threshold:
                span = second["gain_db"] - first["gain_db"]
                fraction = 0.0 if span == 0.0 else (threshold - first["gain_db"]) / span
                return first["angle_deg"] + fraction * (second["angle_deg"] - first["angle_deg"])
            index += direction
        return None

    lower = crossing(-1)
    upper = crossing(1)
    if lower is None or upper is None:
        return None
    return {"width_deg": upper - lower, "lower_deg": lower, "upper_deg": upper,
            "peak_deg": points[peak_index]["angle_deg"]}


def theory_cut(**kwargs: Any) -> dict[str, Any]:
    """Serializable theory cut and geometry summary for the Study UI."""
    contract = load_beam_contract()
    points = beam_cut(contract, **kwargs)
    positions = contract.microphone_positions_mm / 1000.0
    distances = np.linalg.norm(positions[:, None, :] - positions[None, :, :], axis=2)
    nonzero = distances[distances > 1e-9]
    return {
        "points": points,
        "beamwidth_3db": beamwidth(points, 3.0),
        "microphones": contract.microphones,
        "aperture_m": float(np.max(distances)),
        "nearest_spacing_m": float(np.min(nonzero)),
        "speed_of_sound_m_s": contract.speed_of_sound_m_s,
    }