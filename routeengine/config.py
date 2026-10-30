"""Project configuration: study area, end points, data sources, routing rules,
RAG rules and CAD standards, all in one YAML file per project so engineers can
tune a route without touching code."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BNG = "EPSG:27700"


@dataclass(frozen=True)
class SourceConfig:
    name: str  # layer name used everywhere else (e.g. "sssi", "roads")
    type: str  # arcgis | overpass | file
    geometry: str  # polygon | line
    url: str = ""
    query: str = ""  # Overpass QL body (bbox appended) or ArcGIS where clause
    path: str = ""  # for type=file
    layer: str = ""  # for type=file: layer inside a GeoPackage
    class_field: str = ""  # attribute holding the feature class (e.g. highway=primary)
    name_field: str = ""  # attribute holding a human-readable name
    attribution: str = ""
    required: bool = False


@dataclass(frozen=True)
class CostRule:
    layer: str
    mode: str  # exclude | multiply
    factor: float = 1.0
    buffer_m: float = 0.0
    classes: tuple[str, ...] = ()  # restrict to these feature classes (empty = all)


@dataclass(frozen=True)
class CrossingRule:
    layer: str
    penalties: dict[str, float]  # feature class -> extra cost per crossed cell; "*" default
    rag: dict[str, str] = field(default_factory=dict)  # feature class -> RAG; "*" default
    label: str = ""

    def penalty_for(self, cls: str) -> float:
        return float(self.penalties.get(cls, self.penalties.get("*", 0.0)))


@dataclass(frozen=True)
class Project:
    name: str
    description: str
    bbox: tuple[float, float, float, float]  # BNG metres: minx, miny, maxx, maxy
    start: tuple[float, float]
    end: tuple[float, float]
    start_label: str
    end_label: str
    resolution_m: float
    swathe_width_m: float
    alternatives: int
    alternative_penalty: float
    alternative_buffer_m: float
    max_deflection_deg: float
    oblique_crossing_deg: float
    crossing_band_m: float
    sources: tuple[SourceConfig, ...]
    cost_rules: tuple[CostRule, ...]
    crossing_rules: tuple[CrossingRule, ...]
    proximity_rules: tuple[dict[str, Any], ...]
    area_rules: tuple[dict[str, Any], ...]
    cad: dict[str, Any]
    path: Path

    def source(self, name: str) -> SourceConfig:
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(name)


def load_project(path: str | Path) -> Project:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    r = raw["routing"]
    return Project(
        name=raw["name"],
        description=raw.get("description", ""),
        bbox=tuple(raw["study_area"]["bbox"]),
        start=tuple(raw["route"]["start"]["xy"]),
        end=tuple(raw["route"]["end"]["xy"]),
        start_label=raw["route"]["start"].get("label", "A"),
        end_label=raw["route"]["end"].get("label", "B"),
        resolution_m=float(r.get("resolution_m", 10)),
        swathe_width_m=float(r.get("swathe_width_m", 20)),
        alternatives=int(r.get("alternatives", 3)),
        alternative_penalty=float(r.get("alternative_penalty", 4.0)),
        alternative_buffer_m=float(r.get("alternative_buffer_m", 250)),
        max_deflection_deg=float(r.get("max_deflection_deg", 60)),
        oblique_crossing_deg=float(r.get("oblique_crossing_deg", 60)),
        crossing_band_m=float(r.get("crossing_band_m", 30)),
        sources=tuple(SourceConfig(**s) for s in raw["sources"]),
        cost_rules=tuple(
            CostRule(layer=c["layer"], mode=c["mode"], factor=float(c.get("factor", 1.0)),
                     buffer_m=float(c.get("buffer_m", 0.0)), classes=tuple(c.get("classes", ())))
            for c in raw["cost_rules"]
        ),
        crossing_rules=tuple(
            CrossingRule(layer=c["layer"],
                         penalties={str(k): float(v) for k, v in (c["penalty"].items() if isinstance(c["penalty"], dict)
                                                                   else {"*": c["penalty"]}.items())},
                         rag=dict(c.get("rag", {"*": "GREEN"})),
                         label=c.get("label", c["layer"]))
            for c in raw["crossing_rules"]
        ),
        proximity_rules=tuple(raw.get("proximity_rules", [])),
        area_rules=tuple(raw.get("area_rules", [])),
        cad=raw.get("cad", {}),
        path=path,
    )
