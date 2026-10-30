"""Turn constraint layers into a cost surface on a regular BNG grid.

Each cell's cost = base 1.0
  x multiplier of every `multiply` rule that covers it (flood zones, SSSI buffers ...)
  + crossing penalty of every linear feature drawn through it (roads, rail, rivers),
  or infinity where an `exclude` rule applies (SSSI, ancient woodland, buildings).

Crossing penalties are charged per cell traversed, so a perpendicular crossing
(1-2 cells) is cheap and an oblique one (many cells) is expensive: the optimiser
naturally prefers short, square crossings, as an engineer would.
"""
from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
from affine import Affine
from rasterio.features import rasterize

from .config import Project


@dataclass
class Grid:
    cost: np.ndarray  # float64, rows north -> south, np.inf = impassable
    transform: Affine
    resolution: float
    exclusion: np.ndarray  # bool mask of excluded cells

    @property
    def shape(self) -> tuple[int, int]:
        return self.cost.shape

    def to_cell(self, x: float, y: float) -> tuple[int, int]:
        col, row = ~self.transform @ (x, y)
        return int(row), int(col)

    def to_xy(self, row: int, col: int) -> tuple[float, float]:
        x, y = self.transform @ (col + 0.5, row + 0.5)
        return x, y


def _select(gdf: gpd.GeoDataFrame, classes: tuple[str, ...]) -> gpd.GeoDataFrame:
    if gdf.empty or not classes:
        return gdf
    return gdf[gdf["cls"].isin(classes)]


def _mask(geoms, shape, transform, all_touched: bool) -> np.ndarray:
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return np.zeros(shape, dtype=bool)
    return rasterize(((g, 1) for g in geoms), out_shape=shape, transform=transform,
                     fill=0, all_touched=all_touched, dtype="uint8").astype(bool)


def build_grid(project: Project, layers: dict[str, gpd.GeoDataFrame]) -> Grid:
    minx, miny, maxx, maxy = project.bbox
    res = project.resolution_m
    width = int(np.ceil((maxx - minx) / res))
    height = int(np.ceil((maxy - miny) / res))
    transform = Affine(res, 0, minx, 0, -res, maxy)
    shape = (height, width)

    cost = np.ones(shape, dtype=np.float64)
    exclusion = np.zeros(shape, dtype=bool)

    for rule in project.cost_rules:
        gdf = _select(layers.get(rule.layer, gpd.GeoDataFrame()), rule.classes)
        if gdf is None or gdf.empty:
            continue
        geoms = gdf.geometry.buffer(rule.buffer_m) if rule.buffer_m else gdf.geometry
        mask = _mask(geoms, shape, transform, all_touched=rule.mode == "exclude")
        if rule.mode == "exclude":
            exclusion |= mask
        elif rule.mode == "multiply":
            cost[mask] *= rule.factor
        else:
            raise ValueError(f"unknown cost rule mode {rule.mode}")

    for rule in project.crossing_rules:
        gdf = layers.get(rule.layer)
        if gdf is None or gdf.empty:
            continue
        # Features are drawn as a band (default 30 m) so an oblique crossing passes through many more
        # penalised cells than a square one; all_touched keeps the band continuous for diagonal moves.
        half_band = project.crossing_band_m / 2
        for cls, group in gdf.groupby("cls"):
            penalty = rule.penalty_for(str(cls))
            if penalty:
                geoms = group.geometry.buffer(half_band) if half_band else group.geometry
                cost[_mask(geoms, shape, transform, all_touched=True)] += penalty

    cost[exclusion] = np.inf
    return Grid(cost=cost, transform=transform, resolution=res, exclusion=exclusion)
