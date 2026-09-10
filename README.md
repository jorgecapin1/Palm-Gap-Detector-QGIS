# Palm Gap Detector

Version 1.3.1

**Palm Gap Detector** is a QGIS plugin for oil palm inventories. It detects probable missing palm positions by analyzing spacing jumps between georeferenced palm points inside real planting rows.

The plugin is designed specifically for oil palm plantations and assumes a triangular/staggered planting system.

## Main features

- Detects probable missing palms from existing palm point layers.
- Works with optional lot polygons and lot ID fields.
- Supports multiple optional exclusion layers for roads, streams, drains, canals, buildings or non-plantable areas.
- Omits detected gaps that fall inside an exclusion layer or inside its configured buffer.
- Classifies each remaining detected point with `confidence`: `High`, `Medium` or `Low`.
- Adds context fields such as `seq_len`, `cluster_id`, `cluster_sz` and `conf_reason`.
- Exports results to temporary memory layer, GeoPackage or Shapefile.
- Applies automatic categorized symbology by confidence.

## Compatibility

This package is prepared for QGIS 3.x and QGIS 4.x, including Qt5 and Qt6 based installations. Recommended versions: QGIS 3.16 or later and QGIS 4.x up to the current 4.x series. QGIS 2.x is not supported because it uses a different Python/PyQt plugin architecture.

## Recommended data

Use palm point layers in a projected coordinate reference system with meter units, such as UTM. The algorithm uses metric distances to analyze planting spacing and row grouping.

## Confidence logic

A gap is not only evaluated by spacing distance. The plugin also lowers confidence when the detected point is near lot boundaries, part of consecutive missing palms, or part of a larger spatial cluster that may represent a stream, drain, road or non-plantable strip.

## Exclusion layers

You can add more than one exclusion layer using the `+` button. These layers can represent roads, streams, drains, canals, buildings, offices, houses or other non-plantable areas. The plugin applies the configured exclusion buffer and does not create gap points inside those areas.

## Author

by Jorge H Caal Pineda


## Changelog

### 1.3.1

- Resolved QGIS plugin repository Bandit security scan warnings by replacing silent exception handling with explicit fallback handling.
- No detection logic changes were introduced.

### 1.3.0

- Added QGIS 4.x / Qt6 compatibility updates.
