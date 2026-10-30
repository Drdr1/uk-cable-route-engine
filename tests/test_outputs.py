"""End to end on the synthetic area: fetch (from files) -> route -> DXF / Excel / PDF / site."""
import json

import ezdxf
import pytest
from openpyxl import load_workbook

from routeengine import cli, site


@pytest.fixture
def run(project_path, tmp_path):
    rc = cli.main(["all", "--project", str(project_path), "--data", str(tmp_path / "data"), "--out", str(tmp_path / "out")])
    assert rc == 0
    return tmp_path / "out" / project_path.stem


def test_outputs_exist(run):
    for name in ("routes.dxf", "rag-report.xlsx", "rag-report.pdf", "overview.png", "summary.json",
                 "routes.geojson", "crossings.geojson"):
        assert (run / name).stat().st_size > 0, name
    summary = json.loads((run / "summary.json").read_text())
    assert summary["routes"][0]["rank"] == 1
    assert summary["layers"]["sssi"]["status"] == "ok"


def test_dxf_is_in_bng_metres_with_cad_standard_layers(run):
    doc = ezdxf.readfile(run / "routes.dxf")
    assert doc.header["$INSUNITS"] == 6
    layers = {l.dxf.name for l in doc.layers}
    assert {"E-132KV-UGC-ROUTE", "E-132KV-UGC-SWATHE", "E-132KV-UGC-CHAINAGE", "E-132KV-UGC-CROSSINGS",
            "C-SSSI", "C-RAILWAYS"} <= layers
    msp = doc.modelspace()
    [route] = msp.query('LWPOLYLINE[layer=="E-132KV-UGC-ROUTE"]')
    pts = list(route.get_points("xy"))
    assert all(500000 <= x <= 504000 and 100000 <= y <= 103000 for x, y in pts)
    summary = json.loads((run / "summary.json").read_text())
    assert len(msp.query('CIRCLE[layer=="E-132KV-UGC-CROSSINGS"]')) == summary["routes"][0]["crossings"]
    labels = [t.dxf.text for t in msp.query('TEXT[layer=="E-132KV-UGC-CHAINAGE"]')]
    assert "CH 0+000" in labels and "CH 0+500" in labels
    assert doc.layers.get("E-132KV-UGC-SWATHE").dxf.linetype == "DASHED"


def test_excel_report_sheets_and_rag(run):
    wb = load_workbook(run / "rag-report.xlsx")
    assert wb.sheetnames == ["Summary", "Crossings", "Constraints", "Data sources"]
    crossings = list(wb["Crossings"].iter_rows(min_row=2, values_only=True))
    assert any(r[2] == "Railway" and r[8] == "RED" for r in crossings)
    summary = list(wb["Summary"].iter_rows(min_row=2, values_only=True))
    assert summary[0][1] == "Yes"
    sources = {r[0]: r[1] for r in wb["Data sources"].iter_rows(min_row=2, values_only=True)}
    assert sources["power_lines"] == "unavailable"


def test_pdf_report(run):
    data = (run / "rag-report.pdf").read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 50_000  # includes the overview map


def test_static_site(run, project_path, tmp_path):
    index = site.build(run, tmp_path / "site", project_path)
    html = index.read_text()
    assert "Ranked corridors" in html and "routes.dxf" in html
    assert (tmp_path / "site" / "routes.geojson").exists()


def test_cad_template_is_respected(project, layers, tmp_path):
    from dataclasses import replace

    from routeengine import analysis, costsurface, dxf_export, router

    template = tmp_path / "client-template.dxf"
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("CLIENT-TITLEBLOCK", color=2)
    doc.saveas(template)
    proj = replace(project, cad={**project.cad, "template": str(template)})
    grid = costsurface.build_grid(proj, layers)
    routes = router.find_routes(grid, proj.start, proj.end, 1, 4.0, 300)
    out = dxf_export.export_dxf(proj, analysis.assess(proj, routes, layers, []), layers, tmp_path / "r.dxf")
    assert "CLIENT-TITLEBLOCK" in {l.dxf.name for l in ezdxf.readfile(out).layers}
