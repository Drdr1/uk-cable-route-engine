"""Static demo site: interactive map of the routes plus the downloadable outputs."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from html import escape
from pathlib import Path

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cable Route Engine</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {{ --bg:#f5f6f8; --panel:#fff; --ink:#1d2733; --muted:#5d6b7a; --line:#dde2e8;
          --red:#d64545; --amber:#f0a030; --green:#3a9d5d; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--ink); background:var(--bg); }}
  header {{ padding:18px 20px 8px; max-width:1200px; margin:auto; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  main {{ max-width:1200px; margin:auto; padding:8px 20px 30px; display:grid; gap:16px; grid-template-columns: 1fr 360px; }}
  #map {{ height:620px; border-radius:10px; border:1px solid var(--line); }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .card h2 {{ font-size:14px; margin:0 0 10px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  th, td {{ text-align:left; padding:5px 4px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ color:var(--muted); font-weight:600; }}
  .rag {{ display:inline-block; padding:1px 7px; border-radius:4px; color:#fff; font-weight:600; font-size:11px; }}
  .RED {{ background:var(--red); }} .AMBER {{ background:var(--amber); }} .GREEN {{ background:var(--green); }}
  .downloads a {{ display:block; padding:7px 0; color:#1f5fbf; text-decoration:none; border-bottom:1px solid var(--line); }}
  .wide {{ grid-column: 1 / -1; }}
  .previews {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
  .previews img {{ width:100%; border:1px solid var(--line); border-radius:8px; background:#fff; }}
  .note {{ color:var(--muted); font-size:12px; }}
  @media (max-width: 900px) {{ main {{ grid-template-columns:1fr; padding:8px 16px; }} #map {{ height:420px; }}
    .previews {{ grid-template-columns:1fr; }} header {{ padding:16px 16px 6px; }} }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="sub">{description} Built {built} from live public data. Coordinates in outputs: British National Grid (EPSG:27700).</div>
</header>
<main>
  <div id="map"></div>
  <div style="display:grid; gap:16px; align-content:start">
    <div class="card"><h2>Ranked corridors</h2>{routes_table}</div>
    <div class="card downloads"><h2>Outputs</h2>
      <a href="routes.dxf" download>routes.dxf: AutoCAD drawing (BNG, layered)</a>
      <a href="rag-report.xlsx" download>rag-report.xlsx: RAG risk &amp; crossings report</a>
      <a href="rag-report.pdf" target="_blank">rag-report.pdf: report for issue</a>
      <a href="summary.json" target="_blank">summary.json</a>
    </div>
    <div class="card"><h2>Data layers</h2>{layers_table}</div>
  </div>
  <div class="card wide"><h2>Crossings on the recommended route</h2>{crossings_table}</div>
  <div class="card wide"><h2>What the CAD team receives</h2>
    <div class="previews"><figure><img src="dxf-preview.png" alt="Rendering of routes.dxf"><figcaption class="note">routes.dxf rendered directly (not the GIS data)</figcaption></figure>
    <figure><img src="overview.png" alt="Overview map"><figcaption class="note">Overview map used in the PDF report</figcaption></figure></div>
    <p class="note">Illustrative study: Points A and B are hypothetical. Desk-based screening only. Buried services are not in open data, so a utility search is still required. {attribution}</p>
  </div>
</main>
<script>
const colors = ["#d62728","#1f77b4","#ff7f0e","#9467bd"];
const ragColor = {{RED:"#d64545", AMBER:"#f0a030", GREEN:"#3a9d5d"}};
const map = L.map("map");
L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{maxZoom:19,
  attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}}).addTo(map);
const overlays = {{}};
async function add(file, name, style, extra) {{
  try {{
    const r = await fetch(file); if (!r.ok) return null;
    const layer = L.geoJSON(await r.json(), Object.assign({{style}}, extra || {{}})).addTo(map);
    overlays[name] = layer; return layer;
  }} catch (e) {{ return null; }}
}}
(async () => {{
  await add("flood_zone_3.geojson", "Flood Zone 3", {{color:"#4a90d9", weight:0, fillOpacity:0.3}});
  await add("ancient_woodland.geojson", "Ancient woodland", {{color:"#2e6b3a", weight:1, fillOpacity:0.55}});
  await add("sssi.geojson", "SSSI", {{color:"#5aa05a", weight:1, fillOpacity:0.45}});
  const routes = await add("routes.geojson", "Routes", f => ({{color: colors[(f.properties.rank-1)%4],
      weight: f.properties.rank === 1 ? 5 : 3, dashArray: f.properties.rank === 1 ? null : "6 6"}}),
    {{onEachFeature: (f, l) => l.bindPopup(`Route ${{f.properties.rank}}: ${{(f.properties.length_m/1000).toFixed(2)}} km, ${{f.properties.crossings}} crossings, ${{f.properties.rag}}`)}});
  await add("crossings.geojson", "Crossings", null, {{pointToLayer: (f, ll) => L.circleMarker(ll, {{radius:6,
      color:"#222", weight:1, fillColor: ragColor[f.properties.rag], fillOpacity:1}}),
    onEachFeature: (f, l) => l.bindPopup(`<b>${{f.properties.ref}}</b> ${{f.properties.type}} (${{f.properties.cls}}) ${{f.properties.name || ""}}<br>CH ${{f.properties.chainage}}, angle ${{f.properties.angle}}&deg;<br>${{f.properties.rag}}: ${{f.properties.reason}}`)}});
  if (routes) map.fitBounds(routes.getBounds().pad(0.15)); else map.setView([51.2, 0.27], 12);
  L.control.layers(null, overlays, {{collapsed:false}}).addTo(map);
}})();
</script>
</body>
</html>
"""


