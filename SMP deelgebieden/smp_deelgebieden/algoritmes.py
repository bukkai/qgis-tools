"""Processing-algoritmes van de SMP deelgebieden-toolbox."""
import importlib, json, os, sys
from urllib.parse import quote

from qgis.PyQt.QtCore import QVariant, QUrl, QByteArray
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    QgsBlockingNetworkRequest, QgsNetworkAccessManager, QgsCategorizedSymbolRenderer, QgsFeatureRequest, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsFeature, QgsField, QgsGeometry, QgsPalLayerSettings,
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingContext, QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface, QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition, QgsProcessingParameterFeatureSource, QgsProcessingParameterEnum,
    QgsProcessingParameterFileDestination, QgsProcessingParameterNumber, QgsProject,
    QgsRendererCategory, QgsSymbol, QgsVectorFileWriter, QgsVectorLayer,
    QgsVectorLayerSimpleLabeling, QgsWkbTypes, QgsTextFormat, QgsTextBufferSettings,
)

from .installatie import NODIG, installeer, ontbrekend

OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
HTTP_REDEN = {400: "query niet geaccepteerd (400)", 406: "verzoek geweigerd (406)",
              429: "server is te druk, te veel verzoeken (429)", 502: "server tijdelijk onbereikbaar (502)",
              503: "server tijdelijk onbereikbaar (503)", 504: "server reageerde niet op tijd (504)"}
KLEUREN = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
           "#bcbd22", "#17becf", "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5", "#c49c94",
           "#f7b6d2", "#dbdb8d", "#9edae5", "#393b79"]
_BEWAAR = []      # post-processors moeten blijven bestaan tot de lagen geladen zijn


# ------------------------------------------------------------------------------------------
# afhankelijkheden
# ------------------------------------------------------------------------------------------
def kern():
    mist = ontbrekend()
    if mist:
        raise QgsProcessingException(
            "Deze Python-pakketten ontbreken in QGIS: " + ", ".join(mist) +
            ". Draai eerst '0. Benodigdheden controleren' (met 'installeren' aangevinkt).")
    from . import fietsronde_kern
    return fietsronde_kern


# ------------------------------------------------------------------------------------------
# QGIS <-> records
# ------------------------------------------------------------------------------------------
def _waarde(v):
    if v is None or (isinstance(v, QVariant) and v.isNull()):
        return None
    if hasattr(v, "isNull") and callable(v.isNull) and not isinstance(v, (str, bytes)):
        try:
            if v.isNull():
                return None
        except TypeError:
            pass
    return v


def _naar_shapely(geom):
    import shapely
    g = QgsGeometry(geom)
    if QgsWkbTypes.isCurvedType(g.wkbType()):
        g = QgsGeometry(g.constGet().segmentize())
    a = g.get()
    a.dropZValue()
    a.dropMValue()
    return shapely.from_wkb(bytes(g.asWkb()))


def bron_naar_records(bron, doel_crs, context, feedback=None):
    ct = None
    if bron.sourceCrs() != doel_crs:
        ct = QgsCoordinateTransform(bron.sourceCrs(), doel_crs, context.transformContext())
    namen = [f.name() for f in bron.fields()]
    uit = []
    for f in bron.getFeatures():
        if feedback and feedback.isCanceled():
            break
        g = f.geometry()
        if g is None or g.isEmpty():
            continue
        if ct:
            g = QgsGeometry(g)
            g.transform(ct)
        r = {n: _waarde(v) for n, v in zip(namen, f.attributes())}
        r["geometry"] = _naar_shapely(g)
        uit.append(r)
    return uit


