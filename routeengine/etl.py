"""ETL: fetch every configured layer for the study area, clean it, and store it.

Cleaning: invalid geometries repaired, multi-parts exploded, wrong geometry
types dropped, everything clipped to the study area. Each layer's outcome
(feature count, status, error) goes into a manifest that the risk report
quotes, so a missing layer can never silently produce a "clean" route.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import box

from .config import BNG, Project
from .sources import Http, SourceError, fetch

log = logging.getLogger(__name__)

POLY_TYPES = {"Polygon", "MultiPolygon"}
LINE_TYPES = {"LineString", "MultiLineString"}


@dataclass
class LayerStatus:
    name: str
    status: str  # ok | empty | unavailable
    features: int
    source: str
    attribution: str
    fetched_at: str
    error: str = ""


def clean(gdf: gpd.GeoDataFrame, geometry: str, bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf
    gdf = gdf.copy()
    gdf["geometry"] = gdf.geometry.apply(lambda g: g if g.is_valid else make_valid(g))
    gdf = gdf.clip(box(*bbox))
    gdf = gdf.explode(index_parts=False, ignore_index=True)
    allowed = POLY_TYPES if geometry == "polygon" else LINE_TYPES
    gdf = gdf[gdf.geometry.geom_type.isin(allowed) & ~gdf.geometry.is_empty]
    return gdf.reset_index(drop=True).set_crs(BNG, allow_override=True)


def run_etl(project: Project, out_gpkg: Path, http: Http | None = None) -> list[LayerStatus]:
    out_gpkg.parent.mkdir(parents=True, exist_ok=True)
    if out_gpkg.exists():
        out_gpkg.unlink()
    statuses: list[LayerStatus] = []
    for src in project.sources:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            gdf = clean(fetch(src, project.bbox, http), src.geometry, project.bbox)
            status = "ok" if len(gdf) else "empty"
            if len(gdf):
                gdf.to_file(out_gpkg, layer=src.name, driver="GPKG")
            statuses.append(LayerStatus(src.name, status, len(gdf), src.url or src.path, src.attribution, now))
            log.info("%-14s %-6s %6d features", src.name, status, len(gdf))
        except (SourceError, OSError, ValueError) as exc:
            if src.required:
                raise
            log.warning("%-14s unavailable: %s", src.name, exc)
            statuses.append(LayerStatus(src.name, "unavailable", 0, src.url or src.path, src.attribution, now, str(exc)[:300]))
    (out_gpkg.with_suffix(".manifest.json")).write_text(json.dumps([asdict(s) for s in statuses], indent=2))
    return statuses


def load_layers(gpkg: Path, project: Project) -> dict[str, gpd.GeoDataFrame]:
    """All configured layers; layers that were empty or unavailable come back empty."""
    import pyogrio

    present = set(pyogrio.list_layers(gpkg)[:, 0]) if gpkg.exists() else set()
    layers = {}
    for src in project.sources:
        if src.name in present:
            layers[src.name] = gpd.read_file(gpkg, layer=src.name)
        else:
            layers[src.name] = gpd.GeoDataFrame({"cls": [], "label": []}, geometry=[], crs=BNG)
    return layers


def load_manifest(gpkg: Path) -> list[LayerStatus]:
    path = gpkg.with_suffix(".manifest.json")
    if not path.exists():
        return []
    return [LayerStatus(**d) for d in json.loads(path.read_text())]


def to_postgis(layers: dict[str, gpd.GeoDataFrame], url: str, schema: str = "constraints") -> None:
    """Publish the cleaned layers to PostGIS (one table per layer, GiST index on geometry)."""
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(f'create schema if not exists "{schema}"'))
    for name, gdf in layers.items():
        if gdf.empty:
            continue
        gdf.rename_geometry("geom").to_postgis(name, engine, schema=schema, if_exists="replace", index=False)
        with engine.begin() as conn:
            conn.execute(text(f'create index if not exists "{name}_geom_idx" on "{schema}"."{name}" using gist (geom)'))
