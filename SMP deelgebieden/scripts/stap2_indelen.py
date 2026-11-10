"""
stap2_indelen.py - losse versie van "2. Deelgebieden en routes" (zonder QGIS).

  python stap2_indelen.py --netwerk netwerk.gpkg --max_km 7 [--max_ha 40] [--gladheid 150]
                          [--grenslijnen grenzen.gpkg|lijnen] [--uit indeling.gpkg]

Nodig: pip install geopandas shapely pyogrio networkx
"""
import argparse, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ekoza_fietsrondes"))
import geopandas as gpd
import pandas as pd
import fietsronde_kern as kern


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--netwerk", required=True)
    ap.add_argument("--grens", default="grens", help="laagnaam grens in het netwerk-GeoPackage")
    ap.add_argument("--max_km", type=float, default=7.0)
    ap.add_argument("--max_ha", type=float, default=None)
    ap.add_argument("--snap", type=float, default=0.5)
    ap.add_argument("--gladheid", type=float, default=150, help="vingers rechttrekken binnen (m), 0 = uit")
    ap.add_argument("--grenslijnen", default=None, help="GeoPackage-laag met vaste grenzen: pad.gpkg|laagnaam")
    ap.add_argument("--vereenvoudig", type=float, default=15, help="grenzen vereenvoudigen (m)")
    ap.add_argument("--uit", default="indeling.gpkg")
    a = ap.parse_args()

    net = gpd.read_file(a.netwerk, layer="netwerk")
    grens = gpd.read_file(a.netwerk, layer=a.grens).to_crs(net.crs)
    gl = None
    if a.grenslijnen:
        pad, _, laag = a.grenslijnen.partition("|")
        gl = list(gpd.read_file(pad, layer=laag or None).to_crs(net.crs).geometry)
    uit = kern.indelen(net.to_dict("records"), grens.union_all(), a.max_km, a.max_ha, a.snap,
                       gladheid=a.gladheid, grenslijnen=gl, vereenvoudig=a.vereenvoudig)

    if os.path.exists(a.uit):
        os.remove(a.uit)
    for laag in ("deelgebieden", "route", "route_lijn", "start", "netwerk"):
        gpd.GeoDataFrame(uit[laag], crs=net.crs).to_file(a.uit, layer=laag, driver="GPKG")
    map_ = os.path.dirname(os.path.abspath(a.uit))
    for r in gpd.GeoDataFrame(uit["route_lijn"], crs=net.crs).to_crs(4326).itertuples():
        with open(os.path.join(map_, f"route_{r.gebied:02d}.gpx"), "w", encoding="utf-8") as f:
            f.write(kern.gpx_tekst(f"Deelgebied {r.gebied}", r.geometry.coords))
    print("\n" + pd.DataFrame(uit["samenvatting"]).to_string(index=False))
    print(f"\nGeschreven: {a.uit} + route_XX.gpx")


if __name__ == "__main__":
    main()
