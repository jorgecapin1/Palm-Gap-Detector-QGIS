# -*- coding: utf-8 -*-
"""
Core logic for Palm Gap Detector.

Method:
- Detect dominant row orientation, or use a manual orientation.
- Project palms into row/perpendicular coordinates.
- Cluster palms into real planting rows.
- Detect large spacing jumps between consecutive palms inside each row.
- Insert probable missing palm positions as gap points.
- Omit gap points that fall inside optional exclusion layers or their buffer.
- Reclassify confidence using row sequences, spatial clusters and lot boundaries.
"""

import math
import os
from collections import namedtuple, deque

try:
    from qgis.PyQt.QtCore import QVariant
except ImportError:  # pragma: no cover - future Qt compatibility fallback
    class QVariant:  # pylint: disable=too-few-public-methods
        Int = int
        Double = float
        String = str

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsRendererCategory,
    QgsSpatialIndex,
    QgsUnitTypes,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)

PalmPoint = namedtuple("PalmPoint", "fid x y point geom attrs")


class GapDetectionError(Exception):
    """Raised when the gap detector cannot continue."""


def _geometry_type_value(name):
    geometry_type = getattr(QgsWkbTypes, "GeometryType", None)
    if geometry_type is not None and hasattr(geometry_type, name):
        return getattr(geometry_type, name)
    return getattr(QgsWkbTypes, name)


def run_gap_detection(params, iface=None):  # pylint: disable=unused-argument
    points_layer = params["points_layer"]
    lots_layer = params.get("lots_layer")
    lot_field = params.get("lot_field")
    exclusion_layers = params.get("exclusion_layers")
    if exclusion_layers is None:
        legacy_exclusion_layer = params.get("exclusion_layer")
        exclusion_layers = [legacy_exclusion_layer] if legacy_exclusion_layer else []
    exclusion_buffer = float(params.get("exclusion_buffer", 5.0))
    planting_type = params.get("planting_type", "staggered")
    plant_distance = float(params.get("plant_distance", 9.0))
    tolerance = float(params.get("tolerance", 2.0))
    row_tolerance = float(params.get("row_tolerance", plant_distance * 0.45))
    auto_orientation = bool(params.get("auto_orientation", True))
    manual_angle = float(params.get("manual_angle", 0.0))
    min_points_row = int(params.get("min_points_row", 3))
    output_path = params.get("output_path", "")

    if not points_layer or not points_layer.isValid():
        raise GapDetectionError("The palm point layer is not valid.")

    if QgsWkbTypes.geometryType(points_layer.wkbType()) != _geometry_type_value("PointGeometry"):
        raise GapDetectionError("The palm layer must be a point layer.")

    if not _crs_uses_meter_units(points_layer.crs()):
        raise GapDetectionError(
            "The palm layer must use a projected CRS with meter units. "
            "Reproject the layer before running Palm Gap Detector."
        )

    all_points, point_index = _collect_points(points_layer)
    if len(all_points) < 2:
        raise GapDetectionError("The palm layer must contain at least two points.")

    exclusion_geometries = _collect_exclusion_geometries(exclusion_layers, points_layer)

    out_layer = _create_output_layer(points_layer.crs())
    provider = out_layer.dataProvider()

    stats = {
        "gaps": 0,
        "rows": 0,
        "lots": 0,
        "excluded": 0,
        "low_confidence": 0,
        "medium_confidence": 0,
        "high_confidence": 0,
    }
    next_id = 1
    existing_gap_points = []
    planting_type_label = _planting_type_label(planting_type)

    work_units = _build_work_units(all_points, point_index, points_layer, lots_layer, lot_field)

    for lot_name, lot_geom, lot_points in work_units:
        if len(lot_points) < 2:
            continue
        stats["lots"] += 1

        if auto_orientation:
            angle_deg = _estimate_orientation(lot_points, plant_distance)
        else:
            angle_deg = manual_angle

        rows = _cluster_rows(lot_points, angle_deg, row_tolerance)
        rows = [row for row in rows if len(row["points"]) >= min_points_row]
        stats["rows"] += len(rows)

        lot_gaps = []
        for row_number, row in enumerate(rows, start=1):
            row_points = sorted(row["points"], key=lambda p: p["u"])
            created_from_row = _detect_gaps_in_row(
                row_points=row_points,
                row_number=row_number,
                plant_distance=plant_distance,
                tolerance=tolerance,
                lot_geom=lot_geom,
                existing_index=point_index,
                existing_points=all_points,
                existing_gap_points=existing_gap_points,
            )
            lot_gaps.extend(created_from_row)

        lot_gaps, excluded_count = _filter_excluded_gaps(
            gaps=lot_gaps,
            exclusion_geometries=exclusion_geometries,
            exclusion_buffer=exclusion_buffer,
        )
        stats["excluded"] += excluded_count

        _apply_context_confidence(
            gaps=lot_gaps,
            plant_distance=plant_distance,
            tolerance=tolerance,
            lot_geom=lot_geom,
        )

        for gap in lot_gaps:
            feat = QgsFeature(provider.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(gap["point"]))
            confidence = gap.get("confidence", "Medium")
            feat.setAttributes(
                [
                    next_id,
                    "Probable gap",
                    "Spacing jump",
                    "Real rows",
                    str(lot_name),
                    int(gap["row_number"]),
                    int(gap["gap_index"]),
                    round(float(gap["jump_distance"]), 3),
                    int(gap["missing_count"]),
                    round(float(plant_distance), 3),
                    planting_type_label,
                    confidence,
                    int(gap.get("sequence_len", 1)),
                    int(gap.get("cluster_id", 0)),
                    int(gap.get("cluster_size", 1)),
                    _reason_text(gap.get("confidence_reasons", [])),
                ]
            )
            provider.addFeature(feat)
            existing_gap_points.append(gap["point"])
            next_id += 1
            stats["gaps"] += 1
            if confidence == "High":
                stats["high_confidence"] += 1
            elif confidence == "Medium":
                stats["medium_confidence"] += 1
            else:
                stats["low_confidence"] += 1

    out_layer.updateExtents()
    _apply_gap_style(out_layer)

    final_layer = _save_or_add_layer(out_layer, output_path)
    return final_layer, stats


