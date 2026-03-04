import json
import logging
import os
from typing import Dict, Optional
from uuid import UUID
from pathlib import Path

import ee

logger = logging.getLogger(__name__)

# Local storage fallback (when GCS is not available)
LOCAL_OUTPUT_DIR = Path(__file__).parent.parent.parent / "geojson_outputs"
LOCAL_OUTPUT_DIR.mkdir(exist_ok=True)

# Try to import GCS — optional
try:
    from google.cloud import storage as gcs_storage
    _gcs_available = True
except ImportError:
    _gcs_available = False

from ..core.config import settings

_storage_client = None

def _get_storage_client():
    global _storage_client
    if not _gcs_available:
        return None
    if not settings.GCP_PROJECT_ID or not settings.GCS_BUCKET_NAME:
        return None
    if _storage_client is None:
        try:
            _storage_client = gcs_storage.Client(project=settings.GCP_PROJECT_ID)
            logger.info(f"GCS client initialized: project={settings.GCP_PROJECT_ID}")
        except Exception as e:
            logger.warning(f"GCS init failed: {e}. Will use local storage.")
    return _storage_client


def process_and_store_results(
    loss_mask_image: ee.Image,
    aoi_geometry: ee.Geometry,
    request_id: UUID,
) -> Dict[str, Optional[str]]:
    logger.info(f"[{request_id}] Geoprocess: starting")

    # ── 1. Tile URL ────────────────────────────────────────────────────────────
    try:
        map_id = loss_mask_image.getMapId({"palette": ["#FF4136"], "min": 0, "max": 1})
        tile_url = map_id["tile_fetcher"].url_format
        logger.info(f"[{request_id}] Geoprocess: tile URL generated")
    except Exception as e:
        logger.warning(f"[{request_id}] Tile URL generation failed: {e}")
        tile_url = None

    # ── 2. Clean raster ────────────────────────────────────────────────────────
    try:
        min_px = 15
        px_count = loss_mask_image.connectedPixelCount(min_px, False)
        cleaned = loss_mask_image.updateMask(px_count.gte(min_px))
    except Exception as e:
        logger.warning(f"[{request_id}] Raster cleaning failed: {e}")
        cleaned = loss_mask_image

    # ── 3. Vectorize ───────────────────────────────────────────────────────────
    try:
        vectors = cleaned.reduceToVectors(
            geometry=aoi_geometry,
            scale=60,          # increased from 30 to reduce memory usage
            geometryType="polygon",
            eightConnected=False,
            maxPixels=1e9,     # reduced from 1e10
        )
        simplified = vectors.map(lambda f: f.simplify(maxError=100))
        logger.info(f"[{request_id}] Geoprocess: vectorization complete")
    except ee.EEException as e:
        logger.warning(f"[{request_id}] Vectorization failed: {e}")
        return {"geojson_url": None, "tile_url": tile_url}

    # ── 4. Fetch GeoJSON ───────────────────────────────────────────────────────
    try:
        geojson_data = simplified.getInfo()
        feature_count = len(geojson_data.get("features", []))
        logger.info(f"[{request_id}] Geoprocess: {feature_count} features fetched")
    except ee.EEException as e:
        logger.error(f"[{request_id}] GeoJSON fetch failed: {e}")
        return {"geojson_url": None, "tile_url": tile_url}

    if feature_count == 0:
        logger.info(f"[{request_id}] No change features found, skipping upload")
        return {"geojson_url": None, "tile_url": tile_url}

    geojson_str = json.dumps(geojson_data, separators=(",", ":"))
    geojson_url = None

    # ── 5a. Try GCS upload ─────────────────────────────────────────────────────
    client = _get_storage_client()
    if client and settings.GCS_BUCKET_NAME:
        try:
            bucket = client.bucket(settings.GCS_BUCKET_NAME)
            blob_name = f"geojson_outputs/{request_id}_change.geojson"
            blob = bucket.blob(blob_name)
            blob.upload_from_string(geojson_str, content_type="application/geo+json")
            blob.make_public()
            geojson_url = blob.public_url
            logger.info(f"[{request_id}] Uploaded to GCS: {geojson_url}")
        except Exception as e:
            logger.warning(f"[{request_id}] GCS upload failed: {e}. Falling back to local.")

    # ── 5b. Local file fallback ────────────────────────────────────────────────
    if not geojson_url:
        local_path = LOCAL_OUTPUT_DIR / f"{request_id}_change.geojson"
        local_path.write_text(geojson_str, encoding="utf-8")
        # Serve via backend's /geojson/{request_id} endpoint
        geojson_url = f"/api/v1/geojson/{request_id}"
        logger.info(f"[{request_id}] Saved locally: {local_path}")

    return {"geojson_url": geojson_url, "tile_url": tile_url}
