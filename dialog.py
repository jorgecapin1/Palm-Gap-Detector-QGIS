# -*- coding: utf-8 -*-
"""
Palm Gap Detector dialog.
"""

import os

from qgis.PyQt.QtCore import Qt


def _qt_align_right():
    try:
        return Qt.AlignmentFlag.AlignRight
    except AttributeError:  # QGIS 3 / Qt5
        return Qt.AlignRight


def _qt_align_vcenter():
    try:
        return Qt.AlignmentFlag.AlignVCenter
    except AttributeError:  # QGIS 3 / Qt5
        return Qt.AlignVCenter


def _qt_user_role():
    try:
        return Qt.ItemDataRole.UserRole
    except AttributeError:  # QGIS 3 / Qt5
        return Qt.UserRole


def _button_standard(name):
    try:
        return getattr(QDialogButtonBox.StandardButton, name)
    except AttributeError:  # QGIS 3 / Qt5
        return getattr(QDialogButtonBox, name)
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsOpacityEffect,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from qgis.core import QgsMapLayerType, QgsProject, QgsWkbTypes


class PalmGapDetectorDialog(QDialog):
    """Simple plugin dialog created programmatically."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Palm Gap Detector")
        self.setMinimumWidth(640)
        self.setModal(True)

        self.point_layers = []
        self.polygon_layers = []
        self.vector_layers = []

        self._build_ui()
        self._load_layers()
        self._connect_signals()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 10)
        main_layout.setSpacing(10)

        title = QLabel("Palm Gap Detector")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #164b2f;")
        subtitle = QLabel(
            "Detect probable missing palms by analyzing spacing jumps inside real oil palm rows. "
            "The planting system is assumed to be triangular/staggered, which is the standard layout for oil palm plantations."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #444;")
        main_layout.addWidget(title)
        main_layout.addWidget(subtitle)

        data_group = QGroupBox("Input data")
        data_layout = QFormLayout(data_group)
        data_layout.setLabelAlignment(_qt_align_right())

        self.points_combo = QComboBox()
        self.lots_combo = QComboBox()
        self.lot_field_combo = QComboBox()
        self.exclusion_combo = QComboBox()
        self.add_exclusion_button = QPushButton("+")
        self.add_exclusion_button.setToolTip("Add selected exclusion layer")
        self.add_exclusion_button.setFixedWidth(34)
        self.remove_exclusion_button = QPushButton("Remove selected")
        self.exclusion_list = QListWidget()
        self.exclusion_list.setMinimumHeight(70)
        self.exclusion_list.setMaximumHeight(110)

        exclusion_picker = QHBoxLayout()
        exclusion_picker.addWidget(self.exclusion_combo)
        exclusion_picker.addWidget(self.add_exclusion_button)

        data_layout.addRow("Palm point layer:", self.points_combo)
        data_layout.addRow("Optional lot polygon layer:", self.lots_combo)
        data_layout.addRow("Optional lot ID field:", self.lot_field_combo)
        data_layout.addRow("Exclusion layer to add:", exclusion_picker)
        data_layout.addRow("Selected exclusion layers:", self.exclusion_list)
        data_layout.addRow("", self.remove_exclusion_button)
        main_layout.addWidget(data_group)

        params_group = QGroupBox("Detection parameters")
        params_layout = QFormLayout(params_group)
        params_layout.setLabelAlignment(_qt_align_right())

        self.plant_distance = QDoubleSpinBox()
        self.plant_distance.setRange(0.1, 5000.0)
        self.plant_distance.setDecimals(2)
        self.plant_distance.setSingleStep(0.5)
        self.plant_distance.setValue(9.0)
        self.plant_distance.setSuffix(" m")

        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.0, 500.0)
        self.tolerance.setDecimals(2)
        self.tolerance.setSingleStep(0.25)
        self.tolerance.setValue(2.0)
        self.tolerance.setSuffix(" m")

        self.row_tolerance = QDoubleSpinBox()
        self.row_tolerance.setRange(0.1, 500.0)
        self.row_tolerance.setDecimals(2)
        self.row_tolerance.setSingleStep(0.25)
        self.row_tolerance.setValue(self._suggested_row_tolerance())
        self.row_tolerance.setSuffix(" m")

        self.exclusion_buffer = QDoubleSpinBox()
        self.exclusion_buffer.setRange(0.0, 500.0)
        self.exclusion_buffer.setDecimals(2)
        self.exclusion_buffer.setSingleStep(0.5)
        self.exclusion_buffer.setValue(5.0)
        self.exclusion_buffer.setSuffix(" m")

        self.auto_orientation = QCheckBox("Estimate row orientation automatically")
        self.auto_orientation.setChecked(True)

        self.manual_angle = QDoubleSpinBox()
        self.manual_angle.setRange(0.0, 180.0)
        self.manual_angle.setDecimals(2)
        self.manual_angle.setSingleStep(1.0)
        self.manual_angle.setValue(0.0)
        self.manual_angle.setSuffix("°")
        self.manual_angle.setEnabled(False)

        self.min_points_row = QSpinBox()
        self.min_points_row.setRange(2, 50)
        self.min_points_row.setValue(3)

        params_layout.addRow("Plant spacing:", self.plant_distance)
        params_layout.addRow("Search tolerance:", self.tolerance)
        params_layout.addRow("Row grouping tolerance:", self.row_tolerance)
        params_layout.addRow("Exclusion buffer:", self.exclusion_buffer)
        params_layout.addRow("Orientation:", self.auto_orientation)
        params_layout.addRow("Manual row angle:", self.manual_angle)
        params_layout.addRow("Minimum palms per row:", self.min_points_row)
        main_layout.addWidget(params_group)

        output_group = QGroupBox("Output")
        output_layout = QFormLayout(output_group)
        output_layout.setLabelAlignment(_qt_align_right())

        out_line = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Optional. Leave empty to create a temporary memory layer.")
        self.browse_button = QPushButton("Browse...")
        out_line.addWidget(self.output_path)
        out_line.addWidget(self.browse_button)
        output_layout.addRow("Output file:", out_line)
        main_layout.addWidget(output_group)

        self.buttons = QDialogButtonBox(_button_standard("Ok") | _button_standard("Cancel"))
        self.buttons.button(_button_standard("Ok")).setText("Detect palm gaps")
        self.buttons.button(_button_standard("Cancel")).setText("Cancel")
        main_layout.addWidget(self.buttons)

        footer = QLabel("by Jorge H Caal Pineda")
        footer.setAlignment(_qt_align_right() | _qt_align_vcenter())
        footer.setStyleSheet("font-size: 11px; color: #222;")
        opacity = QGraphicsOpacityEffect(footer)
        opacity.setOpacity(0.42)
        footer.setGraphicsEffect(opacity)
        main_layout.addWidget(footer)

    def _connect_signals(self):
        self.buttons.accepted.connect(self._validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        self.browse_button.clicked.connect(self._browse_output)
        self.add_exclusion_button.clicked.connect(self._add_exclusion_layer)
        self.remove_exclusion_button.clicked.connect(self._remove_selected_exclusion_layers)
        self.lots_combo.currentIndexChanged.connect(self._refresh_lot_fields)
        self.auto_orientation.toggled.connect(lambda checked: self.manual_angle.setEnabled(not checked))
        self.plant_distance.valueChanged.connect(self._auto_row_tolerance)

    def _load_layers(self):
        self.points_combo.clear()
        self.lots_combo.clear()
        self.lot_field_combo.clear()
        self.exclusion_combo.clear()
        self.exclusion_list.clear()
        self.point_layers = []
        self.polygon_layers = []
        self.vector_layers = []

        self.lots_combo.addItem("No lot layer", None)
        self.exclusion_combo.addItem("Select an exclusion layer...", None)

        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() != QgsMapLayerType.VectorLayer:
                continue
            self.vector_layers.append(layer)
            self.exclusion_combo.addItem(layer.name(), layer.id())

            geom_type = QgsWkbTypes.geometryType(layer.wkbType())
            if geom_type == QgsWkbTypes.PointGeometry:
                self.point_layers.append(layer)
                self.points_combo.addItem(layer.name(), layer.id())
            elif geom_type == QgsWkbTypes.PolygonGeometry:
                self.polygon_layers.append(layer)
                self.lots_combo.addItem(layer.name(), layer.id())

        self._refresh_lot_fields()

    def _refresh_lot_fields(self):
        self.lot_field_combo.clear()
        self.lot_field_combo.addItem("Use internal lot ID", None)
        lot_layer = self._layer_from_combo(self.lots_combo)
        if not lot_layer:
            self.lot_field_combo.setEnabled(False)
            return
        self.lot_field_combo.setEnabled(True)
        for field in lot_layer.fields():
            self.lot_field_combo.addItem(field.name(), field.name())

    def _suggested_row_tolerance(self):
        distance = self.plant_distance.value() if hasattr(self, "plant_distance") else 9.0
        staggered_row_factor = 0.8660254038
        return round(max(0.5, distance * staggered_row_factor * 0.45), 2)

    def _auto_row_tolerance(self):
        self.row_tolerance.setValue(self._suggested_row_tolerance())

    def _layer_from_combo(self, combo):
        layer_id = combo.currentData()
        if not layer_id:
            return None
        return QgsProject.instance().mapLayer(layer_id)

    def _selected_exclusion_layer_ids(self):
        ids = []
        for row in range(self.exclusion_list.count()):
            item = self.exclusion_list.item(row)
            layer_id = item.data(_qt_user_role())
            if layer_id and layer_id not in ids:
                ids.append(layer_id)
        return ids

    def _selected_exclusion_layers(self):
        layers = []
        for layer_id in self._selected_exclusion_layer_ids():
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer and layer.isValid():
                layers.append(layer)
        return layers

    def _add_exclusion_layer(self):
        layer = self._layer_from_combo(self.exclusion_combo)
        if not layer:
            return
        if layer.id() in self._selected_exclusion_layer_ids():
            return
        item = QListWidgetItem(layer.name())
        item.setData(_qt_user_role(), layer.id())
        self.exclusion_list.addItem(item)

    def _remove_selected_exclusion_layers(self):
        for item in self.exclusion_list.selectedItems():
            row = self.exclusion_list.row(item)
            self.exclusion_list.takeItem(row)


    def _browse_output(self):
        default_name = "palm_gap_detector.gpkg"
        start_path = os.path.join(os.path.expanduser("~"), default_name)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save detected palm gaps",
            start_path,
            "GeoPackage (*.gpkg);;Shapefile (*.shp)",
        )
        if path:
            if not path.lower().endswith((".gpkg", ".shp")):
                path += ".gpkg"
            self.output_path.setText(path)

    def _validate_and_accept(self):
        if self.points_combo.currentIndex() < 0 or not self._layer_from_combo(self.points_combo):
            QMessageBox.warning(self, "Palm Gap Detector", "Select a palm point layer.")
            return
        if self.plant_distance.value() <= 0:
            QMessageBox.warning(self, "Palm Gap Detector", "Plant spacing must be greater than zero.")
            return
        if self.tolerance.value() < 0:
            QMessageBox.warning(self, "Palm Gap Detector", "Search tolerance cannot be negative.")
            return
        self.accept()

    def parameters(self):
        return {
            "points_layer": self._layer_from_combo(self.points_combo),
            "lots_layer": self._layer_from_combo(self.lots_combo),
            "lot_field": self.lot_field_combo.currentData(),
            "exclusion_layers": self._selected_exclusion_layers(),
            "exclusion_buffer": float(self.exclusion_buffer.value()),
            "planting_type": "staggered",
            "plant_distance": float(self.plant_distance.value()),
            "tolerance": float(self.tolerance.value()),
            "row_tolerance": float(self.row_tolerance.value()),
            "auto_orientation": bool(self.auto_orientation.isChecked()),
            "manual_angle": float(self.manual_angle.value()),
            "min_points_row": int(self.min_points_row.value()),
            "output_path": self.output_path.text().strip(),
        }