def _planting_type_label(planting_type):  # pylint: disable=unused-argument
    return "Staggered"


def _collect_points(layer):
    points = []
    index = QgsSpatialIndex()

    for feat in layer.getFeatures():
        geom = feat.geometry()
        if not geom or geom.isEmpty():
            continue

        if geom.isMultipart():
            multi = geom.asMultiPoint()
            if not multi:
                continue
            qpt = QgsPointXY(multi[0])
        else:
            qpt = QgsPointXY(geom.asPoint())

        point_geom = QgsGeometry.fromPointXY(qpt)
        p = PalmPoint(feat.id(), qpt.x(), qpt.y(), qpt, point_geom, feat.attributes())
        points.append(p)

        idx_feat = QgsFeature()
        idx_feat.setId(len(points) - 1)
        idx_feat.setGeometry(point_geom)
        index.addFeature(idx_feat)

    return points, index


def _collect_exclusion_geometries(exclusion_layers, points_layer):
    if not exclusion_layers:
        return []

    geometries = []
    for exclusion_layer in exclusion_layers:
        if not exclusion_layer or not exclusion_layer.isValid():
            continue

        transform = None
        if exclusion_layer.crs() != points_layer.crs():
            transform = QgsCoordinateTransform(exclusion_layer.crs(), points_layer.crs(), QgsProject.instance())

        for feat in exclusion_layer.getFeatures():
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                continue
            geom = QgsGeometry(geom)
            if transform:
                geom.transform(transform)
            geometries.append(geom)
    return geometries


def _build_work_units(all_points, point_index, points_layer, lots_layer, lot_field):
    if not lots_layer or not lots_layer.isValid():
        return [("ALL", None, all_points)]

    if QgsWkbTypes.geometryType(lots_layer.wkbType()) != _geometry_type_value("PolygonGeometry"):
        raise GapDetectionError("The optional lot layer must be a polygon layer.")

    transform = None
    if lots_layer.crs() != points_layer.crs():
        transform = QgsCoordinateTransform(lots_layer.crs(), points_layer.crs(), QgsProject.instance())

    units = []
    used_any = False

    for lot_feat in lots_layer.getFeatures():
        lot_geom = lot_feat.geometry()
        if not lot_geom or lot_geom.isEmpty():
            continue
        lot_geom = QgsGeometry(lot_geom)
        if transform:
            lot_geom.transform(transform)

        lot_name = lot_feat.id()
        if lot_field and lot_field in lots_layer.fields().names():
            lot_name = lot_feat[lot_field]

        candidate_ids = point_index.intersects(lot_geom.boundingBox())
        lot_points = []
        for pid in candidate_ids:
            p = all_points[pid]
            if lot_geom.contains(p.geom) or lot_geom.intersects(p.geom):
                lot_points.append(p)

        if lot_points:
            used_any = True
            units.append((lot_name, lot_geom, lot_points))

    if not used_any:
        raise GapDetectionError("No palm points were found inside the selected lot layer.")

    return units


