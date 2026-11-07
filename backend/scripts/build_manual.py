"""Regenerates docs/high-value-crops-manual.md from the sourced knowledge base (backend/app/services/agri/pathway_data.py).
Run from the repo root:  python backend/scripts/build_manual.py      (the manual is generated; edit the data file, not the markdown)"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.agri import crop_pathways as cp  # noqa: E402

INTRO = """# Growing high-value crops: a starter manual

*Generated {today} from Vayu's sourced knowledge base. Organic and cultural methods only.*

## Read this first

This manual is for someone trying a new crop, including someone farming for the first time. It tells you what each crop needs, what to do when your conditions are not ideal,
and how to grow and look after it. It is built from published sources (ICAR institutes, state agricultural universities, CSIR, FAO, Vikaspedia, and similar) and from general
agronomic knowledge. **Every line says which it is.** A line marked *general knowledge* has not been verified against a source here.

It is an advisory aid, not a guarantee of yield or income, and not a replacement for your local Krishi Vigyan Kendra (KVK). Some crops here fail often or cost a lot to set up; the
risk and capital ratings are meant to be taken seriously.

### Before you spend money (general knowledge)

1. **Visit your nearest KVK** or state agricultural university and tell them the crop you are considering. Ask for their local advice and for planting material they trust.
2. **Get your soil tested** and check your water source and its reliability through the whole season.
3. **Find your buyer first.** Know who will buy, in what form, at what grade, and how you will get it there before you plant at scale.
4. **Start small.** Plant a fraction of what you could. Learn the crop, then expand from what actually worked on your land.
5. **Keep records:** dates, inputs, weather events, problems, harvest weights and sale prices. They are how you learn and how you get credit later.

### About chemicals

{policy}

### How "can it grow here?" is decided

| Tier | Meaning |
|---|---|
| 1. Open-field suitable | Nothing in the climate and soil checks is outside the crop's cited range |
| 2. Open-field with help | Gaps exist but each has a field-level fix (irrigation, drainage, mulch, shade, soil amendment) |
| 3. Controlled environment | The crop is grown in a built space (a mushroom shed, a chilled saffron room), not by matching the open climate |
| 4. Not realistic here | A blocking gap (for example hard frost for a frost-sensitive crop) with no field-level fix |

Monthly average climate cannot show frost nights, hot dry winds or heat waves. Where that matters the report adds a caution; **check your own lowest and highest temperatures.**

## The crops at a glance

| Crop | Setting | Difficulty (1-5) | Capital | Risk | First return |
|---|---|---|---|---|---|
{rows}

"""


def main() -> None:
    rows, parts = [], []
    for c in cp.CROPS:
        f = c["first_timer"]
        rows.append(f"| {c['name']} | {c['setting'].replace('_', ' ')} | {f['difficulty']} | {f['capital']} | {f['risk']} | {f['time_to_first_fruit']} |")
        md = cp.render_markdown(cp.build_report(c["id"]))
        md = md.replace("\n## About chemicals\n\n" + cp.CHEMICAL_POLICY + "\n", "").replace("\n## ", "\n### ")
        parts.append(md.replace("# ", "## ", 1))
    out = INTRO.format(today=date.today().isoformat(), policy=cp.CHEMICAL_POLICY, rows="\n".join(rows)) + "\n\n---\n\n".join(parts)
    dest = ROOT / "docs" / "high-value-crops-manual.md"
    dest.write_text(out, encoding="utf-8")
    print(f"wrote {dest.relative_to(ROOT)}: {len(out.split())} words, {len(cp.CROPS)} crops")


if __name__ == "__main__":
    main()