def _table(headers, rows) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def rag(v: str) -> str:
    return f'<span class="rag {escape(v)}">{escape(v)}</span>'


def build(out_dir: Path, site_dir: Path, project_yaml: Path) -> Path:
    import yaml

    project = yaml.safe_load(project_yaml.read_text())
    summary = json.loads((out_dir / "summary.json").read_text())
    site_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, site_dir / f.name)

    routes = _table(["#", "Length", "Crossings", "RED", "AMBER", "RAG"],
                    [[str(r["rank"]) + (" (rec.)" if r["rank"] == 1 else ""), f'{r["length_m"] / 1000:.2f} km',
                      r["crossings"], r["red"], r["amber"], rag(r["rag"])] for r in summary["routes"]])
    layers = _table(["Layer", "Status", "Features"],
                    [[escape(k), escape(v["status"]), v["features"]] for k, v in summary["layers"].items()])
    crossings = "<p class='note'>No crossings.</p>"
    xs_path = out_dir / "crossings.geojson"
    if xs_path.exists():
        feats = json.loads(xs_path.read_text())["features"]
        crossings = _table(["Ref", "Chainage", "Type", "Class", "Name / ref", "Angle", "RAG", "Reason"],
                           [[escape(p["ref"]), escape(p["chainage"]), escape(p["type"]), escape(p["cls"]),
                             escape(p.get("name") or ""), f'{p["angle"]:.0f}&deg;', rag(p["rag"]), escape(p["reason"])]
                            for p in (f["properties"] for f in feats)])
    attribution = " ".join(sorted({escape(s.get("attribution", "")) for s in project["sources"] if s.get("attribution")}))
    html = PAGE.format(title=escape(project["name"]), description=escape(project.get("description", "")),
                       built=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), routes_table=routes,
                       layers_table=layers, crossings_table=crossings, attribution=attribution)
    (site_dir / "index.html").write_text(html)
    return site_dir / "index.html"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static demo site")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("output"))
    parser.add_argument("--site", type=Path, default=Path("site"))
    args = parser.parse_args()
    print(build(args.out / args.project.stem, args.site, args.project))


if __name__ == "__main__":
    main()
