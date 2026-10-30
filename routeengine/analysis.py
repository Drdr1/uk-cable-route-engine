"""Cross-reference each route against every constraint and produce RAG findings.

* Crossings: every road / railway / watercourse / power line the centreline
  crosses, with chainage, crossing angle and a RAG rating from the project
  rules. Oblique crossings are escalated (longer drill, more land take).
* Area constraints: centreline length inside polygons (e.g. Flood Zone 3),
  with chainage from/to.
* Proximity: closest approach to sensitive features (e.g. ancient woodland
  standing-advice buffer, SSSI impact zone).
* Design checks: sharp deflections in the alignment.
* Data gaps: any layer that could not be fetched is reported, never ignored.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import geopandas as gpd
from shapely.geometry import LineString, MultiPoint, Point
from shapely.ops import unary_union

from .config import Project
from .etl import LayerStatus
from .router import Route

RAG_ORDER = {"GREEN": 0, "AMBER": 1, "RED": 2}


def worst(values) -> str:
    values = list(values)
    return max(values, key=lambda v: RAG_ORDER[v]) if values else "GREEN"


@dataclass
class Crossing:
    ref: str
    layer: str
    type: str
    cls: str
    name: str
    chainage: float
    angle: float
    rag: str
    reason: str
    x: float
    y: float


@dataclass
class Finding:
    category: str  # area | proximity | design | data
    item: str
    detail: str
    from_ch: float | None
    to_ch: float | None
    value: float | None
    rag: str


@dataclass
class RouteAssessment:
    route: Route
    crossings: list[Crossing] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def rag(self) -> str:
        return worst([c.rag for c in self.crossings] + [f.rag for f in self.findings])

    def count(self, rag: str) -> int:
        return sum(1 for c in self.crossings if c.rag == rag) + sum(1 for f in self.findings if f.rag == rag)

    def crossings_by_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.crossings:
            out[c.type] = out.get(c.type, 0) + 1
        return out


def _bearing(line: LineString, distance: float, step: float = 5.0) -> float:
    a = line.interpolate(max(0.0, distance - step))
    b = line.interpolate(min(line.length, distance + step))
    return math.degrees(math.atan2(b.y - a.y, b.x - a.x))


def _feature_bearing(geom, pt: Point, step: float = 5.0) -> float:
    lines = [geom] if geom.geom_type == "LineString" else list(getattr(geom, "geoms", []))
    nearest = min(lines, key=lambda g: g.distance(pt))
    return _bearing(nearest, nearest.project(pt), step)


def crossing_angle(route: LineString, chainage: float, feature, pt: Point) -> float:
    """Acute angle between route and feature at the crossing: 90 = square, 0 = parallel."""
    diff = abs(_bearing(route, chainage) - _feature_bearing(feature, pt)) % 180
    return round(min(diff, 180 - diff), 1)


def _points(geom) -> list[Point]:
    if geom.is_empty:
        return []
    if geom.geom_type == "Point":
        return [geom]
    if geom.geom_type == "MultiPoint":
        return list(geom.geoms)
    # Overlap along the feature (route running in a verge): report its first point.
    pts = []
    for part in getattr(geom, "geoms", [geom]):
        if part.geom_type == "Point":
            pts.append(part)
        elif part.geom_type == "LineString":
            pts.append(Point(part.coords[0]))
    return pts


def find_crossings(project: Project, route: LineString, layers: dict[str, gpd.GeoDataFrame]) -> list[Crossing]:
    found: list[Crossing] = []
    for rule in project.crossing_rules:
        gdf = layers.get(rule.layer)
        if gdf is None or gdf.empty:
            continue
        candidates = gdf[gdf.intersects(route)]
        for _, feat in candidates.iterrows():
            for pt in _points(route.intersection(feat.geometry)):
                if any(c.layer == rule.layer and Point(c.x, c.y).distance(pt) < 5 for c in found):
                    continue
                ch = route.project(pt)
                angle = crossing_angle(route, ch, feat.geometry, pt)
                cls = str(feat["cls"])
                rag = rule.rag.get(cls, rule.rag.get("*", "GREEN"))
                reason = f"{rule.label}: {cls}"
                if angle < project.oblique_crossing_deg:
                    reason = f"{reason}; oblique crossing ({angle:.0f} deg)"
                    if rag == "GREEN":
                        rag = "AMBER"  # longer trenchless drill, more land take
                found.append(Crossing("", rule.layer, rule.label, cls, str(feat.get("label", "") or ""),
                                      round(ch, 1), angle, rag, reason, round(pt.x, 2), round(pt.y, 2)))
    found.sort(key=lambda c: c.chainage)
    found = merge_parallel(found, project.merge_crossings_m)
    for i, c in enumerate(found, start=1):
        c.ref = f"X{i:02d}"
    return found


def merge_parallel(crossings: list[Crossing], within_m: float) -> list[Crossing]:
    """One crossing for a dual carriageway or twin-track railway: same layer and name/class within `within_m`."""
    merged: list[Crossing] = []
    for c in crossings:
        prev = merged[-1] if merged else None
        same = prev and prev.layer == c.layer and (prev.name or prev.cls) == (c.name or c.cls)
        if same and c.chainage - prev.chainage <= within_m:
            width = c.chainage - prev.chainage
            prev.reason = f"{prev.reason}; parallel features over {width:.0f} m (e.g. dual carriageway)"
            if RAG_ORDER[c.rag] > RAG_ORDER[prev.rag]:
                prev.rag = c.rag
            continue
        merged.append(c)
    return merged


def area_findings(project: Project, route: LineString, swathe, layers) -> list[Finding]:
    out = []
    for rule in project.area_rules:
        gdf = layers.get(rule["layer"])
        if gdf is None or gdf.empty:
            continue
        target = unary_union(list(gdf[gdf.intersects(swathe)].geometry))
        if target.is_empty:
            continue
        inside = route.intersection(target)
        parts = [inside] if inside.geom_type == "LineString" else list(getattr(inside, "geoms", []))
        parts = [p for p in parts if p.geom_type == "LineString" and p.length > 0]
        if not parts and swathe.intersects(target):
            # Swathe clips the feature even though the centreline does not.
            ch = route.project(target.centroid)
            out.append(Finding("area", rule["label"], "construction swathe overlaps", ch, ch, 0.0, rule["rag"]))
        sections: list[list[float]] = []  # [from, to, length inside]
        for p in sorted(parts, key=lambda p: route.project(Point(p.coords[0]))):
            a, b = sorted((route.project(Point(p.coords[0])), route.project(Point(p.coords[-1]))))
            if sections and a - sections[-1][1] <= project.merge_area_gap_m:
                sections[-1][1] = max(sections[-1][1], b)
                sections[-1][2] += p.length
            else:
                sections.append([a, b, p.length])
        for a, b, length in sections:
            out.append(Finding("area", rule["label"], f"{length:.0f} m of centreline inside",
                               round(a, 1), round(b, 1), round(length, 1), rule["rag"]))
    return out


def proximity_findings(project: Project, route: LineString, layers) -> list[Finding]:
    out = []
    for rule in project.proximity_rules:
        gdf = layers.get(rule["layer"])
        if gdf is None or gdf.empty:
            continue
        within = float(rule["within_m"])
        near = gdf[gdf.distance(route) <= within]
        if near.empty:
            continue
        dists = near.distance(route)
        closest = near.loc[dists.idxmin()].geometry
        ch = route.project(closest.centroid)
        out.append(Finding("proximity", rule["label"],
                           f"{len(near)} feature(s) within {within:.0f} m; closest {dists.min():.0f} m",
                           round(ch, 1), None, round(float(dists.min()), 1), rule["rag"]))
    return out


def design_findings(project: Project, route: LineString) -> list[Finding]:
    out = []
    coords = list(route.coords)
    for i in range(1, len(coords) - 1):
        (x0, y0), (x1, y1), (x2, y2) = coords[i - 1], coords[i], coords[i + 1]
        b1 = math.degrees(math.atan2(y1 - y0, x1 - x0))
        b2 = math.degrees(math.atan2(y2 - y1, x2 - x1))
        deflection = abs((b2 - b1 + 180) % 360 - 180)
        if deflection > project.max_deflection_deg:
            ch = route.project(Point(x1, y1))
            out.append(Finding("design", "Sharp deflection", f"{deflection:.0f} deg change of direction",
                               round(ch, 1), None, round(deflection, 1), "AMBER"))
    return out


def data_findings(manifest: list[LayerStatus]) -> list[Finding]:
    return [Finding("data", f"Layer '{s.name}' unavailable", s.error or "no data", None, None, None, "AMBER")
            for s in manifest if s.status == "unavailable"]


def fill_holes(geom):
    """Corridor around routes that loop around a constraint has a hole in the middle; close it."""
    from shapely.geometry import MultiPolygon, Polygon

    if geom.geom_type == "Polygon":
        return Polygon(geom.exterior)
    if geom.geom_type == "MultiPolygon":
        return MultiPolygon([Polygon(g.exterior) for g in geom.geoms])
    return geom


def swathe_of(project: Project, route: LineString):
    return route.buffer(project.swathe_width_m / 2, cap_style="flat")


def assess(project: Project, routes: list[Route], layers, manifest: list[LayerStatus]) -> list[RouteAssessment]:
    gaps = data_findings(manifest)
    results = []
    for r in routes:
        swathe = swathe_of(project, r.line)
        a = RouteAssessment(r)
        a.crossings = find_crossings(project, r.line, layers)
        a.findings = (area_findings(project, r.line, swathe, layers)
                      + proximity_findings(project, r.line, layers)
                      + design_findings(project, r.line)
                      + list(gaps))
        results.append(a)
    # Recommend the lowest weighted cost: it already prices every exclusion, buffer, flood zone and
    # crossing. RED/AMBER counts are reported alongside for the engineer's judgement.
    results.sort(key=lambda a: a.route.cost)
    for i, a in enumerate(results, start=1):
        a.route.rank = i
    return results
