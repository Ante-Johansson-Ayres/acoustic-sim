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


def white_noise_gain_db(weights) -> float:
    """Array gain of a steered beam against noise uncorrelated between microphones."""
    weights = np.abs(np.asarray(weights, dtype=np.float64))
    return float(10.0 * math.log10(np.sum(weights) ** 2 / np.sum(weights ** 2)))


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
        "white_noise_gain_db": white_noise_gain_db(contract.gains),
        "aperture_m": float(np.max(distances)),
        "nearest_spacing_m": float(np.min(nonzero)),
        "speed_of_sound_m_s": contract.speed_of_sound_m_s,
    }


def band_frequencies(centre_hz: float, fraction: int = 3, count: int = 21) -> np.ndarray:
    """Frequencies spanning the 1/fraction-octave band centred on centre_hz."""
    half = 2.0 ** (1.0 / (2 * fraction))
    return np.linspace(centre_hz / half, centre_hz * half, count)


def steered_response(contract: BeamContract, source_azimuth_deg: float, source_elevation_deg: float,
                     steer_azimuth_deg, steer_elevation_deg, frequencies_hz,
                     distance_m: float | None = None) -> np.ndarray:
    """Band-averaged power of the plane-wave beam steered to each direction, for one source (1 = no loss).

    The source is a plane wave, or a point at distance_m from the array centre. This is what a steering
    sweep across a fixed speaker measures."""
    positions = contract.microphone_positions_mm / 1000.0
    positions = positions - positions.mean(axis=0, keepdims=True)
    weights = np.asarray(contract.gains, dtype=np.float64)
    a, e = math.radians(source_azimuth_deg), math.radians(source_elevation_deg)
    source = np.array([math.cos(e) * math.sin(a), math.sin(e), math.cos(e) * math.cos(a)])
    if distance_m:
        rel = np.column_stack([positions, np.zeros(len(positions))]) - distance_m * source
        path = np.linalg.norm(rel, axis=1) - distance_m
    else:
        path = -(positions @ source[:2])
    az = np.radians(np.atleast_1d(np.asarray(steer_azimuth_deg, dtype=np.float64)))
    el = np.radians(np.atleast_1d(np.asarray(steer_elevation_deg, dtype=np.float64)))
    az, el = np.broadcast_arrays(az, el)
    steer = np.column_stack([np.cos(el) * np.sin(az), np.sin(el)])
    total = np.zeros(len(steer))
    for frequency in np.atleast_1d(frequencies_hz):
        k = 2.0 * math.pi * float(frequency) / contract.speed_of_sound_m_s
        phase = np.exp(-1j * k * (path[None, :] + steer @ positions.T))
        total += np.abs(phase @ weights) ** 2
    return total / len(np.atleast_1d(frequencies_hz)) / np.sum(np.abs(weights)) ** 2


def steering_cut_db(contract: BeamContract, centre, axis: str, frequencies_hz, distance_m: float | None = None,
                    rel_deg=None) -> tuple[np.ndarray, np.ndarray]:
    """Steering sweep along one axis across a source at centre (az, el): relative angles and dB re the peak."""
    rel = np.arange(-60.0, 60.001, 0.05) if rel_deg is None else np.asarray(rel_deg, dtype=np.float64)
    az = centre[0] + rel if axis == "azimuth" else np.full(rel.shape, float(centre[0]))
    el = centre[1] + rel if axis == "elevation" else np.full(rel.shape, float(centre[1]))
    p = steered_response(contract, centre[0], centre[1], az, el, frequencies_hz, distance_m)
    return rel, 10 * np.log10(np.maximum(p / p.max(), 1e-12))


def first_null_half_width(rel_deg, db) -> float | None:
    """Smallest |angle| of a minimum below -6 dB in a cut peaking at angle 0: half the first-null width."""
    minima = [abs(rel_deg[i]) for i in range(1, len(db) - 1)
              if db[i] < db[i - 1] and db[i] <= db[i + 1] and db[i] < -6]
    return float(min(minima)) if minima else None


def broadside_widths_deg(contract: BeamContract, axis: str, frequencies_hz) -> tuple[float | None, float | None]:
    """(-3 dB width, first-null half width) of the beam straight ahead; None where the visible region ends first."""
    rel, db = steering_cut_db(contract, (0.0, 0.0), axis, frequencies_hz, rel_deg=np.arange(-89.0, 89.0001, 0.02))
    width = beamwidth([{"angle_deg": float(a), "gain_db": float(g)} for a, g in zip(rel, db)])
    return (width["width_deg"] if width else None), first_null_half_width(rel, db)


def diffuse_power(contract: BeamContract, frequency_hz: float, azimuth_deg: float = 0.0, elevation_deg: float = 0.0,
                  members=None) -> float:
    """Beam power in a 3D diffuse field, a^H Gamma a with Gamma = sinc(k d), for all or some microphones (one = 1)."""
    positions = contract.microphone_positions_mm / 1000.0
    weights = np.asarray(contract.gains, dtype=np.float64)
    if members is not None:
        positions, weights = positions[list(members)], weights[list(members)]
    k = 2.0 * math.pi * float(frequency_hz) / contract.speed_of_sound_m_s
    d = np.linalg.norm(positions[:, None, :] - positions[None, :, :], axis=2)
    a = weights * np.exp(1j * k * (positions @ direction_components(azimuth_deg, elevation_deg)))
    return float(np.real(np.conj(a) @ np.sinc(k * d / math.pi) @ a))


