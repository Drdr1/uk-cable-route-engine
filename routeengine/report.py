"""Red-Amber-Green Risk & Crossings Report in Excel and PDF, plus an overview map."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4, landscape  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

from .analysis import RouteAssessment  # noqa: E402
from .config import Project  # noqa: E402
from .dxf_export import chainage_text  # noqa: E402
from .etl import LayerStatus  # noqa: E402

RAG_HEX = {"RED": "D64545", "AMBER": "F0A030", "GREEN": "3A9D5D"}
LAYER_STYLE = {
    "sssi": ("#7fbf7f", "SSSI"),
    "ancient_woodland": ("#2e6b3a", "Ancient woodland"),
    "flood_zone_3": ("#8ec5ff", "Flood Zone 3"),
    "flood_zone_2": ("#cfe6ff", "Flood Zone 2"),
    "buildings": ("#9a9a9a", "Buildings"),
}
LINE_STYLE = {
    "roads": ("#c08a3e", 0.6, "Roads"),
    "railways": ("#333333", 1.2, "Railways"),
    "waterways": ("#3a7bd5", 0.8, "Watercourses"),
    "power_lines": ("#b03aa0", 0.8, "Power lines"),
}
ROUTE_COLORS = ["#d62728", "#1f77b4", "#ff7f0e", "#9467bd"]


def overview_map(project: Project, assessments: list[RouteAssessment], layers, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(11, 8.2), dpi=150)
    handles = []
    for name, (color, label) in LAYER_STYLE.items():
        gdf = layers.get(name)
        if gdf is not None and not gdf.empty:
            gdf.plot(ax=ax, color=color, linewidth=0, alpha=0.85 if name != "flood_zone_3" else 0.6, zorder=1)
            handles.append(Patch(color=color, label=label))
    for name, (color, width, label) in LINE_STYLE.items():
        gdf = layers.get(name)
        if gdf is not None and not gdf.empty:
            gdf.plot(ax=ax, color=color, linewidth=width, zorder=2)
            handles.append(Line2D([], [], color=color, linewidth=width * 2, label=label))
    for a in sorted(assessments, key=lambda a: -a.route.rank):
        x, y = a.route.line.xy
        main = a.route.rank == 1
        color = ROUTE_COLORS[(a.route.rank - 1) % len(ROUTE_COLORS)]
        ax.plot(x, y, color=color, linewidth=3.2 if main else 1.8, linestyle="-" if main else "--", zorder=5)
        handles.append(Line2D([], [], color=color, linewidth=3 if main else 1.8, linestyle="-" if main else "--",
                              label=f"Route {a.route.rank}{' (recommended)' if main else ''}: "
                                    f"{a.route.length_m / 1000:.2f} km, {a.rag}"))
    main = assessments[0]
    for c in main.crossings:
        ax.scatter(c.x, c.y, s=36, color="#" + RAG_HEX[c.rag], edgecolor="black", linewidth=0.5, zorder=6)
    for (x, y), label in ((project.start, project.start_label), (project.end, project.end_label)):
        ax.scatter(x, y, s=120, marker="s", color="black", zorder=7)
        ax.annotate(label, (x, y), xytext=(8, 8), textcoords="offset points", fontsize=8, weight="bold", zorder=7)
    minx, miny, maxx, maxy = project.bbox
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("equal")
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.tick_params(labelsize=7)
    ax.set_xlabel("Easting (m, EPSG:27700)", fontsize=8)
    ax.set_ylabel("Northing (m)", fontsize=8)
    ax.set_title(project.name, fontsize=11, weight="bold")
    ax.legend(handles=handles, loc="lower left", fontsize=7, framealpha=0.92)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _sheet(wb, title: str, headers: list[str], rows: list[list], rag_col: int | None = None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F3E4E")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        ws.append(row)
        if rag_col is not None and row[rag_col] in RAG_HEX:
            c = ws.cell(row=ws.max_row, column=rag_col + 1)
            c.fill = PatternFill("solid", fgColor=RAG_HEX[row[rag_col]])
            c.font = Font(bold=True, color="FFFFFF")
    for i, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if r[i - 1] is not None] or [10])
        ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 60)
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    return ws


def _summary_rows(assessments: list[RouteAssessment]) -> list[list]:
    rows = []
    for a in assessments:
        types = a.crossings_by_type()
        rows.append([a.route.rank, "Yes" if a.route.rank == 1 else "", round(a.route.length_m, 0), round(a.route.cost, 0),
                     len(a.crossings), ", ".join(f"{k}: {v}" for k, v in sorted(types.items())),
                     a.count("RED"), a.count("AMBER"), a.rag])
    return rows


def write_excel(project: Project, assessments: list[RouteAssessment], manifest: list[LayerStatus], path: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    _sheet(wb, "Summary",
           ["Route", "Recommended", "Length (m)", "Weighted cost", "Crossings", "By type", "RED items", "AMBER items", "Overall RAG"],
           _summary_rows(assessments), rag_col=8)
    _sheet(wb, "Crossings",
           ["Route", "Ref", "Type", "Class", "Name / ref", "Chainage", "Chainage (m)", "Angle (deg)", "RAG", "Reason",
            "Easting", "Northing"],
           [[a.route.rank, c.ref, c.type, c.cls, c.name, chainage_text(c.chainage), c.chainage, c.angle, c.rag, c.reason,
             c.x, c.y] for a in assessments for c in a.crossings], rag_col=8)
    _sheet(wb, "Constraints",
           ["Route", "Category", "Item", "Detail", "From (m)", "To (m)", "Value", "RAG"],
           [[a.route.rank, f.category, f.item, f.detail, f.from_ch, f.to_ch, f.value, f.rag]
            for a in assessments for f in a.findings], rag_col=7)
    _sheet(wb, "Data sources",
           ["Layer", "Status", "Features", "Source", "Attribution", "Fetched (UTC)", "Error"],
           [[s.name, s.status, s.features, s.source, s.attribution, s.fetched_at, s.error] for s in manifest])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _table(data, widths, rag_col: int | None = None, font_size: float = 7.5):
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F3E4E")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C0CC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6F8")]),
    ]
    if rag_col is not None:
        for i, row in enumerate(data[1:], start=1):
            if row[rag_col] in RAG_HEX:
                style += [("BACKGROUND", (rag_col, i), (rag_col, i), colors.HexColor("#" + RAG_HEX[row[rag_col]])),
                          ("TEXTCOLOR", (rag_col, i), (rag_col, i), colors.white),
                          ("FONTNAME", (rag_col, i), (rag_col, i), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    return t


def write_pdf(project: Project, assessments: list[RouteAssessment], manifest: list[LayerStatus],
              map_png: Path, path: Path) -> Path:
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=7.5, leading=9.5)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm, title=f"RAG Risk & Crossings Report: {project.name}")
    main = assessments[0]
    story = [
        Paragraph(f"RAG Risk &amp; Crossings Report: {project.name}", styles["Title"]),
        Paragraph(f"{project.description}<br/>{project.start_label} to {project.end_label}. "
                  f"Generated {date.today().isoformat()}. Coordinates: British National Grid (EPSG:27700).", small),
        Spacer(1, 4 * mm),
        Paragraph("Route comparison", styles["Heading3"]),
        _table([["Route", "Recommended", "Length (m)", "Weighted cost", "Crossings", "By type", "RED", "AMBER", "RAG"]]
               + [[str(v) for v in r] for r in _summary_rows(assessments)],
               [16 * mm, 24 * mm, 22 * mm, 26 * mm, 20 * mm, 90 * mm, 14 * mm, 16 * mm, 18 * mm], rag_col=8),
        Spacer(1, 4 * mm),
        Image(str(map_png), width=140 * mm, height=104 * mm),
        PageBreak(),
        Paragraph(f"Crossings: recommended route ({main.route.length_m / 1000:.2f} km)", styles["Heading3"]),
    ]
    rows = [["Ref", "Chainage", "Type", "Class", "Name / ref", "Angle", "RAG", "Reason"]]
    rows += [[c.ref, chainage_text(c.chainage), c.type, c.cls, Paragraph(c.name or "-", small), f"{c.angle:.0f} deg",
              c.rag, Paragraph(c.reason, small)] for c in main.crossings]
    story.append(_table(rows, [14 * mm, 20 * mm, 26 * mm, 28 * mm, 50 * mm, 16 * mm, 16 * mm, 100 * mm], rag_col=6))
    story += [Spacer(1, 4 * mm), Paragraph("Constraints, proximity, design and data checks: recommended route", styles["Heading3"])]
    rows = [["Category", "Item", "Detail", "From", "To", "RAG"]]
    rows += [[f.category, Paragraph(f.item, small), Paragraph(f.detail, small),
              chainage_text(f.from_ch) if f.from_ch is not None else "-",
              chainage_text(f.to_ch) if f.to_ch is not None else "-", f.rag] for f in main.findings]
    if len(rows) == 1:
        rows.append(["-", "No constraint findings", "", "", "", "GREEN"])
    story.append(_table(rows, [22 * mm, 60 * mm, 110 * mm, 20 * mm, 20 * mm, 18 * mm], rag_col=5))
    story += [Spacer(1, 4 * mm), Paragraph("Data sources", styles["Heading3"]),
              _table([["Layer", "Status", "Features", "Attribution", "Fetched (UTC)"]]
                     + [[s.name, s.status, str(s.features), Paragraph(s.attribution or "-", small), s.fetched_at]
                        for s in manifest],
                     [36 * mm, 22 * mm, 20 * mm, 140 * mm, 40 * mm]),
              Spacer(1, 3 * mm),
              Paragraph("<b>Limitations.</b> Desk-based screening only. Existing buried services (gas, water, "
                        "telecoms, other electricity assets) are not in open data: a utility search and statutory "
                        "undertaker enquiries are required. RAG rules are configurable defaults, not a substitute "
                        "for engineering, environmental or planning judgement.", small)]
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.build(story)
    return path
