"""Spectra spectral indices: the 4 added indices (NDMI, NDRE, MSAVI2, NBR) and the honest, single-call statistics.  Earth Engine is NOT reachable from the dev sandbox: images are replaced by stand-ins that
EVALUATE the band maths on numbers, so these check our formulas against the published ones (written independently below), not Earth Engine.   pytest tests/test_spectral_indices.py"""
import math
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from app.services import gee_client as gc
from app.services import gee_remote_sensing as g


class V:
    """A one-pixel image value that evaluates the band arithmetic used by _index_image."""
    def __init__(self, v): self.v = v
    @staticmethod
    def _n(o): return o.v if isinstance(o, V) else o
    def add(self, o): return V(self.v + self._n(o))
    def subtract(self, o): return V(self.v - self._n(o))
    def multiply(self, o): return V(self.v * self._n(o))
    def divide(self, o): return V(self.v / self._n(o))
    def pow(self, n): return V(self.v ** n)
    def sqrt(self): return V(math.sqrt(self.v))
    def rename(self, *_): return self
    def gt(self, t): return V(float(self.v > t))
    def updateMask(self, m): return V(self.v if m.v else None)
    def mask(self): return V(1.0)


class Comp:
    def __init__(self, **bands): self.b = bands
    def select(self, n): return V(self.b[n])
    def normalizedDifference(self, pair): a, c = self.b[pair[0]], self.b[pair[1]]; return V((a - c) / (a + c))


# Sentinel-2 surface reflectances (0-1): blue B2, green B3, red B4, red-edge1 B5, NIR B8, SWIR1 B11, SWIR2 B12.
HEALTHY = dict(B2=0.04, B3=0.08, B4=0.05, B5=0.18, B8=0.40, B11=0.20, B12=0.10)
DRY_BURNT = dict(B2=0.09, B3=0.12, B4=0.20, B5=0.22, B8=0.25, B11=0.38, B12=0.33)
SPARSE = dict(B2=0.10, B3=0.14, B4=0.22, B5=0.24, B8=0.28, B11=0.35, B12=0.30)


def published(r):
    """The published formulas, written out independently of the code under test."""
    g_, red, blue, re1, nir, s1, s2 = r["B3"], r["B4"], r["B2"], r["B5"], r["B8"], r["B11"], r["B12"]
    nd = lambda a, b: (a - b) / (a + b)
    return {"ndvi": nd(nir, red), "ndwi": nd(g_, nir), "mndwi": nd(g_, s1), "ndbi": nd(s1, nir), "savi": 1.5 * (nir - red) / (nir + red + 0.5), "evi": 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1),
            "ndsi": nd(g_, s1), "ndmi": nd(nir, s1), "ndre": nd(nir, re1), "msavi2": (2 * nir + 1 - math.sqrt((2 * nir + 1) ** 2 - 8 * (nir - red))) / 2, "nbr": nd(nir, s2)}


@pytest.mark.parametrize("refl", [HEALTHY, DRY_BURNT, SPARSE], ids=["healthy", "dry_burnt", "sparse"])
def test_every_index_matches_its_published_formula_on_real_numbers(refl):
    want = published(refl); assert set(want) == set(g.INDEX_DEFINITIONS), "every defined index has an independent reference value"
    for idx, w in want.items(): assert g._index_image(Comp(**refl), idx).v == pytest.approx(w, abs=1e-9), idx


def test_the_four_new_indices_have_the_expected_values_and_ordering():
    h, d = published(HEALTHY), published(DRY_BURNT)
    assert h["ndmi"] == pytest.approx(0.3333, abs=1e-3) and h["ndre"] == pytest.approx(0.3793, abs=1e-3) and h["nbr"] == pytest.approx(0.6, abs=1e-3) and 0 < h["msavi2"] < 1
    assert h["ndmi"] > d["ndmi"] and h["nbr"] > d["nbr"] and h["ndre"] > d["ndre"], "drier / burnt ground scores lower than healthy vegetation"


def test_msavi2_radicand_is_never_negative_over_all_physical_reflectances():
    for n in [i / 20 for i in range(0, 21)]:
        for r in [i / 20 for i in range(0, 21)]: assert (2 * n + 1) ** 2 - 8 * (n - r) >= 0, (n, r)
    assert g._index_image(Comp(B8=0.0, B4=0.0), "msavi2").v == pytest.approx(0.0), "no NaN at zero reflectance"
    assert g._index_image(Comp(B8=1.0, B4=0.0), "msavi2").v == pytest.approx(1.0)


def test_ndmi_is_the_ndwi_inside_the_corrected_nddi():
    """Gu et al. build NDDI from Gao's NDWI = (NIR-SWIR)/(NIR+SWIR): that is exactly this platform's NDMI, so the two must agree."""
    for refl in (HEALTHY, SPARSE):
        ndvi, ndmi = published(refl)["ndvi"], g._index_image(Comp(**refl), "ndmi").v; expected = (ndvi - ndmi) / (ndvi + ndmi)
        got = gc._nddi_image(Comp(**refl)).v
        if ndvi + ndmi > gc.NDDI_MIN_DENOMINATOR: assert got == pytest.approx(expected, abs=1e-9)
        else: assert got is None


# ---------------- honest labelling ----------------
def test_definitions_are_complete_and_every_index_has_a_palette():
    for k, d in g.INDEX_DEFINITIONS.items():
        for f in ("label", "formula", "citation", "interpretation"): assert d[f] and len(d[f]) >= 6, (k, f)
        assert len(d["bands"]) >= 2 and all(b.startswith("B") for b in d["bands"]), k
        p = g.INDEX_PALETTES[k]; assert p["min"] < p["max"] and len(p["palette"]) >= 4 and all(c.startswith("#") for c in p["palette"]), k


