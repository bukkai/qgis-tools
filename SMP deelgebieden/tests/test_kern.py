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
sys.path.insert(0, os.path.join(HIER, "..", "smp_deelgebieden"))
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


def test_lees_overpass_multipolygoon():
    """Flat met binnenplaats: buitenrand uit twee ways, binnenring als gat."""
    def punten(*cs):
        return [{"lon": x, "lat": y} for x, y in cs]
    data = {"elements": [{"type": "relation", "id": 9, "tags": {"building": "apartments", "type": "multipolygon"},
                          "members": [
                              {"type": "way", "role": "outer", "geometry": punten((0, 0), (10, 0), (10, 10))},
                              {"type": "way", "role": "outer", "geometry": punten((10, 10), (0, 10), (0, 0))},
                              {"type": "way", "role": "inner", "geometry": punten((4, 4), (6, 4), (6, 6), (4, 6), (4, 4))}]}]}
    r = kern.lees_overpass_vlakken(data)
    assert len(r) == 1 and r[0]["building"] == "apartments"
    assert r[0]["geometry"].area == pytest.approx(100 - 4)
    assert "relation" in kern.overpass_query(51, 5, 52, 6, "building")
    assert "relation" not in kern.overpass_query(51, 5, 52, 6, "highway")


def test_overpass_fout():
    assert kern.overpass_fout({"elements": [{"type": "way"}]}) is None
    assert kern.overpass_fout({"elements": [], "remark": "runtime error: Query timed out in \"query\""})
    # gedeeltelijk antwoord na een timeout: niet stil accepteren
    assert kern.overpass_fout({"elements": [{"type": "way"}], "remark": "runtime error: Query run out of memory"})


def test_voronoi_zonder_ordered(monkeypatch):
    """shapely < 2.1 kent voronoi_polygons(ordered=...) niet; de terugval moet dezelfde cellen geven."""
    import shapely
    from shapely.geometry import box
    pts = np.random.default_rng(1).uniform(0, 1000, (300, 2))
    rand = box(-500, -500, 1500, 1500)
    verwacht = kern._voronoi_cellen(pts, rand)
    origineel = shapely.voronoi_polygons

    def oud(geom, tolerance=0.0, extend_to=None, only_edges=False, **kw):
        if "ordered" in kw:
            raise TypeError("voronoi_polygons() got an unexpected keyword argument 'ordered'")
        return origineel(geom, tolerance=tolerance, extend_to=extend_to, only_edges=only_edges)

    monkeypatch.setattr(shapely, "voronoi_polygons", oud)
    terugval = kern._voronoi_cellen(pts, rand)
    assert len(terugval) == len(pts)
    assert all(a.equals(b) for a, b in zip(verwacht, terugval))
    assert all(c.contains(shapely.Point(p)) for c, p in zip(terugval, pts))


def _vierkant(extra):
    from shapely.geometry import LineString
    hoeken = [(0, 0), (500, 0), (500, 500), (0, 500), (0, 0)]
    net = [dict(id=i, status="verplicht", gewicht=1, naam=f"straat {i}", geometry=LineString([a, b]))
           for i, (a, b) in enumerate(zip(hoeken, hoeken[1:]), 1)]
    return net + [dict(id=None, status="verplicht", gewicht=1, naam="steeg", geometry=LineString(extra))]


def test_waarschuwing_bijna_verbonden():
    """Een lijn die 0,3 m naast een kruispunt eindigt, is niet verbonden: dat moet gemeld worden."""
    from shapely.geometry import box
    meldingen = []
    kern.indelen(_vierkant([(0.3, 0.0), (499.7, 500.0)]), box(-10, -10, 510, 510), 7, log=meldingen.append)
    assert any("niet mee verbonden" in m for m in meldingen)
    meldingen.clear()
    kern.indelen(_vierkant([(0, 0), (500, 500)]), box(-10, -10, 510, 510), 7, log=meldingen.append)
    assert not any("niet mee verbonden" in m for m in meldingen)


def test_waarschuwing_te_lang_stuk():
    """Eén doodlopend stuk dat heen en terug al langer is dan het maximum: melden, niet stil doorgaan."""
    from shapely.geometry import LineString, box
    net = [dict(id=1, status="verplicht", gewicht=1, naam="Lange Dijk", geometry=LineString([(0, 0), (4000, 0)]))]
    meldingen = []
    uit = kern.indelen(net, box(-10, -10, 4010, 10), 7, log=meldingen.append)
    assert uit["samenvatting"][0]["route_km"] > 7
    assert any("langer dan het maximum" in m and "Lange Dijk" in m for m in meldingen)


def test_bijna_verbonden_testgebied(netwerk):
    """Op het echte testgebied mag de melding niet bij elk stukje afgaan."""
    nw = kern.Netwerk(kern.bereid_netwerk([dict(r) for r in netwerk], log=lambda *_: None))
    assert len(kern.bijna_verbonden(nw, 1.0)) < 25


@pytest.mark.traag
@pytest.mark.parametrize("met_grenslijn", [False, True])
def test_indelen_invarianten(netwerk, grens, met_grenslijn):
    gl = None
    if met_grenslijn:
        gl = list(geopandas.read_file(os.path.join(HIER, "..", "testdata", "grenslijn_rivierweg.gpkg"))
                  .to_crs(CRS).geometry)
    max_km = 7.0
    uit = kern.indelen([dict(r) for r in netwerk], grens, max_km, gladheid=300, vereenvoudig=50,
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
