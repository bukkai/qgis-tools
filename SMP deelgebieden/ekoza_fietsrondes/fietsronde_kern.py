"""
fietsronde_kern.py - rekenkern voor het indelen van een projectgebied in fietsrondes.

Wordt gebruikt door de QGIS-plugin (Processing Toolbox) en door de losse scripts
stap1_netwerk.py / stap2_indelen.py. Heeft alleen shapely (>= 2), numpy en networkx nodig.

Een "record" is hier een gewone dict met attributen + 'geometry' (shapely, in meters).

  maak_netwerk(grens, wegen, ...)       -> netwerk-records (status verplicht/optioneel/uit)
  indelen(netwerk, grens, max_km, ...)  -> deelgebieden, routes, startpunten, samenvatting
  lees_overpass(json)                   -> wegen-records (lon/lat) uit een Overpass-antwoord
"""
import heapq, time
from collections import Counter, defaultdict

import numpy as np
import shapely
import shapely.ops
import networkx as nx
from shapely.geometry import LineString, MultiPoint, Point


class Afgebroken(Exception):
    pass


def _niets(*a, **k):
    pass


# ==========================================================================================
# Standaardkeuze per OSM-wegtype: (status, gewicht). Pas aan naar wat jullie willen tellen.
#   verplicht : moet geteld worden
#   optioneel : mag gebruikt worden om ergens te komen
#   uit       : doet niet mee
#   gewicht   : 1 = normaal; hoger = liever vermijden (telt bij het routekiezen als langer)
# ==========================================================================================
TYPES = {
    "residential": ("verplicht", 1), "living_street": ("verplicht", 1), "pedestrian": ("verplicht", 1),
    "unclassified": ("verplicht", 1), "tertiary": ("verplicht", 1), "secondary": ("verplicht", 1),
    "primary": ("verplicht", 1), "road": ("verplicht", 1),
    "service": ("optioneel", 1), "cycleway": ("optioneel", 1), "path": ("optioneel", 1),
    "footway": ("optioneel", 1.3), "track": ("optioneel", 1.2), "bridleway": ("optioneel", 1.3),
    "primary_link": ("optioneel", 1), "secondary_link": ("optioneel", 1), "tertiary_link": ("optioneel", 1),
    "steps": ("optioneel", 5),
    "motorway": ("uit", 1), "motorway_link": ("uit", 1), "trunk": ("uit", 1), "trunk_link": ("uit", 1),
    "platform": ("uit", 1), "construction": ("uit", 1), "proposed": ("uit", 1), "raceway": ("uit", 1),
    "bus_guideway": ("uit", 1), "corridor": ("uit", 1), "elevator": ("uit", 1), "busway": ("uit", 1),
}
ONBEKEND = ("optioneel", 1.5)


def _tag(r, k):
    v = r.get(k)
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def classificeer(r):
    """(status, gewicht, reden) voor één OSM-way (dict met tags)."""
    hw = _tag(r, "highway")
    status, gewicht = TYPES.get(hw, ONBEKEND)
    reden = f"type {hw}"
    t = lambda k: _tag(r, k).lower()
    if t("service") == "alley" or t("footway") == "alley":
        return "verplicht", 1.0, "steeg"
    if t("footway") in ("sidewalk", "traffic_island") or t("path") == "sidewalk":
        return "uit", float(gewicht), "stoep (ligt naast straat)"
    if t("area") == "yes":
        return "uit", float(gewicht), "vlak (area=yes)"
    if t("access") in ("private", "no") and status != "uit":
        return "uit", float(gewicht), f"access={t('access')}"
    if t("bicycle") in ("no", "dismount") and status != "uit":
        gewicht = max(gewicht, 2)
        reden += f", bicycle={t('bicycle')}"
    return status, float(gewicht), reden


def lees_overpass(data):
    """Overpass-JSON ('out tags geom') -> records met tags en LineString in lon/lat."""
    uit = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        cs = [(p["lon"], p["lat"]) for p in el["geometry"] if p]
        if len(cs) < 2:
            continue
        r = dict(el.get("tags", {}))
        r["osm_id"] = el.get("id")
        r["geometry"] = LineString(cs)
        uit.append(r)
    return uit


def overpass_query(zuid, west, noord, oost, sleutel="highway"):
    return (f'[out:json][timeout:180];way["{sleutel}"]({zuid:.6f},{west:.6f},{noord:.6f},{oost:.6f});'
            f'out tags geom;')


def lees_overpass_vlakken(data):
    """Overpass-JSON -> records met tags en Polygon in lon/lat (alleen gesloten ways)."""
    from shapely.geometry import Polygon
    uit = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        cs = [(p["lon"], p["lat"]) for p in el["geometry"] if p]
        if len(cs) < 4 or cs[0] != cs[-1]:
            continue
        r = dict(el.get("tags", {}))
        r["osm_id"] = el.get("id")
        r["geometry"] = Polygon(cs)
        uit.append(r)
    return uit


# ==========================================================================================
# Stap 1: netwerk
# ==========================================================================================
def rond(c, d=2):
    return (round(c[0], d), round(c[1], d))


