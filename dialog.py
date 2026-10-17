# -*- coding: utf-8 -*-
"""
dialog.py — vectorAlign

UI layer. Contains every Qt widget used by the plugin.
"""

import csv
import math
import traceback
import os
import numpy as np

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsPointXY, QgsField, QgsFields, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsVectorFileWriter,
    QgsLineString, QgsPoint, QgsSnappingConfig, QgsPointLocator,
    QgsMapLayer, QgsFeatureRequest, QgsRectangle, QgsSpatialIndex,
    QgsTask, QgsApplication, QgsMapLayerProxyModel
)
from qgis.gui import (
    QgsMapToolEmitPoint, QgsRubberBand, QgsVertexMarker, QgisInterface,
    QgsProjectionSelectionWidget, QgsMapLayerComboBox, QgsCollapsibleGroupBox
)
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QMessageBox, QFileDialog, QGroupBox, QSpinBox,
    QCheckBox, QHeaderView, QToolButton, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QAction, QDialog,
    QAbstractItemView, QScrollArea, QSpacerItem, QSizePolicy, QTabWidget,
    QProgressBar,
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QUrl, QSize, QMetaType, QTimer
from qgis.PyQt.QtGui import QColor, QIcon, QTransform

from .algorithm import TransformationEngine, GeometryTransformTask, AttributeTransferTask


class FileDropListWidget(QListWidget):
    filesDropped = pyqtSignal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setToolTip("Drag files here or use the buttons below")
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: event.ignore()
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: event.ignore()
    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if files:
                self.filesDropped.emit(files)
                event.acceptProposedAction()


class HelpDialog(QDialog):
    def __init__(self, active_tab_index=0, parent=None):
        super().__init__(parent)
        self.active_tab_index = active_tab_index
        if self.active_tab_index == 0: 
            self.setWindowTitle("Help - vectorAlign")
        else: 
            self.setWindowTitle("Help - Attribute Transfer")
            
        # Tightly constrain width so text wraps, and let Qt auto-calculate the perfect height
        self.setFixedWidth(550)
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout()
        # Push all contents to the very top to eliminate any floating center gaps
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        help_text = QLabel()
        help_text.setWordWrap(True)
        help_text.setTextFormat(Qt.TextFormat.RichText)
        help_text.setOpenExternalLinks(True)
        help_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        if self.active_tab_index == 0:
            help_text.setText("""
            <h2>vectorAlign (Spatial Adjustment)</h2>
            <h3>📋 Step 1: Add Input Layers</h3>
            <ul>
                <li>Select vector data in the QGIS Layer Panel and click "Add Selected from QGIS".</li>
                <li><i>How this helps:</i> Protects your project by isolating only the layers you want to modify inside the plugin.</li>
            </ul>
            <h3>🔗 Step 2: Actions</h3>
            <ul>
                <li><b>Smart Align Vector:</b> Auto-centers and scales data.</li>
                <li><i>How this helps:</i> Instantly fixes massive coordinate offsets (like floating CAD files) by ignoring extreme outliers.</li>
                <li><b>Capture Links:</b> Draw manual displacement lines from source to target.</li>
                <li><i>How this helps:</i> Gives you precise control to rotate, scale, or rubbersheet the vector into place based on exact coordinates.</li>
            </ul>
            <h3>▶️ Step 3: Execute</h3>
            <ul>
                <li>Choose "In-Place Edit" or "Export to New File".</li>
                <li><i>How this helps:</i> "In-Place Edit" allows quick corrections, while "Export" protects your original data and lets you re-project into a custom CRS simultaneously.</li>
            </ul>
            """)
        else:
            help_text.setText("""
            <h2>Attribute Transfer</h2>
            <h3>📋 Step 1: Choose Layers</h3>
            <ul>
                <li>Designate a <b>From Layer</b> (source) and <b>To Layer</b> (target).</li>
                <li><i>How this helps:</i> Enforces a one-way data flow to prevent accidental overwrites of valuable source data.</li>
            </ul>
            <h3>⚙️ Step 2: Field Mapping</h3>
            <ul>
                <li>Click <b>⚙ Field Mapping</b> to define which attributes copy over.</li>
                <li><i>How this helps:</i> Seamlessly merges specific data points between layers, even if their tables have completely different structures, or allows you to create brand new target fields directly from the menu.</li>
            </ul>
            """)
            
        layout.addWidget(help_text)
        layout.addSpacing(15)
        
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(120)
        close_btn.clicked.connect(self.accept)
        
        # Center the close button nicely at the bottom
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)


