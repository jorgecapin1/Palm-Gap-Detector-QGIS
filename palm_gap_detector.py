# -*- coding: utf-8 -*-
"""
Palm Gap Detector - main plugin class.
"""

import os

from qgis.PyQt.QtGui import QIcon
try:
    # QGIS 4 / Qt6
    from qgis.PyQt.QtGui import QAction
except ImportError:  # pragma: no cover - QGIS 3 / Qt5
    from qgis.PyQt.QtWidgets import QAction

from qgis.PyQt.QtWidgets import QDialog
from qgis.core import Qgis


def _dialog_exec(dialog):
    """Run a dialog in a way that works with Qt5 and Qt6."""
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


def _enum_value(value):
    """Return comparable int values for Qt5 ints and Qt6 enum values."""
    return int(value.value if hasattr(value, "value") else value)


def _dialog_accepted_value():
    """Accepted dialog code compatible with Qt5 and Qt6."""
    try:
        return QDialog.DialogCode.Accepted
    except AttributeError:  # pragma: no cover - QGIS 3 / Qt5
        return QDialog.Accepted


def _qgis_message_level(name):
    """Message level compatible with QGIS 3 and QGIS 4."""
    if hasattr(Qgis, "MessageLevel") and hasattr(Qgis.MessageLevel, name):
        return getattr(Qgis.MessageLevel, name)
    return getattr(Qgis, name)

from .dialog import PalmGapDetectorDialog
from .gap_detector_core import run_gap_detection


class PalmGapDetectorPlugin:
    """QGIS plugin wrapper."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu_name = "&Palm Gap Detector"

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.svg")
        self.action = QAction(QIcon(icon_path), "Palm Gap Detector", self.iface.mainWindow())
        self.action.setObjectName("PalmGapDetectorAction")
        self.action.setToolTip("Detect probable missing palms from row spacing gaps")
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.menu_name, self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu(self.menu_name, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def run(self):
        dlg = PalmGapDetectorDialog(self.iface.mainWindow())
        result = _dialog_exec(dlg)
        if _enum_value(result) != _enum_value(_dialog_accepted_value()):
            return

        params = dlg.parameters()
        try:
            output_layer, stats = run_gap_detection(params, self.iface)
            if output_layer and output_layer.isValid():
                self.iface.messageBar().pushMessage(
                    "Palm Gap Detector",
                    "Process completed. Gaps detected: {}. Excluded by exclusion layers: {}. Rows analyzed: {}. High: {}, Medium: {}, Low: {}.".format(
                        stats.get("gaps", 0),
                        stats.get("excluded", 0),
                        stats.get("rows", 0),
                        stats.get("high_confidence", 0),
                        stats.get("medium_confidence", 0),
                        stats.get("low_confidence", 0),
                    ),
                    level=_qgis_message_level("Success"),
                    duration=8,
                )
            else:
                self.iface.messageBar().pushMessage(
                    "Palm Gap Detector",
                    "The process finished, but no valid output layer was created.",
                    level=_qgis_message_level("Warning"),
                    duration=8,
                )
        except Exception as exc:  # pylint: disable=broad-except
            self.iface.messageBar().pushMessage(
                "Palm Gap Detector",
                "Error: {}".format(str(exc)),
                level=_qgis_message_level("Critical"),
                duration=12,
            )
