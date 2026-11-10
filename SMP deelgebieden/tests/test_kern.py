"""
Tests voor de rekenkern op het testgebied (Westervoort, OSM-data).

Draaien:  pip install -r requirements-dev.txt && pytest -q
De volledige indeling duurt een paar minuten (markering 'traag'); snel:  pytest -q -m "not traag"

Wat we garanderen (invarianten):
  - elke verplichte straat zit in precies één deelgebied en wordt in de route precies één keer 'tellen'
  - geen enkele ronde is langer dan max_km
  - elke route is een gesloten, aaneengesloten rit
  - deelgebied-vlakken overlappen niet en liggen binnen de grens
"""
import os, sys

import numpy as np
import pytest

geopandas = pytest.importorskip("geopandas")
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "ekoza_fietsrondes"))
import fietsronde_kern as kern  # noqa: E402

GPKG = os.path.join(HIER, "..", "testdata", "westervoort_osm.gpkg")
CRS = 28992


def _laag(naam):
    return geopandas.read_file(GPKG, layer=naam).to_crs(CRS)


@pytest.fixture(scope="module")
def grens():
    return _laag("gangstervoort").union_all()


@pytest.fixture(scope="module")
def netwerk(grens):
    wegen = _laag("highway").to_dict("records")
    gebouwen = _laag("building").to_dict("records")
    return kern.maak_netwerk(grens, wegen, gebouwen=gebouwen, log=lambda *_: None)


def test_netwerk_statussen(netwerk):
    som = {}
    for r in netwerk:
        som[r["status"]] = som.get(r["status"], 0) + r["lengte"]
    assert set(som) <= {"verplicht", "optioneel", "uit"}
    # referentie 10-11-2026: verplicht 74,1 km, optioneel 51,4 km, uit 11,5 km
    assert 65_000 < som["verplicht"] < 85_000


def test_rivierweg_geen_snippers(netwerk):
    """Regressie: de Rivierweg (weinig bebouwing, gescheiden rijbanen) gaf losse verplichte stukjes."""
    fietspad = [r for r in netwerk if r["naam"] == "Rivierweg" and r["highway"] == "cycleway"]
    assert fietspad and all(r["status"] != "verplicht" for r in fietspad)


def test_classificeer():
    assert kern.classificeer({"highway": "residential"})[0] == "verplicht"
    assert kern.classificeer({"highway": "service", "service": "alley"})[0] == "verplicht"
    assert kern.classificeer({"highway": "footway", "footway": "sidewalk"})[0] == "uit"
    assert kern.classificeer({"highway": "residential", "access": "private"})[0] == "uit"
    assert kern.classificeer({"highway": "footway", "bicycle": "no"})[1] >= 2


def test_lees_overpass():
    data = {"elements": [
        {"type": "way", "id": 1, "tags": {"highway": "residential"},
         "geometry": [{"lat": 51.96, "lon": 5.97}, {"lat": 51.961, "lon": 5.971}]},
        {"type": "way", "id": 2, "tags": {"building": "house"},
         "geometry": [{"lat": 51.96, "lon": 5.97}, {"lat": 51.9601, "lon": 5.97},
                      {"lat": 51.9601, "lon": 5.9702}, {"lat": 51.96, "lon": 5.97}]}]}
    assert kern.lees_overpass(data)[0]["geometry"].geom_type == "LineString"
    assert kern.lees_overpass_vlakken(data)[0]["geometry"].geom_type == "Polygon"


@pytest.mark.traag
@pytest.mark.parametrize("met_grenslijn", [False, True])
def test_indelen_invarianten(netwerk, grens, met_grenslijn):
    gl = None
    if met_grenslijn:
        gl = list(geopandas.read_file(os.path.join(HIER, "..", "testdata", "grenslijn_rivierweg.gpkg"))
                  .to_crs(CRS).geometry)
    max_km = 7.0
    uit = kern.indelen([dict(r) for r in netwerk], grens, max_km, gladheid=150, vereenvoudig=15,
                       grenslijnen=gl, log=lambda *_: None)

    # 1. dekking: elke (niet-losse) verplichte straat precies één keer 'tellen'
    verplicht = {r["id"] for r in uit["netwerk"] if r["status"] == "verplicht" and not r["los"]}
    tellen = [r["netwerk_id"] for r in uit["route"] if r["soort"] == "tellen"]
    assert set(tellen) == verplicht
    assert len(tellen) == len(set(tellen))

    # 2. lengte
    assert all(s["route_km"] <= max_km + 1e-6 for s in uit["samenvatting"])

    # 3. gesloten, aaneengesloten routes
    for r in uit["route_lijn"]:
        cs = np.asarray(r["geometry"].coords)
        assert np.hypot(*(cs[0] - cs[-1])) < 0.05
    per_gebied = {}
    for r in sorted(uit["route"], key=lambda r: (r["gebied"], r["volgnr"])):
        per_gebied.setdefault(r["gebied"], []).append(r["geometry"])
    for stukken in per_gebied.values():
        for a, b in zip(stukken, stukken[1:]):
            assert np.hypot(*(np.subtract(a.coords[-1], b.coords[0]))) < 0.05

    # 4. vlakken
    vl = [d["geometry"] for d in uit["deelgebieden"]]
    totaal = sum(v.area for v in vl)
    import shapely
    assert totaal - shapely.union_all(vl).area < 1.0          # geen overlap (m²)
    assert shapely.union_all(vl).difference(grens.buffer(1)).area < 1.0