class LinkTableDialog(QDialog):
    def __init__(self, links, parent_controller, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Link Table")
        self.resize(600, 400)
        self.links = links
        self.parent_controller = parent_controller
        self.highlight_rubber = None
        self._setup_ui()
    def _setup_ui(self):
        layout = QVBoxLayout()
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "From X", "From Y", "To X", "To Y"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.highlight_selected_link)
        self.populate_table()
        layout.addWidget(self.table)
        btn_layout = QHBoxLayout()
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self.delete_selected)
        self.clear_all_btn = QPushButton("Clear All Links")
        self.clear_all_btn.clicked.connect(self.clear_all)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.clear_all_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    def populate_table(self):
        self.table.setRowCount(len(self.links))
        for i, (p1, p2) in enumerate(self.links):
            self.table.setItem(i, 0, QTableWidgetItem(str(i+1)))
            self.table.setItem(i, 1, QTableWidgetItem(f"{p1.x():.3f}"))
            self.table.setItem(i, 2, QTableWidgetItem(f"{p1.y():.3f}"))
            self.table.setItem(i, 3, QTableWidgetItem(f"{p2.x():.3f}"))
            self.table.setItem(i, 4, QTableWidgetItem(f"{p2.y():.3f}"))
    def highlight_selected_link(self):
        if self.highlight_rubber:
            self.parent_controller.canvas.scene().removeItem(self.highlight_rubber)
            self.highlight_rubber = None
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            self.parent_controller.canvas.refresh()
            return
        row = selected_rows[0].row()
        if row < len(self.links):
            p1, p2 = self.links[row]
            self.highlight_rubber = QgsRubberBand(self.parent_controller.canvas, QgsWkbTypes.GeometryType.LineGeometry)
            self.highlight_rubber.setColor(QColor(255, 255, 0))
            self.highlight_rubber.setWidth(4)
            self.highlight_rubber.addPoint(p1)
            self.highlight_rubber.addPoint(p2)
            self.parent_controller.canvas.refresh()
    def delete_selected(self):
        selected_rows = sorted(set(item.row() for item in self.table.selectedItems()), reverse=True)
        if not selected_rows: return QMessageBox.information(self, "Info", "Please select links to delete.")
        if self.highlight_rubber:
            self.parent_controller.canvas.scene().removeItem(self.highlight_rubber)
            self.highlight_rubber = None
        for row in selected_rows:
            if row < len(self.links): del self.links[row]
        self.table.clearSelection()
        self.populate_table()
        self.parent_controller.update_visuals()
    def clear_all(self):
        reply = QMessageBox.question(self, "Clear All", "Clear all links?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if self.highlight_rubber:
                self.parent_controller.canvas.scene().removeItem(self.highlight_rubber)
                self.highlight_rubber = None
            self.table.clearSelection()
            self.links.clear()
            self.table.setRowCount(0)
            self.parent_controller.update_visuals()
    def closeEvent(self, event):
        if self.highlight_rubber:
            self.parent_controller.canvas.scene().removeItem(self.highlight_rubber)
            self.highlight_rubber = None
        self.parent_controller.canvas.refresh()
        event.accept()

class AddNewFieldDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Field to 'To Layer'")
        self.resize(320, 150)
        layout = QVBoxLayout()
        grid = QGridLayout()
        self.name_input = QLineEdit()
        grid.addWidget(QLabel("Field Name:"), 0, 0)
        grid.addWidget(self.name_input, 0, 1)
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Text (String)", "Whole Number (Integer)", "Decimal (Double)"])
        grid.addWidget(QLabel("Field Type:"), 1, 0)
        grid.addWidget(self.type_combo, 1, 1)
        self.length_spin = QSpinBox()
        self.length_spin.setRange(1, 255)
        self.length_spin.setValue(50)
        grid.addWidget(QLabel("Length:"), 2, 0)
        grid.addWidget(self.length_spin, 2, 1)
        layout.addLayout(grid)
        btn_box = QHBoxLayout()
        ok_btn = QPushButton("Add Field")
        ok_btn.setStyleSheet("QPushButton { background-color: #2E7D8C; color: white; border-radius: 4px; padding: 4px 10px; font-weight: bold;}")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addStretch()
        btn_box.addWidget(ok_btn)
        btn_box.addWidget(cancel_btn)
        layout.addLayout(btn_box)
        self.setLayout(layout)
    def get_field(self):
        name = self.name_input.text().strip()
        idx = self.type_combo.currentIndex()
        if idx == 0: return QgsField(name, QMetaType.Type.QString, "String", self.length_spin.value())
        elif idx == 1: return QgsField(name, QMetaType.Type.Int, "Integer")
        else: return QgsField(name, QMetaType.Type.Double, "Real", self.length_spin.value(), 4)

class FieldMappingDialog(QDialog):
    def __init__(self, parent=None, existing_config=None):
        super().__init__(parent)
        self.setWindowTitle("Map Fields: From Layer -> To Layer")
        self.resize(540, 480)
        self.existing_config = existing_config or {}
        self._source_layer = QgsProject.instance().mapLayer(self.existing_config.get('from_layer_id', ''))
        self._ref_layer    = QgsProject.instance().mapLayer(self.existing_config.get('to_layer_id', ''))
        self._setup_ui()
        self._populate_fields()
        self._restore_selections(self.existing_config.get('field_mappings', []))
    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(8)
        src_name = self._source_layer.name() if self._source_layer else '—'
        ref_name = self._ref_layer.name()    if self._ref_layer    else '—'
        info = QLabel(f"<b>From Layer (Source):</b> {src_name}<br><b>To Layer (Target):</b> {ref_name}<br><small style='color:#555;'>Select which 'From Field' values should be copied into your 'To Fields'.</small>")
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setStyleSheet("background:#f0f7f8; border:1px solid #b0d4d8; border-radius:4px; padding:7px 10px;")
        layout.addWidget(info)
        grp = QGroupBox("Attribute Mapping")
        grp_layout = QVBoxLayout()
        self.field_table = QTableWidget()
        self.field_table.setColumnCount(2)
        self.field_table.setHorizontalHeaderLabels(["To Layer Field (Receives Data)", "From Layer Field (Supplies Data)"])
        hh = self.field_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.field_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.field_table.setMinimumHeight(230)
        self.field_table.verticalHeader().setVisible(False)
        self.field_table.setAlternatingRowColors(True)
        grp_layout.addWidget(self.field_table)
        self.add_field_btn = QPushButton("➕ Add New Field to 'To Layer'")
        self.add_field_btn.setToolTip("Creates a brand new field in the Target layer so you can map data to it.")
        self.add_field_btn.setStyleSheet("QPushButton { color: #006600; font-weight: bold; text-align: left; padding: 4px; border: none; } QPushButton:hover { background-color: #e6f2e6; border-radius: 4px; }")
        self.add_field_btn.clicked.connect(self._add_new_field)
        grp_layout.addWidget(self.add_field_btn)
        grp.setLayout(grp_layout)
        layout.addWidget(grp)
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.setStyleSheet("QPushButton { background:#2E7D8C; color:white; border-radius:4px; padding:4px 14px; font-weight:bold; } QPushButton:hover { background:#1e5f6e; }")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    def _populate_fields(self):
        self.field_table.setRowCount(0)
        if not self._ref_layer: return
        src_fields = ["-- Do not update --"]
        if self._source_layer: src_fields.extend([f.name() for f in self._source_layer.fields()])
        for field in self._ref_layer.fields():
            row = self.field_table.rowCount()
            self.field_table.insertRow(row)
            ref_item = QTableWidgetItem(field.name())
            ref_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.field_table.setItem(row, 0, ref_item)
            combo = QComboBox()
            combo.addItems(src_fields)
            match_idx = combo.findText(field.name())
            if match_idx > 0: combo.setCurrentIndex(match_idx)
            self.field_table.setCellWidget(row, 1, combo)
    def _restore_selections(self, field_mappings):
        mapping_dict = {tgt: src for src, tgt in field_mappings}
        for row in range(self.field_table.rowCount()):
            to_fld = self.field_table.item(row, 0).text()
            if to_fld in mapping_dict:
                combo = self.field_table.cellWidget(row, 1)
                idx = combo.findText(mapping_dict[to_fld])
                if idx >= 0: combo.setCurrentIndex(idx)
    def _add_new_field(self):
        if not self._ref_layer: return
        dialog = AddNewFieldDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_field = dialog.get_field()
            if not new_field.name(): return QMessageBox.warning(self, "Invalid Name", "Field name cannot be empty.")
            if self._ref_layer.fields().indexOf(new_field.name()) != -1: return QMessageBox.warning(self, "Duplicate", f"Field '{new_field.name()}' already exists.")
            was_editable = self._ref_layer.isEditable()
            if was_editable: self._ref_layer.addAttribute(new_field)
            else: self._ref_layer.dataProvider().addAttributes([new_field])
            self._ref_layer.updateFields()
            current_config = self.get_config()
            self._populate_fields()
            self._restore_selections(current_config.get('field_mappings', []))
    def get_config(self):
        mappings = []
        for row in range(self.field_table.rowCount()):
            to_fld = self.field_table.item(row, 0).text()
            combo = self.field_table.cellWidget(row, 1)
            if combo.currentIndex() > 0:
                from_fld = combo.currentText()
                mappings.append((from_fld, to_fld))
        return {'field_mappings': mappings}


class MapToolCaptureLink(QgsMapToolEmitPoint):
    linkCaptured = pyqtSignal(QgsPointXY, QgsPointXY)
    
    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.first_point = None
        self.snapper = self.canvas.snappingUtils()
        self.temp_rubber = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.temp_rubber.setColor(QColor(0, 0, 255, 100))
        self.temp_rubber.setWidth(1)
        self.temp_rubber.setLineStyle(Qt.PenStyle.DashLine)
        self.snap_marker = QgsVertexMarker(self.canvas)
        self.snap_marker.setColor(QColor(255, 0, 0))
        self.snap_marker.setIconType(QgsVertexMarker.IconType.ICON_BOX)
        self.snap_marker.hide()
        self.start_marker = None

    def _snap_point(self, p):
        match = self.snapper.snapToMap(p)
        if match.isValid():
            self.snap_marker.setCenter(match.point())
            self.snap_marker.show()
            return match.point()
        else:
            self.snap_marker.hide()
            return p

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.first_point is not None:
                self.first_point = None
                self.temp_rubber.reset(QgsWkbTypes.GeometryType.LineGeometry)
                if self.start_marker:
                    self.canvas.scene().removeItem(self.start_marker)
                    self.start_marker = None
                self.canvas.refresh()
                event.accept()
                return
        super().keyPressEvent(event)

    def canvasReleaseEvent(self, event):
        point = self._snap_point(self.toMapCoordinates(event.pos()))
        if self.first_point is None:
            self.first_point = point
            self.start_marker = QgsVertexMarker(self.canvas)
            self.start_marker.setCenter(point)
            self.start_marker.setColor(QColor(0, 200, 0))
            self.start_marker.setIconType(QgsVertexMarker.IconType.ICON_CROSS)
            self.start_marker.setPenWidth(2)
        else:
            self.linkCaptured.emit(self.first_point, point)
            self.temp_rubber.reset(QgsWkbTypes.GeometryType.LineGeometry)
            if self.start_marker:
                self.canvas.scene().removeItem(self.start_marker)
                self.start_marker = None
            self.first_point = None

    def canvasMoveEvent(self, event):
        current_point = self._snap_point(self.toMapCoordinates(event.pos()))
        if self.first_point:
            self.temp_rubber.reset(QgsWkbTypes.GeometryType.LineGeometry)
            self.temp_rubber.addPoint(self.first_point)
            self.temp_rubber.addPoint(current_point)

    def deactivate(self):
        self.temp_rubber.reset()
        self.first_point = None
        if self.snap_marker: 
            self.snap_marker.hide()
            self.canvas.scene().removeItem(self.snap_marker)
            self.snap_marker = None
        if getattr(self, 'start_marker', None): 
            self.canvas.scene().removeItem(self.start_marker)
            self.start_marker = None
        super().deactivate()


class MapToolSelectLink(QgsMapToolEmitPoint):
    linkSelected = pyqtSignal(int)
    
    def __init__(self, canvas, parent_controller):
        super().__init__(canvas)
        self.canvas = canvas
        self.parent_controller = parent_controller
        self.highlight_rubber = None
        self.selected_link_index = None
        
    def canvasReleaseEvent(self, event):
        click_point = self.toMapCoordinates(event.pos())
        tolerance = 10 
        map_tolerance = tolerance * self.canvas.mapUnitsPerPixel()
        
        min_distance = float('inf')
        closest_link_index = None
        
        for i, (p1, p2) in enumerate(self.parent_controller.displacement_links):
            distance = self._point_to_line_distance(click_point, p1, p2)
            if distance < min_distance and distance < map_tolerance:
                min_distance = distance
                closest_link_index = i
        
        if self.highlight_rubber:
            self.canvas.scene().removeItem(self.highlight_rubber)
            self.highlight_rubber = None
        
        if closest_link_index is not None:
            self.selected_link_index = closest_link_index
            p1, p2 = self.parent_controller.displacement_links[closest_link_index]
            self.highlight_rubber = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
            self.highlight_rubber.setColor(QColor(255, 255, 0))
            self.highlight_rubber.setWidth(4)
            self.highlight_rubber.addPoint(p1)
            self.highlight_rubber.addPoint(p2)
            self.canvas.refresh()
            self.linkSelected.emit(closest_link_index)
    
    def _point_to_line_distance(self, point, line_start, line_end):
        line_vec_x = line_end.x() - line_start.x()
        line_vec_y = line_end.y() - line_start.y()
        point_vec_x = point.x() - line_start.x()
        point_vec_y = point.y() - line_start.y()
        line_len_sq = line_vec_x**2 + line_vec_y**2
        if line_len_sq == 0:
            return math.sqrt((point.x() - line_start.x())**2 + (point.y() - line_start.y())**2)
        t = max(0, min(1, (point_vec_x * line_vec_x + point_vec_y * line_vec_y) / line_len_sq))
        closest_x = line_start.x() + t * line_vec_x
        closest_y = line_start.y() + t * line_vec_y
        return math.sqrt((point.x() - closest_x)**2 + (point.y() - closest_y)**2)
    
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.selected_link_index is not None:
                if self.selected_link_index < len(self.parent_controller.displacement_links):
                    del self.parent_controller.displacement_links[self.selected_link_index]
                    self.parent_controller.update_visuals()
                    if self.highlight_rubber:
                        self.canvas.scene().removeItem(self.highlight_rubber)
                        self.highlight_rubber = None
                    self.selected_link_index = None
                    self.canvas.refresh()
                    QMessageBox.information(self.parent_controller, "Link Deleted", "Selected link has been deleted.")
                event.accept()
                return
        super().keyPressEvent(event)
    
    def deactivate(self):
        if self.highlight_rubber:
            self.canvas.scene().removeItem(self.highlight_rubber)
            self.highlight_rubber = None
        self.selected_link_index = None
        super().deactivate()


class VectorAlignToolbar(QDockWidget):
    def __init__(self, iface: QgisInterface, parent=None):
        super().__init__("vectorAlign", parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        
        self.displacement_links = [] 
        self.last_run_links = [] 
        self.map_tool = None
        self.select_link_tool = None
        self.current_method = 'affine'
        self.target_markers = [] 
        self.transformation_preview_bands = []

        self.preview_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.preview_rubber_band.setColor(QColor(0, 0, 255, 150))
        self.preview_rubber_band.setWidth(2)

        self.link_table_dialog = None
        self.is_compact_mode = False
        self.transfer_config = None
        self.transfer_tool = None
        self.current_task = None 
        self.current_execution_mode = "inplace_standard"
        self.export_target_path = ""
        self.export_target_crs = None
        
        self.init_ui()
        QgsProject.instance().layersRemoved.connect(self.on_layers_removed_globally)

    def init_ui(self):
        main_widget = QWidget()
        self.setMinimumWidth(320)

        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(5, 5, 5, 5)
        outer_layout.setSpacing(4)

        top_layout = QHBoxLayout()

        self.minimize_btn = QToolButton()
        self.minimize_btn.setText("−")
        self.minimize_btn.setStyleSheet("QToolButton { background-color: #2196F3; color: white; border-radius: 10px; font-weight: bold; font-size: 16px; min-width: 20px; min-height: 20px; max-width: 20px; max-height: 20px; } QToolButton:hover { background-color: #1976D2; }")
        self.minimize_btn.clicked.connect(self.toggle_compact_mode)
        top_layout.addWidget(self.minimize_btn)
        top_layout.addStretch()

        self.help_btn = QToolButton()
        self.help_btn.setText("?")
        self.help_btn.setStyleSheet("QToolButton { background-color: #4CAF50; color: white; border-radius: 10px; font-weight: bold; font-size: 14px; min-width: 20px; min-height: 20px; max-width: 20px; max-height: 20px; } QToolButton:hover { background-color: #45a049; }")
        self.help_btn.clicked.connect(self.show_help)
        top_layout.addWidget(self.help_btn)
        outer_layout.addLayout(top_layout)

        self.compact_bar = QWidget()
        compact_layout = QHBoxLayout()
        compact_layout.setContentsMargins(0, 0, 0, 0)
        compact_layout.setSpacing(4)

        _compact_btn_style = "QPushButton { background-color: #2E7D8C; color: white; border-radius: 4px; font-size: 11px; padding: 3px 8px; } QPushButton:hover { background-color: #1e5f6e; } QPushButton:checked { background-color: #1a4a55; border: 2px solid #4cB6C5; }"
        
        self.compact_preview_btn = QPushButton("Preview")
        self.compact_preview_btn.setStyleSheet(_compact_btn_style)
        self.compact_preview_btn.clicked.connect(self.preview_transformation)

        self.compact_run_btn = QPushButton("Run vectorAlign")
        self.compact_run_btn.setStyleSheet(_compact_btn_style)
        self.compact_run_btn.clicked.connect(self.run_transformation)

        self.compact_capture_at_btn = QPushButton("🔗 Links")
        self.compact_capture_at_btn.setCheckable(True)
        self.compact_capture_at_btn.setStyleSheet(_compact_btn_style)
        self.compact_capture_at_btn.toggled.connect(self._at_capture_links_toggled)

        self.compact_transfer_btn = QPushButton("▶ Transfer")
        self.compact_transfer_btn.setStyleSheet(_compact_btn_style)
        self.compact_transfer_btn.clicked.connect(self.run_attribute_transfer)

        self.compact_at_preview_btn = QPushButton("🔍 Preview")
        self.compact_at_preview_btn.setStyleSheet(_compact_btn_style)
        self.compact_at_preview_btn.clicked.connect(self.at_preview_table)

        compact_layout.addWidget(self.compact_preview_btn)
        compact_layout.addWidget(self.compact_run_btn)
        compact_layout.addWidget(self.compact_capture_at_btn)
        compact_layout.addWidget(self.compact_transfer_btn)
        compact_layout.addWidget(self.compact_at_preview_btn)
        self.compact_bar.setLayout(compact_layout)
        self.compact_bar.hide()
        outer_layout.addWidget(self.compact_bar)

        self.tab_widget = QTabWidget()
        self.tab_widget.tabBar().setExpanding(True)
        self.tab_widget.setStyleSheet("QTabWidget::pane { border: 1px solid #b0c4c8; border-top: 2px solid #2E7D8C; background: #ffffff; } QTabBar::tab { background: #dce8ea; color: #2c4a50; padding: 6px 10px; border: 1px solid #b0c4c8; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; font-size: 11px; min-width: 120px; } QTabBar::tab:selected { background: #2E7D8C; color: #ffffff; font-weight: bold; border-color: #2E7D8C; }")
        
        self.tab_widget.addTab(self._build_spatial_adjustment_tab(), "vectorAlign")
        self.tab_widget.addTab(self._build_attribute_transfer_tab(), "Attribute Transfer")
        self.tab_widget.currentChanged.connect(self._update_compact_bar)
        outer_layout.addWidget(self.tab_widget)

        self.stretch_item = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        outer_layout.addItem(self.stretch_item)

        main_widget.setLayout(outer_layout)
        self.setWidget(main_widget)

        if self.snap_chk.isChecked():
            self.toggle_global_snapping(True)
        self._update_compact_bar(0)

    def _build_spatial_adjustment_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)

        self.input_group = QgsCollapsibleGroupBox("1. Input Layers (Vector)")
        input_layout = QVBoxLayout()
        self.layer_list = FileDropListWidget()
        self.layer_list.filesDropped.connect(self.on_files_dropped)
        self.layer_list.setMinimumHeight(80)
        input_layout.addWidget(self.layer_list)

        btn_box = QHBoxLayout()
        self.btn_add_toc = QPushButton("Add Selected from QGIS")
        self.btn_add_toc.clicked.connect(self.add_layers_from_toc)
        self.btn_remove_layer = QPushButton("Remove")
        self.btn_remove_layer.clicked.connect(self.remove_selected_layers)
        btn_box.addWidget(self.btn_add_toc)
        btn_box.addWidget(self.btn_remove_layer)
        input_layout.addLayout(btn_box)

        self.selected_only_chk = QCheckBox("Selected features only (within layers)")
        input_layout.addWidget(self.selected_only_chk)
        self.input_group.setLayout(input_layout)
        layout.addWidget(self.input_group)

        self.tools_group = QgsCollapsibleGroupBox("2. Actions")
        tools_layout = QVBoxLayout()

        self.smart_align_btn = QPushButton("╰┈❯❯❯❯ Smart Align Vector")
        self.smart_align_btn.setToolTip("Auto-aligns layers to canvas center, ignoring extreme outliers.")
        self.smart_align_btn.setStyleSheet("QPushButton { font-weight: bold; background-color: #4CAF50; color: white; border-radius: 4px; padding: 6px 8px; } QPushButton:hover { background-color: #45a049; }")
        self.smart_align_btn.clicked.connect(self.run_smart_alignment)
        tools_layout.addWidget(self.smart_align_btn)

        row1 = QHBoxLayout()
        self.add_link_btn = QPushButton("Capture Links")
        self.add_link_btn.setCheckable(True)
        self.add_link_btn.toggled.connect(self.toggle_link_creation)
        row1.addWidget(self.add_link_btn)

        self.select_link_btn = QPushButton("Select/Delete Links")
        self.select_link_btn.setCheckable(True)
        self.select_link_btn.toggled.connect(self.toggle_link_selection)
        row1.addWidget(self.select_link_btn)
        tools_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.snap_chk = QCheckBox("Global Snapping")
        self.snap_chk.setChecked(True)
        self.snap_chk.toggled.connect(self.toggle_global_snapping)
        row2.addWidget(self.snap_chk)
        tools_layout.addLayout(row2)

        self.tools_group.setLayout(tools_layout)
        layout.addWidget(self.tools_group)

        self.config_group = QgsCollapsibleGroupBox("3. Configuration")
        config_layout = QHBoxLayout()

        self.method_btn = QPushButton("Method: Affine")
        self.method_menu = QMenu(self)
        self._setup_method_menu()
        self.method_btn.setMenu(self.method_menu)
        config_layout.addWidget(self.method_btn)

        self.links_btn = QPushButton("Links Options")
        links_menu = QMenu(self)
        links_menu.addAction("View Table", self.view_link_table)
        links_menu.addAction("Save Links...", self.save_links_to_file)
        links_menu.addAction("Load Links...", self.load_links_from_file)
        self.links_btn.setMenu(links_menu)
        config_layout.addWidget(self.links_btn)

        self.config_group.setLayout(config_layout)
        layout.addWidget(self.config_group)

        exec_group = QgsCollapsibleGroupBox("4. Execute")
        exec_layout = QVBoxLayout()
        exec_layout.setSpacing(6)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Mode:"))
        
        self.exec_mode_combo = QComboBox()
        self.exec_mode_combo.addItems(["In-Place Edit (Modify Original Layer)", "Export to New File (Advanced)"])
        self.exec_mode_combo.currentIndexChanged.connect(self._toggle_execution_mode)
        mode_layout.addWidget(self.exec_mode_combo)
        exec_layout.addLayout(mode_layout)
        
        self.export_widget = QWidget()
        export_layout = QVBoxLayout()
        export_layout.setContentsMargins(0, 0, 0, 0)
        self.export_widget.setLayout(export_layout)
        self.export_widget.hide() 
        
        path_row = QHBoxLayout()
        self.export_path_input = QLineEdit()
        self.export_path_input.setPlaceholderText("Select output path...")
        self.export_path_input.textChanged.connect(self._update_run_buttons_state)
        
        self.export_browse_btn = QPushButton("...")
        self.export_browse_btn.setFixedWidth(30)
        self.export_browse_btn.clicked.connect(self._browse_export_path)
        
        path_row.addWidget(QLabel("Output File:"))
        path_row.addWidget(self.export_path_input)
        path_row.addWidget(self.export_browse_btn)
        export_layout.addLayout(path_row)
        
        crs_row = QHBoxLayout()
        self.crs_selector = QgsProjectionSelectionWidget()
        self.crs_selector.setCrs(QgsProject.instance().crs())
        crs_row.addWidget(QLabel("Target CRS:"))
        crs_row.addWidget(self.crs_selector)
        export_layout.addLayout(crs_row)
        
        zm_row = QHBoxLayout()
        self.preserve_z_chk = QCheckBox("Preserve Z values")
        self.preserve_m_chk = QCheckBox("Preserve M values")
        self.preserve_z_chk.setChecked(False) 
        self.preserve_m_chk.setChecked(False) 
        zm_row.addWidget(self.preserve_z_chk)
        zm_row.addWidget(self.preserve_m_chk)
        zm_row.addStretch()
        export_layout.addLayout(zm_row)
        
        exec_layout.addWidget(self.export_widget)

        btn_row = QHBoxLayout()
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.clicked.connect(self.preview_transformation)
        btn_row.addWidget(self.preview_btn)

        self.adjust_btn = QPushButton("Run vectorAlign")
        self.adjust_btn.setMinimumHeight(32)
        self.adjust_btn.setStyleSheet("QPushButton { font-weight: bold; background-color: #2E7D8C; color: white; border-radius: 4px; padding: 4px 8px; } QPushButton:hover { background-color: #1e5f6e; }")
        self.adjust_btn.clicked.connect(self.run_transformation)
        btn_row.addWidget(self.adjust_btn)
        exec_layout.addLayout(btn_row)

        prog_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        
        self.cancel_task_btn = QPushButton("Cancel")
        self.cancel_task_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; border-radius: 4px; padding: 4px 8px;")
        self.cancel_task_btn.hide()
        self.cancel_task_btn.clicked.connect(self.cancel_current_task)
        
        prog_row.addWidget(self.progress_bar)
        prog_row.addWidget(self.cancel_task_btn)
        exec_layout.addLayout(prog_row)

        exec_group.setLayout(exec_layout)
        layout.addWidget(exec_group)

        layout.addStretch()
        tab.setLayout(layout)
        return tab

    def _build_attribute_transfer_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        input_group = QgsCollapsibleGroupBox("1. Input Layers")
        input_grid = QGridLayout()
        input_grid.setVerticalSpacing(5)
        input_grid.setHorizontalSpacing(6)

        input_grid.addWidget(QLabel("From Layer:"), 0, 0)
        self.at_from_combo = QgsMapLayerComboBox()
        self.at_from_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        self.at_from_combo.layerChanged.connect(self._at_layer_combo_changed)
        input_grid.addWidget(self.at_from_combo, 0, 1)

        input_grid.addWidget(QLabel("To Layer:"), 1, 0)
        self.at_to_combo = QgsMapLayerComboBox()
        self.at_to_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        self.at_to_combo.layerChanged.connect(self._at_layer_combo_changed)
        input_grid.addWidget(self.at_to_combo, 1, 1)

        self.field_mapping_btn = QPushButton("⚙  Field Mapping")
        self.field_mapping_btn.setStyleSheet("QPushButton { background-color: #f0f7f8; border: 1px solid #2E7D8C; border-radius: 4px; color: #2E7D8C; padding: 3px 8px; } QPushButton:hover { background-color: #d4eaed; }")
        self.field_mapping_btn.clicked.connect(self.show_field_mapping_dialog)
        input_grid.addWidget(self.field_mapping_btn, 2, 0, 1, 2)

        self.at_map_label = QLabel("Fields mapped:  <i>none — click Field Mapping to configure</i>")
        self.at_map_label.setTextFormat(Qt.TextFormat.RichText)
        self.at_map_label.setStyleSheet("padding: 1px 0px; color: #666; font-size: 11px;")
        input_grid.addWidget(self.at_map_label, 3, 0, 1, 2)

        input_group.setLayout(input_grid)
        layout.addWidget(input_group)
        self._at_layer_combo_changed()

        actions_group = QgsCollapsibleGroupBox("2. Actions  (Control Point Links)")
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(5)

        link_row = QHBoxLayout()
        self.at_add_link_btn = QPushButton("Capture Links")
        self.at_add_link_btn.setCheckable(True)
        self.at_add_link_btn.toggled.connect(self._at_capture_links_toggled)
        link_row.addWidget(self.at_add_link_btn)

        self.at_select_link_btn = QPushButton("Select/Delete Links")
        self.at_select_link_btn.setCheckable(True)
        self.at_select_link_btn.toggled.connect(self._at_select_links_toggled)
        link_row.addWidget(self.at_select_link_btn)

        actions_layout.addLayout(link_row)
        actions_group.setLayout(actions_layout)
        layout.addWidget(actions_group)

        execute_group = QgsCollapsibleGroupBox("3. Execute")
        execute_layout = QVBoxLayout()
        execute_layout.setSpacing(5)

        self.at_generate_output_chk = QCheckBox("Generate Output as New Layer")
        self.at_generate_output_chk.setChecked(False) 
        execute_layout.addWidget(self.at_generate_output_chk)

        exec_row = QHBoxLayout()
        self.attr_transfer_btn = QPushButton("╰┈➤  Run Transfer")
        self.attr_transfer_btn.setMinimumHeight(32)
        self.attr_transfer_btn.setStyleSheet("QPushButton { font-weight: bold; background-color: #2E7D8C; color: white; border-radius: 4px; padding: 4px 8px; } QPushButton:hover  { background-color: #1e5f6e; }")
        self.attr_transfer_btn.clicked.connect(self.run_attribute_transfer)
        exec_row.addWidget(self.attr_transfer_btn)

        self.at_preview_btn = QPushButton("🔍  Preview Result")
        self.at_preview_btn.setMinimumHeight(32)
        self.at_preview_btn.setStyleSheet("QPushButton { background-color: #f5f5f5; border: 1px solid #aaaaaa; border-radius: 4px; padding: 4px 8px; } QPushButton:hover { background-color: #e0e0e0; }")
        self.at_preview_btn.clicked.connect(self.at_preview_table)
        exec_row.addWidget(self.at_preview_btn)

        execute_layout.addLayout(exec_row)
        execute_group.setLayout(execute_layout)
        layout.addWidget(execute_group)

        layout.addStretch()
        tab.setLayout(layout)
        return tab

    def _toggle_execution_mode(self, index):
        if index == 1:
            self.export_widget.show()
        else:
            self.export_widget.hide()
        self._update_run_buttons_state()

    def _update_run_buttons_state(self):
        if self.exec_mode_combo.currentIndex() == 1:
            path = self.export_path_input.text().strip()
            parent_dir = os.path.dirname(path)
            valid_exts = ['.shp', '.gpkg', '.geojson']
            has_valid_ext = any(path.lower().endswith(ext) for ext in valid_exts)
            
            if os.path.exists(parent_dir) and has_valid_ext:
                self.adjust_btn.setEnabled(True)
                self.smart_align_btn.setEnabled(True)
            else:
                self.adjust_btn.setEnabled(False)
                self.smart_align_btn.setEnabled(False)
        else:
            self.adjust_btn.setEnabled(True)
            self.smart_align_btn.setEnabled(True)

    def _browse_export_path(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Export File", "", "GeoPackage (*.gpkg);;Shapefile (*.shp);;GeoJSON (*.geojson)")
        if path:
            self.export_path_input.setText(path)
            self._update_run_buttons_state()

    def _at_capture_links_toggled(self, checked):
        if checked and self.at_select_link_btn.isChecked():
            self.at_select_link_btn.blockSignals(True); self.at_select_link_btn.setChecked(False); self.at_select_link_btn.blockSignals(False)
        self.add_link_btn.blockSignals(True); self.add_link_btn.setChecked(checked); self.add_link_btn.blockSignals(False)
        self.toggle_link_creation(checked)

    def _at_select_links_toggled(self, checked):
        if checked and self.at_add_link_btn.isChecked():
            self.at_add_link_btn.blockSignals(True); self.at_add_link_btn.setChecked(False); self.at_add_link_btn.blockSignals(False)
        self.select_link_btn.blockSignals(True); self.select_link_btn.setChecked(checked); self.select_link_btn.blockSignals(False)
        self.toggle_link_selection(checked)

    def _at_layer_combo_changed(self):
        from_lyr = self.at_from_combo.currentLayer()
        to_lyr = self.at_to_combo.currentLayer()
        from_id = from_lyr.id() if from_lyr else None
        to_id = to_lyr.id() if to_lyr else None
        
        if self.transfer_config is None: 
            self.transfer_config = {'from_layer_id': from_id, 'to_layer_id': to_id, 'field_mappings': []}
        else:
            if self.transfer_config.get('from_layer_id') != from_id or self.transfer_config.get('to_layer_id') != to_id:
                self.transfer_config['field_mappings'] = []
            self.transfer_config['from_layer_id'] = from_id
            self.transfer_config['to_layer_id']   = to_id
            
        n = len(self.transfer_config.get('field_mappings', []))
        self.at_map_label.setText(f"Fields mapped:  <b>{n} field(s)</b>" if n else "Fields mapped:  <i>none — click Field Mapping to configure</i>")

    def _setup_method_menu(self):
        methods = [
            ("Similarity", "similarity"), ("Affine", "affine"), ("Projective", "projective"),
            ("Polynomial Order 2", "poly2"), ("Polynomial Order 3", "poly3"), ("Thin Plate Spline", "spline")
        ]
        self.method_actions = {}
        for name, code in methods:
            action = QAction(name, self)
            action.setCheckable(True)
            if code == 'affine': action.setChecked(True)
            action.triggered.connect(lambda c, cod=code, n=name: self.set_method(cod, n))
            self.method_menu.addAction(action)
            self.method_actions[code] = action

    def set_method(self, code, name):
        self.current_method = code
        self.method_btn.setText(f"Method: {name}")
        for c, a in self.method_actions.items(): a.setChecked(c == code)

    def add_layers_from_toc(self):
        for layer in self.iface.layerTreeView().selectedLayers():
            if layer.type() != QgsMapLayer.LayerType.VectorLayer: continue
            if self._is_layer_in_list(layer.id()): continue
            item = QListWidgetItem(layer.name())
            item.setData(Qt.ItemDataRole.UserRole, layer.id())
            self.layer_list.addItem(item)

    def on_files_dropped(self, file_paths):
        for path in file_paths:
            layer = QgsVectorLayer(path, os.path.splitext(os.path.basename(path))[0], "ogr")
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
                if not self._is_layer_in_list(layer.id()):
                    item = QListWidgetItem(layer.name())
                    item.setData(Qt.ItemDataRole.UserRole, layer.id())
                    self.layer_list.addItem(item)

    def remove_selected_layers(self):
        for item in self.layer_list.selectedItems(): self.layer_list.takeItem(self.layer_list.row(item))

    def _is_layer_in_list(self, layer_id):
        for i in range(self.layer_list.count()):
            if self.layer_list.item(i).data(Qt.ItemDataRole.UserRole) == layer_id: return True
        return False

    def get_target_layers(self):
        return [lyr for i in range(self.layer_list.count()) for lyr in [QgsProject.instance().mapLayer(self.layer_list.item(i).data(Qt.ItemDataRole.UserRole))] if lyr and lyr.isValid()]

    def on_layers_removed_globally(self, layer_ids):
        for i in reversed(range(self.layer_list.count())):
            if self.layer_list.item(i).data(Qt.ItemDataRole.UserRole) in layer_ids: self.layer_list.takeItem(i)

    def toggle_link_creation(self, checked):
        if checked:
            self.clear_preview()
            if self.select_link_btn.isChecked(): self.select_link_btn.setChecked(False)
            self.map_tool = MapToolCaptureLink(self.canvas)
            self.map_tool.linkCaptured.connect(self.add_link)
            self.canvas.setMapTool(self.map_tool)
        else:
            if self.map_tool:
                self.canvas.unsetMapTool(self.map_tool)
                self.map_tool = None
        if hasattr(self, 'at_add_link_btn'):
            self.at_add_link_btn.blockSignals(True); self.at_add_link_btn.setChecked(checked); self.at_add_link_btn.blockSignals(False)

    def toggle_link_selection(self, checked):
        if checked:
            self.clear_preview()
            if self.add_link_btn.isChecked(): self.add_link_btn.setChecked(False)
            self.select_link_tool = MapToolSelectLink(self.canvas, self)
            self.select_link_tool.linkSelected.connect(self.on_link_selected)
            self.canvas.setMapTool(self.select_link_tool)
        else:
            if self.select_link_tool:
                self.canvas.unsetMapTool(self.select_link_tool)
                self.select_link_tool = None
        if hasattr(self, 'at_select_link_btn'):
            self.at_select_link_btn.blockSignals(True); self.at_select_link_btn.setChecked(checked); self.at_select_link_btn.blockSignals(False)

    def on_link_selected(self, link_index): pass

    def add_link(self, p1, p2):
        self.displacement_links.append((p1, p2))
        self.update_visuals()

    def update_visuals(self):
        self.preview_rubber_band.reset(QgsWkbTypes.GeometryType.LineGeometry)
        for m in self.target_markers: self.canvas.scene().removeItem(m)
        self.target_markers.clear()
        
        multiline = []
        for p1, p2 in self.displacement_links:
            multiline.append([p1, p2])
            marker = QgsVertexMarker(self.canvas)
            marker.setCenter(p2)
            marker.setColor(QColor(255, 0, 0))
            marker.setIconType(QgsVertexMarker.IconType.ICON_CROSS)
            marker.setPenWidth(2)
            self.target_markers.append(marker)
        if multiline: self.preview_rubber_band.addGeometry(QgsGeometry.fromMultiPolylineXY(multiline), None)

    def _add_preview_rubber(self, geom, layer=None):
        rb = QgsRubberBand(self.canvas, QgsWkbTypes.geometryType(geom.wkbType()))
        rb.setColor(QColor(255, 165, 0, 150))
        rb.setWidth(2)
        rb.setZValue(9999)
        if layer:
            rb.setToGeometry(geom, layer)
        else:
            rb.setToGeometry(geom, None)
        self.transformation_preview_bands.append(rb)

    def preview_transformation(self):
        if self.add_link_btn.isChecked(): self.add_link_btn.setChecked(False)
        if hasattr(self, 'at_add_link_btn') and self.at_add_link_btn.isChecked():
            self.at_add_link_btn.blockSignals(True); self.at_add_link_btn.setChecked(False); self.at_add_link_btn.blockSignals(False)
        if self.select_link_btn.isChecked(): self.select_link_btn.setChecked(False)
        if hasattr(self, 'at_select_link_btn') and self.at_select_link_btn.isChecked():
            self.at_select_link_btn.blockSignals(True); self.at_select_link_btn.setChecked(False); self.at_select_link_btn.blockSignals(False)

        self.clear_preview()
        layers = self.get_target_layers()
        if not layers: return

        if not self.displacement_links:
            return QMessageBox.information(self, "Preview", "Please capture displacement links first.")

        src = [x[0] for x in self.displacement_links]
        tgt = [x[1] for x in self.displacement_links]
        valid, msg = TransformationEngine.validate_control_points(src, tgt, self.current_method)
        if not valid: return QMessageBox.warning(self, "Validation", msg)

        params = self._get_params(src, tgt)
        
        if params is not None:
            is_export = (self.exec_mode_combo.currentIndex() == 1)
            params['preserve_z'] = self.preserve_z_chk.isChecked() if is_export else False
            params['preserve_m'] = self.preserve_m_chk.isChecked() if is_export else False
            
        count = 0
        for layer in layers:
            feats = layer.selectedFeatures() if self.selected_only_chk.isChecked() and layer.selectedFeatureCount() > 0 else layer.getFeatures()
            for feat in feats:
                if not feat.hasGeometry(): continue
                new_geom = TransformationEngine.transform_geometry(feat.geometry(), params, self.current_method)
                self._add_preview_rubber(new_geom, layer)
                count += 1
        
        if count == 0: 
            QMessageBox.warning(self, "Info", "No features found to preview.")
        else:
            self.canvas.refresh()

    def _ensure_layers_editable(self, layers):
        editable_layers = []
        cloned_names = []
        for layer in layers:
            if not layer or not layer.isValid(): continue
            if layer.isEditable() or layer.startEditing():
                editable_layers.append(layer)
                continue
            
            crs = layer.crs().authid()
            wkb_str = QgsWkbTypes.displayString(layer.wkbType())
            mem_layer = QgsVectorLayer(f"{wkb_str}?crs={crs}", f"{layer.name()}_Editable", "memory")
            provider = mem_layer.dataProvider()
            provider.addAttributes(layer.fields())
            mem_layer.updateFields()
            provider.addFeatures([QgsFeature(f) for f in layer.getFeatures()])
            QgsProject.instance().addMapLayer(mem_layer)
            mem_layer.startEditing()
            editable_layers.append(mem_layer)
            cloned_names.append(layer.name())
            
            for i in range(self.layer_list.count()):
                if self.layer_list.item(i).data(Qt.ItemDataRole.UserRole) == layer.id():
                    self.layer_list.takeItem(i)
                    new_item = QListWidgetItem(mem_layer.name())
                    new_item.setData(Qt.ItemDataRole.UserRole, mem_layer.id())
                    self.layer_list.insertItem(i, new_item)
                    break
        if cloned_names:
            self.iface.messageBar().pushMessage("Info", f"Auto-created editable memory layers for read-only inputs: {', '.join(cloned_names)}", level=0, duration=5)
        return editable_layers

    def _show_blinking_warning(self, title, text):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setTextFormat(Qt.TextFormat.RichText)
        msg_box.setText(text)
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Cancel)

        self._blink_count = 0
        timer = QTimer(msg_box) 
        def toggle_color():
            if self._blink_count >= 4:
                timer.stop()
                msg_box.setStyleSheet("")
                return
            if self._blink_count % 2 == 0:
                msg_box.setStyleSheet("QMessageBox { background-color: #ff9999; } QLabel { color: black; }")
            else:
                msg_box.setStyleSheet("")
            self._blink_count += 1

        timer.timeout.connect(toggle_color)
        timer.start(150)
        return msg_box.exec()

    def _start_task(self, task):
        self.current_task = task
        self._cancel_apply = False
        self.progress_bar.setFormat("Calculating... %p%")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.cancel_task_btn.show()
        
        self.adjust_btn.setEnabled(False)
        self.smart_align_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.attr_transfer_btn.setEnabled(False)
        
        task.progressChanged.connect(lambda val: self.progress_bar.setValue(int(val)))
        QgsApplication.taskManager().addTask(task)

    def cancel_current_task(self):
        if self.current_task: self.current_task.cancel()
        else: self._cancel_apply = True
        self.cancel_task_btn.setEnabled(False)
        self.cancel_task_btn.setText("Canceling...")

    def _on_task_finished(self, success, results, error_msg):
        self.current_task = None
        if not success:
            self._reset_ui()
            if error_msg == "Canceled": self.iface.messageBar().pushMessage("Canceled", "Adjustment aborted.", level=0, duration=4)
            else: QMessageBox.critical(self, "Error", f"Failed:\n{error_msg}")
            return

        try:
            if self.current_execution_mode.startswith('export'):
                self._apply_export(results)
            else:
                self._apply_inplace_setup(results)
        except Exception as e:
            self._reset_ui()
            QMessageBox.critical(self, "Post-Processing Error", f"An error occurred while applying the result:\n{traceback.format_exc()}")

    def _apply_export(self, results):
        export_path = self.export_target_path
        target_crs = self.export_target_crs
        modified_layer_names = []
        
        for ldata in results:
            original_layer = QgsProject.instance().mapLayer(ldata['layer_id'])
            if not original_layer: continue
            
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.fileEncoding = "UTF-8"
            
            layer_export_path = export_path
            ext = os.path.splitext(export_path)[1].lower()
            
            if ext == '.gpkg':
                options.driverName = "GPKG"
                options.layerName = original_layer.name() + "_Adjusted"
                if os.path.exists(export_path):
                    options.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            elif ext == '.shp':
                options.driverName = "ESRI Shapefile"
                if len(results) > 1:
                    base, _ = os.path.splitext(export_path)
                    layer_export_path = f"{base}_{original_layer.name()}.shp"
            else:
                options.driverName = "GeoJSON"
                if len(results) > 1:
                    base, _ = os.path.splitext(export_path)
                    layer_export_path = f"{base}_{original_layer.name()}.geojson"
                    
            crs_str = target_crs.authid()
            wkb_str = QgsWkbTypes.displayString(original_layer.wkbType())
            mem_layer = QgsVectorLayer(f"{wkb_str}?crs={crs_str}", original_layer.name() + "_temp", "memory")
            provider = mem_layer.dataProvider()
            provider.addAttributes(original_layer.fields())
            mem_layer.updateFields()
            
            geometries = ldata['geometries']
            feats_to_add = []
            
            from qgis.core import QgsCoordinateTransform
            crs_transform = None
            if original_layer.crs() != target_crs:
                crs_transform = QgsCoordinateTransform(original_layer.crs(), target_crs, QgsProject.instance())
            
            for feat in original_layer.getFeatures():
                nf = QgsFeature(feat)
                if feat.id() in geometries:
                    geom = geometries[feat.id()]
                    if crs_transform:
                        geom.transform(crs_transform)
                    nf.setGeometry(geom)
                else:
                    if crs_transform and nf.hasGeometry():
                        geom = nf.geometry()
                        geom.transform(crs_transform)
                        nf.setGeometry(geom)
                feats_to_add.append(nf)
                
            provider.addFeatures(feats_to_add)
            
            write_result = QgsVectorFileWriter.writeAsVectorFormatV3(
                mem_layer, layer_export_path, QgsProject.instance().transformContext(), options
            )
            err = write_result[0]
            
            if err == QgsVectorFileWriter.WriterError.NoError:
                if ext == '.gpkg':
                    out_lyr = QgsVectorLayer(f"{layer_export_path}|layername={options.layerName}", options.layerName, "ogr")
                else:
                    out_lyr = QgsVectorLayer(layer_export_path, original_layer.name() + "_Adjusted", "ogr")
                
                if out_lyr.isValid():
                    QgsProject.instance().addMapLayer(out_lyr)
                    modified_layer_names.append(original_layer.name())
            else:
                msg = write_result[1] if len(write_result) > 1 else str(err)
                QMessageBox.warning(self, "Export Error", f"Failed to save {original_layer.name()}:\n{msg}")

        self._reset_ui()
        if modified_layer_names:
            QMessageBox.information(
                self, 
                "Export Success", 
                f"Successfully exported {len(modified_layer_names)} layer(s) to:\n{export_path}\n\n"
                "The original layers remain unchanged."
            )

    def _apply_inplace_setup(self, results):
        self.progress_bar.setFormat("Applying Edits... %p%")
        self._apply_queue = []
        self._total_to_apply = sum(len(ldata['geometries']) for ldata in results)
        self._applied_count = 0
        self._modified_layer_names = []
        
        self.dynamic_chunk_size = max(100, min(5000, max(1, self._total_to_apply // 10)))
        
        for ldata in results:
            layer = QgsProject.instance().mapLayer(ldata['layer_id'])
            if layer:
                self._apply_queue.append({'layer': layer, 'geometries': list(ldata['geometries'].items()), 'count': 0, 'total': len(ldata['geometries'])})
                
        self._current_layer_index = 0
        if self._apply_queue:
            self._apply_queue[0]['layer'].beginEditCommand("vectorAlign")
            QTimer.singleShot(10, self._apply_inplace_chunk)
        else:
            self._reset_ui()
            QMessageBox.warning(self, "No Changes", "No features were moved.")

    def _apply_inplace_chunk(self):
        if self._cancel_apply:
            if self._current_layer_index < len(self._apply_queue):
                layer = self._apply_queue[self._current_layer_index]['layer']
                layer.destroyEditCommand()
                layer.triggerRepaint()
            self._reset_ui()
            self.iface.messageBar().pushMessage("Canceled", "Changes reverted.", level=0, duration=4)
            return

        if self._current_layer_index >= len(self._apply_queue):
            self._reset_ui()
            self.canvas.refresh()
            QMessageBox.information(self, "Success", f"Moved {self._applied_count} features across {len(self._modified_layer_names)} layer(s).\n⚠️ Layers are in Edit Mode. Please 'Save Layer Edits'.")
            return
            
        current_data = self._apply_queue[self._current_layer_index]
        layer, geoms = current_data['layer'], current_data['geometries']
        
        chunk_size = self.dynamic_chunk_size
        start, end = current_data['count'], min(current_data['count'] + chunk_size, current_data['total'])
        
        layer.blockSignals(True)
        for i in range(start, end): layer.changeGeometry(geoms[i][0], geoms[i][1])
        layer.blockSignals(False)
        
        current_data['count'] = end
        self._applied_count += (end - start)
        self.progress_bar.setValue(80 + int((self._applied_count / self._total_to_apply) * 20))
        
        if end == current_data['total']:
            layer.endEditCommand()
            layer.updateExtents()
            layer.triggerRepaint()
            self._modified_layer_names.append(layer.name())
            self._current_layer_index += 1
            if self._current_layer_index < len(self._apply_queue):
                self._apply_queue[self._current_layer_index]['layer'].beginEditCommand("vectorAlign")
                
        QTimer.singleShot(10, self._apply_inplace_chunk)

    def _reset_ui(self):
        self.progress_bar.hide()
        self.progress_bar.setFormat("%p%")
        self.cancel_task_btn.hide()
        self.cancel_task_btn.setEnabled(True)
        self.cancel_task_btn.setText("Cancel")
        self.preview_btn.setEnabled(True)
        self.attr_transfer_btn.setEnabled(True)
        self._update_run_buttons_state()

    def run_transformation(self):
        is_export = (self.exec_mode_combo.currentIndex() == 1)
        if is_export:
            export_path = self.export_path_input.text().strip()
            if not export_path:
                return QMessageBox.warning(self, "Export Path Missing", "Please specify an Output File path.")
            self.current_execution_mode = 'export_standard'
            self.export_target_path = export_path
            self.export_target_crs = self.crs_selector.crs()
        else:
            self.current_execution_mode = 'inplace_standard'
            
        self._execute_adjustment()

    def _execute_adjustment(self):
        self.clear_preview()
        layers = self.get_target_layers()
        if not layers: return

        if self.displacement_links and getattr(self, 'last_run_links', None):
            same_links = len(self.displacement_links) == len(self.last_run_links)
            if same_links:
                for (s1, t1), (s2, t2) in zip(self.displacement_links, self.last_run_links):
                    if s1.x() != s2.x() or s1.y() != s2.y() or t1.x() != t2.x() or t1.y() != t2.y():
                        same_links = False
                        break
            if same_links:
                reply = self._show_blinking_warning(
                    "Readjustment detected",
                    "<b> You are using the same links.</b><br><br>"
                    "This adjustment has already been run with the same links.<br>"
                    "Do you want to proceed?"
                )
                if reply == QMessageBox.StandardButton.Cancel: return

        self.last_run_links = [(QgsPointXY(s.x(), s.y()), QgsPointXY(t.x(), t.y())) for s, t in self.displacement_links]

        src = [x[0] for x in self.displacement_links]
        tgt = [x[1] for x in self.displacement_links]
        valid, msg = TransformationEngine.validate_control_points(src, tgt, self.current_method)
        if not valid: return QMessageBox.warning(self, "Validation", msg)

        params = self._get_params(src, tgt)
        
        if params is not None:
            is_export = (self.exec_mode_combo.currentIndex() == 1)
            params['preserve_z'] = self.preserve_z_chk.isChecked() if is_export else False
            params['preserve_m'] = self.preserve_m_chk.isChecked() if is_export else False
        
        target_layers = self._ensure_layers_editable(layers) if self.current_execution_mode == 'inplace_standard' else layers
        
        layers_data = []
        for layer in target_layers:
            source_features = layer.selectedFeatures() if self.selected_only_chk.isChecked() and layer.selectedFeatureCount() > 0 else layer.getFeatures()
            feat_dict = {}
            for feat in source_features:
                if feat.hasGeometry(): feat_dict[feat.id()] = QgsGeometry(feat.geometry())
            if feat_dict: layers_data.append({'layer_id': layer.id(), 'features': feat_dict})
                
        if not layers_data: return QMessageBox.warning(self, "No Valid Geometries", "Could not find geometries to adjust.")

        task = GeometryTransformTask("vectorAlign", layers_data, TransformationEngine, params, self.current_method, None, self._on_task_finished)
        self._start_task(task)

    def run_smart_alignment(self):
        is_export = (self.exec_mode_combo.currentIndex() == 1)
        if is_export:
            export_path = self.export_path_input.text().strip()
            if not export_path:
                return QMessageBox.warning(self, "Export Path Missing", "Please specify an Output File path.")
            self.current_execution_mode = 'export_smart'
            self.export_target_path = export_path
            self.export_target_crs = self.crs_selector.crs()
        else:
            self.current_execution_mode = 'inplace_smart'
            
        self._execute_smart_alignment()

    def _execute_smart_alignment(self):
        layers = self.get_target_layers()
        if not layers: return QMessageBox.warning(self, "No Layers", "Please add input layers first.")
            
        layers_dict = {lyr.id(): lyr for lyr in layers}
        transform, msg = TransformationEngine.calculate_smart_transform(layers_dict, self.canvas)
        if transform is None: return QMessageBox.warning(self, "Alignment Failed", msg)
            
        target_layers = self._ensure_layers_editable(layers) if self.current_execution_mode == 'inplace_smart' else layers
        
        layers_data = []
        for layer in target_layers:
            source_features = layer.selectedFeatures() if self.selected_only_chk.isChecked() and layer.selectedFeatureCount() > 0 else layer.getFeatures()
            feat_dict = {}
            for feat in source_features:
                if feat.hasGeometry(): feat_dict[feat.id()] = QgsGeometry(feat.geometry())
            if feat_dict: layers_data.append({'layer_id': layer.id(), 'features': feat_dict})
                
        if not layers_data: return QMessageBox.warning(self, "No Valid Geometries", "Could not find geometries to adjust.")

        task = GeometryTransformTask("Smart Align Vector", layers_data, TransformationEngine, None, None, transform, self._on_task_finished)
        self._start_task(task)

    def _get_params(self, src, tgt):
        if self.current_method == 'similarity': return TransformationEngine.compute_similarity_transform(src, tgt)
        if self.current_method == 'affine': return TransformationEngine.compute_affine_transform(src, tgt)
        if self.current_method == 'projective': return TransformationEngine.compute_projective_transform(src, tgt)
        if self.current_method == 'spline': return TransformationEngine.compute_tps_transform(src, tgt)
        if self.current_method.startswith('poly'): return TransformationEngine.compute_polynomial_transform(src, tgt, int(self.current_method[-1]))
        return None

    def clear_preview(self):
        for rb in self.transformation_preview_bands: self.canvas.scene().removeItem(rb)
        self.transformation_preview_bands.clear()
        self.canvas.refresh()

    def toggle_global_snapping(self, enabled):
        config = QgsProject.instance().snappingConfig()
        if config.enabled() != enabled:
            config.setEnabled(enabled)
            QgsProject.instance().setSnappingConfig(config)
            self.canvas.refresh()

    def view_link_table(self):
        if self.link_table_dialog: self.link_table_dialog.close()
        self.link_table_dialog = LinkTableDialog(self.displacement_links, self, self)
        self.link_table_dialog.exec()

    def show_help(self):
        current_tab_index = self.tab_widget.currentIndex()
        help_dialog = HelpDialog(active_tab_index=current_tab_index, parent=self)
        help_dialog.exec()

    def _update_compact_bar(self, tab_index=None):
        if tab_index is None: tab_index = self.tab_widget.currentIndex()
        on_sa = (tab_index == 0)
        self.compact_preview_btn.setVisible(on_sa)
        self.compact_run_btn.setVisible(on_sa)
        self.compact_capture_at_btn.setVisible(not on_sa)
        self.compact_transfer_btn.setVisible(not on_sa)
        self.compact_at_preview_btn.setVisible(not on_sa)

    def toggle_compact_mode(self):
        self.is_compact_mode = not self.is_compact_mode
        main_layout = self.widget().layout()
        if self.is_compact_mode:
            self.tab_widget.hide()
            self.compact_bar.show()
            self._update_compact_bar()
            self.minimize_btn.setText("□")
            if hasattr(self, 'stretch_item') and self.stretch_item: main_layout.removeItem(self.stretch_item)
            self.setMinimumHeight(60)
            self.setMaximumHeight(80)
        else:
            self.compact_bar.hide()
            self.tab_widget.show()
            self.minimize_btn.setText("−")
            if hasattr(self, 'stretch_item') and self.stretch_item: main_layout.addItem(self.stretch_item)
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        self.widget().adjustSize()
        self.adjustSize()

    def run_attribute_transfer(self):
        self._at_layer_combo_changed()
        cfg             = self.transfer_config or {}
        src_layer_id    = cfg.get('from_layer_id', '')
        ref_layer_id    = cfg.get('to_layer_id', '')
        mappings        = cfg.get('field_mappings', [])

        src_layer = QgsProject.instance().mapLayer(src_layer_id)  
        tgt_layer = QgsProject.instance().mapLayer(ref_layer_id)  

        if not src_layer or not tgt_layer: return QMessageBox.warning(self, "Setup Required", "Please select both From and To Layers.")
        if not self.displacement_links: return QMessageBox.warning(self, "No Control Points", "You need at least 2–3 links to match features spatially.")

        src_pts = [x[0] for x in self.displacement_links]
        tgt_pts = [x[1] for x in self.displacement_links]
        valid, msg = TransformationEngine.validate_control_points(src_pts, tgt_pts, self.current_method)
        if not valid: return QMessageBox.warning(self, "Insufficient Control Points", msg)

        if not mappings:
            reply = QMessageBox.question(self, "No Fields Selected", "No fields mapped. Continue anyway?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes: return

        try: 
            params = self._get_params(src_pts, tgt_pts)
            if params is not None:
                params['preserve_z'] = False
                params['preserve_m'] = False
        except Exception as exc: return QMessageBox.critical(self, "Transform Error", f"Failed to compute transformation:\n{exc}")

        source_data = []
        for f in src_layer.getFeatures():
            if f.hasGeometry():
                attrs = {fld.name(): f[fld.name()] for fld in src_layer.fields()}
                source_data.append((f.id(), QgsGeometry(f.geometry()), attrs))
                
        target_data = {f.id(): QgsGeometry(f.geometry()) for f in tgt_layer.getFeatures() if f.hasGeometry()}
        
        crs_src_authid = src_layer.crs().authid()
        crs_tgt_authid = tgt_layer.crs().authid()

        task = AttributeTransferTask(
            "Attribute Transfer", source_data, target_data, TransformationEngine, 
            params, self.current_method, crs_src_authid, crs_tgt_authid, mappings, self._on_attr_transfer_task_finished
        )
        
        self._at_generate_new = self.at_generate_output_chk.isChecked()
        self._at_tgt_layer_id = ref_layer_id
        
        self._start_task(task)

    def _on_attr_transfer_task_finished(self, success, results_dict, unmatched_count, error_msg):
        self.current_task = None
        if not success:
            self._reset_ui()
            if error_msg == "Canceled": self.iface.messageBar().pushMessage("Canceled", "Transfer aborted.", level=0, duration=4)
            else: QMessageBox.critical(self, "Error", f"Failed:\n{error_msg}")
            return
            
        try:
            tgt_layer = QgsProject.instance().mapLayer(self._at_tgt_layer_id)
            if not tgt_layer: raise Exception("Target layer no longer exists in QGIS.")
            
            self._matched_count = len(results_dict)
            self._unmatched_count = unmatched_count
            self._target_to_edit = tgt_layer
            
            if self._at_generate_new:
                crs_str  = tgt_layer.crs().authid()
                wkb_str  = QgsWkbTypes.displayString(tgt_layer.wkbType())
                out_name = f"{tgt_layer.name()}_AT_Updated"
                self._out_layer = QgsVectorLayer(f"{wkb_str}?crs={crs_str}", out_name, "memory")
                self._out_layer.dataProvider().addAttributes(tgt_layer.fields())
                self._out_layer.updateFields()
                
                self.progress_bar.setFormat("Creating New Layer... %p%")
                self.progress_bar.setValue(0)
                self.progress_bar.show()
                
                self._new_layer_queue = list(tgt_layer.getFeatures())
                self._new_layer_results = results_dict
                self._new_layer_total = len(self._new_layer_queue)
                self._new_layer_processed = 0
                
                self._chunk_size = max(100, min(5000, max(1, self._new_layer_total // 10)))
                
                QTimer.singleShot(10, self._build_new_layer_chunk)
                
            else:
                if not tgt_layer.isEditable(): tgt_layer.startEditing()
                
                for fid, mapped_attrs in results_dict.items():
                    for fld_name, val in mapped_attrs.items():
                        idx = tgt_layer.fields().indexOf(fld_name)
                        if idx != -1: tgt_layer.changeAttributeValue(fid, idx, val)
                
                self._reset_ui()
                msg_lines = [f"✔  Attribute Transfer complete.\n", f"   Updated Layer        : {tgt_layer.name()}", f"   Features Transferred : {self._matched_count} matched"]
                if self._unmatched_count: msg_lines.append(f"⚠  {self._unmatched_count} 'From Layer' features had no nearby 'To Layer' target.")
                msg_footer = "\n\n⚠️ IMPORTANT: 'To Layer' is in Edit Mode.\nPlease verify the changes and 'Save Layer Edits'."
                QMessageBox.information(self, "Attribute Transfer", "\n".join(msg_lines) + msg_footer)
            
        except Exception as e:
            self._reset_ui()
            QMessageBox.critical(self, "Post-Processing Error", f"An error occurred while applying the transfer:\n{traceback.format_exc()}")

    def _build_new_layer_chunk(self):
        try:
            start = self._new_layer_processed
            end = min(start + self._chunk_size, self._new_layer_total)
            
            feats_to_add = []
            for i in range(start, end):
                feat = self._new_layer_queue[i]
                new_feat = QgsFeature(feat)
                if feat.id() in self._new_layer_results:
                    for fld_name, val in self._new_layer_results[feat.id()].items():
                        idx = self._out_layer.fields().indexOf(fld_name)
                        if idx != -1: new_feat.setAttribute(idx, val)
                feats_to_add.append(new_feat)
                
            self._out_layer.dataProvider().addFeatures(feats_to_add)
            self._new_layer_processed = end
            
            pct = (self._new_layer_processed / self._new_layer_total) * 100
            self.progress_bar.setValue(int(pct))
            
            if self._new_layer_processed >= self._new_layer_total:
                QgsProject.instance().addMapLayer(self._out_layer)
                self._reset_ui()
                
                msg_lines = [f"✔  Attribute Transfer complete.\n", f"   Updated Layer        : {self._out_layer.name()}", f"   Features Transferred : {self._matched_count} matched"]
                if self._unmatched_count: msg_lines.append(f"⚠  {self._unmatched_count} 'From Layer' features had no nearby 'To Layer' target.")
                QMessageBox.information(self, "Attribute Transfer", "\n".join(msg_lines))
            else:
                QTimer.singleShot(10, self._build_new_layer_chunk)
                
        except Exception as e:
            self._reset_ui()
            QMessageBox.critical(self, "Layer Creation Error", f"Failed to build the new layer:\n{traceback.format_exc()}")

    def show_field_mapping_dialog(self):
        self._at_layer_combo_changed()
        if not self.transfer_config.get('to_layer_id'): return QMessageBox.warning(self, "Setup Required", "Please select 'To Layer' and 'From Layer' first.")
        dialog = FieldMappingDialog(self, existing_config=self.transfer_config)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result = dialog.get_config()
            self.transfer_config['field_mappings'] = result['field_mappings']
            n = len(self.transfer_config['field_mappings'])
            self.at_map_label.setText(f"Fields mapped:  <b>{n} field(s)</b>" if n else "Fields mapped:  <i>none — click Field Mapping to configure</i>")

    def at_preview_table(self):
        tgt_id = (self.transfer_config or {}).get('to_layer_id', '')
        tgt_layer = QgsProject.instance().mapLayer(tgt_id)
        if tgt_layer:
            if hasattr(self, 'at_generate_output_chk') and not self.at_generate_output_chk.isChecked(): return self.iface.showAttributeTable(tgt_layer)
            for lyr in QgsProject.instance().mapLayers().values():
                if lyr.name() == f"{tgt_layer.name()}_AT_Updated": return self.iface.showAttributeTable(lyr)
        QMessageBox.information(self, "Preview", "Run 'Run Transfer' first to view output.")

    def save_links_to_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save", "", "CSV (*.csv)")
        if path:
            with open(path, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(["id","fx","fy","tx","ty"])
                for i, (p1, p2) in enumerate(self.displacement_links): w.writerow([i, p1.x(), p1.y(), p2.x(), p2.y()])

    def load_links_from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load", "", "CSV (*.csv)")
        if path:
            with open(path, 'r') as f:
                for row in csv.DictReader(f):
                    self.displacement_links.append((QgsPointXY(float(row['fx']), float(row['fy'])), QgsPointXY(float(row['tx']), float(row['ty']))))
            self.update_visuals()

    def closeEvent(self, event):
        if self.link_table_dialog:
            if self.link_table_dialog.highlight_rubber: self.canvas.scene().removeItem(self.link_table_dialog.highlight_rubber)
            self.link_table_dialog.close()
            self.link_table_dialog = None
        if self.map_tool: self.canvas.unsetMapTool(self.map_tool)
        if self.select_link_tool: self.canvas.unsetMapTool(self.select_link_tool)
        self.clear_preview()
        self.displacement_links.clear()
        if getattr(self, 'preview_rubber_band', None) is not None:
            self.preview_rubber_band.reset(QgsWkbTypes.GeometryType.LineGeometry)
        for marker in self.target_markers: self.canvas.scene().removeItem(marker)
        self.target_markers.clear()
        self.canvas.refresh()
        event.accept()