def _estimate_orientation(points, plant_distance):
    """Estimate dominant row angle in degrees, modulo 180."""
    if len(points) < 2:
        return 0.0

    index = QgsSpatialIndex()
    for i, p in enumerate(points):
        feat = QgsFeature()
        feat.setId(i)
        feat.setGeometry(p.geom)
        index.addFeature(feat)

    min_d = max(0.01, plant_distance * 0.45)
    max_d = plant_distance * 1.65
    bins = [0.0] * 180

    for i, p in enumerate(points):
        neighbor_ids = index.nearestNeighbor(p.point, 10)
        for j in neighbor_ids:
            if j <= i or j >= len(points):
                continue
            q = points[j]
            dx = q.x - p.x
            dy = q.y - p.y
            dist = math.hypot(dx, dy)
            if dist < min_d or dist > max_d:
                continue
            angle = math.degrees(math.atan2(dy, dx)) % 180.0
            bin_index = int(round(angle)) % 180
            bins[bin_index] += 1.0 / max(dist, 0.01)

    if not any(bins):
        return 0.0

    best_bin = max(range(180), key=lambda idx: bins[idx])

    sx = 0.0
    sy = 0.0
    for offset in range(-8, 9):
        idx = (best_bin + offset) % 180
        weight = bins[idx]
        angle_rad = math.radians(idx * 2.0)
        sx += math.cos(angle_rad) * weight
        sy += math.sin(angle_rad) * weight

    if sx == 0.0 and sy == 0.0:
        return float(best_bin)

    refined = math.degrees(math.atan2(sy, sx)) / 2.0
    if refined < 0:
        refined += 180.0
    return refined % 180.0


