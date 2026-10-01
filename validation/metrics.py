"""
metrics.py - validation metrics, numpy/pandas only (no sklearn/scipy needed at runtime).
Cross-checked against sklearn/scipy in selftest.py.
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- bootstrap
def cluster_bootstrap(stat_fn, clusters, n_boot=2000, seed=42, min_clusters=5):
    """95% percentile CI, resampling whole CLUSTERS (zones/cases), not individual rows,
    because rows from one zone are not independent. Returns None if too few clusters."""
    clusters = np.asarray(clusters)
    uniq = np.unique(clusters)
    if len(uniq) < min_clusters:
        return None
    members = [np.where(clusters == c)[0] for c in uniq]
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        idx = np.concatenate([members[i] for i in pick])
        try:
            v = stat_fn(idx)
        except Exception:
            v = None
        if v is not None and np.isfinite(v):
            vals.append(v)
    if len(vals) < n_boot * 0.5:
        return None
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return [float(lo), float(hi)]


# --------------------------------------------------------------- regression
def regression_metrics(y, p, baseline=None):
    y = np.asarray(y, float); p = np.asarray(p, float); n = len(y)
    err = p - y
    out = {"n": n, "mae": float(np.mean(np.abs(err))),
           "rmse": float(np.sqrt(np.mean(err ** 2))), "bias": float(np.mean(err))}
    sst = float(np.sum((y - y.mean()) ** 2))
    out["r2"] = float(1 - np.sum(err ** 2) / sst) if n > 1 and sst > 0 else None   # can be negative
    if n > 2 and y.std() > 0 and p.std() > 0:
        out["pearson_r"] = float(np.corrcoef(y, p)[0, 1])
        out["spearman_rho"] = float(np.corrcoef(pd.Series(y).rank().values, pd.Series(p).rank().values)[0, 1])
        slope, intercept = np.polyfit(y, p, 1)                   # pred = slope*obs + intercept
        out["slope"], out["intercept"] = float(slope), float(intercept)
        out["lin_ccc"] = float(2 * np.cov(y, p, bias=True)[0, 1] / (y.var() + p.var() + (y.mean() - p.mean()) ** 2))
    if baseline is not None:
        b = np.asarray(baseline, float)
        mse_b = float(np.mean((b - y) ** 2))
        out["baseline_rmse"] = float(np.sqrt(mse_b))
        out["mse_skill_vs_baseline"] = float(1 - np.mean(err ** 2) / mse_b) if mse_b > 0 else None
    return out


# ----------------------------------------------------------- classification
def confusion(y_true, y_pred, labels):
    ix = {l: i for i, l in enumerate(labels)}
    M = np.zeros((len(labels), len(labels)), dtype=int)
    for t, p in zip(y_true, y_pred):
        M[ix[t], ix[p]] += 1
    return M          # rows = reference/true, cols = predicted


def kappa_from_matrix(M, quadratic=False):
    M = np.asarray(M, float); n = M.sum(); k = M.shape[0]
    if n == 0:
        return None
    E = np.outer(M.sum(1), M.sum(0)) / n
    i, j = np.indices((k, k))
    W = ((i - j) ** 2 / max((k - 1) ** 2, 1)) if quadratic else (i != j).astype(float)
    den = (W * E).sum()
    return float(1 - (W * M).sum() / den) if den > 0 else None


def classification_from_matrix(M, labels, ordinal=False):
    M = np.asarray(M, float); n = M.sum()
    row, col, diag = M.sum(1), M.sum(0), np.diag(M)
    pa = np.divide(diag, row, out=np.full(len(labels), np.nan), where=row > 0)    # recall / 1-omission
    ua = np.divide(diag, col, out=np.full(len(labels), np.nan), where=col > 0)    # precision / 1-commission
    f1 = np.divide(2 * pa * ua, pa + ua, out=np.full(len(labels), np.nan), where=(pa + ua) > 0)
    out = {"n": int(n), "overall_accuracy": float(diag.sum() / n) if n else None,
           "kappa": kappa_from_matrix(M),
           "balanced_accuracy": float(np.nanmean(pa)) if np.isfinite(pa).any() else None,
           "macro_f1": float(np.nanmean(f1)) if np.isfinite(f1).any() else None,
           "producers_accuracy": {l: (None if np.isnan(v) else round(float(v), 4)) for l, v in zip(labels, pa)},
           "users_accuracy": {l: (None if np.isnan(v) else round(float(v), 4)) for l, v in zip(labels, ua)},
           "f1": {l: (None if np.isnan(v) else round(float(v), 4)) for l, v in zip(labels, f1)},
           "support": {l: int(v) for l, v in zip(labels, row)}}
    if ordinal and len(labels) > 2:
        out["kappa_quadratic_weighted"] = kappa_from_matrix(M, quadratic=True)
        i, j = np.indices(M.shape)
        out["within_one_class_accuracy"] = float(M[np.abs(i - j) <= 1].sum() / n) if n else None
    return out


def classification_metrics(y_true, y_pred, labels=None, ordinal=False):
    if labels is None:
        labels = sorted(set(y_true) | set(y_pred))
    M = confusion(y_true, y_pred, labels)
    out = classification_from_matrix(M, labels, ordinal)
    return out, M, labels


# ------------------------------------------------------------------- binary
def roc_auc(y, s):
    y = np.asarray(y, int); s = np.asarray(s, float)
    npos, nneg = int(y.sum()), int((1 - y).sum())
    if npos == 0 or nneg == 0:
        return None
    r = pd.Series(s).rank().values                     # average ranks handle ties
    return float((r[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def binary_metrics(y, s, threshold):
    y = np.asarray(y, int); s = np.asarray(s, float); pred = (s >= threshold).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum()); fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum()); tn = int(((pred == 0) & (y == 0)).sum())
    d = lambda a, b: float(a / b) if b else None
    prec, rec = d(tp, tp + fp), d(tp, tp + fn)
    return {"n": len(y), "n_event": int(y.sum()), "prevalence": float(y.mean()), "threshold": threshold,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": prec, "recall_POD": rec, "specificity": d(tn, tn + fp),
            "false_alarm_rate_FPR": d(fp, fp + tn),
            "f1": (2 * prec * rec / (prec + rec)) if prec and rec else None,
            "accuracy": d(tp + tn, len(y)), "roc_auc": roc_auc(y, s)}


# ------------------------------------------------------------------ ranking
def ranking_row(pred_ranked, truth_ranked, k=3):
    pk, tk = list(pred_ranked[:k]), list(truth_ranked[:k])
    if not tk or not pk:
        return None
    return {"model_top1_in_truth_topk": float(pk[0] in tk),
            "truth_top1_in_model_topk": float(truth_ranked[0] in pk),
            "overlap_frac": len(set(pk) & set(tk)) / min(k, len(tk), len(pk))}
