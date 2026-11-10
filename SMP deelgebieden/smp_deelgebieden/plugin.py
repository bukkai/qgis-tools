from qgis.core import QgsApplication

from .provider import DeelgebiedenProvider


class DeelgebiedenPlugin:
    def __init__(self):
        self.provider = None

    def initProcessing(self):
        self.provider = DeelgebiedenProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

    def unload(self):
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
