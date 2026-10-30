import pytest
from shapely.geometry import box

from routeengine.config import SourceConfig
from routeengine.etl import clean, run_etl
from routeengine.sources import SourceError, esri_to_shapely, fetch_arcgis, fetch_overpass, overpass_bbox


class FakeHttp:
    def __init__(self, get_pages=(), post_body=None, fail=False):
        self.get_pages = list(get_pages)
        self.post_body = post_body
        self.fail = fail
        self.calls = []

    def get(self, url, params):
        self.calls.append(params)
        if self.fail:
            raise SourceError("down")
        return self.get_pages.pop(0)

    def post(self, url, data):
        self.calls.append(data)
        if self.fail:
            raise SourceError("down")
        return self.post_body


def esri_square(x0, y0, size, hole=False):
    outer = [[x0, y0], [x0, y0 + size], [x0 + size, y0 + size], [x0 + size, y0], [x0, y0]]  # clockwise
    rings = [outer]
    if hole:
        h = size / 4
        rings.append([[x0 + h, y0 + h], [x0 + 3 * h, y0 + h], [x0 + 3 * h, y0 + 3 * h], [x0 + h, y0 + 3 * h], [x0 + h, y0 + h]])
    return {"rings": rings}


def test_esri_polygon_with_hole():
    poly = esri_to_shapely(esri_square(0, 0, 100, hole=True))
    assert poly.geom_type == "Polygon"
    assert len(poly.interiors) == 1
    assert poly.area == pytest.approx(100 * 100 - 50 * 50)


def test_esri_polyline():
    line = esri_to_shapely({"paths": [[[0, 0], [10, 0]], [[0, 5], [10, 5]]]})
    assert line.geom_type == "MultiLineString"


def test_arcgis_query_is_bng_bbox_and_pages_until_done():
    src = SourceConfig(name="sssi", type="arcgis", geometry="polygon", url="https://x/FeatureServer/0", name_field="NAME")
    http = FakeHttp(get_pages=[
        {"features": [{"attributes": {"NAME": "A"}, "geometry": esri_square(500100, 100100, 50)}], "exceededTransferLimit": True},
        {"features": [{"attributes": {"NAME": "B"}, "geometry": esri_square(500300, 100300, 50)}]},
    ])
    gdf = fetch_arcgis(src, (500000, 100000, 501000, 101000), http, page_size=1)
    assert list(gdf["label"]) == ["A", "B"]
    assert gdf.crs.to_epsg() == 27700
    assert http.calls[0]["inSR"] == 27700 and http.calls[0]["outSR"] == 27700
    assert http.calls[0]["geometry"] == "500000,100000,501000,101000"
    assert http.calls[1]["resultOffset"] == 1


def test_overpass_converts_to_bng_and_keeps_classes():
    src = SourceConfig(name="roads", type="overpass", geometry="line", query='way["highway"]({bbox});',
                       class_field="highway", name_field="ref")
    body = {"elements": [{"type": "way", "tags": {"highway": "primary", "ref": "A21"},
                          "geometry": [{"lat": 51.19, "lon": 0.27}, {"lat": 51.20, "lon": 0.28}]}]}
    http = FakeHttp(post_body=body)
    gdf = fetch_overpass(src, (552500, 141500, 565500, 151500), http)
    assert gdf.crs.to_epsg() == 27700
    assert gdf.iloc[0]["cls"] == "primary" and gdf.iloc[0]["label"] == "A21"
    x, y = gdf.geometry.iloc[0].coords[0]
    assert 550000 < x < 570000 and 140000 < y < 152000
    assert "51.1" in http.calls[0]["data"] and "out tags geom" in http.calls[0]["data"]


def test_overpass_bbox_order_is_south_west_north_east():
    s, w, n, e = map(float, overpass_bbox((552500, 141500, 565500, 151500)).split(","))
    assert s < n and w < e and 50 < s < 52 and -1 < w < 1


def test_clean_repairs_clips_and_drops_wrong_types():
    import geopandas as gpd
    from shapely.geometry import LineString, Polygon

    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
    gdf = gpd.GeoDataFrame({"cls": ["a", "b"], "label": ["", ""]},
                           geometry=[bowtie, LineString([(0, 0), (5, 5)])], crs="EPSG:27700")
    out = clean(gdf, "polygon", (0, 0, 5, 10))
    assert len(out) >= 1
    assert all(out.geometry.is_valid)
    assert set(out.geometry.geom_type) <= {"Polygon", "MultiPolygon"}
    assert out.total_bounds[2] <= 5


def test_optional_layer_failure_is_recorded_not_fatal(project, tmp_path, monkeypatch):
    from routeengine import etl

    real_fetch = etl.fetch

    def flaky(src, bbox, http=None):
        if src.name == "flood_zone_3":
            raise SourceError("503 from service")
        return real_fetch(src, bbox, http)

    monkeypatch.setattr(etl, "fetch", flaky)
    statuses = {s.name: s for s in run_etl(project, tmp_path / "d.gpkg")}
    assert statuses["flood_zone_3"].status == "unavailable"
    assert "503" in statuses["flood_zone_3"].error
    assert statuses["sssi"].status == "ok"
    assert statuses["power_lines"].status == "unavailable"  # configured file does not exist


def test_required_layer_failure_stops_the_run(project, tmp_path, monkeypatch):
    from dataclasses import replace

    from routeengine import etl

    project = replace(project, sources=tuple(replace(s, required=(s.name == "sssi")) for s in project.sources))
    monkeypatch.setattr(etl, "fetch", lambda src, bbox, http=None: (_ for _ in ()).throw(SourceError("down")))
    with pytest.raises(SourceError):
        run_etl(project, tmp_path / "d.gpkg")


def test_clip_box_helper():
    assert box(0, 0, 1, 1).area == 1
