# uk-cable-route-engine

Automated route optimisation for grid-connection cables. It pulls live UK constraint data, finds least-cost corridors around environmental and civil constraints, and hands clean outputs to the drawing office:

- **DXF** in British National Grid, layered to a CAD standard
- **RAG Risk & Crossings Report** in Excel and PDF

**Live demo** (rebuilt weekly from live data): https://drdr1.github.io/uk-cable-route-engine/

The demo is an illustrative 132 kV underground cable between two hypothetical points near Tonbridge, Kent. The area has dense ancient woodland, SSSIs, the River Medway floodplain, main-line railways and the A21/A26.

```
 Natural England (SSSI, Ancient Woodland) ─┐
 Environment Agency (Flood Zone 3) ────────┤ fetch by bbox in EPSG:27700, page, retry
 OpenStreetMap / OS Data Hub ──────────────┤ clean: repair, clip, explode, type-check
   (roads, rail, rivers, buildings, OHL)   ┘ → GeoPackage (+ PostGIS) + data manifest
                      │
                      ▼
 cost surface (10 m grid): exclusions = impassable, buffers & flood zones = multipliers,
 linear features = crossing bands (square crossings cheapest)
                      │
                      ▼
 Dijkstra least-cost path (8-connected, true distance) → safe simplification
 → ranked alternatives (penalty method, scored on the original surface)
                      │
                      ▼
 crossings (chainage, angle, RAG) · area / proximity / design / data-gap findings
                      │
          ┌───────────┼──────────────┬───────────────┐
      routes.dxf   rag-report.xlsx   rag-report.pdf   web map
```

## Outputs

**`routes.dxf`** has real BNG coordinates in metres (`$INSUNITS` = m), so it drops onto OS mapping and survey data. Layer names, colours and linetypes come from the project's CAD standard, or from the client's template DXF. It contains:
- the recommended centreline
- the construction swathe (dashed)
- chainage ticks every 100 m, labelled `CH 1+500`
- crossing markers coloured by RAG with reference and chainage
- alternative routes
- constraints clipped to the corridor

**`rag-report.xlsx` / `rag-report.pdf`** cover:
- **Route comparison:** length, weighted cost (the ranking), crossings by type, RED/AMBER counts.
- **Crossings:** every road, railway, watercourse and power line crossed, with chainage, crossing angle and RAG.
- **Constraint findings:** e.g. 335 m inside Flood Zone 3 from CH 3+416 to 3+751; ancient woodland within the 15 m standing-advice buffer; SSSI within 500 m.
- **Design checks:** sharp deflections in the alignment.
- **Data sources:** a manifest with attribution and fetch time.

If a layer could not be fetched, the report says so in AMBER. A missing layer can never produce a falsely "clean" route.

| Overview map (synthetic test corridor) | The DXF itself, rendered |
|---|---|
| ![overview](docs/synthetic-overview.png) | ![dxf](docs/synthetic-dxf.png) |

## How routing works

**Cost surface.** Every rule is configured per project in YAML (see `projects/weald-demo.yaml`):
- `exclude` makes cells impassable: SSSI, ancient woodland, buildings plus 10 m.
- Residential amenity: cells within 60 m of buildings cost ×2.5, so routes prefer open land to threading through towns.
- `multiply` scales cost: Flood Zone 3 ×3, SSSI 100 m buffer ×3, ancient woodland 15 m buffer ×6.
- Crossing penalties are set per feature class: motorway 500, A-road 150, railway 500, river 250, minor stream 40.

**Square crossings by construction.** Linear features are drawn as 30 m bands and penalised per cell. An oblique crossing passes through far more penalised cells than a square one, so the optimiser prefers short, square crossings as an engineer would. The report still flags any crossing under 60° as oblique.

**Alternatives.** After each route, a corridor around it is made more expensive and the search runs again. Each alternative is a genuinely different corridor, ranked by weighted cost on the original surface, with RED/AMBER counts alongside for the engineer's judgement.

**Safe simplification.** The staircase grid path is straightened with Douglas–Peucker, then re-checked so the straightened line never cuts into an excluded area.

**End points.** Real end points often fall inside a building footprint. They are snapped to the nearest passable ground, and the distance moved is recorded.

## Run it

```bash
pip install -e .
routeengine all --project projects/weald-demo.yaml      # fetch live data + route
python -m routeengine.site --project projects/weald-demo.yaml   # optional web map
ls output/weald-demo/   # routes.dxf rag-report.xlsx rag-report.pdf overview.png dxf-preview.png ...
```

Publish the cleaned layers to PostGIS (one table per layer, GiST-indexed):

```bash
docker compose up -d
pip install -e ".[postgis]"
routeengine postgis --project projects/weald-demo.yaml --postgis-url postgresql+psycopg2://routes:routes@localhost:5432/routes
```

## Data sources

| Layer | Demo source | Production source |
|---|---|---|
| SSSI | Natural England ArcGIS Feature Service | same |
| Ancient woodland | Natural England ArcGIS Feature Service | same |
| Flood Zone 3 | Environment Agency, DEFRA Data Services Platform (OGC API Features) | same |
| Roads, railways, watercourses, buildings, power lines | OpenStreetMap via Overpass | OS Data Hub: MasterMap Topography and Highways (API key and licence) |
| Client data (survey, land ownership, existing assets) | — | any GeoPackage / Shapefile / GeoJSON (`type: file`) |

Adding a source is a few lines of YAML. ArcGIS Feature/Map services, OGC API Features (DEFRA DSP, OS NGD API), Overpass and local files are built in.

## Tests

`pytest` runs 34 tests offline on a synthetic study area in real BNG coordinates. It covers:
- **Sources:** ArcGIS paging, Esri JSON polygons with holes, OGC API discovery, paging and CRS fallback, Overpass parsing and coordinate order.
- **ETL:** geometry repair, clipping, optional versus required layer failures.
- **Routing:** cost surface rules, avoiding exclusions, end-point snapping, distinct alternatives, blocked corridors.
- **Analysis:** crossing chainage, angle and RAG, oblique escalation, dual carriageways as one crossing, flood-zone sections, data-gap reporting.
- **Outputs:** DXF units, layers, coordinates and chainage labels; client template layers preserved; Excel and PDF contents; the static site.
- **PostGIS:** round trip with a spatial index and `ST_Intersects`, against a real PostGIS service in CI.

The `live-demo` workflow runs the whole chain on live data weekly and on every push.

## Limits (by design, stated in every report)

- **Screening tool.** It produces candidate corridors and a screening report, not a design. Engineering, environmental and planning judgement still apply.
- **Buried services.** Existing gas, water, telecoms and electricity assets are not in open data, so a utility search is still needed.
- **Default rules.** Penalties and RAG rules are illustrative defaults, tuned per project in YAML.
- **Not yet built.** Overhead-line specifics (span lengths, tower placement, clearances) and HDD entry/exit pit siting would be extensions.
- **DWG.** DWG output needs a converter (e.g. ODA File Converter); DXF opens natively in AutoCAD.

Data: contains Natural England data © Natural England copyright and database right. © Environment Agency copyright and/or database right. © OpenStreetMap contributors (ODbL).