def _lijnen(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    if hasattr(g, "geoms"):
        return [x for p in g.geoms for x in _lijnen(p)]
    return []


def _splits_op_knopen(recs):
    gebruik = Counter()
    for r in recs:
        cs = [rond(c) for c in r["geometry"].coords]
        for c in set(cs[1:-1]):
            gebruik[c] += 1
        gebruik[cs[0]] += 2
        gebruik[cs[-1]] += 2
        for c, n in Counter(cs).items():
            if n > 1:
                gebruik[c] += 2
    uit = []
    for r in recs:
        cs = list(r["geometry"].coords)
        deel = [cs[0]]
        for c in cs[1:]:
            deel.append(c)
            if gebruik[rond(c)] >= 2 and len(deel) >= 2:
                lijn = LineString(deel)
                if lijn.length > 0:
                    n = dict(r)
                    n["geometry"] = lijn
                    uit.append(n)
                deel = [c]
    return uit


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _voeg_knopen_samen(seg, tol):
    eind = [(rond(r["geometry"].coords[0]), rond(r["geometry"].coords[-1])) for r in seg]
    uf = _UF()
    alle = sorted({p for e in eind for p in e})
    for p in alle:
        uf.find(p)
    rot = [_tag(r, "junction").lower() in ("roundabout", "circular") for r in seg]
    for i, r in enumerate(seg):
        if rot[i] and r["status"] != "uit":
            uf.union(*eind[i])
    bruikbaar = sorted({p for i, e in enumerate(eind) if seg[i]["status"] != "uit" for p in e})
    if bruikbaar:
        pts = shapely.points(np.array(bruikbaar))
        a, b = shapely.STRtree(pts).query(pts, predicate="dwithin", distance=tol)
        for i, j in zip(a, b):
            if i < j:
                uf.union(bruikbaar[i], bruikbaar[j])
    leden = defaultdict(list)
    for p in alle:
        leden[uf.find(p)].append(p)
    midden = {k: tuple(np.mean(v, axis=0)) for k, v in leden.items()}
    n_weg = 0
    for i, r in enumerate(seg):
        u, v = eind[i]
        cu, cv = uf.find(u), uf.find(v)
        g = r["geometry"]
        if cu == cv and (rot[i] or g.length < 3 * tol) and r["status"] != "uit":
            r["status"], r["reden"] = "uit", "binnen samengevoegd kruispunt"
            n_weg += 1
            continue
        cs = list(g.coords)
        cs[0], cs[-1] = midden[cu], midden[cv]
        nieuw = LineString(cs)
        if nieuw.length < 0.01 or (len(cs) == 2 and cu == cv):
            r["status"], r["reden"] = "uit", "binnen samengevoegd kruispunt"   # niets over
            continue
        r["geometry"] = nieuw
    return sum(1 for v in leden.values() if len(v) > 1), n_weg


def _dubbele_rijbanen(seg, afstand):
    kand = [i for i, r in enumerate(seg) if r["status"] == "verplicht" and r["naam"] and
            _tag(r, "oneway").lower() == "yes"]
    if not kand:
        return 0
    geoms = [seg[i]["geometry"] for i in kand]
    boom = shapely.STRtree(geoms)
    gedaan, n = set(), 0
    for pi, i in enumerate(kand):
        if i in gedaan:
            continue
        g1 = seg[i]["geometry"]
        buf1 = g1.buffer(afstand, cap_style="flat")
        for pj in boom.query(buf1, predicate="intersects"):
            k = kand[pj]
            if k == i or k in gedaan or seg[k]["naam"] != seg[i]["naam"]:
                continue
            g2 = seg[k]["geometry"]
            f1 = g1.intersection(g2.buffer(afstand, cap_style="flat")).length / g1.length
            f2 = g2.intersection(buf1).length / g2.length
            if f1 > 0.6 and f2 > 0.6:
                weg = k if g2.length >= g1.length else i
                seg[weg]["status"], seg[weg]["reden"] = "optioneel", "tweede rijbaan"
                gedaan.update((i, k))
                n += 1
                break
    return n


# Bebouwing: welke gebouwen tellen mee, en welke paden kunnen verplicht worden
GEEN_GEBOUW = {"shed", "garage", "garages", "carport", "greenhouse", "hut", "roof", "ruins", "construction",
               "bunker", "container", "kiosk", "toilets", "transformer_tower", "service", "static_caravan",
               "bicycle_parking", "farm_auxiliary", "barn", "stable", "cowshed"}
STRAATTYPEN = {"residential", "living_street", "pedestrian", "unclassified", "tertiary", "secondary",
               "primary", "road"}
PADTYPEN = {"footway", "path", "cycleway", "service", "track", "bridleway"}
GEEN_PAD = {"parking_aisle", "driveway", "drive-through", "emergency_access"}


def _bebouwing(seg, gebouwen, afstand, min_opp, drempel, parallel, log):
    """Status op basis van bebouwing: verplicht = langs bebouwing; straten zonder bebouwing en
    paden die naast een getelde straat lopen worden optioneel."""
    polys = []
    for r in gebouwen:
        g = r.get("geometry")
        if g is None or g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        if _tag(r, "building").lower() in GEEN_GEBOUW or g.area < min_opp:
            continue
        polys.append(g)
    log(f"{len(polys)} gebouwen gebruikt (van {len(gebouwen)}; schuurtjes, garages en < {min_opp:g} m² tellen niet)")
    if not polys:
        log("LET OP: geen bruikbare gebouwen - status blijft op basis van wegtype")
        return
    zone = shapely.union_all(shapely.buffer(polys, afstand, quad_segs=4))
    shapely.prepare(zone)
    geoms = [r["geometry"] for r in seg]
    lengtes = shapely.length(geoms)
    frac = shapely.length(shapely.intersection(geoms, zone)) / np.maximum(lengtes, 1e-9)
    for r, f in zip(seg, frac):
        r["bebouwd"] = round(float(f), 2)

    # 1. straten: verplicht als er bebouwing langs staat
    for r in seg:
        if r["status"] == "uit" or r["reden"] == "steeg":
            continue
        if _tag(r, "highway") in STRAATTYPEN:
            if r["bebouwd"] >= drempel:
                r["status"], r["reden"] = "verplicht", "straat langs bebouwing"
            else:
                r["status"], r["reden"] = "optioneel", "straat zonder bebouwing"
    # 2. paden langs bebouwing: verplicht, tenzij er al een getelde straat naast loopt
    # elke straat telt hier (ook zonder bebouwing): een fietspad naast een straat volgt de straat
    straten = [r["geometry"] for r in seg if r["status"] != "uit" and _tag(r, "highway") in STRAATTYPEN]
    naast = shapely.union_all(shapely.buffer(straten, parallel, cap_style="flat")) if straten else None
    if naast is not None:
        shapely.prepare(naast)
    n_pad = 0
    for r in seg:
        if r["status"] == "uit" or r["reden"] == "steeg" or _tag(r, "highway") not in PADTYPEN:
            continue
        if _tag(r, "service").lower() in GEEN_PAD or r["bebouwd"] < drempel:
            continue
        g = r["geometry"]
        langs_straat = g.intersection(naast).length / g.length if naast is not None else 0
        if langs_straat < 0.5:
            r["status"], r["reden"] = "verplicht", "pad langs bebouwing"
            n_pad += 1
        else:
            r["reden"] += ", loopt naast een straat"
    log(f"Bebouwing (binnen {afstand:g} m): {n_pad} paden/achterpaden verplicht gemaakt")


def _gelijktrekken(seg, kort, log):
    """Korte stukjes van een straat die anders zijn dan de rest van die straat (verplicht tussen
    optioneel of andersom) krijgen dezelfde status als de straat eromheen."""
    if not kort:
        return
    kan = {"straat langs bebouwing", "straat zonder bebouwing", "pad langs bebouwing"}
    per_naam = defaultdict(list)
    for i, r in enumerate(seg):
        if r["status"] != "uit" and r["naam"] and (r["reden"] in kan or r["reden"].startswith("type ")):
            per_naam[(r["naam"], _tag(r, "highway") in STRAATTYPEN)].append(i)
    n_om = 0
    for _ in range(4):
        veranderd = 0
        for idx in per_naam.values():
            if len(idx) < 3:
                continue
            knoop = defaultdict(list)
            for i in idx:
                g = seg[i]["geometry"]
                knoop[rond(g.coords[0])].append(i)
                knoop[rond(g.coords[-1])].append(i)
            G = nx.Graph()
            G.add_nodes_from(idx)
            for lst in knoop.values():
                for a in lst:
                    for b in lst:
                        if a < b:
                            G.add_edge(a, b)
            for st in ("verplicht", "optioneel"):
                sub = G.subgraph([i for i in idx if seg[i]["status"] == st])
                for run in nx.connected_components(sub):
                    lengte = sum(seg[i]["geometry"].length for i in run)
                    if lengte >= kort:
                        continue
                    buren = {j for i in run for j in G.neighbors(i)} - run
                    if len(buren) < 2 or any(seg[j]["status"] == st for j in buren):
                        continue
                    ander = "optioneel" if st == "verplicht" else "verplicht"
                    for i in run:
                        seg[i]["status"] = ander
                        seg[i]["reden"] = f"gelijkgetrokken met de rest van {seg[i]['naam']}"
                    veranderd += len(run)
        n_om += veranderd
        if not veranderd:
            break
    if n_om:
        log(f"{n_om} korte stukjes gelijkgetrokken met de rest van hun straat (< {kort:g} m)")


def maak_netwerk(grens, wegen, marge=150, samenvoegen=12, rijbaan=30, log=print, gebouwen=None,
                 afstand=25, min_opp=30, drempel=0.25, parallel=20, kort=75):
    """grens: shapely (multi)polygoon; wegen: records met OSM-tags + LineString (zelfde CRS, meters).
    gebouwen: records met polygonen (BAG/OSM). Gegeven = verplicht op basis van bebouwing:
      afstand  : gebouw binnen zoveel m van de weg telt als 'langs bebouwing'
      min_opp  : kleinere gebouwen (schuurtjes) tellen niet
      drempel  : minimaal aandeel van het wegstuk dat langs bebouwing ligt
      parallel : pad binnen zoveel m van een straat = begeleidend pad, wordt niet apart verplicht
      kort     : kortere afwijkende stukjes binnen een straat krijgen de status van de rest van die straat
    Geeft netwerk-records terug."""
    buf = grens.buffer(marge)
    recs = []
    for r in wegen:
        g = r.get("geometry")
        if g is None or g.is_empty:
            continue
        for lijn in _lijnen(g.intersection(buf) if not buf.contains(g) else g):
            n = dict(r)
            n["geometry"] = lijn
            recs.append(n)
    for r in recs:
        r["status"], r["gewicht"], r["reden"] = classificeer(r)
        r["naam"] = _tag(r, "name")
    log(f"{len(wegen)} OSM-lijnen ingelezen, {len(recs)} binnen grens + {marge:g} m")
    seg = _splits_op_knopen(recs)
    log(f"{len(seg)} segmenten na opknippen op kruispunten")
    n_clus, n_weg = _voeg_knopen_samen(seg, samenvoegen)
    log(f"{n_clus} kruispunten/rotondes samengevoegd; {n_weg} korte stukjes vallen daarbinnen")
    for r in seg:
        r["bebouwd"] = None
    if gebouwen:
        _bebouwing(seg, gebouwen, afstand, min_opp, drempel, parallel, log)
        _gelijktrekken(seg, kort, log)
    n_dub = _dubbele_rijbanen(seg, rijbaan)
    log(f"{n_dub} segmenten als tweede rijbaan op optioneel gezet")
    mid = shapely.line_interpolate_point([r["geometry"] for r in seg], 0.5, normalized=True)
    binnen = shapely.within(mid, grens)
    for r, b in zip(seg, binnen):
        if not b and r["status"] == "verplicht":
            r["status"], r["reden"] = "optioneel", "buiten grens"
    uit = []
    for i, r in enumerate(seg, 1):
        uit.append(dict(id=i, status=r["status"], gewicht=float(r["gewicht"]),
                        lengte=round(r["geometry"].length, 1), naam=r["naam"],
                        highway=_tag(r, "highway"), reden=r["reden"], osm_id=_tag(r, "osm_id"),
                        bebouwd=r["bebouwd"], geometry=r["geometry"]))
    som = defaultdict(float)
    for r in uit:
        som[r["status"]] += r["lengte"]
    log("Lengte per status: " + ", ".join(f"{k} {v / 1000:.1f} km" for k, v in sorted(som.items())))
    return uit


# ==========================================================================================
# Stap 2: indelen en routes
# ==========================================================================================
CUTOFF = 6000      # m (gewogen): verder zoeken we niet voor verbindingen
KNN = 10           # oneven knopen: alleen de 10 dichtstbijzijnde als koppelkandidaat


def _getal(v, standaard):
    try:
        f = float(v)
        return standaard if f != f else f
    except (TypeError, ValueError):
        return standaard


def bereid_netwerk(recs, snap=0.5, log=print):
    """Netwerk-records opschonen (status/gewicht/id) en bijgetekende lijnen vastknopen."""
    net = []
    for r in recs:
        st = _tag(r, "status").lower()
        if st == "uit":
            continue
        if st not in ("verplicht", "optioneel"):
            log(f"LET OP: lijn met onbekende status '{st}' -> optioneel")
            st = "optioneel"
        for lijn in _lijnen(r.get("geometry")):
            n = dict(r)
            n["status"], n["gewicht"] = st, max(0.1, _getal(r.get("gewicht"), 1.0))
            n["naam"] = _tag(r, "naam")
            n["geometry"] = lijn
            net.append(n)
    ids = [r.get("id") for r in net]
    if any(i is None or _getal(i, None) is None for i in ids) or len(set(ids)) != len(ids):
        for i, r in enumerate(net, 1):
            r["id"] = i
    for r in net:
        r["id"] = int(r["id"])

    # bijgetekende lijnen vastknopen: los eindpunt (bijna) op een andere lijn -> die lijn knippen
    knopen = Counter()
    for r in net:
        knopen[rond(r["geometry"].coords[0])] += 1
        knopen[rond(r["geometry"].coords[-1])] += 1
    geoms = [r["geometry"] for r in net]
    boom = shapely.STRtree(geoms)
    splits = defaultdict(list)
    for i, g in enumerate(geoms):
        for c in (g.coords[0], g.coords[-1]):
            if knopen[rond(c)] > 1:
                continue
            p = Point(c)
            for j in boom.query(p, predicate="dwithin", distance=snap):
                if j == i:
                    continue
                h = geoms[j]
                d = h.project(p)
                if snap < d < h.length - snap:
                    splits[j].append(d)
    if splits:
        nieuw = [r for j, r in enumerate(net) if j not in splits]
        for j, ds in splits.items():
            h = geoms[j]
            grenzen = [0] + sorted(set(ds)) + [h.length]
            for d0, d1 in zip(grenzen[:-1], grenzen[1:]):
                n = dict(net[j])
                n["geometry"] = shapely.ops.substring(h, d0, d1)
                nieuw.append(n)
        net = nieuw
        for i, r in enumerate(net, 1):
            r["id"] = i
        log(f"{len(splits)} lijnen doorgeknipt om bijgetekende lijnen vast te knopen")
        eindpunten = [rond(r["geometry"].coords[k]) for r in net for k in (0, -1)]
        kp = shapely.points(np.array(eindpunten))
        kboom = shapely.STRtree(kp)
        for r in net:
            cs = list(r["geometry"].coords)
            for k in (0, -1):
                p = Point(cs[k])
                for j in kboom.query(p, predicate="dwithin", distance=snap):
                    q = kp[j]
                    if 0 < p.distance(q) <= snap:
                        cs[k] = (q.x, q.y)
                        break
            r["geometry"] = LineString(cs)
    return net


class Netwerk:
    def __init__(self, net):
        self.knoop, xy = {}, []
        self.e = {}           # eid -> (u, v, len, cost, verplicht)
        self.adj = defaultdict(list)
        self.geom, self.naam = {}, {}

        def k(c):
            c = rond(c)
            if c not in self.knoop:
                self.knoop[c] = len(xy)
                xy.append(c)
            return self.knoop[c]

        for r in net:
            g = r["geometry"]
            u, v = k(g.coords[0]), k(g.coords[-1])
            ln = g.length
            cost = ln * r["gewicht"]
            self.e[r["id"]] = (u, v, ln, cost, r["status"] == "verplicht")
            self.adj[u].append((v, cost, ln, r["id"]))
            if v != u:
                self.adj[v].append((u, cost, ln, r["id"]))
            self.geom[r["id"]] = g
            self.naam[r["id"]] = r.get("naam", "")
        self.xy = np.array(xy)
        self.cache = {}
        self.verplicht = [eid for eid, x in self.e.items() if x[4]]
        self.mid = {e: tuple(np.asarray(g.interpolate(0.5, normalized=True).coords[0])) for e, g in self.geom.items()}

    def dijkstra(self, bronnen, cutoff=CUTOFF):
        best = {}
        heap = [(0.0, 0.0, b, -1, -1) for b in bronnen]
        heapq.heapify(heap)
        while heap:
            c, l, n, prev, eid = heapq.heappop(heap)
            if n in best:
                continue
            best[n] = (c, l, prev, eid)
            for m, ec, el, mid in self.adj[n]:
                if m not in best and c + ec <= cutoff:
                    heapq.heappush(heap, (c + ec, l + el, m, n, mid))
        return best

    def van(self, n):
        if n not in self.cache:
            self.cache[n] = self.dijkstra([n])
        return self.cache[n]

    @staticmethod
    def pad(boom, doel):
        eids = []
        while boom[doel][2] != -1:
            eids.append(boom[doel][3])
            doel = boom[doel][2]
        return eids[::-1]


def route(nw, eids):
    """Kortste rondrit langs alle eids (rural postman). Geeft (lengte_m, extra eids)."""
    eids = list(eids)
    if not eids:
        return 0.0, []
    extra = []
    graad = defaultdict(int)
    uf = _UF()
    for eid in eids:
        u, v = nw.e[eid][:2]
        graad[u] += 1
        graad[v] += 1
        uf.union(u, v)

    comps = defaultdict(set)
    for n in graad:
        comps[uf.find(n)].add(n)
    comps = list(comps.values())
    if len(comps) > 1:
        welke = {n: i for i, c in enumerate(comps) for n in c}
        T = nx.Graph()
        T.add_nodes_from(range(len(comps)))
        for i, c in enumerate(comps):
            beste = {}
            for n, (cst, _, _, _) in nw.dijkstra(c).items():
                j = welke.get(n)
                if j is not None and j != i and (j not in beste or cst < beste[j][0]):
                    beste[j] = (cst, n)
            for j, (cst, n) in beste.items():
                if not T.has_edge(i, j) or T[i][j]["weight"] > cst:
                    T.add_edge(i, j, weight=cst, bron=c, doel=n)
        if not nx.is_connected(T):
            return float("inf"), []           # onbereikbaar deel: deze combinatie kan niet
        for i, j, d in nx.minimum_spanning_edges(T, data=True):
            for eid in nw.pad(nw.dijkstra(d["bron"]), d["doel"]):
                extra.append(eid)
                u, v = nw.e[eid][:2]
                graad[u] += 1
                graad[v] += 1

    oneven = [n for n, g in graad.items() if g % 2]
    if oneven:
        S = set(oneven)
        M = nx.Graph()
        M.add_nodes_from(oneven)
        for n in oneven:
            boom = nw.van(n)
            for cst, m in sorted((boom[m][0], m) for m in S if m != n and m in boom)[:KNN]:
                M.add_edge(n, m, weight=cst)
        paren = set(nx.min_weight_matching(M))
        gekoppeld = {x for p in paren for x in p}
        rest = [n for n in oneven if n not in gekoppeld]
        while rest:
            n = rest.pop()
            boom = nw.dijkstra([n], cutoff=1e12)
            m = min(rest, key=lambda m: boom.get(m, (1e18,))[0])
            rest.remove(m)
            paren.add((n, m))
        for n, m in paren:
            boom = nw.van(n)
            if m not in boom:
                boom = nw.dijkstra([n], cutoff=1e12)
            extra += nw.pad(boom, m)
    return sum(nw.e[e][2] for e in eids) + sum(nw.e[e][2] for e in extra), extra


def omhulling_ha(nw, eids):
    pts = [nw.xy[x] for e in eids for x in nw.e[e][:2]]
    if len(pts) < 3:
        return 0.0
    return MultiPoint(pts).convex_hull.area / 1e4


def _groei(nw, max_m, max_ha, open_, gestopt):
    open_ = set(open_)
    gebieden = []
    while open_:
        if gestopt():
            raise Afgebroken()
        lijst = list(open_)
        pts = np.array([nw.mid[e] for e in lijst])
        zaad = lijst[int(np.argmax(((pts - pts.mean(axis=0)) ** 2).sum(axis=1)))]
        u, v = nw.e[zaad][:2]
        boom = nw.dijkstra([u, v], cutoff=1e12)
        volgorde = sorted((min(boom.get(nw.e[e][0], (1e18,))[0], boom.get(nw.e[e][1], (1e18,))[0]), e)
                          for e in open_)
        volgorde = [e for d, e in volgorde if d < 1e17]
        if zaad not in volgorde:
            volgorde.insert(0, zaad)

        def past(k):
            return route(nw, volgorde[:k])[0] <= max_m and \
                (not max_ha or omhulling_ha(nw, volgorde[:k]) <= max_ha)

        lo, hi = 1, len(volgorde)
        if past(hi):
            lo = hi
        else:
            stap = 1
            while lo + stap < hi and past(lo + stap):
                lo += stap
                stap *= 2
            hi = min(hi, lo + stap)
            while hi - lo > 1:
                m = (lo + hi) // 2
                if past(m):
                    lo = m
                else:
                    hi = m
        gebieden.append(volgorde[:lo])
        open_ -= set(volgorde[:lo])
    return gebieden


def _voeg_kleine_samen(nw, gebieden, max_m, max_ha):
    veranderd = True
    while veranderd and len(gebieden) > 1:
        veranderd = False
        lengtes = [route(nw, g)[0] for g in gebieden]
        for i in np.argsort(lengtes):
            if lengtes[i] > 0.5 * max_m:
                break
            ci = np.mean([nw.mid[e] for e in gebieden[i]], axis=0)
            kand = sorted(range(len(gebieden)),
                          key=lambda j: np.hypot(*(np.mean([nw.mid[e] for e in gebieden[j]], axis=0) - ci)))
            for j in kand[1:4]:
                samen = gebieden[i] + gebieden[j]
                if route(nw, samen)[0] <= max_m and (not max_ha or omhulling_ha(nw, samen) <= max_ha):
                    gebieden[j] = samen
                    del gebieden[i]
                    veranderd = True
                    break
            if veranderd:
                break
    return gebieden


def _deel_in(nw, open_, max_m, max_ha, log, gestopt, voortgang=_niets):
    t0 = time.time()
    beste = _voeg_kleine_samen(nw, _groei(nw, max_m, max_ha, open_, gestopt), max_m, max_ha)
    k = len(beste)
    langste = max(route(nw, g)[0] for g in beste)
    log(f"  eerste indeling: {k} gebieden, langste ronde {langste / 1000:.2f} km ({time.time() - t0:.0f} s)")
    voortgang(0.15)
    lo, hi = 0.5 * max_m, max_m
    for stap in range(6):
        b = (lo + hi) / 2
        g = _voeg_kleine_samen(nw, _groei(nw, b, max_ha, open_, gestopt), b, max_ha)
        if len(g) <= k:
            hi = b
            l2 = max(route(nw, x)[0] for x in g)
            if len(g) < k or l2 < langste:
                beste, k, langste = g, len(g), l2
        else:
            lo = b
        voortgang(0.15 + 0.14 * (stap + 1))
    log(f"  gelijkgetrokken: {k} gebieden, langste ronde {langste / 1000:.2f} km ({time.time() - t0:.0f} s)")
    return beste


# ------------------------------------------------------------------------------------------
# zones (vaste grenzen) en gladmaken
# ------------------------------------------------------------------------------------------
def maak_zones(grens, grenslijnen):
    """Deel de grens op met zelf getekende lijnen (of vlakken). Geeft een lijst polygonen."""
    if not grenslijnen:
        return [grens]
    lijnen = [grens.boundary]
    for g in grenslijnen:
        if g is None or g.is_empty:
            continue
        lijnen.append(g.boundary if g.geom_type in ("Polygon", "MultiPolygon") else g)
    vlakken = list(shapely.ops.polygonize(shapely.union_all(lijnen)))
    zones = [v for v in vlakken if grens.contains(v.representative_point()) and v.area > 1]
    return zones or [grens]


def _wijs_zones_toe(nw, zones, log, grenslijnen=None):
    """Elke verplichte straat naar een zone. Straten die óp een grenslijn liggen gaan als
    aaneengesloten groep naar één aangrenzende zone (die met de minste straatlengte)."""
    if len(zones) == 1:
        return {e: 0 for e in nw.verplicht}
    boom = shapely.STRtree(zones)
    zone_van, open_ = {}, []
    langs = None
    if grenslijnen:
        langs = shapely.union_all([g.boundary if g.geom_type in ("Polygon", "MultiPolygon") else g
                                   for g in grenslijnen if g is not None and not g.is_empty]).buffer(3)
    for e in nw.verplicht:
        if langs is not None and nw.geom[e].intersection(langs).length > 0.5 * nw.geom[e].length:
            open_.append(e)          # straat ligt (vrijwel) óp een getekende grenslijn
            continue
        p = Point(nw.mid[e])
        hits = [int(i) for i in boom.query(p, predicate="within")]
        hits = [i for i in hits if zones[i].contains(nw.geom[e].interpolate(0.5, normalized=True))]
        if len(hits) == 1 and nw.geom[e].intersection(zones[hits[0]]).length > 0.5 * nw.geom[e].length:
            zone_van[e] = hits[0]
        else:
            open_.append(e)
    lengte = defaultdict(float)
    for e, z in zone_van.items():
        lengte[z] += nw.e[e][2]
    # groepen langs een grenslijn
    G = nx.Graph()
    for e in open_:
        G.add_node(e)
    per_knoop = defaultdict(list)
    for e in open_:
        for k in nw.e[e][:2]:
            per_knoop[k].append(e)
    for es in per_knoop.values():
        for a, b in zip(es, es[1:]):
            G.add_edge(a, b)
    for groep in nx.connected_components(G):
        lijn = shapely.union_all([nw.geom[e] for e in groep])
        buren = [int(i) for i in boom.query(lijn, predicate="dwithin", distance=2)]
        if not buren:
            buren = [int(np.argmin([z.distance(lijn) for z in zones]))]
        z = min(buren, key=lambda i: lengte[i])
        for e in groep:
            zone_van[e] = z
            lengte[z] += nw.e[e][2]
    if open_:
        log(f"{len(open_)} straatstukken liggen op een grenslijn en zijn per straat aan één kant ingedeeld")
    return zone_van


def _maak_glad(nw, gebieden, zone_g, straal, max_m, max_ha, log, gestopt, rondes=6):
    """Vingers en uitstulpingen wegwerken: een straat waarvan de omgeving (binnen 'straal' m)
    vooral bij een ander gebied hoort, gaat naar dat gebied - als beide rondes dan nog passen."""
    if not straal or len(gebieden) < 2:
        return gebieden, zone_g
    gebied_van = {e: i for i, g in enumerate(gebieden) for e in g}
    eids = list(gebied_van)
    pts = shapely.points(np.array([nw.mid[e] for e in eids]))
    boom = shapely.STRtree(pts)
    a, b = boom.query(pts, predicate="dwithin", distance=straal)
    buren = defaultdict(list)
    for i, j in zip(a, b):
        if i != j:
            buren[eids[i]].append(eids[j])
    lengtes = {i: route(nw, g)[0] for i, g in enumerate(gebieden)}
    totaal = 0
    for ronde in range(rondes):
        if gestopt():
            raise Afgebroken()
        kand = defaultdict(list)          # (van, naar) -> [(dominantie, e)]
        for e in eids:
            eigen = gebied_van[e]
            w = defaultdict(float)
            for f in buren[e]:
                j = gebied_van[f]
                if zone_g[j] == zone_g[eigen]:
                    w[j] += nw.e[f][2]
            if not w:
                continue
            j, wj = max(w.items(), key=lambda x: x[1])
            tot = sum(w.values())
            if j != eigen and wj > 0.6 * tot and wj > 2 * w.get(eigen, 0):
                kand[(eigen, j)].append((wj / tot, e))
        verplaatst = 0
        for (i, j), lijst in sorted(kand.items(), key=lambda x: -len(x[1])):
            lijst.sort(reverse=True)
            stukjes = [e for _, e in lijst if gebied_van[e] == i]
            pogingen = [stukjes] + [[e] for e in stukjes[:25]] if len(stukjes) > 1 else [stukjes]
            for nr_poging, zet in enumerate(pogingen):
                zet = [e for e in zet if gebied_van[e] == i]
                if not zet:
                    continue
                s = set(zet)
                nieuw_i = [e for e in gebieden[i] if e not in s]
                nieuw_j = gebieden[j] + zet
                li = route(nw, nieuw_i)[0] if nieuw_i else 0.0
                lj = route(nw, nieuw_j)[0]
                if li <= max_m and lj <= max_m and (not max_ha or omhulling_ha(nw, nieuw_j) <= max_ha):
                    gebieden[i], gebieden[j] = nieuw_i, nieuw_j
                    lengtes[i], lengtes[j] = li, lj
                    for e in zet:
                        gebied_van[e] = j
                    verplaatst += len(zet)
                    if nr_poging == 0:
                        break
        totaal += verplaatst
        if not verplaatst:
            break
    # lege gebieden opruimen
    paren = [(g, z) for g, z in zip(gebieden, zone_g) if g]
    log(f"Gladmaken (straal {straal:g} m): {totaal} straatstukken verplaatst"
        + (f", {len(gebieden) - len(paren)} gebied(en) opgeheven" if len(paren) < len(gebieden) else ""))
    return [g for g, _ in paren], [z for _, z in paren]


def _maak_vlakken(nw, gebieden, zones, zone_g, vereenvoudig=0, zone_poly=None):
    """Vlak per gebied: elk stuk grond hoort bij het gebied van de dichtstbijzijnde verplichte straat
    binnen dezelfde zone. Met 'vereenvoudig' (m) worden de grenzen rechter getrokken."""
    vlakken = {}
    # netwerkdelen binnen dezelfde zone delen samen de zonepolygoon
    per_poly = defaultdict(list)
    for i, z in enumerate(zone_g):
        per_poly[id(zone_poly[z]) if zone_poly else z].append(i)
    poly_van = {}
    for i, z in enumerate(zone_g):
        poly_van[i] = zone_poly[z] if zone_poly else zones[z]
    for sleutel, mijn in per_poly.items():
        zpoly = poly_van[mijn[0]]
        if not mijn:
            continue
        if len(mijn) == 1:
            vlakken[mijn[0] + 1] = zpoly
            continue
        pts, lab = [], []
        for i in mijn:
            for e in gebieden[i]:
                geom = nw.geom[e]
                for f in np.linspace(0, 1, max(2, int(geom.length / 10))):
                    p = geom.interpolate(f, normalized=True)
                    pts.append((p.x, p.y))
                    lab.append(i + 1)
        pts = np.array(pts)
        _, idx = np.unique(np.round(pts, 1), axis=0, return_index=True)
        vor = shapely.voronoi_polygons(MultiPoint(pts[idx]), extend_to=zpoly.buffer(1000), ordered=True)
        lab = np.array(lab)[idx]
        cellen = list(vor.geoms)
        for i in mijn:
            vlakken[i + 1] = shapely.union_all([c for c, l in zip(cellen, lab) if l == i + 1]).intersection(zpoly)
    if vereenvoudig and hasattr(shapely, "coverage_simplify") and len(vlakken) > 1:
        try:
            sleutels = sorted(vlakken)
            geoms = [shapely.make_valid(vlakken[k]) for k in sleutels]
            nieuw = shapely.coverage_simplify(geoms, vereenvoudig, simplify_boundary=False)
            for k, g in zip(sleutels, nieuw):
                if g is not None and not g.is_empty and g.is_valid:
                    vlakken[k] = g
        except Exception:
            pass
    return vlakken


def _herstel_enclaves(nw, gebieden, zones, zone_g, max_m, max_ha, log, zone_poly=None, rondes=3):
    for _ in range(rondes):
        vlakken = _maak_vlakken(nw, gebieden, zones, zone_g, 0, zone_poly)
        delen = []
        for i, v in vlakken.items():
            for d in getattr(v, "geoms", [v]):
                if d.geom_type == "Polygon":
                    delen.append((i, d))
        verplaatst = 0
        for i in sorted({i for i, _ in delen}):
            mijn = sorted((d for j, d in delen if j == i), key=lambda d: -d.area)
            for d in mijn[1:]:
                g = gebieden[i - 1]
                stukjes = [e for e in g if d.contains(Point(nw.mid[e]))]
                if not stukjes or len(stukjes) == len(g):
                    continue
                buren = sorted(((d.intersection(x).length, j) for j, x in delen if j != i and zone_g[j - 1] == zone_g[i - 1] and x.touches(d)),
                               reverse=True)
                for _, j in buren:
                    nieuw_j = gebieden[j - 1] + stukjes
                    s = set(stukjes)
                    nieuw_i = [e for e in g if e not in s]
                    if (route(nw, nieuw_j)[0] <= max_m and route(nw, nieuw_i)[0] <= max_m and
                            (not max_ha or omhulling_ha(nw, nieuw_j) <= max_ha)):
                        gebieden[j - 1], gebieden[i - 1] = nieuw_j, nieuw_i
                        verplaatst += 1
                        break
        if not verplaatst:
            break
        log(f"{verplaatst} losliggende stukjes naar het omringende gebied verplaatst")
    return gebieden


def indelen(netwerk, grens, max_km=7.0, max_ha=None, snap=0.5, log=print, gestopt=lambda: False,
            voortgang=_niets, gladheid=0, grenslijnen=None, vereenvoudig=0):
    """netwerk: records (status, gewicht, naam, id, geometry); grens: shapely polygoon.
    gladheid   : straal (m) voor het rechttrekken van vingers/uitstulpingen (0 = uit)
    grenslijnen: lijst shapely-lijnen/vlakken die als vaste grens dienen (bv. hoofdweg, water)
    vereenvoudig: grenzen van de vlakken rechter trekken (m, alleen voor de weergave)
    Geeft dict met lijsten records: deelgebieden, route, route_lijn, start, netwerk + samenvatting."""
    max_m = max_km * 1000
    net = bereid_netwerk(netwerk, snap, log)
    nw = Netwerk(net)
    if not nw.verplicht:
        raise ValueError("Er zijn geen verplichte stukken in het netwerk.")
    vl = sum(nw.e[e][2] for e in nw.verplicht)
    log(f"Netwerk: {len(nw.e)} stukken, {len(nw.xy)} knopen; verplicht {vl / 1000:.1f} km")

    # losse delen van het netwerk: elk deel apart indelen; kleine eilandjes (< 300 m) overslaan
    G = nx.Graph([(x[0], x[1]) for x in nw.e.values()])
    deel_van = {}
    for d, comp in enumerate(nx.connected_components(G)):
        for n in comp:
            deel_van[n] = d
    lengte_deel = defaultdict(float)
    for e in nw.verplicht:
        lengte_deel[deel_van[nw.e[e][0]]] += nw.e[e][2]
    los = {e for e in nw.verplicht if lengte_deel[deel_van[nw.e[e][0]]] < 300}
    if los:
        log(f"LET OP: {len(los)} verplichte stukken ({sum(nw.e[e][2] for e in los):.0f} m) liggen los van het "
            f"netwerk en worden overgeslagen - zie kolom 'los' in de netwerklaag (aansluiting controleren)")
        nw.verplicht = [e for e in nw.verplicht if e not in los]
    n_delen = len({deel_van[nw.e[e][0]] for e in nw.verplicht})
    if n_delen > 1:
        log(f"Het netwerk bestaat uit {n_delen} losse delen; die worden apart ingedeeld")

    zones = maak_zones(grens, grenslijnen)
    zone_van = _wijs_zones_toe(nw, zones, log, grenslijnen)
    if len(zones) > 1:
        log(f"Vaste grenzen: gebied opgedeeld in {len(zones)} zones")
    log(f"Indelen (max {max_km:g} km per ronde{f', max {max_ha:g} ha' if max_ha else ''})...")
    gebieden, zone_g = [], []
    totaal = len(nw.verplicht)
    gedaan = 0
    groepen = defaultdict(list)            # (zone, netwerkdeel) -> verplichte stukken
    for e in nw.verplicht:
        groepen[(zone_van[e], deel_van[nw.e[e][0]])].append(e)
    nummer = {k: i for i, k in enumerate(sorted(groepen))}
    for (z, d), open_ in sorted(groepen.items()):
        if len(groepen) > 1:
            log(f"Zone {z + 1}{f', netwerkdeel {d + 1}' if n_delen > 1 else ''}: "
                f"{sum(nw.e[e][2] for e in open_) / 1000:.1f} km straat")
        aandeel = len(open_) / totaal
        g = _deel_in(nw, open_, max_m, max_ha, log, gestopt,
                     lambda f, g0=gedaan, a=aandeel: voortgang(int(80 * (g0 + f * a))))
        gedaan += aandeel
        gebieden += g
        zone_g += [nummer[(z, d)]] * len(g)
    zone_poly = {nummer[(z, d)]: zones[z] for (z, d) in groepen}
    if gladheid:
        gebieden, zone_g = _maak_glad(nw, gebieden, zone_g, gladheid, max_m, max_ha, log, gestopt)
    voortgang(85)
    volgorde = sorted(range(len(gebieden)), key=lambda i: (-round(np.mean([nw.mid[e][1] for e in gebieden[i]]) / 500),
                                                         np.mean([nw.mid[e][0] for e in gebieden[i]])))
    gebieden = [gebieden[i] for i in volgorde]
    zone_g = [zone_g[i] for i in volgorde]
    gebieden = _herstel_enclaves(nw, gebieden, zones, zone_g, max_m, max_ha, log, zone_poly)
    voortgang(95)
    vlakken = _maak_vlakken(nw, gebieden, zones, zone_g, vereenvoudig, zone_poly)

    uit = dict(deelgebieden=[], route=[], route_lijn=[], start=[], samenvatting=[])
    gebied_van = {}
    for i, g in enumerate(gebieden, 1):
        for e in g:
            gebied_van[e] = i
        ln, extra = route(nw, g)
        vlak = vlakken[i]
        M = nx.MultiGraph()
        for e in g:
            M.add_edge(nw.e[e][0], nw.e[e][1], key=("v", e))
        for k, e in enumerate(extra):
            M.add_edge(nw.e[e][0], nw.e[e][1], key=("x", e, k))
        knopen = list(M.nodes)
        c = vlak.centroid if not vlak.is_empty else MultiPoint([nw.mid[e] for e in g]).centroid
        start = knopen[int(np.argmin(((nw.xy[knopen] - np.array([c.x, c.y])) ** 2).sum(axis=1)))]
        eigen, gezien, coords = set(g), set(), []
        for nr, (u, v, key) in enumerate(nx.eulerian_circuit(M, source=start, keys=True), 1):
            e = key[1]
            geom = nw.geom[e]
            if rond(geom.coords[0]) != tuple(nw.xy[u]):
                geom = geom.reverse()
            soort = "verbinding" if e not in eigen else ("herhaling" if e in gezien else "tellen")
            gezien.add(e)
            uit["route"].append(dict(gebied=i, volgnr=nr, soort=soort, naam=nw.naam.get(e, ""),
                                     lengte=round(geom.length, 1), netwerk_id=e, geometry=geom))
            cs = list(geom.coords)
            coords += cs if not coords else cs[1:]
        lijn = LineString(coords)
        uit["route_lijn"].append(dict(gebied=i, route_km=round(lijn.length / 1000, 2), geometry=lijn))
        uit["start"].append(dict(gebied=i, geometry=Point(nw.xy[start])))
        vk = sum(nw.e[e][2] for e in g)
        s = dict(gebied=i, verplicht_km=round(vk / 1000, 2), route_km=round(ln / 1000, 2),
                 extra_km=round((ln - vk) / 1000, 2), extra_pct=int(round(100 * (ln - vk) / vk)),
                 ha=round(vlak.area / 1e4, 1), omhulling_ha=round(omhulling_ha(nw, g), 1), zone=zones.index(zone_poly[zone_g[i - 1]]) + 1)
        uit["samenvatting"].append(s)
        uit["deelgebieden"].append(dict(s, geometry=vlak))
    for r in net:
        r["gebied"] = gebied_van.get(r["id"], 0)
        r["los"] = int(r["id"] in los)
    uit["netwerk"] = net
    tot_v = sum(s["verplicht_km"] for s in uit["samenvatting"])
    tot_r = sum(s["route_km"] for s in uit["samenvatting"])
    log(f"Klaar: {len(gebieden)} gebieden; verplicht {tot_v:.1f} km, te fietsen {tot_r:.1f} km")
    voortgang(100)
    return uit


def gpx_tekst(naam, coords_wgs):
    regels = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<gpx version="1.1" creator="Ekoza fietsrondes" xmlns="http://www.topografix.com/GPX/1/1">',
              f"<trk><name>{naam}</name><trkseg>"]
    regels += [f'<trkpt lat="{y:.7f}" lon="{x:.7f}"/>' for x, y in coords_wgs]
    regels.append("</trkseg></trk></gpx>")
    return "\n".join(regels) + "\n"
