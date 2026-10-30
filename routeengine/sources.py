"""Fetch vector layers for a study area from public UK services.

* ArcGIS FeatureServer / MapServer (Natural England, Environment Agency, most
  council and DEFRA services): bbox query in British National Grid, paged.
* Overpass (OpenStreetMap): roads, rail, waterways, buildings, power lines.
  In production the same layers come from the OS Data Hub (MasterMap
  Topography / Highways), which needs an API key and licence.
* Local files (GeoPackage, Shapefile, GeoJSON) for client-supplied data.

Every fetcher returns a GeoDataFrame in EPSG:27700 with `cls` (feature class)
and `label` columns, so the rest of the engine never cares where data came from.
"""
from __future__ import annotations

import logging
import time
from typing import Protocol

import geopandas as gpd
import requests
from pyproj import Transformer
from shapely.geometry import LinearRing, LineString, MultiLineString, MultiPolygon, Polygon, shape

from .config import BNG, SourceConfig

log = logging.getLogger(__name__)

USER_AGENT = "uk-cable-route-engine/0.1 (+https://github.com/Drdr1/uk-cable-route-engine)"


class SourceError(Exception):
    pass


class Http(Protocol):
    def get(self, url: str, params: dict) -> dict: ...

    def post(self, url: str, data: dict) -> dict: ...


class RequestsHttp:
    def __init__(self, retries: int = 3, timeout: tuple[float, float] = (15, 180)):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.retries = retries
        self.timeout = timeout

    def _call(self, method: str, url: str, **kwargs) -> dict:
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
                if resp.status_code == 429 or resp.status_code >= 500:
                    self.last_status = resp.status_code
                    raise SourceError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                body = resp.json()
                if isinstance(body, dict) and "error" in body:
                    raise SourceError(f"service error: {body['error']}")
                return body
            except (requests.RequestException, ValueError, SourceError) as exc:
                last = exc
                if attempt < self.retries:
                    # Rate limits (429) and gateway timeouts need a real pause, not a quick retry.
                    slow = getattr(self, "last_status", 0) in (429, 502, 503, 504)
                    time.sleep((10 if slow else 2) * 2 ** (attempt - 1))
                    self.last_status = 0
        raise SourceError(f"{url}: {last}")

    def get(self, url: str, params: dict) -> dict:
        return self._call("GET", url, params=params)

    def post(self, url: str, data: dict) -> dict:
        return self._call("POST", url, data=data)


def _empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"cls": [], "label": []}, geometry=[], crs=BNG)


def esri_to_shapely(geom: dict):
    """Esri JSON geometry -> shapely. Outer rings are clockwise, holes counter-clockwise."""
    if "rings" in geom:
        shells, holes = [], []
        for ring in geom["rings"]:
            if len(ring) < 4:
                continue
            (holes if LinearRing(ring).is_ccw else shells).append(ring)
        polys = []
        for shell in shells:
            outer = Polygon(shell)
            inner = [h for h in holes if outer.contains(Polygon(h).representative_point())]
            polys.append(Polygon(shell, inner))
        if not polys:
            return None
        return polys[0] if len(polys) == 1 else MultiPolygon(polys)
    if "paths" in geom:
        lines = [LineString(p) for p in geom["paths"] if len(p) >= 2]
        return lines[0] if len(lines) == 1 else MultiLineString(lines)
    return None