def _project_xy(x, y, angle_deg):
    theta = math.radians(angle_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    u = x * cos_t + y * sin_t
    v = -x * sin_t + y * cos_t
    return u, v


def _cluster_rows(points, angle_deg, row_tolerance):
    projected = []
    for p in points:
        u, v = _project_xy(p.x, p.y, angle_deg)
        projected.append({"palm": p, "u": u, "v": v})

    projected.sort(key=lambda item: item["v"])
    rows = []

    for item in projected:
        if not rows:
            rows.append({"mean_v": item["v"], "points": [item]})
            continue

        current = rows[-1]
        if abs(item["v"] - current["mean_v"]) <= row_tolerance:
            current["points"].append(item)
            current["mean_v"] = sum(p["v"] for p in current["points"]) / len(current["points"])
        else:
            rows.append({"mean_v": item["v"], "points": [item]})

    return rows


def _detect_gaps_in_row(
    row_points,
    row_number,
    plant_distance,
    tolerance,
    lot_geom,
    existing_index,
    existing_points,
    existing_gap_points,
):
    gaps = []
    if len(row_points) < 2:
        return gaps

    min_jump = plant_distance + tolerance
    duplicate_gap_radius = max(0.1, tolerance)
    existing_palm_radius = max(0.1, tolerance)

    for i in range(len(row_points) - 1):
        p1 = row_points[i]["palm"]
        p2 = row_points[i + 1]["palm"]
        dx = p2.x - p1.x
        dy = p2.y - p1.y
        segment_len = math.hypot(dx, dy)

        if segment_len <= min_jump:
            continue

        estimated_slots = int(round(segment_len / plant_distance))
        missing_count = max(0, estimated_slots - 1)
        if missing_count <= 0:
            continue

        implied_spacing = segment_len / float(missing_count + 1)
        if abs(implied_spacing - plant_distance) > max(tolerance * 2.5, plant_distance * 0.45):
            continue

        base_confidence, base_reasons = _base_confidence_label(
            implied_spacing=implied_spacing,
            plant_distance=plant_distance,
            tolerance=tolerance,
            missing_count=missing_count,
        )

        if missing_count == 2:
            base_confidence = _decrease_confidence(base_confidence)
            base_reasons.append("two consecutive gaps")
        elif missing_count >= 3:
            base_confidence = "Low"
            base_reasons.append("three or more consecutive gaps")

        for gap_idx in range(1, missing_count + 1):
            fraction = gap_idx / float(missing_count + 1)
            gx = p1.x + dx * fraction
            gy = p1.y + dy * fraction
            gap_point = QgsPointXY(gx, gy)
            gap_geom = QgsGeometry.fromPointXY(gap_point)

            if lot_geom and not (lot_geom.contains(gap_geom) or lot_geom.intersects(gap_geom)):
                continue

            if _has_existing_palm_near(gap_point, existing_index, existing_points, existing_palm_radius):
                continue

            if _has_existing_gap_near(gap_point, existing_gap_points, duplicate_gap_radius):
                continue

            gap_u, _ = _project_xy(gx, gy, math.degrees(math.atan2(dy, dx)) % 180.0)
            gaps.append(
                {
                    "point": gap_point,
                    "geom": gap_geom,
                    "row_number": row_number,
                    "segment_index": i,
                    "gap_index": gap_idx,
                    "jump_distance": segment_len,
                    "missing_count": missing_count,
                    "implied_spacing": implied_spacing,
                    "confidence": base_confidence,
                    "confidence_reasons": list(base_reasons),
                    "sequence_len": missing_count,
                    "u": gap_u,
                }
            )

    return gaps


def _base_confidence_label(implied_spacing, plant_distance, tolerance, missing_count):  # pylint: disable=unused-argument
    deviation = abs(implied_spacing - plant_distance)
    high_limit = max(tolerance, plant_distance * 0.12)
    medium_limit = max(tolerance * 2.0, plant_distance * 0.28)

    if deviation <= high_limit:
        return "High", ["spacing matches plant distance"]
    if deviation <= medium_limit:
        return "Medium", ["moderate spacing deviation"]
    return "Low", ["high spacing deviation"]


def _apply_context_confidence(gaps, plant_distance, tolerance, lot_geom):
    if not gaps:
        return

    for gap in gaps:
        gap["cluster_id"] = 0
        gap["cluster_size"] = 1

        if lot_geom and gap.get("geom"):
            try:
                boundary = lot_geom.boundary()
                boundary_distance = gap["geom"].distance(boundary)
                if boundary_distance <= max(tolerance * 2.0, plant_distance * 0.25):
                    gap["confidence"] = _decrease_confidence(gap["confidence"])
                    gap["confidence_reasons"].append("near lot boundary")
            except (RuntimeError, ValueError, TypeError, AttributeError) as boundary_error:
                gap["confidence_reasons"].append(
                    "lot boundary check skipped: {}".format(type(boundary_error).__name__)
                )

    clusters = _build_gap_clusters(gaps, plant_distance, tolerance)
    for cluster_id, cluster_indices in enumerate(clusters, start=1):
        cluster_size = len(cluster_indices)
        row_count = len(set(gaps[idx]["row_number"] for idx in cluster_indices))

        for idx in cluster_indices:
            gap = gaps[idx]
            gap["cluster_id"] = cluster_id
            gap["cluster_size"] = cluster_size

            if cluster_size >= 6 or (cluster_size >= 3 and row_count >= 3):
                gap["confidence"] = "Low"
                gap["confidence_reasons"].append("large aligned gap cluster / possible non-plantable area")
            elif cluster_size >= 3:
                gap["confidence"] = _decrease_confidence(gap["confidence"])
                gap["confidence_reasons"].append("near other detected gaps")


def _filter_excluded_gaps(gaps, exclusion_geometries, exclusion_buffer):
    if not gaps or not exclusion_geometries:
        return gaps, 0

    kept = []
    excluded_count = 0
    for gap in gaps:
        geom = gap.get("geom")
        if geom and _near_exclusion(geom, exclusion_geometries, exclusion_buffer):
            excluded_count += 1
            continue
        kept.append(gap)
    return kept, excluded_count


def _build_gap_clusters(gaps, plant_distance, tolerance):
    if not gaps:
        return []

    index = QgsSpatialIndex()
    for i, gap in enumerate(gaps):
        feat = QgsFeature()
        feat.setId(i)
        feat.setGeometry(gap["geom"])
        index.addFeature(feat)

    cluster_radius = max(plant_distance * 1.6, tolerance * 3.0, 1.0)
    visited = set()
    clusters = []

    for i, gap in enumerate(gaps):
        if i in visited:
            continue
        queue = deque([i])
        visited.add(i)
        component = []

        while queue:
            current_id = queue.popleft()
            component.append(current_id)
            point = gaps[current_id]["point"]
            rect = QgsRectangle(
                point.x() - cluster_radius,
                point.y() - cluster_radius,
                point.x() + cluster_radius,
                point.y() + cluster_radius,
            )
            for neighbor_id in index.intersects(rect):
                if neighbor_id in visited or neighbor_id < 0 or neighbor_id >= len(gaps):
                    continue
                neighbor_point = gaps[neighbor_id]["point"]
                if _point_distance(point, neighbor_point) <= cluster_radius:
                    visited.add(neighbor_id)
                    queue.append(neighbor_id)

        clusters.append(component)

    return clusters


def _near_exclusion(gap_geom, exclusion_geometries, buffer_distance):
    if buffer_distance < 0:
        buffer_distance = 0.0

    for geom in exclusion_geometries:
        if not geom or geom.isEmpty():
            continue
        geometry_check_failed = False
        try:
            if geom.contains(gap_geom) or geom.intersects(gap_geom):
                return True
            if gap_geom.distance(geom) <= buffer_distance:
                return True
        except (RuntimeError, ValueError, TypeError, AttributeError):
            geometry_check_failed = True

        if geometry_check_failed:
            continue
    return False


def _decrease_confidence(confidence):
    if confidence == "High":
        return "Medium"
    if confidence == "Medium":
        return "Low"
    return "Low"


def _point_distance(point_a, point_b):
    return math.hypot(point_a.x() - point_b.x(), point_a.y() - point_b.y())


def _has_existing_palm_near(point, index, points, radius):
    rect = QgsRectangle(point.x() - radius, point.y() - radius, point.x() + radius, point.y() + radius)
    for pid in index.intersects(rect):
        p = points[pid]
        if math.hypot(point.x() - p.x, point.y() - p.y) <= radius:
            return True
    return False


def _has_existing_gap_near(point, existing_gap_points, radius):
    radius_sq = radius * radius
    for gp in existing_gap_points:
        dx = point.x() - gp.x()
        dy = point.y() - gp.y()
        if dx * dx + dy * dy <= radius_sq:
            return True
    return False


def _reason_text(reasons):
    if not reasons:
        return ""
    clean = []
    for reason in reasons:
        if reason and reason not in clean:
            clean.append(reason)
    return "; ".join(clean)[:240]


def _crs_uses_meter_units(crs):
    """Return True when the CRS uses meters, compatible with QGIS 3 and 4."""
    unit = crs.mapUnits()
    distance_unit = getattr(Qgis, "DistanceUnit", None)
    if distance_unit is not None:
        for candidate in ("Meters", "Meter", "DistanceMeters"):
            candidate_value = getattr(distance_unit, candidate, None)
            if candidate_value is not None and unit == candidate_value:
                return True

    qgs_unit_types_distance = getattr(QgsUnitTypes, "DistanceUnit", None)
    if qgs_unit_types_distance is not None:
        for candidate in ("Meters", "Meter", "DistanceMeters"):
            candidate_value = getattr(qgs_unit_types_distance, candidate, None)
            if candidate_value is not None and unit == candidate_value:
                return True

    legacy_meters = getattr(QgsUnitTypes, "DistanceMeters", None)
    return legacy_meters is not None and unit == legacy_meters


def _writer_overwrite_action():
    """Create/overwrite action compatible with QGIS 3 and 4."""
    action_enum = getattr(QgsVectorFileWriter, "ActionOnExistingFile", None)
    if action_enum is not None:
        action_value = getattr(action_enum, "CreateOrOverwriteFile", None)
        if action_value is not None:
            return action_value
    legacy_action = getattr(QgsVectorFileWriter, "CreateOrOverwriteFile", None)
    if legacy_action is not None:
        return legacy_action
    return 0


def _writer_no_error():
    """No-error value compatible with QGIS 3 and 4."""
    writer_error = getattr(QgsVectorFileWriter, "WriterError", None)
    if writer_error is not None:
        no_error = getattr(writer_error, "NoError", None)
        if no_error is not None:
            return no_error
    legacy_no_error = getattr(QgsVectorFileWriter, "NoError", None)
    if legacy_no_error is not None:
        return legacy_no_error
    return 0


def _write_vector_layer(layer, output_path, options):
    """Write a vector layer using the newest available QGIS writer API."""
    transform_context = QgsProject.instance().transformContext()
    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3"):
        return QgsVectorFileWriter.writeAsVectorFormatV3(layer, output_path, transform_context, options)
    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV2"):
        return QgsVectorFileWriter.writeAsVectorFormatV2(layer, output_path, transform_context, options)

    # Legacy fallback for early QGIS 3 builds.
    error = QgsVectorFileWriter.writeAsVectorFormat(
        layer,
        output_path,
        options.fileEncoding,
        layer.crs(),
        options.driverName,
    )
    if isinstance(error, tuple):
        return error
    return (error, "")


def _create_output_layer(crs):
    uri = "Point?crs={}".format(crs.authid())
    layer = QgsVectorLayer(uri, "Palm Gap Detector - detected gaps", "memory")
    provider = layer.dataProvider()

    provider.addAttributes(
        [
            QgsField("id", QVariant.Int),
            QgsField("status", QVariant.String, "", 40),
            QgsField("gap_type", QVariant.String, "", 40),
            QgsField("method", QVariant.String, "", 40),
            QgsField("lot", QVariant.String, "", 80),
            QgsField("row_id", QVariant.Int),
            QgsField("gap_idx", QVariant.Int),
            QgsField("jump_m", QVariant.Double, "", 12, 3),
            QgsField("missing", QVariant.Int),
            QgsField("theor_m", QVariant.Double, "", 12, 3),
            QgsField("plant_type", QVariant.String, "", 20),
            QgsField("confidence", QVariant.String, "", 20),
            QgsField("seq_len", QVariant.Int),
            QgsField("cluster_id", QVariant.Int),
            QgsField("cluster_sz", QVariant.Int),
            QgsField("conf_reason", QVariant.String, "", 250),
        ]
    )
    layer.updateFields()
    return layer


def _make_symbol(color_rgb):
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "color": color_rgb,
            "outline_color": "255,255,255,255",
            "outline_width": "0.4",
            "size": "3.2",
        }
    )
    return symbol


