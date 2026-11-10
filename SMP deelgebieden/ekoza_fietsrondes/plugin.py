from qgis.core import QgsApplication

from .provider import FietsrondesProvider


class FietsrondesPlugin:
    def __init__(self):
        self.provider = None

    def initProcessing(self):
        self.provider = FietsrondesProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

    def unload(self):
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
