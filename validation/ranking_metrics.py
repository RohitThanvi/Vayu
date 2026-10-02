"""
ranking_metrics.py - tie-aware ranking metrics, PR-AUC, Brier / reliability. numpy only.
Cross-checked against scikit-learn / scipy in selftest.py where an equivalent exists.

Tie policy: items with equal scores share their positions; every metric is the EXPECTATION under uniformly random
tie-breaking, so a model can neither gain nor lose from an arbitrary (e.g. alphabetical) tie order.
"""
import math
import numpy as np


def tie_groups(scores, tol=1e-9):
    """Indices sorted by descending score, grouped by equal score -> list of (first_pos, last_pos, [idx...]) (1-based)."""
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    groups, pos = [], 1
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and abs(scores[order[j + 1]] - scores[order[i]]) <= tol:
            j += 1
        groups.append((pos, pos + (j - i), order[i:j + 1]))
        pos += j - i + 1
        i = j + 1
    return groups


def prob_in_topk(scores, k):
    """P(item lands in the top-k) under random tie-breaking, for each item."""
    p = [0.0] * len(scores)
    for a, b, idx in tie_groups(scores):
        v = 1.0 if b <= k else 0.0 if a > k else (k - a + 1) / (b - a + 1)
        for i in idx:
            p[i] = v
    return p


def expected_top1_hit(scores, target):
    a, b, idx = tie_groups(scores)[0]
    return (1.0 / len(idx)) if target in idx else 0.0


def expected_reciprocal_rank(scores, target):
    for a, b, idx in tie_groups(scores):
        if target in idx:
            return float(np.mean([1.0 / p for p in range(a, b + 1)]))
    return 0.0


def ndcg_at_k(scores, relevance, k):
    """Tie-aware NDCG@k (McSherry & Najork 2008): tied items share the average gain of the positions they occupy."""
    rel = np.asarray(relevance, float)
    if rel.sum() <= 0:
        return None
    dcg = 0.0
    for a, b, idx in tie_groups(scores):
        avg = float(np.mean(rel[idx]))
        for pos in range(a, min(b, k) + 1):
            dcg += avg / math.log2(pos + 1)
    ideal = sorted(rel, reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return float(dcg / idcg) if idcg > 0 else None


def kendall_tau_b(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x); conc = disc = tx = ty = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = np.sign(x[i] - x[j]), np.sign(y[i] - y[j])
            if dx == 0 and dy == 0: continue
            if dx == 0: tx += 1
            elif dy == 0: ty += 1
            elif dx == dy: conc += 1
            else: disc += 1
    den = math.sqrt((conc + disc + tx) * (conc + disc + ty))
    return float((conc - disc) / den) if den > 0 else None


def spearman(x, y):
    import pandas as pd
    rx, ry = pd.Series(x).rank().values, pd.Series(y).rank().values
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def pr_auc(y, s):
    """Average precision (step-wise PR-AUC), ties handled by thresholding on distinct scores."""
    y = np.asarray(y, int); s = np.asarray(s, float); P = int(y.sum())
    if P == 0:
        return None
    order = np.argsort(-s, kind="stable"); ys, ss = y[order], s[order]
    tp = fp = 0; prev_recall = 0.0; ap = 0.0; i = 0
    while i < len(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1] == ss[i]:
            j += 1
        tp += int(ys[i:j + 1].sum()); fp += int((1 - ys[i:j + 1]).sum())
        recall = tp / P; ap += (recall - prev_recall) * (tp / (tp + fp)); prev_recall = recall; i = j + 1
    return float(ap)


def brier(y, p):
    y = np.asarray(y, float); p = np.asarray(p, float)
    return float(np.mean((p - y) ** 2))


def reliability(y, p, bins=10):
    """Reliability table + expected calibration error. Meaningful only if `p` is a CALIBRATED probability."""
    y = np.asarray(y, float); p = np.asarray(p, float)
    edges = np.linspace(0, 1, bins + 1); rows = []; ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.sum():
            rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()), "mean_pred": round(float(p[m].mean()), 4),
                         "observed_rate": round(float(y[m].mean()), 4)})
            ece += m.sum() / len(p) * abs(y[m].mean() - p[m].mean())
    return rows, float(ece)


def case_ranking_metrics(ids, scores, rel, k=3):
    """All per-case ranking metrics. ids: crop ids; scores: model scores; rel: observed area share per crop."""
    rel = np.asarray(rel, float)
    if rel.sum() <= 0 or len(ids) < 2:
        return None
    top1 = int(np.argmax(rel)); truthk = sorted(range(len(ids)), key=lambda i: (-rel[i], i))[:k]
    truthk = [i for i in truthk if rel[i] > 0]
    pk = prob_in_topk(list(scores), k)
    groups = tie_groups(list(scores))
    return {
        "top1_acc": expected_top1_hit(list(scores), top1),
        "top3_recall": pk[top1],                                           # observed #1 crop inside model top-3
        "overlap_frac": float(sum(pk[i] for i in truthk) / min(k, len(truthk))),
        "ndcg_k": ndcg_at_k(list(scores), rel, k), "mrr": expected_reciprocal_rank(list(scores), top1),
        "kendall_tau_b": kendall_tau_b(scores, rel), "spearman": spearman(scores, rel),
        "tie_at_top": float(len(groups[0][2]) > 1),
        "tie_at_cutoff": float(any(a <= k < b for a, b, _ in groups)),
    }
