# SMP deelgebieden

QGIS-plugin om een projectgebied voor soortenmonitoring op te delen in **fietsrondes van maximaal X km**,
met per ronde de **kortste route** langs alle te tellen straten, steegjes en paden.

## Installeren
1. Download `smp_deelgebieden-<versie>.zip` (of maak hem met `python scripts/maak_zip.py`).
2. QGIS → *Plugins → Beheren en installeren → Installeren vanaf ZIP*. Sta zo nodig experimentele plugins toe.
3. In de Processing Toolbox verschijnt **SMP deelgebieden**.
4. Had je de oude versie **Ekoza fietsrondes** geïnstalleerd? Verwijder die dan (Plugins → Beheren en installeren → Geïnstalleerd), anders staan beide in de toolbox.

Nodig: QGIS 3.44 of nieuwer. De standaardinstallatie voor Windows bevat alles wat de plugin nodig heeft
(shapely, numpy, networkx). Op **macOS** ontbreekt networkx mogelijk; installeer dat dan eenmalig in Terminal met
`/Applications/QGIS.app/Contents/MacOS/bin/python3 -m pip install --user networkx "shapely>=2"` en start QGIS opnieuw.

## Gebruik
1. **Netwerk maken**: kies de projectgrens. Wegen en gebouwen kun je leeg laten (dan komen ze uit OpenStreetMap) of zelf opgeven (QuickOSM, BAG). Kijk de laag `netwerk` na: kolommen `status`, `gewicht`, `reden`. Pas aan waar nodig en sla op.
2. **Deelgebieden en routes**: kies het netwerk, de grens en het maximum aantal km (standaard 7). Eventueel ook vaste grenzen (hoofdweg, water) en een maximumoppervlak.

Uitvoer: deelgebieden (met km en ha), route per gebied, startpunten en een GPX per ronde.

Achtergrond, algoritme en keuzes: [`docs/BRIEF.md`](docs/BRIEF.md).

## Ontwikkelen
Zie [`CLAUDE.md`](CLAUDE.md) voor structuur, afspraken en commando's.

## Licentie
De plugin (`smp_deelgebieden/`) valt onder de **GNU General Public License, versie 2 of later**
(GPL-2.0-or-later), net als QGIS zelf; zie [`smp_deelgebieden/LICENSE`](smp_deelgebieden/LICENSE).
Dat is ook wat de officiële QGIS-pluginrepository vraagt. De rest van de repository `qgis-tools` valt
onder de MIT-licentie in de hoofdmap.

## Bronnen
Testdata in `testdata/`: © OpenStreetMap-bijdragers, beschikbaar onder de Open Database License (ODbL).
