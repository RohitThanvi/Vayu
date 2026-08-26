import logging

from fastapi import APIRouter, HTTPException

from ..services.global_layers import get_layer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/layers", tags=["Layers"])

VALID_LAYERS = ("ndvi", "sar", "thermal")   # true_color served client-side from EOX, not via this endpoint — see global_layers.py


@router.get("/{layer_key}", summary="Get a cached tile URL for a toggleable satellite layer")
async def get_layer_endpoint(layer_key: str):
    if layer_key not in VALID_LAYERS:
        raise HTTPException(status_code=400, detail=f"Unknown layer '{layer_key}'. Must be one of {VALID_LAYERS}")
    try:
        result = get_layer(layer_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to build layer '{layer_key}': {e}")
    if not result:
        raise HTTPException(status_code=404, detail=f"Layer '{layer_key}' not found")
    return result
