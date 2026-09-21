"""
accuracy_stats.py — confusion matrix, overall accuracy, per-class
producer's/user's accuracy, and Cohen's kappa, computed from
(predicted_label, true_label) pairs. The standard accuracy-assessment
methodology in the remote sensing literature (Congalton 1991, "A review
of assessing the accuracy of classifications of remotely sensed data",
Remote Sensing of Environment 37(1):35-46 — the paper that established
this exact table format as the field's convention; Cohen 1960 for kappa
itself).

This is deliberately GEE-independent, pure Python — the sampling step
(pulling a classified raster's value at each reference point) lives in
gee_remote_sensing.py, next to the classification tools it validates;
this module only does the statistics once predicted/true label pairs
exist, so the math is unit-testable without any Earth Engine dependency
or network call.

Producer's accuracy and user's accuracy are NOT the same number and are
reported separately, not averaged into one figure — collapsing them would
hide exactly the asymmetry a scientist needs to see (a class the
classifier over-predicts has high producer's/low user's accuracy; a class
it misses has the reverse), same "don't collapse away the caveat"
principle used throughout this project (training vs. test accuracy for
the RF classifier, dNBR vs. RBR for burn severity).
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

MIN_REFERENCE_POINTS = 4


def compute_confusion_matrix(pairs: List[Tuple[str, str]]) -> Dict[str, Any]:
    """
    pairs: a list of (predicted_label, true_label) tuples — one per
    reference point, already matched (the caller is responsible for
    sampling the classified raster and pairing each result with the
    reference point's known true label; a point with no valid predicted
    value, e.g. it fell on a masked/no-data pixel, should be excluded
    before calling this, not passed in with a None predicted label).
    """
    n = len(pairs)
    if n < MIN_REFERENCE_POINTS:
        return {"status": "insufficient_data",
                "note": f"Need at least {MIN_REFERENCE_POINTS} reference points with valid predictions, got {n}."}

    classes = sorted({label for pair in pairs for label in pair})

    matrix: Dict[str, Dict[str, int]] = {t: {p: 0 for p in classes} for t in classes}
    for predicted, true in pairs:
        matrix[true][predicted] += 1

    row_totals = {t: sum(matrix[t].values()) for t in classes}          # actual count per true class
    col_totals = {p: sum(matrix[t][p] for t in classes) for p in classes}  # predicted count per class

    correct = sum(matrix[c][c] for c in classes)
    overall_accuracy = correct / n

    producers = {}  # recall: of everything ACTUALLY this class, what fraction did the classifier catch
    users = {}      # precision: of everything PREDICTED this class, what fraction was actually right
    for c in classes:
        producers[c] = round(matrix[c][c] / row_totals[c], 4) if row_totals[c] > 0 else None
        users[c] = round(matrix[c][c] / col_totals[c], 4) if col_totals[c] > 0 else None

    # Cohen's kappa (1960): agreement corrected for the agreement expected
    # by chance alone, given the observed marginal totals. kappa=0 means
    # no better than chance; 1 means perfect agreement.
    p_e = sum((row_totals[c] / n) * (col_totals[c] / n) for c in classes)
    kappa = (overall_accuracy - p_e) / (1 - p_e) if p_e < 1 else None

    return {
        "status": "ok",
        "n": n,
        "classes": classes,
        "confusion_matrix": matrix,          # matrix[true_class][predicted_class] = count
        "row_totals": row_totals,
        "col_totals": col_totals,
        "overall_accuracy": round(overall_accuracy, 4),
        "producers_accuracy": producers,      # per class: recall
        "users_accuracy": users,              # per class: precision
        "kappa": round(kappa, 4) if kappa is not None else None,
        "method": (
            "Standard remote-sensing accuracy assessment (Congalton 1991, Remote Sensing of Environment "
            "37(1):35-46; kappa: Cohen 1960). Overall accuracy = correct / total. Producer's accuracy (per "
            "class) = correctly classified / actually that class in the reference set (omission error = "
            "1 - producer's). User's accuracy (per class) = correctly classified / predicted that class "
            "(commission error = 1 - user's). Kappa corrects agreement for chance, given the reference set's "
            "own class proportions."
        ),
    }
