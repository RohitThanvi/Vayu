# Geofence boundary data

Vayu's marine agent warns when a position is near (or inside) a boundary, but **this folder ships with no
boundary data on purpose**: a wrong or outdated border line given to a fisherman is a safety and legal risk,
so the lines must come from an authoritative source that *you* have checked.

Drop one or more GeoJSON `FeatureCollection` files (`*.geojson`) here (or point `GEOFENCE_DIR` elsewhere).
Coordinates are WGS84 `[longitude, latitude]`.

Each feature needs a geometry (`LineString`, `MultiLineString`, `Polygon` or `MultiPolygon`) and these
`properties` (all optional except `name`):

| property    | meaning |
|-------------|---------|
| `name`      | shown to the user, e.g. `"India-Sri Lanka maritime boundary line"` |
| `kind`      | free label: `imbl`, `eez`, `mpa`, `restricted`, … |
| `mode`      | polygons only. `"restricted"` (default): alert when **inside**. `"permitted"`: alert when **outside** (e.g. a polygon of waters a boat licence allows) |
| `warn_km`   | distance at which a WARNING starts (default 25) |
| `danger_km` | distance at which a DANGER starts (default 8) |
| `source`    | where the geometry came from + date — shown in the alert |
| `note`      | extra guidance shown in the alert |

Suggested authoritative sources to evaluate: Marine Regions (EEZ and maritime boundary lines), the Protected
Planet / WDPA marine protected areas, and your state fisheries department for local restricted zones.
Boundary lines are indicative for screening only; they are not a substitute for official guidance.