def records_naar_gpkg(records, crs, pad, laagnaam, geomtype, context):
    import shapely
    if not records:
        raise QgsProcessingException(f"Geen objecten voor laag '{laagnaam}'")
    vl = QgsVectorLayer(f"{geomtype}", laagnaam, "memory")
    vl.setCrs(crs)
    sleutels = [k for k in records[0] if k not in ("geometry", "fid")]   # fid maakt GPKG zelf
    velden = []
    for k in sleutels:
        voorbeeld = next((r[k] for r in records if r.get(k) is not None), "")
        if isinstance(voorbeeld, bool) or isinstance(voorbeeld, int):
            t = QVariant.LongLong
        elif isinstance(voorbeeld, float):
            t = QVariant.Double
        else:
            t = QVariant.String
        velden.append(QgsField(k, t))
    pr = vl.dataProvider()
    pr.addAttributes(velden)
    vl.updateFields()
    feats = []
    for r in records:
        f = QgsFeature(vl.fields())
        g = QgsGeometry()
        g.fromWkb(shapely.to_wkb(r["geometry"]))
        if geomtype.startswith("Multi"):
            g.convertToMultiType()
        f.setGeometry(g)
        attrs = []
        for k, fld in zip(sleutels, velden):
            v = r.get(k)
            if v is not None and fld.type() == QVariant.String:
                v = str(v)
            elif v is not None and fld.type() == QVariant.LongLong:
                v = int(v)
            elif v is not None and fld.type() == QVariant.Double:
                v = float(v)
            attrs.append(v)
        f.setAttributes(attrs)
        feats.append(f)
    pr.addFeatures(feats)
    opt = QgsVectorFileWriter.SaveVectorOptions()
    opt.driverName = "GPKG"
    opt.layerName = laagnaam
    opt.fileEncoding = "UTF-8"
    opt.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteLayer if os.path.exists(pad)
                                else QgsVectorFileWriter.CreateOrOverwriteFile)
    res = QgsVectorFileWriter.writeAsVectorFormatV3(vl, pad, context.transformContext(), opt)
    if res[0] != QgsVectorFileWriter.NoError:
        raise QgsProcessingException(f"Schrijven van '{laagnaam}' naar {pad} mislukt: {res[1]}")


def laad_na_afloop(context, pad, laagnaam, weergavenaam, stijl=None):
    vl = QgsVectorLayer(f"{pad}|layername={laagnaam}", weergavenaam, "ogr")
    if not vl.isValid():
        return
    context.temporaryLayerStore().addMapLayer(vl)
    details = QgsProcessingContext.LayerDetails(weergavenaam, context.project(), laagnaam)
    if stijl:
        pp = StijlPostProcessor(stijl)
        _BEWAAR.append(pp)
        details.setPostProcessor(pp)
    context.addLayerToLoadOnCompletion(vl.id(), details)


def _bestand(pad):
    return os.path.normcase(os.path.abspath(pad.split("|")[0])) if pad else ""


def controleer_uitvoer(alg, p, context, pad, invoer):
    """Vóór het rekenen: het uitvoerbestand mag geen invoer overschrijven en niet in gebruik zijn."""
    doel = _bestand(pad)
    for naam, tekst in invoer:
        laag = alg.parameterAsVectorLayer(p, naam, context)
        if laag is not None and _bestand(laag.source()) == doel:
            raise QgsProcessingException(
                f"Het uitvoerbestand is hetzelfde bestand als de invoer bij '{tekst}' ({pad}). Het wordt bij het "
                "opslaan helemaal vervangen, dus dan raak je die invoer kwijt. Kies een andere bestandsnaam.")
    project = context.project()
    if project is not None and os.path.exists(pad):
        geladen = sorted({l.name() for l in project.mapLayers().values()
                          if hasattr(l, "source") and _bestand(l.source()) == doel})
        if geladen:
            raise QgsProcessingException(
                f"Het uitvoerbestand {pad} wordt vervangen, maar lagen daaruit staan nog in het project "
                f"({', '.join(geladen)}). Verwijder die lagen eerst uit het project, of kies een andere bestandsnaam.")


def maak_vrij(pad):
    """Bestaand uitvoerbestand weghalen; nette fout als dat niet kan (op Windows: bestand in gebruik)."""
    if not os.path.exists(pad):
        return
    try:
        os.remove(pad)
    except OSError as e:
        raise QgsProcessingException(
            f"Kan {pad} niet vervangen: het bestand is in gebruik ({e.strerror}). Haal lagen uit dit bestand uit "
            "het project of sluit het in andere programma's, en probeer het opnieuw, of kies een andere bestandsnaam.")


