"""The shape contract of compute_vegetation_drought_index()['indices'], shared by its producer (gee_remote_sensing) and its consumers (agri/risk_assessment) so they cannot drift apart again.
Dependency-free on purpose: importing it must not initialise Earth Engine. (A previous version of the v2 risk assessment read indices['VHI'] while the producer wrote indices['vhi'], so the VHI indicator was
always 'unavailable' in production while its test, built on a hand-written upper-case fixture, passed.)"""
from typing import Any, Dict

from .gee_sampling import num

VDI_KEYS = ("VCI", "TCI", "VHI")


def vdi_index_key(key: str) -> str:
    """Key under which an index is stored in the 'indices' dict (lower case)."""
    return key.lower()


def package_vdi_indices(st: Dict[str, Any], meta: Dict[str, tuple], layer_for) -> Dict[str, Dict[str, Any]]:
    """The 'indices' dict. A statistic that is missing is None, never 0: a VHI of 0 would mean extreme drought."""
    out = {}
    for key in VDI_KEYS:
        label, formula, cite, interp = meta[key]
        out[vdi_index_key(key)] = {"label": label, "formula": formula, "citation": cite, "interpretation": interp,
                                   "mean": num(st, f"{key}_mean", 2), "min": num(st, f"{key}_min", 2), "max": num(st, f"{key}_max", 2), "std_dev": num(st, f"{key}_stdDev", 2), "map_layer": layer_for(key)}
    return out
