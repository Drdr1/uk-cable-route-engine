"""Command line: fetch constraints, route, export.

    routeengine fetch  --project projects/weald-demo.yaml
    routeengine route  --project projects/weald-demo.yaml
    routeengine all    --project projects/weald-demo.yaml   # fetch + route
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from shapely.ops import unary_union

from . import analysis, costsurface, dxf_export, etl, report, router
from .config import load_project

log = logging.getLogger("routeengine")


def run_route(project, gpkg: Path, out: Path) -> dict:
    t0 = time.monotonic()
    layers = etl.load_layers(gpkg, project)
    manifest = etl.load_manifest(gpkg)
    grid = costsurface.build_grid(project, layers)
    exclusion_geoms = [layers[r.layer].geometry.buffer(r.buffer_m) if r.buffer_m else layers[r.layer].geometry
                       for r in project.cost_rules if r.mode == "exclude" and not layers[r.layer].empty]
    obstacles = unary_union([g for s in exclusion_geoms for g in s]) if exclusion_geoms else None
    routes = router.find_routes(grid, project.start, project.end, project.alternatives,
                                project.alternative_penalty, project.alternative_buffer_m, obstacles)
    assessments = analysis.assess(project, routes, layers, manifest)
    log.info("routed %d alternative(s) in %.1fs", len(assessments), time.monotonic() - t0)

    out.mkdir(parents=True, exist_ok=True)
    dxf = dxf_export.export_dxf(project, assessments, layers, out / "routes.dxf")
    map_png = report.overview_map(project, assessments, layers, out / "overview.png")
    xlsx = report.write_excel(project, assessments, manifest, out / "rag-report.xlsx")
    pdf = report.write_pdf(project, assessments, manifest, map_png, out / "rag-report.pdf")
    try:
        dxf_export.render_preview(dxf, out / "dxf-preview.png")
    except Exception as exc:  # preview is a nice-to-have
        log.warning("DXF preview failed: %s", exc)

    summary = {
        "project": project.name,
        "routes": [{"rank": a.route.rank, "length_m": round(a.route.length_m), "cost": round(a.route.cost),
                    "crossings": len(a.crossings), "red": a.count("RED"), "amber": a.count("AMBER"), "rag": a.rag}
                   for a in assessments],
        "layers": {s.name: {"status": s.status, "features": s.features} for s in manifest},
        "outputs": [p.name for p in (dxf, xlsx, pdf, map_png)],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    write_geojson(project, assessments, layers, out)
    return summary


def write_geojson(project, assessments, layers, out: Path) -> None:
    """WGS84 GeoJSON for the web map."""
    import geopandas as gpd
    from shapely.geometry import Point

    routes = gpd.GeoDataFrame(
        [{"rank": a.route.rank, "length_m": round(a.route.length_m), "rag": a.rag, "crossings": len(a.crossings)}
         for a in assessments],
        geometry=[a.route.line for a in assessments], crs="EPSG:27700")
    routes.to_crs(4326).to_file(out / "routes.geojson", driver="GeoJSON")
    main = assessments[0]
    if main.crossings:
        xs = gpd.GeoDataFrame(
            [{"ref": c.ref, "type": c.type, "cls": c.cls, "name": c.name,
              "chainage": dxf_export.chainage_text(c.chainage), "angle": c.angle, "rag": c.rag, "reason": c.reason}
             for c in main.crossings],
            geometry=[Point(c.x, c.y) for c in main.crossings], crs="EPSG:27700")
        xs.to_crs(4326).to_file(out / "crossings.geojson", driver="GeoJSON")
    corridor = analysis.fill_holes(unary_union([a.route.line for a in assessments]).buffer(1500))
    for name in ("sssi", "ancient_woodland", "flood_zone_3"):
        gdf = layers.get(name)
        if gdf is not None and not gdf.empty:
            clipped = gdf[gdf.intersects(corridor)].copy()
            clipped["geometry"] = clipped.geometry.simplify(5)
            clipped[["label", "geometry"]].to_crs(4326).to_file(out / f"{name}.geojson", driver="GeoJSON")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="routeengine", description="Automated cable route optimisation")
    parser.add_argument("command", choices=["fetch", "route", "all", "postgis"])
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("output"))
    parser.add_argument("--postgis-url", help="SQLAlchemy URL, for the postgis command")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    project = load_project(args.project)
    gpkg = args.data / f"{args.project.stem}.gpkg"
    if args.command in ("fetch", "all"):
        statuses = etl.run_etl(project, gpkg)
        if all(s.status != "ok" for s in statuses):
            log.error("no layer could be fetched")
            return 1
    if args.command in ("route", "all"):
        summary = run_route(project, gpkg, args.out / args.project.stem)
        print(json.dumps(summary["routes"], indent=2))
    if args.command == "postgis":
        etl.to_postgis(etl.load_layers(gpkg, project), args.postgis_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
