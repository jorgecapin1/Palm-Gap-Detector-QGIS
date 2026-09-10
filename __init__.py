# -*- coding: utf-8 -*-
"""
Palm Gap Detector
QGIS plugin entry point.
"""


def classFactory(iface):  # pylint: disable=invalid-name
    from .palm_gap_detector import PalmGapDetectorPlugin
    return PalmGapDetectorPlugin(iface)
