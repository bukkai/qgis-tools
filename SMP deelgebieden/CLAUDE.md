# CLAUDE.md: Ekoza fietsrondes

QGIS-plugin waarmee ecologen van Ekoza een projectgebied opdelen in fietsrondes (max. X km) en per
ronde de kortste route langs alle te tellen straten krijgen. **Lees eerst `docs/BRIEF.md`**: daarin
staan het doel, het algoritme, de besluiten, de bekende problemen en de backlog.

## Structuur
- `ekoza_fietsrondes/`: de plugin (dit hele mapje gaat in de ZIP)
  - `fietsronde_kern.py`: alle rekenlogica. **Geen QGIS- of geopandas-imports**, alleen shapely ≥ 2, numpy, networkx.
  - `algoritmes.py`: Processing-algoritmes, I/O (QGIS ↔ records), opmaak. Geen rekenlogica hier.
- `scripts/`: CLI-versies (geopandas voor I/O).
- `tests/test_kern.py`: pytest op de kern. `tests/qgis_rooktest.py`: draait de plugin in PyQGIS.
- `testdata/`: OSM-uitsnede Westervoort (grens `gangstervoort`, lagen `highway`, `building`) + grenslijn.

## Werkafspraken
- **Taal**: code-identifiers, docstrings, meldingen en documentatie in het **Nederlands** (gebruikers en team zijn Nederlands). Houd de bestaande naamgeving aan (`gebieden`, `verplicht`, `route`, …).
- **Records**: de kern werkt met dicts met attributen + `geometry` (shapely, meters). Houd die grens tussen kern en QGIS-laag scherp.
- **Harde invarianten** (moeten na elke wijziging gelden; staan in de tests):
  1. elke niet-losse verplichte straat precies één keer `tellen`, in precies één gebied;
  2. geen ronde langer dan `max_km`;
  3. elke route is gesloten en aaneengesloten;
  4. deelgebied-vlakken overlappen niet en liggen binnen de grens.
- Gladmaken, enclaves, vaste grenzen enzovoort mogen het maximum **nooit** overschrijden.
- Nieuwe instellingen: verstandige standaard, en in QGIS onder "Geavanceerd" tenzij gebruikers ze echt nodig hebben. Zet in de help (`shortHelpString`) wat ze doen.
- Meldingen aan de gebruiker gaan via `log(...)`: kort, in gewone taal, met een actie als er iets mis is.
- Na een gedragswijziging: `docs/BRIEF.md` bijwerken (§4 algoritme, §5 resultaten, §8 lessen) en de versie in `metadata.txt` ophogen.

## Commando's
```bash
pip install -r requirements-dev.txt
pytest -q -m "not traag"        # snel (< 30 s)
pytest -q                       # alles, ±4,5 min (volledige indeling testgebied, met en zonder grenslijn)
python scripts/stap1_netwerk.py --gpkg testdata/westervoort_osm.gpkg --grens gangstervoort --wegen highway --gebouwen building --uit /tmp/netwerk.gpkg
python scripts/stap2_indelen.py --netwerk /tmp/netwerk.gpkg --max_km 7 --uit /tmp/indeling.gpkg
python tests/qgis_rooktest.py   # met de Python van QGIS (zie kop van het bestand)
python scripts/maak_zip.py      # dist/ekoza_fietsrondes-<versie>.zip om in QGIS te installeren
```

## Valkuilen
- De gebruiker draait QGIS 3.44 op **macOS**. Dat er geen shapely 2.1+ is (dus geen `coverage_simplify`), moet stil terugvallen.
- Bewaar in PyQGIS-tests een referentie naar de provider.
- Schrijf geen `fid` mee naar GeoPackage-uitvoer.
- Overpass-servers geven geregeld 502/429. Altijd terugvallen op de volgende server en een duidelijke fout met het QuickOSM-alternatief.
- Testdata zijn OSM-gegevens (ODbL): bronvermelding in README houden.
