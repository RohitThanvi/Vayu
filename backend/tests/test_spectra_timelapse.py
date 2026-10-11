"""Pure-Python tests for the Spectra timelapse engine and a regression guard for the MODIS date-filter bug.
Earth Engine itself is not reachable here, so these cover the logic that does not need it."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import datetime as dt
import inspect

import pytest

from app.services import gee_remote_sensing as rs

d = dt.date.fromisoformat


def test_period_labels_and_alignment():
    assert [p[0] for p in rs._timelapse_periods(d("2010-03-01"), d("2012-02-01"), 12)] == ["2010", "2011", "2012"]
    assert [p[0] for p in rs._timelapse_periods(d("2024-02-10"), d("2025-01-20"), 3)] == ["2024 Q1", "2024 Q2", "2024 Q3", "2024 Q4", "2025 Q1"]
    assert [p[0] for p in rs._timelapse_periods(d("2024-11-05"), d("2025-02-02"), 1)] == ["2024-11", "2024-12", "2025-01", "2025-02"]


def test_quarters_start_on_calendar_quarters():
    assert [(p[1], p[2]) for p in rs._timelapse_periods(d("2024-05-15"), d("2024-12-31"), 3)] == [(2024, 4), (2024, 7), (2024, 10)]


def test_sig_and_find_key():
    assert rs._sig(1.23456789e-5) == 1.2346e-05 and rs._sig(None) is None
    assert rs._find_key({"p2": 1, "p98": 9}, "p98") == 9
    assert rs._find_key({"v_p2": 1, "v_p98": 9}, "p2") == 1
    assert rs._find_key({}, "p2") is None


@pytest.mark.parametrize("kwargs,msg", [
    ({"frequency": "weekly"}, "Unknown frequency"),
    ({"layer": "nope"}, "Unknown timelapse layer"),
    ({"frequency": "monthly"}, "At most"),       # 1990-2020 monthly = 372 frames
])
def test_validation_errors_before_any_earth_engine_call(kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        rs.compute_timelapse({"type": "Polygon"}, "1990-01-01", "2020-12-31", **kwargs)


def test_every_layer_is_registered():
    assert set(rs.TIMELAPSE_LAYERS) == {"natural_color", "ndvi", "night_lights", "no2", "sar_vv", "soil_moisture"}


def test_modis_maps_keep_the_time_property():
    """Regression: arithmetic on an ee.Image drops system:time_start, so a collection .map()-ed with it matches NOTHING in a
    later filterDate (the drought index failed with 'no data in the time range' for every year). Any function that maps MODIS
    images and is date-filtered afterwards must copy the property back."""
    src = inspect.getsource(rs._build_vhi_stack)
    assert src.count('copyProperties(img, ["system:time_start"])') >= 2, "both the NDVI and LST map functions must preserve system:time_start"


def test_landsat_prep_keeps_the_time_property():
    assert 'copyProperties(img, ["system:time_start"])' in inspect.getsource(rs._landsat_merged)
