"""Tests voor smp_deelgebieden/installatie.py (stap 0), zonder QGIS en zonder iets te installeren."""
import os, sys

import numpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from smp_deelgebieden import installatie  # noqa: E402


def test_niets_ontbreekt_in_testomgeving():
    assert installatie.ontbrekend() == []


def test_numpy_wordt_vastgehouden():
    assert f"numpy=={numpy.__version__}" in installatie.vaste_versies()


def test_pip_opdracht():
    cmd = installatie.pip_opdracht("py", ["networkx", "shapely>=2"], "vast.txt")
    assert cmd[:4] == ["py", "-m", "pip", "install"]
    assert "--user" in cmd
    assert cmd[cmd.index("-c") + 1] == "vast.txt"
    assert cmd[-2:] == ["networkx", "shapely>=2"]
    assert "-c" not in installatie.pip_opdracht("py", ["networkx"])


def test_python_exe_bestaat():
    exe = installatie.python_exe()
    assert exe and os.path.isfile(exe)


def test_installeer_meldt_fout_zonder_te_crashen(tmp_path):
    nep = tmp_path / ("bestaat_niet.exe" if os.name == "nt" else "bestaat_niet")
    gelukt, regels = installatie.installeer(["networkx"], exe=str(nep), log=lambda *_: None)
    assert not gelukt and regels
