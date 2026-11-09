"""Regenerates the GENERAL (not location-specific) crop guides as PDFs: docs/manuals/Vayu_<crop>_guide.pdf, from the sourced knowledge base
(backend/app/services/agri/pathway_data.py). Handy for expert review (e.g. a KVK or ICAR scientist) without running the app.
Run from the repo root:  python backend/scripts/build_manual.py      (edit the data file, not the PDFs; farmers get the personalised version from the app)"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.agri import crop_pathways as cp, pathway_pdf as pp  # noqa: E402


def main() -> None:
    out = ROOT / "docs" / "manuals"
    out.mkdir(parents=True, exist_ok=True)
    for crop in cp.CROPS:
        pdf = pp.make_pathway_pdf(cp.build_report(crop["id"]), None, location_label="General guide (no location chosen)", generated_on=date.today().isoformat())
        (out / f"Vayu_{crop['id']}_guide.pdf").write_bytes(pdf)
        print(f"wrote docs/manuals/Vayu_{crop['id']}_guide.pdf ({len(pdf) // 1024} KB)")


if __name__ == "__main__":
    main()
