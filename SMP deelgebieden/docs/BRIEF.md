# Brief: SMP deelgebieden (voorheen "Ekoza fietsrondes")

*Stand van zaken 10 november 2026, v0.3.2. Overdracht van de verkenning in Claude (chat) naar dit repo.*

## 1. Aanleiding en doel

Ekoza doet soortenmonitoring in bebouwd gebied (SMP: onder meer huismus, gierzwaluw, vleermuizen).
Een projectgebied wordt opgedeeld in **deelgebieden**. Elk deelgebied wordt door één collega **op de
fiets** geïnventariseerd. Eisen:

- **Maximaal 7 km fietsen per ronde** (onderhandelbaar; de tool rekent met elk maximum).
- Er komt mogelijk ook een **maximumoppervlak** per deelgebied (nog onbekend; zit er al in als optie).
- Alle relevante straten, steegjes en paden moeten **minstens één keer** gereden worden.
- Grenzen moeten voor collega's **logisch** zijn (geen kronkels of enclaves).
- Per ronde een **route** (volgorde, GPX), zodat de veldwerker niet zelf hoeft te puzzelen.

Collega's zijn enthousiast; dit repo maakt er een onderhouden teamtool van.

## 2. Wat er nu is

Een **QGIS-plugin** (Processing Toolbox-groep "SMP deelgebieden") plus losse scripts, met een
rekenkern zonder QGIS-afhankelijkheid.

| Onderdeel | Bestand | Wat |
|---|---|---|
| Rekenkern | `smp_deelgebieden/fietsronde_kern.py` | Alle logica; alleen shapely ≥ 2, numpy, networkx |
| QGIS-laag | `smp_deelgebieden/algoritmes.py` | 3 Processing-algoritmes, I/O tussen QGIS en "records", opmaak |
| Plugin-skelet | `__init__.py`, `plugin.py`, `provider.py`, `metadata.txt` | Standaard QGIS-plugin, `hasProcessingProvider=yes` |
| CLI | `scripts/stap1_netwerk.py`, `scripts/stap2_indelen.py` | Zelfde kern, I/O via geopandas |
| Tests | `tests/test_kern.py` (pytest), `tests/qgis_rooktest.py` (PyQGIS) | Zie §7 |
| Testdata | `testdata/westervoort_osm.gpkg` | Grens `gangstervoort`, OSM-lagen `highway` en `building` (© OpenStreetMap-bijdragers, ODbL) |

### Werkwijze voor de gebruiker
0. **Benodigdheden controleren**: kijkt of shapely/numpy/networkx in de Python van QGIS zitten en installeert met `pip --user` wat ontbreekt.
1. **Netwerk maken**: grens + wegen (+ gebouwen) → laag `netwerk` met per stuk `status` (verplicht / optioneel / uit), `gewicht`, `reden`, `bebouwd`. De gebruiker kijkt dit na en past het aan in QGIS (status wijzigen, steegjes bijtekenen met snappen).
2. **Deelgebieden en routes**: netwerk + grens (+ vaste grenslijnen) → `deelgebieden` (vlakken met km/ha), `route_lijn`, `start`, `route` (elk stuk met volgnummer en soort *tellen / herhaling / verbinding*), plus een GPX per ronde.

## 3. Begrippen

- **verplicht**: moet in een ronde geteld worden; hoort bij precies één deelgebied.
- **optioneel**: mag gebruikt worden om ergens te komen, hoeft niet geteld.
- **uit**: doet niet mee (stoep naast straat, privé, snelweg, stukjes binnen een samengevoegd kruispunt).
- **gewicht**: kostenfactor bij het route kiezen (1 = normaal; 2 = "lopen met de fiets"; 5 = trap). De gerapporteerde lengtes zijn altijd echte meters.
- **ronde / route**: gesloten rit die alle verplichte stukken van één deelgebied rijdt.

## 4. Algoritme

### Stap 1: netwerk (`maak_netwerk`)
1. Wegen knippen op grens + marge (150 m; buiten de grens alleen optioneel, als verbinding).
2. Opknippen op gedeelde OSM-knopen (kruispunten).
3. **Classificatie per wegtype** (`TYPES`, plus uitzonderingen in `classificeer`): steeg altijd verplicht, stoep uit, `access=private` uit, `bicycle=no` → gewicht 2.
4. **Knopen samenvoegen**: rotondes (hele ring) en kruispunten binnen 12 m worden één knoop; eindpunten schuiven naar het clustermidden.
5. **Bebouwing** (standaard aan; `_bebouwing`):
   - Gebouwen filteren: `GEEN_GEBOUW` (schuur, garage, kas, …) en < 30 m² tellen niet.
   - `bebouwd` = aandeel van het wegstuk binnen 25 m van een gebouw.
   - Straattypen: verplicht als `bebouwd ≥ 0,25`, anders optioneel.
   - Paden (voet/fiets/service/track): verplicht als langs bebouwing, **behalve** als ze voor ≥ 50% binnen 20 m van een straat lopen (begeleidend pad volgt de straat; anders tel je dubbel).