def controleer_grens(alg, p, context, recs, naam="GRENS", andere=("GEBOUWEN", "WEGEN", "NETWERK", "GRENSLIJNEN")):
    """Vang een verkeerd gekozen grenslaag af (bv. de gebouwenlaag) voordat het rekenen begint."""
    gekozen = p.get(naam)
    for a in andere:
        if a in p and p.get(a) is not None and str(p.get(a)) == str(gekozen):
            raise QgsProcessingException(
                f"Bij 'Projectgrens' staat dezelfde laag als bij '{a.lower()}'. Kies de laag met de projectgrens.")
    if len(recs) > 50:
        raise QgsProcessingException(
            f"De projectgrens bestaat uit {len(recs)} vlakken. Dat lijkt niet op een projectgrens "
            "(misschien is de gebouwenlaag gekozen?). Kies de laag met de grens van het projectgebied.")


# ------------------------------------------------------------------------------------------
# stijlen
# ------------------------------------------------------------------------------------------
def _symbool(laag, kleur, breedte=None, vulling=None, rand=None, stippel=False):
    s = QgsSymbol.defaultSymbol(laag.geometryType())
    s.setColor(QColor(kleur))
    sl = s.symbolLayer(0)
    if laag.geometryType() == QgsWkbTypes.LineGeometry and breedte is not None:
        s.setWidth(breedte)
        if stippel:
            from qgis.PyQt.QtCore import Qt
            sl.setPenStyle(Qt.DashLine)
    if laag.geometryType() == QgsWkbTypes.PolygonGeometry:
        c = QColor(kleur)
        c.setAlpha(vulling if vulling is not None else 70)
        sl.setFillColor(c)
        sl.setStrokeColor(QColor(rand or "#333333"))
        sl.setStrokeWidth(0.5)
    if laag.geometryType() == QgsWkbTypes.PointGeometry:
        s.setSize(3)
    return s


def stijl_laag(laag, soort):
    if soort == "netwerk":
        cats = [QgsRendererCategory("verplicht", _symbool(laag, "#d7191c", 0.7), "verplicht"),
                QgsRendererCategory("optioneel", _symbool(laag, "#7f7f7f", 0.3), "optioneel"),
                QgsRendererCategory("uit", _symbool(laag, "#c9c9c9", 0.2, stippel=True), "uit")]
        laag.setRenderer(QgsCategorizedSymbolRenderer("status", cats))
    elif soort in ("deelgebieden", "route_lijn"):
        idx = laag.fields().indexOf("gebied")
        waarden = sorted(v for v in laag.uniqueValues(idx) if v is not None)
        cats = [QgsRendererCategory(v, _symbool(laag, KLEUREN[(int(v) - 1) % len(KLEUREN)], 0.6), f"Gebied {v}")
                for v in waarden]
        laag.setRenderer(QgsCategorizedSymbolRenderer("gebied", cats))
        if soort == "deelgebieden":
            pal = QgsPalLayerSettings()
            pal.fieldName = "'Gebied ' || \"gebied\" || '\\n' || format_number(\"route_km\", 1) || ' km'"
            pal.isExpression = True
            tf = QgsTextFormat()
            tf.setSize(10)
            buf = QgsTextBufferSettings()
            buf.setEnabled(True)
            buf.setSize(1)
            tf.setBuffer(buf)
            pal.setFormat(tf)
            laag.setLabeling(QgsVectorLayerSimpleLabeling(pal))
            laag.setLabelsEnabled(True)
    elif soort == "start":
        laag.renderer().setSymbol(_symbool(laag, "#000000"))
    laag.triggerRepaint()


class StijlPostProcessor(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, soort):
        super().__init__()
        self.soort = soort

    def postProcessLayer(self, layer, context, feedback):
        if isinstance(layer, QgsVectorLayer):
            stijl_laag(layer, self.soort)


