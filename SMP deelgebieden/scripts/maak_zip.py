"""Maakt dist/smp_deelgebieden-<versie>.zip om in QGIS te installeren ('Installeren vanaf ZIP')."""
import configparser, os, zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PLUGIN = os.path.join(ROOT, "smp_deelgebieden")

meta = configparser.ConfigParser()
meta.read(os.path.join(PLUGIN, "metadata.txt"), encoding="utf-8")
versie = meta["general"]["version"]
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
doel = os.path.join(ROOT, "dist", f"smp_deelgebieden-{versie}.zip")
with zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as z:
    for map_, mappen, bestanden in os.walk(PLUGIN):
        mappen[:] = [m for m in mappen if m != "__pycache__"]
        for b in bestanden:
            if b.endswith((".pyc", ".DS_Store")):
                continue
            pad = os.path.join(map_, b)
            z.write(pad, os.path.relpath(pad, ROOT))
print(doel)