def test_new_indices_are_labelled_honestly():
    D = g.INDEX_DEFINITIONS
    assert "not nitrogen directly" in D["ndre"]["interpretation"] and "chlorophyll" in D["ndre"]["interpretation"] and "20 m" in D["ndre"]["interpretation"]
    assert "NOT the open-water NDWI" in D["ndmi"]["interpretation"] and "McFeeters" in D["ndwi"]["label"] and "Gao" in D["ndmi"]["label"]
    assert "single-date" in D["nbr"]["interpretation"] and "dNBR" in D["nbr"]["interpretation"]
    assert "soil" in D["msavi2"]["interpretation"].lower() and "Qi et al. 1994" in D["msavi2"]["citation"]
    assert D["ndre"]["bands"] == ["B8", "B5"] and D["ndmi"]["bands"] == ["B8", "B11"] and D["nbr"]["bands"] == ["B8", "B12"] and D["msavi2"]["bands"] == ["B8", "B4"]


# ---------------- compute_spectral_indices ----------------
class Res:
    def __init__(self, v): self.v = v
    def getInfo(self): return self.v


class Region:
    is_point = False
    def centroid(self, maxError=None): p = Region(); p.is_point = True; return p


class Node:
    def __init__(self, poly=None, point=None): self.poly, self.point, self.calls = poly, point, 0
    def reduceRegion(self, **kw): self.calls += 1; return Res(self.point if getattr(kw["geometry"], "is_point", False) else self.poly)
    def select(self, name): return Node(self.poly, self.point) if name != "B4" else self._b4
    def mask(self): return self._valid
    def __getattr__(self, name): return lambda *a, **k: self


class Reducer:
    def __getattr__(self, name): return lambda *a, **k: self
    def combine(self, *a, **k): return self


@pytest.fixture
def run(monkeypatch):
    def go(stats_poly, stats_point=None, valid=1.0, indices=None):
        stack = Node(stats_poly, stats_point); b4 = Node(); b4._valid = Node({"B4": valid}, {"B4": valid}); b4.select = lambda n: b4
        comp = SimpleNamespace(select=lambda n: SimpleNamespace(mask=lambda: b4._valid))
        built = []
        monkeypatch.setattr(g, "ee", SimpleNamespace(Image=SimpleNamespace(cat=lambda imgs: (built.append(len(imgs)), stack)[1]), Reducer=Reducer()))
        monkeypatch.setattr(g, "_index_image", lambda c, i: Node())
        monkeypatch.setattr(g, "_polygon_geometry", lambda a: Region()); monkeypatch.setattr(g, "_s2_composite", lambda r, s, e: (comp, 7))
        monkeypatch.setattr(g, "_tile_layer", lambda *a, **k: {"t": 1}); monkeypatch.setattr(g, "_download_url", lambda *a, **k: "u"); monkeypatch.setattr(g, "_region_area_km2", lambda r: 2.0)
        monkeypatch.setattr(g, "_scene_provenance", lambda *a, **k: [])
        r = g.compute_spectral_indices({"type": "Polygon"}, "2026-06-01", "2026-06-30", indices); return r, stack, built
    return go


def full_stats(ids, base=0.5):
    out = {}
    for i in ids:
        b = i.upper(); out.update({f"{b}_mean": base, f"{b}_min": base - 0.2, f"{b}_max": base + 0.2, f"{b}_stdDev": 0.05})
    return out


def test_default_is_all_eleven_indices_in_one_reduction(run):
    ids = list(g.INDEX_DEFINITIONS); r, stack, built = run(full_stats(ids))
    assert list(r["indices"]) == ids and len(ids) == 11 and built == [11], "all eleven stacked into one image"
    assert stack.calls == 1, "one Earth Engine reduction for the whole stack, not one per index"
    assert r["indices"]["ndre"]["mean"] == 0.5 and r["indices"]["nbr"]["min"] == 0.3 and r["valid_pixel_fraction"] == 1.0 and r["sampling"] == "aoi" and r["sampling_note"] is None


def test_a_subset_of_indices_is_honoured(run):
    r, stack, built = run(full_stats(["ndre", "nbr"]), indices=["ndre", "nbr"]); assert list(r["indices"]) == ["ndre", "nbr"] and built == [2]


def test_unknown_index_is_rejected_listing_the_valid_ones(run):
    with pytest.raises(ValueError) as e: run(full_stats(["ndvi"]), indices=["ndvi", "ndxx"])
    assert "ndxx" in str(e.value) and "msavi2" in str(e.value)


def test_a_missing_statistic_is_none_never_zero(run):
    ids = list(g.INDEX_DEFINITIONS); null = {k: None for k in full_stats(ids)}
    r, _, _ = run(null, null, valid=None)
    for i, d in r["indices"].items(): assert d["mean"] is None and d["min"] is None and d["max"] is None and d["std_dev"] is None, i
    assert r["valid_pixel_fraction"] is None and r["sampling"] is None


def test_a_genuine_zero_mean_stays_a_value(run):
    ids = ["ndvi", "ndre"]; s = full_stats(ids); s["NDRE_mean"] = 0.0; r, _, _ = run(s, indices=ids); assert r["indices"]["ndre"]["mean"] == 0.0


def test_tiny_area_falls_back_to_the_pixel_at_the_centre_and_says_so(run):
    ids = ["ndvi", "ndmi"]; null = {k: None for k in full_stats(ids)}; r, _, _ = run(null, full_stats(ids, 0.4), indices=ids)
    assert r["indices"]["ndmi"]["mean"] == 0.4 and r["sampling"] == "pixel_at_centroid" and "smaller than one Sentinel-2 pixel" in r["sampling_note"]