# ------------------------------------------------------------------------------------------
# 0. Benodigdheden
# ------------------------------------------------------------------------------------------
class ControleerAlgoritme(QgsProcessingAlgorithm):
    INSTALLEER = "INSTALLEER"

    def name(self): return "0_benodigdheden"
    def displayName(self): return "0. Benodigdheden controleren"
    def createInstance(self): return ControleerAlgoritme()
    def flags(self): return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def shortHelpString(self):
        return ("Controleert of shapely (2.x), numpy en networkx beschikbaar zijn in de Python van QGIS. "
                "Met 'installeren' aangevinkt worden ontbrekende pakketten met pip geïnstalleerd "
                "(in je gebruikersmap, geen beheerdersrechten nodig). De numpy van QGIS wordt daarbij niet "
                "bijgewerkt. Daarna QGIS opnieuw starten.\n\n"
                "Lukt installeren niet:\n"
                "• Windows: open de OSGeo4W Shell (startmenu, map QGIS) en typ\n"
                "  python -m pip install --user networkx \"shapely>=2\"\n"
                "• macOS: open Terminal en typ\n"
                "  /Applications/QGIS.app/Contents/MacOS/bin/python3 -m pip install --user networkx \"shapely>=2\"\n"
                "  (staat QGIS ergens anders, of heet de app anders, pas het pad dan aan).")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterBoolean(self.INSTALLEER, "Ontbrekende pakketten installeren", True))

    def processAlgorithm(self, p, context, feedback):
        mist = ontbrekend()
        feedback.pushInfo(f"Python: {sys.version.split()[0]} ({sys.exec_prefix})")
        for mod in NODIG:
            try:
                m = importlib.import_module(mod)
                feedback.pushInfo(f"  {mod}: {getattr(m, '__version__', '?')}")
            except Exception:
                feedback.pushInfo(f"  {mod}: ONTBREEKT")
        if not mist:
            feedback.pushInfo("Alles aanwezig. Je kunt stap 1 en 2 gebruiken.")
            return {}
        if not self.parameterAsBoolean(p, self.INSTALLEER, context):
            feedback.reportError("Ontbrekend: " + ", ".join(mist))
            return {}
        gelukt, regels = installeer(mist, log=feedback.pushInfo)
        for regel in regels:
            feedback.pushInfo(regel)
        if not gelukt:
            raise QgsProcessingException("Installeren is niet gelukt, zie de meldingen hierboven. Handmatig "
                                         "installeren kan ook, zie de help van deze stap.")
        feedback.pushInfo("Geïnstalleerd. Start QGIS opnieuw en draai deze stap nog eens ter controle.")
        return {}


