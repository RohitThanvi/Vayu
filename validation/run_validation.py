#!/usr/bin/env python3
"""
run_validation.py - run Vayu's analysis endpoints over ground-truth cases and compute validation metrics.

  python run_validation.py --cases cases.json --base-url https://YOUR-BACKEND.onrender.com --dry-run
  python run_validation.py --cases cases.json --base-url https://YOUR-BACKEND.onrender.com            # test split only
  python run_validation.py --cases cases.json --base-url ... --split tune                             # tuning zones
  python run_validation.py --cases cases.json --base-url ... --only crop_ranking,confusion

Re-running resumes from the on-disk response cache (pass --no-cache to force fresh calls).
Outputs (validation_runs/<timestamp>/): results.csv, metrics.json, report.md, confusion_*.csv, manifest.json
"""
import argparse, csv, hashlib, json, platform, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np, pandas as pd

from client import VayuClient, ReplayClient
from validators import VALIDATORS


def git_hash():
    """Short hash, with '-dirty' when the working tree has uncommitted changes."""
    try:
        return subprocess.check_output(["git", "describe", "--always", "--dirty", "--abbrev=7"], stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


def md_table(d):
    lines = ["| metric | value |", "|---|---|"]
    for k, v in d.items():
        if k.startswith("_"): continue
        lines.append(f"| {k} | {json.dumps(v) if isinstance(v, (dict, list)) else v} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--base-url", default="", help="live backend (not needed with --replay)")
    ap.add_argument("--replay", default="", metavar="PROFILES_DIR",
                    help="re-score SAVED profiles with this checkout's engine instead of calling the backend (offline, instant; tests scoring logic only)")
    ap.add_argument("--out", default="validation_runs")
    ap.add_argument("--split", default="test", choices=["test", "tune", "all"])
    ap.add_argument("--only", default="", help="comma-separated validator types")
    ap.add_argument("--delay", type=float, default=5.0, help="seconds between API calls (RS endpoint is 15/min)")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--model-version", default="", help="git commit of the deployed backend (defaults to local git hash)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="run the first selected case, print raw outputs, stop")
    a = ap.parse_args()

    cases_path = Path(a.cases).resolve(); base_dir = cases_path.parent
    raw = json.loads(cases_path.read_text(encoding="utf-8"))
    meta, cases = (raw.get("meta", {}), raw["cases"]) if isinstance(raw, dict) else ({}, raw)
    if a.split != "all": cases = [c for c in cases if c.get("split", "test") == a.split]
    if a.only: cases = [c for c in cases if c["type"] in a.only.split(",")]
    if not cases:
        sys.exit(f"No cases selected (split={a.split}). Cases need a 'split' of 'tune' or 'test'.")
    if a.dry_run: cases = cases[:1]

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(a.out) / run_id; run_dir.mkdir(parents=True, exist_ok=True)
    version = a.model_version or git_hash()
    profiles_dir = Path(a.out) / "_profiles"
    if a.replay:
        client = ReplayClient(a.replay)
        version = f"replay@{git_hash()}"
        a.no_cache = True
        print(f"REPLAY mode: re-scoring saved profiles from {a.replay} with the local engine ({version}). No network. Valid only for the scoring logic.")
    else:
        if not a.base_url:
            sys.exit("--base-url is required unless --replay is used")
        # Responses are cached PER MODEL VERSION so a code change can never silently reuse old answers.
        # A '-dirty' tree has no stable identity, so caching is switched off for it.
        if version.endswith("-dirty"):
            print(f"model version '{version}' has uncommitted changes -> response cache DISABLED for this run "
                  "(commit first, or pass --model-version, to make runs resumable)")
            a.no_cache = True
        cache_dir = Path(a.out) / "_cache" / re.sub(r"[^A-Za-z0-9_.-]+", "_", version)
        client = VayuClient(a.base_url, cache_dir, timeout=a.timeout, delay=a.delay, profiles_dir=profiles_dir)

    rows = []
    for i, c in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {c['case_id']}  ({c['type']}, {c['endpoint']}, split={c.get('split','test')})", flush=True)
        row = VALIDATORS[c["type"]].run_case(c, client, base_dir, use_cache=not a.no_cache)
        if row["status"] != "ok": print("   ->", row["status"])
        rows.append(row)
        if a.dry_run:
            print(json.dumps(row, indent=2, default=str)); return

    # ---- results + metrics
    pd.DataFrame(rows).to_csv(run_dir / "results.csv", index=False)
    metrics = {}
    for t in sorted({r["type"] for r in rows}):
        sub = [r for r in rows if r["type"] == t]
        ctx = {"label_order": (meta.get("label_order") or {}).get(t)}
        metrics[t] = VALIDATORS[t].summarize(sub, ctx)
        metrics[t]["_coverage"] = {"cases": len(sub), "ok": sum(r["status"] == "ok" for r in sub),
                                   "failed": [f"{r['case_id']}: {r['status']}" for r in sub if r["status"] != "ok"]}

    mats = {}
    def strip(o, path=""):
        if isinstance(o, dict):
            for k in list(o):
                if k == "_matrix": mats[path.strip("_") or "matrix"] = o.pop(k)
                else: strip(o[k], f"{path}_{k}")
    strip(metrics)
    for name, m in mats.items():
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        pd.DataFrame(m["rows_true_cols_pred"], index=[f"true:{l}" for l in m["labels"]],
                     columns=[f"pred:{l}" for l in m["labels"]]).to_csv(run_dir / f"confusion_{name}.csv")
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")

    # what code did the BACKEND actually run? (the local git hash only says which harness/checkout was used)
    ev_counts = pd.Series([x.get("engine_version") for x in rows if x.get("engine_version")]).value_counts().to_dict()
    if len(ev_counts) > 1:
        print(f"WARNING: responses came from {len(ev_counts)} different backend versions {ev_counts} - a deploy happened mid-run; results are mixed.")
    if ev_counts and set(ev_counts) <= {"pre-versioning", "unknown"}:
        print("NOTE: backend does not report a version (older deploy, or RENDER_GIT_COMMIT unset); record it by hand.")
    manifest = {"run_id": run_id, "backend_engine_version": ev_counts, "utc": datetime.now(timezone.utc).isoformat(), "base_url": a.base_url or "(replay)", "replay_profiles": a.replay or None,
                "split": a.split, "model_version": version, "n_cases": len(cases),
                "cases_file_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
                "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # ---- report
    rep = [f"# Vayu validation report - {run_id}", "",
           f"- harness checkout: `{manifest['model_version']}`  |  backend engine: `{manifest['backend_engine_version']}`  |  split: **{a.split}**  |  cases file sha256: `{manifest['cases_file_sha256'][:12]}`",
           "- Confidence intervals are 95% percentile bootstraps resampling ZONES (not rows).",
           "- Metrics are only valid for the conditions covered by the cases listed in results.csv; nothing here "
           "generalises beyond those agro-climatic zones / seasons.", ""]
    if a.split != "test":
        rep.append("> **WARNING:** this run used non-test cases. Do not quote these numbers as held-out accuracy.\n")
    for t, m in metrics.items():
        cov = m.pop("_coverage")
        rep += [f"## {t}", f"coverage: {cov['ok']}/{cov['cases']} cases succeeded"]
        rep += [f"- FAILED {f}" for f in cov["failed"]]
        flat = {}
        for k, v in m.items():
            if isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()) and t in ("scalar", "confusion"):
                rep += [f"\n### {k}", md_table(v)]
            else:
                flat[k] = v
        if flat: rep += ["", md_table(flat)]
        rep.append("")
    (run_dir / "report.md").write_text("\n".join(rep), encoding="utf-8")

    # ---- optional scatter plots for scalar groups
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        df = pd.DataFrame([r for r in rows if r["type"] == "scalar" and r["status"] == "ok"])
        for (ep, path), g in (df.groupby(["endpoint", "output_path"]) if len(df) else []):
            fig, ax = plt.subplots(figsize=(4.5, 4.5)); lo, hi = min(g.obs.min(), g.pred.min()), max(g.obs.max(), g.pred.max())
            ax.plot([lo, hi], [lo, hi], "k--", lw=1); ax.scatter(g.obs, g.pred)
            ax.set_xlabel(f"observed ({g.unit.iloc[0]})"); ax.set_ylabel("Vayu"); ax.set_title(f"{ep}\n{path}", fontsize=9)
            fig.tight_layout(); fig.savefig(run_dir / f"scatter_{ep.replace(':','_')}_{path.replace('.','_')}.png", dpi=130); plt.close(fig)
    except ImportError:
        pass

    print(f"\nDone. {run_dir}\\report.md" if sys.platform == "win32" else f"\nDone. {run_dir}/report.md")


if __name__ == "__main__":
    main()
