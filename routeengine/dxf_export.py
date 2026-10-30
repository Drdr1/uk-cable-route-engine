"""Write routes, constraints and crossings to DXF for the AutoCAD teams.

Coordinates are British National Grid metres, so the drawing lands at its
real-world position and lines up with OS base mapping and survey data.
Layer names, colours and linetypes come from the project's CAD standard
(or an optional template DXF), so drafters start detailed design straight
away instead of re-tracing a GIS export.
"""
from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf import zoom
from shapely.geometry import LineString
from shapely.ops import unary_union

from .analysis import RouteAssessment, fill_holes, swathe_of
from .config import Project

RAG_COLOR = {"RED": 1, "AMBER": 30, "GREEN": 3}

DEFAULT_LAYERS = {
    "route": {"name": "E-CABLE-ROUTE", "color": 1},
    "alternative": {"name": "E-CABLE-ROUTE-ALT", "color": 8},
    "swathe": {"name": "E-CABLE-SWATHE", "color": 4, "linetype": "DASHED"},
    "chainage": {"name": "E-CABLE-CHAINAGE", "color": 7},
    "crossing": {"name": "E-CROSSINGS", "color": 6},
    "endpoints": {"name": "E-ENDPOINTS", "color": 5},
    "constraint": {"name": "C-{layer}", "color": 9},
}


def chainage_text(ch: float) -> str:
    return f"{int(ch // 1000)}+{int(round(ch % 1000)):03d}"


def _layer(doc, spec: dict) -> str:
    name = spec["name"]
    if name not in doc.layers:
        attrs = {"color": spec.get("color", 7)}
        if spec.get("linetype") and spec["linetype"] in doc.linetypes:
            attrs["linetype"] = spec["linetype"]
        doc.layers.add(name, **attrs)
    return name


def _polyline(msp, coords, layer: str, closed: bool = False, color: int | None = None):
    attribs = {"layer": layer}
    if color is not None:
        attribs["color"] = color
    return msp.add_lwpolyline([(x, y) for x, y, *_ in coords], close=closed, dxfattribs=attribs)


def _text(msp, text: str, xy, height: float, layer: str, color: int | None = None, rotation: float = 0.0):
    attribs = {"layer": layer, "height": height, "rotation": rotation}
    if color is not None:
        attribs["color"] = color
    return msp.add_text(text, dxfattribs=attribs).set_placement(xy)


def _polygons(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]


def export_dxf(project: Project, assessments: list[RouteAssessment], layers, path: Path) -> Path:
    cad = project.cad or {}
    specs = {**DEFAULT_LAYERS, **cad.get("layers", {})}
    template = cad.get("template")
    doc = ezdxf.readfile(template) if template else ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6  # metres
    doc.header["$MEASUREMENT"] = 1
    msp = doc.modelspace()
    h = float(cad.get("text_height", 6.0))
    tick_every = float(cad.get("chainage_tick_m", 100))
    label_every = float(cad.get("chainage_label_m", 500))

    # Constraints, clipped to a corridor around the routes to keep the drawing light.
    corridor = fill_holes(unary_union([a.route.line for a in assessments]).buffer(float(cad.get("constraint_corridor_m", 750))))
    colors = cad.get("constraint_colors", {})
    for name, gdf in layers.items():
        if gdf is None or gdf.empty:
            continue
        spec = dict(specs["constraint"])
        spec["name"] = spec["name"].format(layer=name.upper())
        spec["color"] = colors.get(name, spec.get("color", 9))
        lname = _layer(doc, spec)
        for geom in gdf[gdf.intersects(corridor)].geometry.intersection(corridor):
            if geom.is_empty:
                continue
            if geom.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection"):
                for poly in _polygons(geom):
                    _polyline(msp, poly.exterior.coords, lname, closed=True)
                    for ring in poly.interiors:
                        _polyline(msp, ring.coords, lname, closed=True)
            else:
                for part in ([geom] if geom.geom_type == "LineString" else getattr(geom, "geoms", [])):
                    if part.geom_type == "LineString":
                        _polyline(msp, part.coords, lname)

    route_layer = _layer(doc, specs["route"])
    alt_layer = _layer(doc, specs["alternative"])
    swathe_layer = _layer(doc, specs["swathe"])
    ch_layer = _layer(doc, specs["chainage"])
    x_layer = _layer(doc, specs["crossing"])
    ep_layer = _layer(doc, specs["endpoints"])

    for a in assessments:
        line: LineString = a.route.line
        is_main = a.route.rank == 1
        _polyline(msp, line.coords, route_layer if is_main else alt_layer)
        if not is_main:
            mid = line.interpolate(0.5, normalized=True)
            _text(msp, f"ALTERNATIVE {a.route.rank}", (mid.x, mid.y), h * 1.5, alt_layer)
            continue
        for poly in _polygons(swathe_of(project, line)):
            _polyline(msp, poly.exterior.coords, swathe_layer, closed=True)

        d = 0.0
        while d <= line.length:
            p = line.interpolate(d)
            ahead = line.interpolate(min(line.length, d + 1))
            back = line.interpolate(max(0.0, d - 1))
            dx, dy = ahead.x - back.x, ahead.y - back.y
            norm = (dx * dx + dy * dy) ** 0.5 or 1.0
            nx, ny = -dy / norm, dx / norm
            size = 8 if d % label_every == 0 else 4
            msp.add_line((p.x - nx * size, p.y - ny * size), (p.x + nx * size, p.y + ny * size), dxfattribs={"layer": ch_layer})
            if d % label_every == 0:
                _text(msp, f"CH {chainage_text(d)}", (p.x + nx * (size + 3), p.y + ny * (size + 3)), h, ch_layer)
            d += tick_every

        for c in a.crossings:
            color = RAG_COLOR[c.rag]
            msp.add_circle((c.x, c.y), radius=h * 1.5, dxfattribs={"layer": x_layer, "color": color})
            label = f"{c.ref} {c.type.upper()} {c.name or c.cls} CH {chainage_text(c.chainage)} [{c.rag}]"
            _text(msp, label, (c.x + h * 2, c.y + h * 2), h * 0.8, x_layer, color)

    for xy, label in ((project.start, project.start_label), (project.end, project.end_label)):
        msp.add_circle(xy, radius=h * 3, dxfattribs={"layer": ep_layer})
        _text(msp, label, (xy[0] + h * 4, xy[1] + h * 4), h * 1.8, ep_layer)

    zoom.extents(msp)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def render_preview(dxf_path: Path, png_path: Path) -> Path:
    """PNG rendering of the DXF itself (not the GIS data), to show what CAD will open."""
    from ezdxf.addons.drawing import matplotlib as dxf_mpl

    doc = ezdxf.readfile(dxf_path)
    dxf_mpl.qsave(doc.modelspace(), str(png_path), bg="#FFFFFF", dpi=200)
    return png_path
