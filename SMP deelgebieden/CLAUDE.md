# CLAUDE.md: SMP deelgebieden

QGIS-plugin waarmee ecologen van Ekoza een projectgebied opdelen in fietsrondes (max. X km) en per
ronde de kortste route langs alle te tellen straten krijgen. **Lees eerst `docs/BRIEF.md`**: daarin
staan het doel, het algoritme, de besluiten, de bekende problemen en de backlog.

## Structuur
- `smp_deelgebieden/`: de plugin (dit hele mapje gaat in de ZIP)
  - `fietsronde_kern.py`: alle rekenlogica. **Geen QGIS- of geopandas-imports**, alleen shapely ≥ 2, numpy, networkx.
  - `algoritmes.py`: Processing-algoritmes, I/O (QGIS ↔ records), opmaak. Geen rekenlogica hier.
  - `installatie.py`: stap 0 (pip). Geen QGIS-imports, zodat pytest het kan testen.
- `scripts/`: CLI-versies (geopandas voor I/O).
- `tests/test_kern.py`, `tests/test_installatie.py`: pytest. `tests/qgis_rooktest.py`, `tests/qgis_downloadtest.py`: draaien de plugin in PyQGIS.
- Deze map staat in de repo `qgis-tools`; de CI-workflow staat in `../.github/workflows/smp-deelgebieden.yml`.
- Licentie van de plugin: GPL-2.0-or-later (`smp_deelgebieden/LICENSE`); de rest van qgis-tools is MIT.
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
pytest -q                       # alles, 5–13 min (volledige indeling testgebied, met en zonder grenslijn)
python scripts/stap1_netwerk.py --gpkg testdata/westervoort_osm.gpkg --grens gangstervoort --wegen highway --gebouwen building --uit /tmp/netwerk.gpkg
python scripts/stap2_indelen.py --netwerk /tmp/netwerk.gpkg --max_km 7 --uit /tmp/indeling.gpkg
python tests/qgis_rooktest.py   # met de Python van QGIS (zie kop van het bestand), ±5 min
python tests/qgis_downloadtest.py  # idem, stap 1 met download uit OSM (heeft internet nodig)
python scripts/maak_zip.py      # dist/smp_deelgebieden-<versie>.zip om in QGIS te installeren
```

## Valkuilen
- De gebruiker draait QGIS 3.44 op **macOS**, collega's waarschijnlijk op Windows. Mogelijk is er geen shapely 2.1+ (dus geen `coverage_simplify` en geen `voronoi_polygons(ordered=...)`); dat moet stil terugvallen. CI test met shapely 2.0. Gebruik geen shapely-functies of -parameters van na 2.0 zonder terugval.
- Stap 1 en 2 controleren vóór het rekenen dat het uitvoerbestand geen invoer is en niet in het project geladen is (`controleer_uitvoer`); houd dat zo.
- Bewaar in PyQGIS-tests een referentie naar de provider.
- Schrijf geen `fid` mee naar GeoPackage-uitvoer.
- Overpass-servers geven geregeld 502/429. Altijd terugvallen op de volgende server en een duidelijke fout met het QuickOSM-alternatief.
- Testdata zijn OSM-gegevens (ODbL): bronvermelding in README houden.
