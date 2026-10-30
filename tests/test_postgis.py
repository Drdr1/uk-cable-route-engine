"""PostGIS publishing. Runs when POSTGIS_URL is set (CI provides a PostGIS service)."""
import os

import geopandas as gpd
import pytest

from routeengine.etl import to_postgis

URL = os.environ.get("POSTGIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="POSTGIS_URL not set")


def test_layers_round_trip_with_spatial_index(layers):
    from sqlalchemy import create_engine, text

    to_postgis(layers, URL, schema="constraints_test")
    engine = create_engine(URL)
    back = gpd.read_postgis('select * from constraints_test.sssi', engine, geom_col="geom")
    assert len(back) == 1
    assert back.crs.to_epsg() == 27700
    assert back.geometry.iloc[0].equals_exact(layers["sssi"].geometry.iloc[0], 0.01)
    with engine.connect() as conn:
        idx = conn.execute(text("select indexname from pg_indexes where schemaname='constraints_test' and tablename='sssi'")).scalars().all()
        hits = conn.execute(text(
            "select count(*) from constraints_test.railways r "
            "where ST_Intersects(r.geom, ST_MakeEnvelope(502900, 100000, 503100, 103000, 27700))")).scalar()
    assert "sssi_geom_idx" in idx
    assert hits == 1