def fetch_arcgis(src: SourceConfig, bbox: tuple[float, float, float, float], http: Http,
                 page_size: int = 1000) -> gpd.GeoDataFrame:
    url = src.url.rstrip("/") + "/query"
    base = {
        "where": src.query or "1=1",
        "geometry": ",".join(str(v) for v in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 27700,
        "outSR": 27700,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "f": "json",
        "resultRecordCount": page_size,
    }
    geoms, classes, labels = [], [], []
    offset = 0
    while True:
        body = http.get(url, {**base, "resultOffset": offset})
        features = body.get("features", [])
        for f in features:
            g = esri_to_shapely(f.get("geometry") or {})
            if g is None or g.is_empty:
                continue
            attrs = f.get("attributes", {})
            geoms.append(g)
            classes.append(str(attrs.get(src.class_field, "")) if src.class_field else src.name)
            labels.append(str(attrs.get(src.name_field) or "") if src.name_field else "")
        if not body.get("exceededTransferLimit") or not features:
            break
        offset += len(features)
    if not geoms:
        return _empty()
    return gpd.GeoDataFrame({"cls": classes, "label": labels}, geometry=geoms, crs=BNG)


_to_wgs84 = Transformer.from_crs(BNG, "EPSG:4326", always_xy=True)

OVERPASS_ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
OVERPASS_PAUSE_S = 3.0


def overpass_bbox(bbox: tuple[float, float, float, float], lonlat: bool = False) -> str:
    """WGS84 bbox: south,west,north,east for Overpass; west,south,east,north (lon/lat) for OGC APIs."""
    xs, ys = _to_wgs84.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    if lonlat:
        return f"{min(xs):.6f},{min(ys):.6f},{max(xs):.6f},{max(ys):.6f}"
    return f"{min(ys):.6f},{min(xs):.6f},{max(ys):.6f},{max(xs):.6f}"


def fetch_overpass(src: SourceConfig, bbox: tuple[float, float, float, float], http: Http) -> gpd.GeoDataFrame:
    selector = src.query.replace("{bbox}", overpass_bbox(bbox))
    ql = f"[out:json][timeout:180];({selector});out tags geom;"
    body = None
    last: Exception | None = None
    for endpoint in [src.url or OVERPASS_ENDPOINTS[0]] + [e for e in OVERPASS_ENDPOINTS if e != src.url]:
        time.sleep(OVERPASS_PAUSE_S)  # be polite: public Overpass instances rate-limit bursts
        try:
            body = http.post(endpoint, {"data": ql})
            break
        except SourceError as exc:
            last = exc
            log.warning("%s: Overpass %s failed (%s), trying next instance", src.name, endpoint, exc)
    if body is None:
        raise SourceError(f"all Overpass instances failed: {last}")
    geoms, classes, labels = [], [], []
    for el in body.get("elements", []):
        coords = [(p["lon"], p["lat"]) for p in el.get("geometry", []) if p]
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        if src.geometry == "polygon":
            if len(coords) < 4 or coords[0] != coords[-1]:
                continue
            geom = Polygon(coords)
        else:
            geom = LineString(coords)
        geoms.append(geom)
        classes.append(tags.get(src.class_field, src.name) if src.class_field else src.name)
        labels.append(tags.get(src.name_field, "") or tags.get("ref", "") if src.name_field else "")
    if not geoms:
        return _empty()
    gdf = gpd.GeoDataFrame({"cls": classes, "label": labels}, geometry=geoms, crs="EPSG:4326")
    return gdf.to_crs(BNG)


BNG_URI = "http://www.opengis.net/def/crs/EPSG/0/27700"


def _ogc_collection(src: SourceConfig, http: Http) -> str:
    """Configured collection id, or discover it: first collection whose id/title contains `query`."""
    if src.layer:
        return src.layer
    body = http.get(src.url.rstrip("/") + "/collections", {"f": "json"})
    collections = body.get("collections", [])
    if not collections:
        raise SourceError("OGC API: no collections")
    wanted = (src.query or "").lower()
    for c in collections:
        if not wanted or wanted in (c.get("id", "") + " " + c.get("title", "")).lower():
            log.info("%s: using OGC collection %r of %s", src.name, c.get("id"),
                     [x.get("id") for x in collections])
            return c["id"]
    raise SourceError(f"OGC API: no collection matching {wanted!r} in {[c.get('id') for c in collections]}")


def fetch_ogcapi(src: SourceConfig, bbox: tuple[float, float, float, float], http: Http,
                 page_size: int = 1000, max_pages: int = 200) -> gpd.GeoDataFrame:
    """OGC API - Features (EA/DEFRA Data Services Platform, OS NGD API). Requests BNG; falls back to WGS84."""
    collection = _ogc_collection(src, http)
    url = f"{src.url.rstrip('/')}/collections/{collection}/items"
    attempts = [
        ({"bbox": ",".join(str(v) for v in bbox), "bbox-crs": BNG_URI, "crs": BNG_URI}, BNG),
        ({"bbox": overpass_bbox(bbox, lonlat=True)}, "EPSG:4326"),
    ]
    last: Exception | None = None
    for params, crs in attempts:
        try:
            features, next_url, next_params = [], url, {**params, "limit": page_size, "f": "json"}
            for _ in range(max_pages):
                body = http.get(next_url, next_params)
                features += body.get("features", [])
                nxt = next((l["href"] for l in body.get("links", []) if l.get("rel") == "next"), None)
                if not nxt or not body.get("features"):
                    break
                next_url, next_params = nxt, {}
            break
        except SourceError as exc:
            last = exc
    else:
        raise SourceError(f"OGC API {url}: {last}")
    if features:
        props = features[0].get("properties", {})
        log.info("%s: %d features; attributes %s", src.name, len(features),
                 {k: props[k] for k in list(props)[:12]})
    rows, geoms = [], []
    for f in features:
        if not f.get("geometry"):
            continue
        props = f.get("properties", {})
        cls = str(props.get(src.class_field, "")) if src.class_field else src.name
        if src.class_values and src.class_field in props and cls not in src.class_values:
            continue
        geoms.append(shape(f["geometry"]))
        rows.append({"cls": cls, "label": str(props.get(src.name_field) or "") if src.name_field else ""})
    if not geoms:
        return _empty()
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=crs)
    if crs == BNG and gdf.total_bounds[2] < 1000:  # server ignored crs and sent lon/lat
        gdf = gdf.set_crs("EPSG:4326", allow_override=True)
    return gdf.to_crs(BNG)


def fetch_file(src: SourceConfig, bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    try:
        gdf = gpd.read_file(src.path, bbox=bbox, layer=src.layer or None)
    except Exception as exc:  # pyogrio raises its own error types for missing/corrupt files
        raise SourceError(f"{src.path}: {exc}") from exc
    if gdf.crs is None:
        raise SourceError(f"{src.path} has no CRS")
    gdf = gdf.to_crs(BNG)
    gdf["cls"] = gdf[src.class_field].astype(str) if src.class_field else src.name
    gdf["label"] = gdf[src.name_field].fillna("").astype(str) if src.name_field else ""
    return gdf[["cls", "label", "geometry"]]


def fetch(src: SourceConfig, bbox: tuple[float, float, float, float], http: Http | None = None) -> gpd.GeoDataFrame:
    http = http or RequestsHttp()
    if src.type == "arcgis":
        return fetch_arcgis(src, bbox, http)
    if src.type == "overpass":
        return fetch_overpass(src, bbox, http)
    if src.type == "ogcapi":
        return fetch_ogcapi(src, bbox, http)
    if src.type == "file":
        return fetch_file(src, bbox)
    raise ValueError(f"unknown source type {src.type}")


def geojson_to_shapely(geom: dict):
    return shape(geom)