def directivity_index_db(contract: BeamContract, frequency_hz: float, azimuth_deg: float = 0.0,
                         elevation_deg: float = 0.0) -> float:
    """10 log10 of the directivity factor |sum w|^2 / (a^H Gamma a), Gamma = sinc(k d) (diffuse field)."""
    weights = np.asarray(contract.gains, dtype=np.float64)
    return float(10.0 * math.log10(np.sum(weights) ** 2
                                   / diffuse_power(contract, frequency_hz, azimuth_deg, elevation_deg)))


def band_directivity_index_db(contract: BeamContract, frequencies_hz) -> float:
    """Directivity index straight ahead over a band: the power average of 1/DF, as band-limited noise sees it."""
    inverse = np.mean([10 ** (-directivity_index_db(contract, f) / 10) for f in np.atleast_1d(frequencies_hz)])
    return float(-10 * np.log10(inverse))


def pattern_power(contract: BeamContract, frequency_hz: float, steer_u: float, steer_v: float, u, v) -> np.ndarray:
    """|B|^2 of the plane-wave beam steered to direction cosines (steer_u, steer_v), at (u, v); 1 on the beam."""
    positions = contract.microphone_positions_mm / 1000.0
    weights = np.asarray(contract.gains, dtype=np.float64)
    k = 2.0 * math.pi * float(frequency_hz) / contract.speed_of_sound_m_s
    phase = k * (np.multiply.outer(np.asarray(u, dtype=np.float64) - steer_u, positions[:, 0])
                 + np.multiply.outer(np.asarray(v, dtype=np.float64) - steer_v, positions[:, 1]))
    return np.abs((weights * np.exp(1j * phase)).sum(-1)) ** 2 / np.sum(weights) ** 2


def peak_sidelobe_db(contract: BeamContract, frequency_hz: float, azimuth_deg: float = 0.0,
                     elevation_deg: float = 0.0, grid: int = 361) -> float | None:
    """Highest level (dB re the beam) in the visible region outside the main lobe; None if the main lobe fills it.

    The main lobe is the ellipse reaching, along each axis, the first rise after the pattern drops below -6 dB."""
    u0, v0 = direction_components(azimuth_deg, elevation_deg)
    radii = []
    for du, dv in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        r = np.arange(0.002, 2.0, 0.002)
        p = pattern_power(contract, frequency_hz, u0, v0, u0 + du * r, v0 + dv * r)
        rising = np.nonzero((p[1:] > p[:-1]) & (p[:-1] < 0.25))[0]
        radii.append(r[rising[0]] if rising.size else 2.0)
    ru, rv = max(radii[:2]), max(radii[2:])
    g = np.linspace(-1.0, 1.0, grid)
    U, V = np.meshgrid(g, g)
    outside = (U ** 2 + V ** 2 <= 1) & (((U - u0) / ru) ** 2 + ((V - v0) / rv) ** 2 > 1)
    if not outside.any():
        return None
    return float(10 * np.log10(pattern_power(contract, frequency_hz, u0, v0, U[outside], V[outside]).max()))


def fit_cut(angles_deg, levels_db, errors_db, model_angles_deg, model_db, drop_db: float = 6.0,
            shifts=None, scales=None) -> dict[str, Any] | None:
    """Weighted least-squares fit of a theory cut to a measured one: level + model((angle - shift) * scale).

    Uses the points within drop_db of the strongest. The model is in dB with its peak at angle 0. Returns
    shift and scale with 1-sigma errors (profile chi-square, errors inflated by sqrt(reduced chi-square)
    when it exceeds 1), the fitted -3 dB width (theory width / scale), level and reduced chi-square."""
    angles = np.asarray(angles_deg, dtype=np.float64)
    levels = np.asarray(levels_db, dtype=np.float64)
    errors = np.maximum(np.asarray(errors_db, dtype=np.float64), 0.05)
    use = levels >= np.nanmax(levels) - drop_db
    if use.sum() < 4:
        return None
    x, y, w = angles[use], levels[use], 1.0 / errors[use] ** 2
    shifts = np.arange(-6.0, 6.0001, 0.02) if shifts is None else np.asarray(shifts)
    scales = np.arange(0.6, 1.6001, 0.005) if scales is None else np.asarray(scales)
    grid = (x[None, None, :] - shifts[:, None, None]) * scales[None, :, None]
    predicted = np.interp(grid, model_angles_deg, model_db, left=np.nan, right=np.nan)
    residual = y[None, None, :] - predicted
    level = np.nansum(w * residual, axis=2) / np.sum(w)
    chi2 = np.nansum(w * (residual - level[..., None]) ** 2, axis=2)
    chi2[np.isnan(predicted).any(axis=2)] = np.inf
    i, j = np.unravel_index(int(np.argmin(chi2)), chi2.shape)
    dof = max(1, int(use.sum()) - 3)
    reduced = float(chi2[i, j] / dof)
    limit = chi2[i, j] + max(1.0, reduced)

    def sigma(profile, values):
        inside = values[profile <= limit]
        return float((inside.max() - inside.min()) / 2.0) if inside.size else None

    width = beamwidth([{"angle_deg": float(a), "gain_db": float(g)} for a, g in zip(model_angles_deg, model_db)])
    theory_width = width["width_deg"] if width else None
    scale_sigma = sigma(chi2.min(axis=0), scales)
    return {"shift_deg": float(shifts[i]), "shift_sigma_deg": sigma(chi2.min(axis=1), shifts),
            "scale": float(scales[j]), "scale_sigma": scale_sigma, "level_db": float(level[i, j]),
            "width_3db_deg": float(theory_width / scales[j]) if theory_width else None,
            "width_sigma_deg": float(theory_width * scale_sigma / scales[j] ** 2) if theory_width and scale_sigma else None,
            "theory_width_3db_deg": theory_width, "reduced_chi2": reduced, "points": int(use.sum()),
            "edge": bool(i in (0, len(shifts) - 1) or j in (0, len(scales) - 1))}