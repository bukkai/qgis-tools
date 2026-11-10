"""
Rooktest van de plugin in QGIS zonder scherm: draait stap 1 en 2 via Processing op het testgebied
en controleert dat de lagen geladen en opgemaakt worden.

Draaien met de Python van QGIS (niet via pytest):
  Linux  : QT_QPA_PLATFORM=offscreen python3 tests/qgis_rooktest.py   (python3 met PyQGIS, bv. /usr/bin/python3.12)
  macOS  : /Applications/QGIS.app/Contents/MacOS/bin/python3 tests/qgis_rooktest.py   (pad kan per versie verschillen)
  Windows: in de OSGeo4W Shell:  python tests\\qgis_rooktest.py
"""
import os, sys, tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import QgsApplication, QgsProcessingFeedback, QgsProject, QgsVectorLayer  # noqa: E402

HIER = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HIER, ".."))

app = QgsApplication([], False)
app.initQgis()
for kandidaat in ("/usr/share/qgis/python/plugins",
                  os.path.join(QgsApplication.prefixPath(), "python", "plugins"),
                  os.path.join(QgsApplication.pkgDataPath(), "python", "plugins")):
    if os.path.isdir(kandidaat):
        sys.path.append(kandidaat)
sys.path.insert(0, ROOT)
from processing.core.Processing import Processing  # noqa: E402
Processing.initialize()
import processing  # noqa: E402
from smp_deelgebieden.provider import DeelgebiedenProvider  # noqa: E402

provider = DeelgebiedenProvider()          # referentie bewaren, anders ruimt Python hem op
QgsApplication.processingRegistry().addProvider(provider)


class Log(QgsProcessingFeedback):
    def pushInfo(self, t):
        print("  >", t)


gpkg = os.path.join(ROOT, "testdata", "westervoort_osm.gpkg")
grens = QgsVectorLayer(gpkg + "|layername=gangstervoort", "grens", "ogr")
wegen = QgsVectorLayer(gpkg + "|layername=highway", "wegen", "ogr")
gebouwen = QgsVectorLayer(gpkg + "|layername=building", "gebouwen", "ogr")
uit = tempfile.mkdtemp()

# verkeerde grens moet direct een nette fout geven
try:
    processing.run("smpdeelgebieden:1_netwerk", {"GRENS": gebouwen, "WEGEN": wegen, "GEBOUWEN": gebouwen,
                                                 "UITVOER": os.path.join(uit, "x.gpkg")})
    raise SystemExit("FOUT: gebouwenlaag als grens werd niet afgevangen")
except Exception as e:
    print("OK  verkeerde grens afgevangen:", str(e).strip().splitlines()[-1][:100])

processing.runAndLoadResults("smpdeelgebieden:1_netwerk", {
    "GRENS": grens, "WEGEN": wegen, "GEBOUWEN": gebouwen, "UITVOER": os.path.join(uit, "netwerk.gpkg")},
    feedback=Log())
net = QgsVectorLayer(os.path.join(uit, "netwerk.gpkg") + "|layername=netwerk", "netwerk", "ogr")
assert net.featureCount() > 1000
processing.runAndLoadResults("smpdeelgebieden:2_indelen", {
    "NETWERK": net, "GRENS": grens, "MAX_KM": 7, "UITVOER": os.path.join(uit, "indeling.gpkg")}, feedback=Log())

geladen = {l.name(): l for l in QgsProject.instance().mapLayers().values()}
for naam in ("netwerk", "deelgebieden", "route_lijn", "start"):
    assert naam in geladen, f"laag {naam} niet geladen"
assert type(geladen["deelgebieden"].renderer()).__name__ == "QgsCategorizedSymbolRenderer"
assert geladen["deelgebieden"].labelsEnabled()
gpx = [f for f in os.listdir(uit) if f.endswith(".gpx")]
assert gpx, "geen GPX geschreven"
print(f"OK  rooktest geslaagd: {geladen['deelgebieden'].featureCount()} gebieden, {len(gpx)} GPX-bestanden ({uit})")