def _apply_gap_style(layer):
    categories = [
        QgsRendererCategory("High", _make_symbol("255,0,0,220"), "High"),
        QgsRendererCategory("Medium", _make_symbol("255,140,0,220"), "Medium"),
        QgsRendererCategory("Low", _make_symbol("255,230,0,230"), "Low"),
    ]
    layer.setRenderer(QgsCategorizedSymbolRenderer("confidence", categories))


def _save_or_add_layer(layer, output_path):
    if not output_path:
        QgsProject.instance().addMapLayer(layer)
        return layer

    output_path = os.path.normpath(output_path)
    ext = os.path.splitext(output_path)[1].lower()
    driver = "GPKG" if ext == ".gpkg" else "ESRI Shapefile"

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = driver
    options.fileEncoding = "UTF-8"
    options.layerName = "palm_gaps"
    options.actionOnExistingFile = _writer_overwrite_action()

    result = _write_vector_layer(layer, output_path, options)

    error_code = result[0]
    error_message = result[1] if len(result) > 1 else ""
    if error_code != _writer_no_error():
        raise GapDetectionError("Could not save the output: {}".format(error_message))

    if ext == ".gpkg":
        uri = "{}|layername=palm_gaps".format(output_path)
    else:
        uri = output_path

    saved_layer = QgsVectorLayer(uri, "Palm Gap Detector - detected gaps", "ogr")
    if not saved_layer.isValid():
        raise GapDetectionError("The output was saved, but QGIS could not load it automatically.")

    _apply_gap_style(saved_layer)
    QgsProject.instance().addMapLayer(saved_layer)
    return saved_layer
