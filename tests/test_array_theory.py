from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from array_theory import array_factor, beam_cut, beamwidth
from heimdall_acoustic import load_beam_contract
import sim_server


def test_array_factor_is_normalized_at_the_steering_direction():
    contract = load_beam_contract()
    assert array_factor(contract, 12.0, -8.0, 12.0, -8.0, 2000.0) == pytest.approx(1.0)


def test_broadside_cut_is_symmetric_and_narrows_with_frequency():
    low = beam_cut(frequency_hz=1000.0, minimum_deg=-60.0, maximum_deg=60.0)
    high = beam_cut(frequency_hz=4000.0, minimum_deg=-60.0, maximum_deg=60.0)
    for left, right in zip(low, reversed(low)):
        assert left["gain_db"] == pytest.approx(right["gain_db"], abs=1e-9)
    low_width = beamwidth(low)
    high_width = beamwidth(high)
    assert low_width is not None and high_width is not None
    assert low_width["width_deg"] == pytest.approx(40.0, abs=2.0)
    assert high_width["width_deg"] == pytest.approx(10.0, abs=1.0)


def test_beam_cut_rejects_invalid_axis():
    with pytest.raises(ValueError, match="axis"):
        beam_cut(axis="range")


def test_study_theory_cut_endpoint():
    with TestClient(sim_server.app) as client:
        response = client.get("/study/theory/cut", params={"frequency_hz": 2000, "step_deg": 2})
        assert response.status_code == 200
        body = response.json()
        assert len(body["points"]) == 61
        assert body["beamwidth_3db"]["width_deg"] == pytest.approx(20.0, abs=2.0)
        assert client.get("/study/theory/cut", params={"axis": "range"}).status_code == 400