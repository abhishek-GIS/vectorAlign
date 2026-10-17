# -*- coding: utf-8 -*-
"""
/***************************************************************************
 vectorAlign
                                 A QGIS plugin
 Advanced spatial adjustment tool with displacement links, snapping,
 and live preview functionality.
                              -------------------
        begin                : 2026
        copyright            : (C) 2026 by Abhishek Bhalare
        email                : abhishekbhalare@gmail.com
 ***************************************************************************/

 This file initializes the plugin, making it known to QGIS.
"""

def classFactory(iface):
    """Load VectorAlignPlugin class from file plugin.py.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .plugin import VectorAlignPlugin
    return VectorAlignPlugin(iface)