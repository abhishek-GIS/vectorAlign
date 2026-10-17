# -*- coding: utf-8 -*-
"""
plugin.py — vectorAlign

Thin QGIS integration layer. Registers the menu entry / toolbar icon and
launches the dockable VectorAlignToolbar (defined in dialog.py).
"""

import os.path

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QAction

try:
    from . import resources  # noqa: F401
    _HAS_RESOURCES = True
except ImportError:
    _HAS_RESOURCES = False

from .dialog import VectorAlignToolbar


class VectorAlignPlugin:
    """QGIS Plugin Implementation for vectorAlign."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.toolbar = None
        self.action = None
        self.menu = "&vectorAlign"

    def _icon(self):
        if _HAS_RESOURCES:
            return QIcon(":/plugins/vectoralign/icon.png")
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        return QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

    def initGui(self):
        self.action = QAction(
            self._icon(),
            "vectorAlign",
            self.iface.mainWindow(),
        )
        self.action.triggered.connect(self.run)
        self.action.setEnabled(True)
        self.action.setCheckable(False)
        self.action.setStatusTip("Open the vectorAlign panel")

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu(self.menu, self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginVectorMenu(self.menu, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

        if self.toolbar is not None:
            self.toolbar.close()
            self.toolbar.deleteLater()
            self.toolbar = None

    def run(self):
        if self.toolbar is None:
            self.toolbar = VectorAlignToolbar(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.toolbar)
            self.toolbar.destroyed.connect(self._on_toolbar_destroyed)
            self.toolbar.show()
        else:
            if not self.toolbar.isVisible():
                self.toolbar.show()
            self.toolbar.raise_()

    def _on_toolbar_destroyed(self, *args):
        self.toolbar = None