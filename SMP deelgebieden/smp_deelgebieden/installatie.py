"""Hulpfuncties voor '0. Benodigdheden controleren': de Python van QGIS vinden en pip veilig aanroepen.
Geen QGIS-imports, zodat dit ook met pytest te testen is."""
import importlib, os, shutil, subprocess, sys, tempfile

NODIG = {"shapely": "shapely>=2", "numpy": "numpy", "networkx": "networkx"}
VASTHOUDEN = ("numpy",)        # niet laten bijwerken: GDAL en QGIS zelf zijn ertegen gebouwd
PIP_TIMEOUT = 600              # s


def ontbrekend():
    """Pakketspecificaties (voor pip) van wat ontbreekt of te oud is."""
    mist = []
    for mod, spec in NODIG.items():
        try:
            m = importlib.import_module(mod)
            if mod == "shapely" and int(m.__version__.split(".")[0]) < 2:
                mist.append(spec)
        except Exception:
            mist.append(spec)
    return mist


def python_exe():
    """Het python-programma van QGIS (niet het QGIS-programma zelf, dat is sys.executable in QGIS)."""
    kandidaten = [os.path.join(sys.exec_prefix, "python.exe"), os.path.join(sys.exec_prefix, "python3.exe"),
                  os.path.join(sys.exec_prefix, "bin", "python3"), os.path.join(sys.exec_prefix, "bin", "python")]
    if os.path.basename(sys.executable).lower().startswith("python"):
        kandidaten.insert(0, sys.executable)
    for k in kandidaten:
        if os.path.isfile(k):
            return k
    return shutil.which("python3") or shutil.which("python")


def vaste_versies():
    """Regels voor een pip-constraints-bestand: de nu geïnstalleerde versies van VASTHOUDEN."""
    regels = []
    for mod in VASTHOUDEN:
        try:
            regels.append(f"{mod}=={importlib.import_module(mod).__version__}")
        except Exception:
            pass
    return regels


def pip_opdracht(exe, pakketten, constraints=None):
    cmd = [exe, "-m", "pip", "install", "--user", "--upgrade-strategy", "only-if-needed",
           "--disable-pip-version-check", "--no-warn-script-location"]
    if constraints:
        cmd += ["-c", constraints]
    return cmd + list(pakketten)


def installeer(pakketten, exe=None, log=print):
    """pip install --user, met numpy vastgehouden op de huidige versie. Geeft (gelukt, uitvoerregels)."""
    exe = exe or python_exe()
    if not exe:
        return False, ["Kan de Python van QGIS niet vinden."]
    vast = vaste_versies()
    with tempfile.TemporaryDirectory() as map_:
        constraints = None
        if vast:
            constraints = os.path.join(map_, "vast.txt")
            with open(constraints, "w", encoding="utf-8") as f:
                f.write("\n".join(vast) + "\n")
        cmd = pip_opdracht(exe, pakketten, constraints)
        log("Uitvoeren: " + " ".join(cmd) + (f"  (vastgehouden: {', '.join(vast)})" if vast else ""))
        extra = {}
        if os.name == "nt":       # geen zwart consolevenster laten verschijnen
            extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT, **extra)
        except subprocess.TimeoutExpired:
            return False, [f"pip reageerde na {PIP_TIMEOUT // 60} minuten nog niet (geen internet of proxy?)."]
        except OSError as e:
            return False, [f"Kan pip niet starten: {e}"]
    regels = (res.stdout + res.stderr).splitlines()[-25:]
    if res.returncode != 0 and any("No module named pip" in r for r in regels):
        regels.append("In deze Python zit geen pip. Installeer de pakketten handmatig, zie de help.")
    return res.returncode == 0, regels