# ------------------------------------------------------------------------------------------
# 1. Netwerk maken
# ------------------------------------------------------------------------------------------
class NetwerkAlgoritme(QgsProcessingAlgorithm):
    GRENS, WEGEN, BEPAAL, GEBOUWEN, AFSTAND, MARGE, SAMENVOEGEN, RIJBAAN, MIN_OPP, DREMPEL, PARALLEL, UITVOER = \
        "GRENS", "WEGEN", "BEPAAL", "GEBOUWEN", "AFSTAND", "MARGE", "SAMENVOEGEN", "RIJBAAN", "MIN_OPP", \
        "DREMPEL", "PARALLEL", "UITVOER"
    KORT = "KORT"
    KEUZES = ["Bebouwing: verplicht = langs gebouwen (aanbevolen)", "Alleen wegtype"]

    def name(self): return "1_netwerk"
    def displayName(self): return "1. Netwerk maken"
    def createInstance(self): return NetwerkAlgoritme()

    def shortHelpString(self):
        return (
            "Maakt van de wegen binnen de projectgrens een fietsnetwerk om in te delen.\n\n"
            "Wegenlaag leeg laten = wegen worden automatisch uit OpenStreetMap gehaald. Of geef een "
            "QuickOSM-laag (key 'highway', value leeg).\n\n"
            "Elk stuk krijgt een status: verplicht (moet geteld), optioneel (mag gebruikt worden om "
            "ergens te komen) of uit. In de kolom 'reden' staat waarom.\n\n"
            "Verplicht bepalen op basis van bebouwing (aanbevolen): een straat is verplicht als er gebouwen "
            "binnen de opgegeven afstand staan; straten zonder bebouwing worden optioneel. Voet- en "
            "fietspaden langs bebouwing (bv. paden waar de voordeuren aan liggen) worden verplicht, behalve "
            "als ze naast een straat lopen (begeleidend fietspad: dat volgt de straat). Korte afwijkende stukjes "
            "binnen een straat worden gelijkgetrokken met de rest van die straat. Gebouwenlaag leeg = gebouwen "
            "uit OpenStreetMap. Een BAG-pandenlaag (PDOK) is nauwkeuriger. Schuurtjes, garages en gebouwen "
            "kleiner dan het minimum tellen niet. Kolom 'bebouwd' = aandeel van het stuk langs bebouwing.\n\n"
            "Alleen wegtype: woonstraten en doorgaande wegen verplicht, paden optioneel.\n\n"
            "Rotondes en kruispunten die "
            "dicht bij elkaar liggen worden één knoop; van gescheiden rijbanen wordt er één optioneel.\n\n"
            "Daarna: netwerklaag nakijken en aanpassen (status, gewicht, steegjes bijtekenen met "
            "snappen aan), dan '2. Deelgebieden en routes'.\n\n"
            "Gewicht: 1 = normaal, hoger = liever vermijden (telt bij het routekiezen als langer).")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.GRENS, "Projectgrens", [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.WEGEN, "Wegen (OSM highway-lijnen; leeg = automatisch downloaden)",
            [QgsProcessing.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.BEPAAL, "Verplicht bepalen op basis van", self.KEUZES,
                                                     defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.GEBOUWEN, "Gebouwen (BAG-panden of OSM; leeg = automatisch uit OSM)",
            [QgsProcessing.TypeVectorPolygon], optional=True))
        for naam, tekst, std, adv in (
                (self.AFSTAND, "Langs bebouwing = gebouw binnen (m)", 25, False),
                (self.MARGE, "Marge buiten de grens als verbinding (m)", 150, False),
                (self.MIN_OPP, "Gebouwen kleiner dan (m²) tellen niet", 30, True),
                (self.DREMPEL, "Minimaal aandeel van een wegstuk langs bebouwing (0-1)", 0.25, True),
                (self.PARALLEL, "Pad binnen zoveel m van een straat = begeleidend pad (m)", 20, True),
                (self.KORT, "Afwijkende stukjes korter dan (m) gelijktrekken met de straat", 75, True),
                (self.SAMENVOEGEN, "Kruispunten binnen zoveel m samenvoegen", 12, True),
                (self.RIJBAAN, "Max. afstand tussen gescheiden rijbanen (m)", 30, True)):
            par = QgsProcessingParameterNumber(naam, tekst, QgsProcessingParameterNumber.Double, std, minValue=0)
            if adv:
                par.setFlags(par.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
            self.addParameter(par)
        self.addParameter(QgsProcessingParameterFileDestination(
            self.UITVOER, "Netwerk (GeoPackage)", "GeoPackage (*.gpkg)"))

    def processAlgorithm(self, p, context, feedback):
        context.setInvalidGeometryCheck(QgsFeatureRequest.GeometryNoCheck)
        k = kern()
        import shapely
        grens_bron = self.parameterAsSource(p, self.GRENS, context)
        wegen_bron = self.parameterAsSource(p, self.WEGEN, context)
        pad = self.parameterAsFileOutput(p, self.UITVOER, context)
        controleer_uitvoer(self, p, context, pad, ((self.GRENS, "Projectgrens"), (self.WEGEN, "Wegen"),
                                                   (self.GEBOUWEN, "Gebouwen")))
        marge = self.parameterAsDouble(p, self.MARGE, context)
        crs = grens_bron.sourceCrs()
        if not crs.isValid() or crs.isGeographic():
            crs = QgsCoordinateReferenceSystem("EPSG:28992")
            feedback.pushInfo("Grens is niet in meters; er wordt gerekend in RD New (EPSG:28992).")
        grens_recs = bron_naar_records(grens_bron, crs, context)
        if not grens_recs:
            raise QgsProcessingException("De grenslaag is leeg.")
        controleer_grens(self, p, context, grens_recs)
        grens = shapely.union_all([r["geometry"] for r in grens_recs])
        if wegen_bron is not None:
            wegen = bron_naar_records(wegen_bron, crs, context, feedback)
        else:
            wegen = self.download(k, grens, marge, crs, context, feedback, "highway")
        gebouwen = None
        if self.parameterAsEnum(p, self.BEPAAL, context) == 0:
            geb_bron = self.parameterAsSource(p, self.GEBOUWEN, context)
            if geb_bron is not None:
                gebouwen = bron_naar_records(geb_bron, crs, context, feedback)
            else:
                gebouwen = self.download(k, grens, marge, crs, context, feedback, "building")
        if feedback.isCanceled():
            return {}
        feedback.setProgress(30)
        net = k.maak_netwerk(grens, wegen, marge, self.parameterAsDouble(p, self.SAMENVOEGEN, context),
                             self.parameterAsDouble(p, self.RIJBAAN, context), log=feedback.pushInfo,
                             gebouwen=gebouwen, afstand=self.parameterAsDouble(p, self.AFSTAND, context),
                             min_opp=self.parameterAsDouble(p, self.MIN_OPP, context),
                             drempel=self.parameterAsDouble(p, self.DREMPEL, context),
                             parallel=self.parameterAsDouble(p, self.PARALLEL, context),
                             kort=self.parameterAsDouble(p, self.KORT, context))
        feedback.setProgress(90)
        maak_vrij(pad)
        records_naar_gpkg(net, crs, pad, "netwerk", "LineString", context)
        records_naar_gpkg([dict(geometry=grens)], crs, pad, "grens", "MultiPolygon", context)
        laad_na_afloop(context, pad, "netwerk", "netwerk", "netwerk")
        feedback.pushInfo(f"\nGeschreven: {pad}\nKijk de laag 'netwerk' na (kolommen status, gewicht, reden), "
                          "sla de wijzigingen op en draai dan stap 2.")
        return {self.UITVOER: pad}

    def download(self, k, grens, marge, crs, context, feedback, sleutel="highway"):
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        naar_wgs = QgsCoordinateTransform(crs, wgs, context.transformContext())
        terug = QgsCoordinateTransform(wgs, crs, context.transformContext())
        g = QgsGeometry.fromWkt(grens.buffer(marge + 50).envelope.wkt)
        g.transform(naar_wgs)
        bb = g.boundingBox()
        query = k.overpass_query(bb.yMinimum(), bb.xMinimum(), bb.yMaximum(), bb.xMaximum(), sleutel)
        wat = "wegen" if sleutel == "highway" else "gebouwen"
        data, fouten = None, []
        # De standaard netwerk-timeout van QGIS (60 s) is korter dan de tijd die we de server geven;
        # tijdelijk verhogen en daarna altijd terugzetten.
        oud = QgsNetworkAccessManager.timeout()
        QgsNetworkAccessManager.setTimeout(max(oud, (k.OVERPASS_TIMEOUT + 30) * 1000))
        try:
            for server in OVERPASS:
                if feedback.isCanceled():
                    return []
                host = QUrl(server).host()
                feedback.pushInfo(f"{wat.capitalize()} downloaden van {host} ...")
                req = QNetworkRequest(QUrl(server))
                req.setHeader(QNetworkRequest.ContentTypeHeader, "application/x-www-form-urlencoded")
                br = QgsBlockingNetworkRequest()
                fout = br.post(req, QByteArray(("data=" + quote(query)).encode()), feedback=feedback)
                if fout != QgsBlockingNetworkRequest.NoError:
                    status = br.reply().attribute(QNetworkRequest.HttpStatusCodeAttribute)
                    reden = HTTP_REDEN.get(status, br.errorMessage())
                    feedback.pushInfo(f"  mislukt: {reden}")
                    fouten.append(f"{host}: {reden}")
                    continue
                inhoud = bytes(br.reply().content()).decode("utf-8", errors="replace")
                try:
                    data = json.loads(inhoud)
                except Exception:
                    begin = " ".join(inhoud.split())[:120]
                    feedback.pushInfo(f"  geen bruikbaar antwoord (geen JSON, waarschijnlijk een foutpagina): {begin}")
                    fouten.append(f"{host}: geen bruikbaar antwoord")
                    data = None
                    continue
                probleem = k.overpass_fout(data)
                if probleem:
                    feedback.pushInfo(f"  server meldt: {probleem}")
                    fouten.append(f"{host}: {probleem}")
                    data = None
                    continue
                break
        finally:
            QgsNetworkAccessManager.setTimeout(oud)
        if not data:
            raise QgsProcessingException(
                f"Downloaden van {wat} uit OpenStreetMap is bij alle servers mislukt ({'; '.join(fouten)}). "
                f"Probeer het later opnieuw, of haal ze op met de plugin QuickOSM (key '{sleutel}', value leeg) "
                f"en geef die laag op bij '{wat}'.")
        import shapely
        recs = k.lees_overpass(data) if sleutel == "highway" else k.lees_overpass_vlakken(data)
        if not recs:
            raise QgsProcessingException(
                f"Er zijn geen {wat} gevonden in OpenStreetMap binnen de projectgrens. Klopt de grens "
                "(ligt hij op de goede plek op de kaart)?")
        for r in recs:
            q = QgsGeometry.fromWkt(r["geometry"].wkt)
            q.transform(terug)
            r["geometry"] = shapely.from_wkb(bytes(q.asWkb()))
        feedback.pushInfo(f"{len(recs)} {wat} gedownload")
        return recs


# ------------------------------------------------------------------------------------------
# 2. Deelgebieden en routes
# ------------------------------------------------------------------------------------------
class IndelenAlgoritme(QgsProcessingAlgorithm):
    NETWERK, GRENS, MAX_KM, MAX_HA, GLADHEID, GRENSLIJNEN, VEREENVOUDIG, SNAP, GPX, UITVOER = \
        "NETWERK", "GRENS", "MAX_KM", "MAX_HA", "GLADHEID", "GRENSLIJNEN", "VEREENVOUDIG", "SNAP", "GPX", "UITVOER"

    def name(self): return "2_indelen"
    def displayName(self): return "2. Deelgebieden en routes"
    def createInstance(self): return IndelenAlgoritme()

    def shortHelpString(self):
        return (
            "Deelt het netwerk uit stap 1 op in deelgebieden die elk in één fietsronde van maximaal "
            "X km te doen zijn, en berekent per deelgebied de kortste ronde langs alle verplichte "
            "stukken (inclusief doodlopende straten heen-en-terug en verbindingsstukken).\n\n"
            "Max. oppervlakte (0 = geen): benadering via de omhullende van de straten.\n\n"
            "Gladheid (m): trekt vingers en uitstulpingen recht. Een straat waarvan de omgeving binnen "
            "deze afstand vooral bij een ander gebied hoort, gaat naar dat gebied, zolang beide rondes "
            "onder het maximum blijven. 0 = uit, 150 = aanbevolen.\n\n"
            "Vaste grenzen (optioneel): lijnen die je zelf tekent, bijvoorbeeld langs een hoofdweg, spoor "
            "of watergang. Gebieden gaan daar nooit overheen. Laat de lijnen de projectgrens kruisen, "
            "anders delen ze niets op. Een straat die op zo'n lijn ligt, gaat in zijn geheel naar één kant.\n\n"
            "Uitvoer (GeoPackage): deelgebieden, route_lijn, start (geladen in het project), plus "
            "'route' (elk stuk met volgnummer en soort: tellen / herhaling / verbinding) en 'netwerk' "
            "met kolom gebied. Optioneel een GPX per ronde naast de GeoPackage.\n\n"
            "Rekentijd: ongeveer een minuut per 60 km straat.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.NETWERK, "Netwerk (uit stap 1, eventueel aangepast)", [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.GRENS, "Projectgrens", [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_KM, "Max. lengte per ronde (km)", QgsProcessingParameterNumber.Double, 7.0, minValue=0.5))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_HA, "Max. oppervlakte per gebied (ha, 0 = geen)", QgsProcessingParameterNumber.Double,
            0.0, minValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.GLADHEID, "Gladheid: vingers rechttrekken binnen (m, 0 = uit)", QgsProcessingParameterNumber.Double,
            150.0, minValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.GRENSLIJNEN, "Vaste grenzen (lijnen, bv. hoofdweg of water)",
            [QgsProcessing.TypeVectorLine, QgsProcessing.TypeVectorPolygon], optional=True))
        ver = QgsProcessingParameterNumber(self.VEREENVOUDIG, "Grenzen vereenvoudigen (m, alleen weergave)",
                                           QgsProcessingParameterNumber.Double, 15.0, minValue=0)
        ver.setFlags(ver.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(ver)
        snap = QgsProcessingParameterNumber(self.SNAP, "Bijgetekende lijnen vastknopen binnen (m)",
                                            QgsProcessingParameterNumber.Double, 0.5, minValue=0)
        snap.setFlags(snap.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(snap)
        self.addParameter(QgsProcessingParameterBoolean(self.GPX, "GPX per ronde opslaan", True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.UITVOER, "Indeling (GeoPackage)", "GeoPackage (*.gpkg)"))

    def processAlgorithm(self, p, context, feedback):
        context.setInvalidGeometryCheck(QgsFeatureRequest.GeometryNoCheck)
        k = kern()
        import shapely
        pad = self.parameterAsFileOutput(p, self.UITVOER, context)
        controleer_uitvoer(self, p, context, pad, ((self.NETWERK, "Netwerk"), (self.GRENS, "Projectgrens"),
                                                   (self.GRENSLIJNEN, "Vaste grenzen")))
        net_bron = self.parameterAsSource(p, self.NETWERK, context)
        crs = net_bron.sourceCrs()
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException("Het netwerk moet in een metrisch stelsel staan (bv. RD New).")
        for veld in ("status", "gewicht"):
            if net_bron.fields().indexOf(veld) < 0:
                raise QgsProcessingException(f"Kolom '{veld}' ontbreekt: gebruik het netwerk uit stap 1.")
        net = bron_naar_records(net_bron, crs, context, feedback)
        grens_recs = bron_naar_records(self.parameterAsSource(p, self.GRENS, context), crs, context)
        controleer_grens(self, p, context, grens_recs)
        grens = shapely.union_all([r["geometry"] for r in grens_recs])
        max_ha = self.parameterAsDouble(p, self.MAX_HA, context) or None
        gl_bron = self.parameterAsSource(p, self.GRENSLIJNEN, context)
        grenslijnen = [r["geometry"] for r in bron_naar_records(gl_bron, crs, context)] if gl_bron else None
        try:
            uit = k.indelen(net, grens, self.parameterAsDouble(p, self.MAX_KM, context), max_ha,
                            self.parameterAsDouble(p, self.SNAP, context), log=feedback.pushInfo,
                            gestopt=feedback.isCanceled, voortgang=feedback.setProgress,
                            gladheid=self.parameterAsDouble(p, self.GLADHEID, context),
                            grenslijnen=grenslijnen,
                            vereenvoudig=self.parameterAsDouble(p, self.VEREENVOUDIG, context))
        except k.Afgebroken:
            return {}
        except ValueError as e:
            raise QgsProcessingException(str(e))

        maak_vrij(pad)
        for laag, typ in (("deelgebieden", "MultiPolygon"), ("route_lijn", "LineString"), ("start", "Point"),
                          ("route", "LineString"), ("netwerk", "LineString")):
            records_naar_gpkg(uit[laag], crs, pad, laag, typ, context)
        if self.parameterAsBoolean(p, self.GPX, context):
            naar_wgs = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"),
                                              context.transformContext())
            for r in uit["route_lijn"]:
                g = QgsGeometry.fromWkt(r["geometry"].wkt)
                g.transform(naar_wgs)
                cs = [(pt.x(), pt.y()) for pt in g.asPolyline()]
                gpx = os.path.join(os.path.dirname(pad), f"{os.path.splitext(os.path.basename(pad))[0]}"
                                                         f"_route_{r['gebied']:02d}.gpx")
                with open(gpx, "w", encoding="utf-8") as f:
                    f.write(k.gpx_tekst(f"Deelgebied {r['gebied']}", cs))
        for laag in ("deelgebieden", "route_lijn", "start"):
            laad_na_afloop(context, pad, laag, laag, laag)

        feedback.pushInfo("\nGebied | verplicht km | route km | extra % | ha")
        for s in uit["samenvatting"]:
            feedback.pushInfo(f"{s['gebied']:6d} | {s['verplicht_km']:12.2f} | {s['route_km']:8.2f} | "
                              f"{s['extra_pct']:7d} | {s['ha']:.1f}")
        return {self.UITVOER: pad}