6. **Gelijktrekken** (`_gelijktrekken`): een afwijkend stuk (< 75 m) binnen een straat met dezelfde naam, met aan beide kanten de andere status, krijgt de status van de straat.
7. **Gescheiden rijbanen**: zelfde naam, beide `oneway=yes`, parallel binnen 30 m → de langste wordt optioneel.

### Stap 2: indelen en routes (`indelen`)
- **Routelengte** = *rural postman*: alle verplichte stukken plus de verbindingen tussen losse delen (MST over kortste paden) plus een minimum-gewicht-koppeling van oneven knopen (`nx.min_weight_matching`, alleen de 10 dichtstbijzijnde kandidaten per knoop). Dat is inclusief doodlopende straten heen en terug. In het testgebied is de route ongeveer 40–50% langer dan de straatlengte.
- **Indelen** (`_groei`): steeds het buitenste nog niet ingedeelde stuk als zaad nemen, stukken op netwerkafstand toevoegen (exponentieel + binair zoeken) tot de route vol is. Daarna kleine restgebieden samenvoegen en **gelijktrekken**: het kleinste budget zoeken dat hetzelfde aantal gebieden oplevert (langste ronde zo kort mogelijk).
- **Zones**: zelf getekende grenslijnen delen de grens op met `polygonize`; elke zone, en elk los netwerkdeel, wordt apart ingedeeld. Een straat óp een grenslijn gaat als geheel naar één kant. Losse netwerkdelen met minder dan 300 m verplicht worden overgeslagen en gemeld (kolom `los`).
- **Gladmaken** (`_maak_glad`, standaard 150 m): een straat waarvan de omgeving vooral bij een ander gebied hoort, verhuist, mits beide rondes binnen het maximum blijven. 150 m werkte het best (75 doet weinig, 250 minder goed).
- **Enclaves herstellen**: losliggende stukjes van een vlak naar het omringende gebied, als dat past.
- **Vlakken**: Voronoi rond punten (om de 10 m) op de verplichte straten, per zone, geknipt op de zone; daarna `coverage_simplify` (15 m, alleen als shapely ≥ 2.1).
- **Route**: Euler-circuit (`nx.eulerian_circuit`) op verplichte + extra stukken, met de start op de knoop die het dichtst bij het midden van het vlak ligt.

## 5. Resultaten testgebied (Westervoort, ±385 ha)

| Variant | Verplicht | Gebieden (7 km) | Te fietsen | Rekentijd stap 2 |
|---|---|---|---|---|
| Alleen wegtype | 63 km | 14 | ±91 km | ±70 s |
| Bebouwing (standaard) | 74 km | 16 | ±106 km | ±150 s |
| Wegtype + vaste grens Rivierweg | 63 km | 14 (zone oost: rondes 5–6 km) | ±90 km | ±80 s |

Bij 7,5 km waren het 13 gebieden en bij 8 km 12 (variant "alleen wegtype").

## 6. Besluiten en waarom

| Besluit | Reden |
|---|---|
| Kern zonder geopandas, alleen shapely/numpy/networkx | Minder kans op conflicten met de Python van QGIS (numpy/pandas-versies) |
| Plugin met Processing-provider (geen losse scripts in de toolbox) | Installeren vanaf ZIP, eigen groep, help per stap, en later in de pluginrepository |
| Netwerk eerst als bewerkbare laag, pas daarna indelen | Ecologen kennen het gebied; automatische classificatie blijft een voorstel |
| Verplicht op basis van **bebouwing**, niet wegtype | Voor gebouwbewonende soorten telt de gevel, niet het wegtype |
| Echte routelengte als harde grens (niet een vuistregel) | De vuistregel "×1,2–1,4" bleek in hofjeswijken te optimistisch |
| Gladheid en vaste grenzen nooit ten koste van het maximum | 7 km is de harde eis; mooiere vormen alleen binnen die ruimte |
| Wegen/gebouwen uit OSM (Overpass, 3 servers als terugval) of eigen laag | QuickOSM was lastig voor collega's (de standaardserver geeft vaak 502) |

