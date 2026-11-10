from qgis.core import QgsProcessingProvider

from .algoritmes import ControleerAlgoritme, NetwerkAlgoritme, IndelenAlgoritme


class FietsrondesProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        for alg in (ControleerAlgoritme(), NetwerkAlgoritme(), IndelenAlgoritme()):
            self.addAlgorithm(alg)

    def id(self):
        return "ekozafietsrondes"

    def name(self):
        return "Ekoza fietsrondes"

    def longName(self):
        return "Ekoza fietsrondes - projectgebied indelen in fietsrondes"
