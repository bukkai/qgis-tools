from qgis.core import QgsProcessingProvider

from .algoritmes import NetwerkAlgoritme, IndelenAlgoritme


class DeelgebiedenProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        for alg in (NetwerkAlgoritme(), IndelenAlgoritme()):
            self.addAlgorithm(alg)

    def id(self):
        return "smpdeelgebieden"

    def name(self):
        return "SMP deelgebieden"

    def longName(self):
        return "SMP deelgebieden - projectgebied indelen in fietsrondes"