## 7. Testen en kwaliteit

- `pytest -q`: 6 tests, ±4,5 min. Bevat invarianten (dekking, maximum, gesloten routes, vlakken zonder overlap), een regressietest (Rivierweg-snippers) en parsers. Snel: `pytest -q -m "not traag"`.
- `tests/qgis_rooktest.py`: stap 1 + 2 via Processing, controle op laden, opmaak en GPX, en de afvang van een verkeerde grens. Getest met QGIS 3.34 (Ubuntu, Python 3.12).
- **Niet getest** (geen internet in de verkenningsomgeving): de Overpass-download in QGIS en `pip install` via stap 0 op Windows en macOS.
- De gebruiker draait **QGIS 3.44 op macOS** (Python 3.12, GEOS 3.14). Daar hebben stap 1 en 2 met de juiste lagen nog niet volledig gedraaid (zie §8).

## 8. Bekende problemen en lessen

- **Verkeerde grenslaag** (gebouwenlaag gekozen) maakte stap 1 extreem traag en de uitkomst onbruikbaar → nu afgevangen in `controleer_grens` (zelfde laag als een andere invoer, of > 50 vlakken).
- Een verplicht straatje dat los van het netwerk lag, liet stap 2 vastlopen → nu: kleine losse delen overslaan, `route()` geeft `inf` voor onbereikbare combinaties.
- `fid` uit invoer-GeoPackages veroorzaakte UNIQUE-fouten bij het schrijven → wordt niet meegeschreven.
- Gedegenereerde lijnen na het samenvoegen van knopen → op `uit`; plus `GeometryNoCheck` in de algoritmes.
- Bij het testen in PyQGIS moet je een referentie naar de provider bewaren, anders wordt hij opgeruimd.
- Bij een `TEMPORARY_OUTPUT` meldde QGIS op macOS aan het eind "Execution failed" tijdens "Loading resulting layers". Nog niet onderzocht; het advies is nu een vaste bestandsnaam.

## 9. Backlog (voorstel, nog te prioriteren met het team)

**Robuustheid en installatie**
1. Stap 0 (`pip --user`) controleren op macOS (QGIS.app) en Windows (OSGeo4W/standalone); `python_exe()` zo nodig aanpassen.
2. Overpass-download in de echte QGIS testen (timeouts, grote gebieden, foutmeldingen).
3. "Execution failed" bij tijdelijke uitvoer op macOS uitzoeken; eventueel standaard naast het QGIS-project opslaan.
4. CI (GitHub Actions): `pytest -m "not traag"` op elke push, de volledige set nightly; eventueel een QGIS-docker voor de rooktest.

**Data en kwaliteit**
5. **BAG-panden via PDOK** (OGC API / WFS) als standaard gebouwenbron in plaats van OSM.
6. Classificatieregels laten nakijken door ecologen (afstand 25 m, drempel 0,25, achterpaden, flats, bedrijventerreinen). Eventueel per project instelbaar via een tabel.
7. Voorbeeldlagen en stijlen (`.qml`) meeleveren; de netwerklaag visueel onderscheid laten maken tussen "straat" en "pad".

**Functionaliteit**
8. Startpunt kunnen kiezen (bv. station, parkeerplaats, eigen adres) of per gebied vastzetten.
9. Export naar **QField** (route + deelgebied als laag in het veldproject) — sluit aan op het lopende QField-traject (SMP Westervoort).
10. Route-instructies/afslagen of een printbare kaart per ronde (atlas-layout).
11. Meerdere bezoekronden plannen (bv. verschillende startrichting per bezoek).
12. Snelheid: stap 2 duurt ±2,5 min voor 75 km; kan sneller met caching van de koppeling of parallelle zones.

**Beheer**
13. Licentie kiezen (de officiële QGIS-pluginrepository vereist GPL-2.0+ of compatibel) en eventueel publiceren.
14. Versiebeheer en changelog; `metadata.txt` (versie, changelog, `homepage`, `tracker`, `repository`) vullen zodra de GitHub-repo bestaat.

## 10. Open vragen voor het team
- Is 7 km hard, of mag een ronde in uitzonderingen iets langer zijn?
- Wat is het maximumoppervlak per deelgebied, en geldt dat naast de 7 km?
- Moeten achterpaden standaard mee, ook als ze in OSM ontbreken? (Dan moet er bijgetekend worden.)
- Wie beheert de plugin, en via welk kanaal installeren collega's updates (ZIP of pluginrepository)?
