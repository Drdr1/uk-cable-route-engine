from dataclasses import replace

import pytest
from shapely.geometry import LineString

from routeengine.analysis import assess, crossing_angle, design_findings, find_crossings, worst
from routeengine.etl import LayerStatus
from routeengine.router import Route

STRAIGHT = LineString([(500300, 102600), (503700, 102600)])  # crosses road, rail, river; runs through FZ3


def test_crossings_have_chainage_angle_and_rag(project, layers):
    xs = find_crossings(project, STRAIGHT, layers)
    assert [c.type for c in xs] == ["Road", "Railway", "Watercourse"]
    assert [c.ref for c in xs] == ["X01", "X02", "X03"]
    assert xs[0].chainage == pytest.approx(500, abs=1)
    assert xs[1].chainage == pytest.approx(2700, abs=1)
    assert [c.rag for c in xs] == ["AMBER", "RED", "AMBER"]  # secondary road, railway, river
    assert xs[1].angle == pytest.approx(90, abs=0.5)
    assert xs[1].name == "Example Main Line"


def test_oblique_crossing_of_a_minor_feature_is_escalated(project, layers):
    import geopandas as gpd

    stream = gpd.GeoDataFrame({"cls": ["stream"], "label": [""]},
                              geometry=[LineString([(501000, 102300), (502000, 102900)])], crs="EPSG:27700")
    xs = find_crossings(project, STRAIGHT, dict(layers, waterways=stream, roads=layers["roads"].iloc[0:0],
                                                  railways=layers["railways"].iloc[0:0]))
    assert len(xs) == 1
    assert xs[0].angle == pytest.approx(31, abs=1)
    assert xs[0].rag == "AMBER"
    assert "oblique" in xs[0].reason


def test_crossing_angle_square_and_parallelish():
    route = LineString([(0, 0), (100, 0)])
    assert crossing_angle(route, 50, LineString([(50, -50), (50, 50)]), __import__("shapely").Point(50, 0)) == pytest.approx(90)
    assert crossing_angle(route, 50, LineString([(0, -10), (100, 10)]), __import__("shapely").Point(50, 0)) < 15


def test_assessment_reports_areas_proximity_and_data_gaps(project, layers):
    route = Route(1, STRAIGHT, 1000.0, [])
    manifest = [LayerStatus("power_lines", "unavailable", 0, "x", "", "now", "timeout")]
    [a] = assess(project, [route], layers, manifest)
    fz = [f for f in a.findings if f.item == "Flood Zone 3"]
    assert fz and fz[0].value == pytest.approx(800, abs=1)
    assert fz[0].from_ch == pytest.approx(2200, abs=1)
    assert any(f.category == "proximity" and "SSSI" in f.item for f in a.findings)
    assert any(f.category == "data" and "power_lines" in f.item for f in a.findings)
    assert a.rag == "RED"  # railway crossing
    assert a.crossings_by_type() == {"Road": 1, "Railway": 1, "Watercourse": 1}


def test_route_through_a_sssi_is_red(project, layers):
    through = LineString([(500300, 101500), (503700, 101500)])
    [a] = assess(project, [Route(1, through, 1.0, [])], layers, [])
    assert any(f.item == "SSSI" and f.rag == "RED" for f in a.findings)


def test_sharp_deflections_are_flagged(project):
    zigzag = LineString([(0, 0), (100, 0), (100, 100)])
    found = design_findings(project, zigzag)
    assert len(found) == 1 and found[0].value == pytest.approx(90)
    assert not design_findings(replace(project, max_deflection_deg=95), zigzag)


def test_worst():
    assert worst(["GREEN", "AMBER"]) == "AMBER"
    assert worst(["GREEN", "RED", "AMBER"]) == "RED"
    assert worst([]) == "GREEN"
