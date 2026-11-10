# Ekoza fietsrondes

QGIS-plugin om een projectgebied voor soortenmonitoring op te delen in **fietsrondes van maximaal X km**,
met per ronde de **kortste route** langs alle te tellen straten, steegjes en paden.

## Installeren
1. Download `ekoza_fietsrondes-<versie>.zip` (of maak hem met `python scripts/maak_zip.py`).
2. QGIS → *Plugins → Beheren en installeren → Installeren vanaf ZIP*. Sta zo nodig experimentele plugins toe.
3. In de Processing Toolbox verschijnt **Ekoza fietsrondes**. Draai eenmalig **0. Benodigdheden controleren** en start QGIS opnieuw.

## Gebruik
1. **Netwerk maken**: kies de projectgrens. Wegen en gebouwen kun je leeg laten (dan komen ze uit OpenStreetMap) of zelf opgeven (QuickOSM, BAG). Kijk de laag `netwerk` na: kolommen `status`, `gewicht`, `reden`. Pas aan waar nodig en sla op.
2. **Deelgebieden en routes**: kies het netwerk, de grens en het maximum aantal km (standaard 7). Eventueel ook vaste grenzen (hoofdweg, water) en een maximumoppervlak.

Uitvoer: deelgebieden (met km en ha), route per gebied, startpunten en een GPX per ronde.

Achtergrond, algoritme en keuzes: [`docs/BRIEF.md`](docs/BRIEF.md).

## Ontwikkelen
Zie [`CLAUDE.md`](CLAUDE.md) voor structuur, afspraken en commando's.

## Bronnen
Testdata in `testdata/`: © OpenStreetMap-bijdragers, beschikbaar onder de Open Database License (ODbL).
