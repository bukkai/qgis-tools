"""
Downloadtest in QGIS: draait stap 1 op het testgebied zonder wegen- en gebouwenlaag, zodat de plugin
wegen en gebouwen zelf uit OpenStreetMap (Overpass) haalt. Heeft internet nodig.

Draaien met de Python van QGIS (niet via pytest), net als tests/qgis_rooktest.py:
  Windows: "C:\\Program Files\\QGIS 3.44.8\\bin\\python-qgis-ltr.bat" tests\\qgis_downloadtest.py
  macOS  : /Applications/QGIS.app/Contents/MacOS/bin/python3 tests/qgis_downloadtest.py
"""
import os, sys, tempfile, time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import (QgsApplication, QgsNetworkAccessManager, QgsProcessingFeedback,  # noqa: E402
                       QgsVectorLayer)

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


grens = QgsVectorLayer(os.path.join(ROOT, "testdata", "westervoort_osm.gpkg") + "|layername=gangstervoort",
                       "grens", "ogr")
uit = os.path.join(tempfile.mkdtemp(), "netwerk.gpkg")
timeout_voor = QgsNetworkAccessManager.timeout()
t0 = time.time()
processing.run("smpdeelgebieden:1_netwerk", {"GRENS": grens, "UITVOER": uit}, feedback=Log())
assert QgsNetworkAccessManager.timeout() == timeout_voor, "netwerk-timeout van QGIS is niet teruggezet"
net = QgsVectorLayer(uit + "|layername=netwerk", "netwerk", "ogr")
assert net.featureCount() > 1000, f"te weinig wegstukken: {net.featureCount()}"
print(f"OK  downloadtest geslaagd: {net.featureCount()} wegstukken in {time.time() - t0:.0f} s ({uit})")
