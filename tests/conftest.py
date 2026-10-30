"""Synthetic study area in real BNG coordinates, so the whole engine runs offline.

    y=103000 +-----------------------------------------------+
             |          gap (passable)        | FZ3 |        |
             |   road    +------+             |     |  rail  river
             |    |      | SSSI |             +-----+   |     |
       A ->  |    |      |      |                       |     |  <- B
             |    |      +------+                       |     |
             |    |      gap 100 m (costly)              |     |
             |    |      | AW   |                       |     |
    y=100000 +-----------------------------------------------+
           x=500000                                        x=504000
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
import yaml
from shapely.geometry import LineString, box

from routeengine.config import load_project

BBOX = [500000, 100000, 504000, 103000]
START = [500300, 101500]
END = [503700, 101500]


def synthetic_layers() -> dict[str, gpd.GeoDataFrame]:
    def gdf(rows):
        return gpd.GeoDataFrame([{"cls": c, "label": l} for c, l, _ in rows],
                                geometry=[g for _, _, g in rows], crs="EPSG:27700")
    return {
        "sssi": gdf([("sssi", "Example Meadows SSSI", box(501500, 100800, 502200, 102300))]),
        "ancient_woodland": gdf([("aw", "Example Wood", box(501600, 100000, 502100, 100700))]),
        "flood_zone_3": gdf([("fz3", "", box(502500, 101800, 503300, 102800))]),
        "buildings": gdf([("building", "", box(500250, 101450, 500350, 101550)),
                          ("building", "", box(500600, 102000, 500650, 102050))]),
        "roads": gdf([("secondary", "B1234", LineString([(500800, 100000), (500800, 103000)]))]),
        "railways": gdf([("rail", "Example Main Line", LineString([(503000, 100000), (503000, 103000)]))]),
        "waterways": gdf([("river", "River Example", LineString([(503400, 100000), (503450, 103000)]))]),
    }


def write_project(tmp: Path, layers: dict[str, gpd.GeoDataFrame], extra_sources=()) -> Path:
    src_gpkg = tmp / "source.gpkg"
    for name, g in layers.items():
        g.to_file(src_gpkg, layer=name, driver="GPKG")
    template = yaml.safe_load((Path(__file__).parent.parent / "projects" / "weald-demo.yaml").read_text())
    geometry = {"roads": "line", "railways": "line", "waterways": "line", "power_lines": "line"}
    sources = [{"name": n, "type": "file", "geometry": geometry.get(n, "polygon"), "path": str(src_gpkg), "layer": n,
                "class_field": "cls", "name_field": "label", "attribution": "synthetic"} for n in layers]
    # A configured layer with no data at all, to exercise the empty path.
    sources.append({"name": "power_lines", "type": "file", "geometry": "line", "path": str(tmp / "missing.gpkg"),
                    "attribution": "synthetic"})
    sources += list(extra_sources)
    template.update({
        "name": "Synthetic test corridor",
        "study_area": {"bbox": BBOX},
        "route": {"start": {"xy": START, "label": "A"}, "end": {"xy": END, "label": "B"}},
        "sources": sources,
    })
    path = tmp / "synthetic.yaml"
    path.write_text(yaml.safe_dump(template, sort_keys=False))
    return path


@pytest.fixture
def layers():
    return synthetic_layers()


@pytest.fixture
def project_path(tmp_path, layers):
    return write_project(tmp_path, layers)


@pytest.fixture
def project(project_path):
    return load_project(project_path)
