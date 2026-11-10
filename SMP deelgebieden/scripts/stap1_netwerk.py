"""
stap1_netwerk.py - losse versie van "1. Netwerk maken" (zonder QGIS).

  python stap1_netwerk.py --gpkg testgebied.gpkg --grens gangstervoort --wegen highway --uit netwerk.gpkg

Nodig: pip install geopandas shapely pyogrio networkx
De rekenregels (TYPES: welk wegtype verplicht/optioneel/uit) staan in fietsronde_kern.py.
"""
import argparse, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ekoza_fietsrondes"))
import geopandas as gpd
import fietsronde_kern as kern


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpkg", required=True)
    ap.add_argument("--grens", required=True, help="laagnaam projectgrens")
    ap.add_argument("--wegen", required=True, help="laagnaam OSM-wegen (QuickOSM, highway=*)")
    ap.add_argument("--wegen_gpkg", default=None)
    ap.add_argument("--uit", default="netwerk.gpkg")
    ap.add_argument("--marge", type=float, default=150)
    ap.add_argument("--samenvoegen", type=float, default=12)
    ap.add_argument("--rijbaan", type=float, default=30)
    ap.add_argument("--gebouwen", default=None, help="laagnaam gebouwen (BAG/OSM) in hetzelfde GeoPackage")
    ap.add_argument("--afstand", type=float, default=25, help="gebouw binnen zoveel m = langs bebouwing")
    ap.add_argument("--min_opp", type=float, default=30, help="kleinere gebouwen tellen niet (m2)")
    a = ap.parse_args()

    grens = gpd.read_file(a.gpkg, layer=a.grens)
    if grens.crs is None or grens.crs.is_geographic:
        grens = grens.to_crs(28992)
    wegen = gpd.read_file(a.wegen_gpkg or a.gpkg, layer=a.wegen).to_crs(grens.crs)
    recs = [dict(r) for r in wegen.to_dict("records")]
    geb = None
    if a.gebouwen:
        geb = gpd.read_file(a.gpkg, layer=a.gebouwen).to_crs(grens.crs).to_dict("records")
    net = kern.maak_netwerk(grens.union_all(), recs, a.marge, a.samenvoegen, a.rijbaan, gebouwen=geb,
                            afstand=a.afstand, min_opp=a.min_opp)
    gpd.GeoDataFrame(net, crs=grens.crs).to_file(a.uit, layer="netwerk", driver="GPKG")
    grens[["geometry"]].to_file(a.uit, layer="grens", driver="GPKG")
    print(f"Geschreven: {a.uit} (lagen 'netwerk' en 'grens')")


if __name__ == "__main__":
    main()
