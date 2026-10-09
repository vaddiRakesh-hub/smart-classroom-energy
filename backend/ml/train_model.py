"""Train the occupancy-prediction Random Forest on simulated campus history.

Run:  python -m ml.train_model        (from the backend/ folder)

Label  : is the room occupied in the NEXT 5-minute interval?
Inputs : timetable + PIR history + light/temperature/humidity (see features.py)
"""
import json
import os
import random
import sys
from collections import deque
from datetime import date, timedelta

import joblib
import numpy as np
try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                                 precision_score, recall_score, roc_auc_score)
except ImportError as err:
    RandomForestClassifier = None
    accuracy_score = confusion_matrix = f1_score = precision_score = recall_score = roc_auc_score = None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import METRICS_PATH, MODEL_PATH, STEP_MIN        # noqa: E402
from ml.features import FEATURES, build_vector, motion_features  # noqa: E402
from simulate import RoomDay, random_state                    # noqa: E402
from timetable import ROOMS, build_timetable, slots_for       # noqa: E402


def generate_dataset(days=100, start=date(2026, 1, 5), seed=7):
    rng = random.Random(seed)
    timetable = build_timetable()
    X, y, day_idx, meta = [], [], [], []
    for d in range(days):
        day = start + timedelta(days=d)
        for room in ROOMS:
            slots = slots_for(timetable, room["id"], day.weekday(), room["capacity"]) if day.weekday() < 5 else []
            world = RoomDay(room, slots, day, seed_extra=seed)
            history = deque(maxlen=20)           # (abs_minute, pir) newest first
            state = random_state(rng)
            for minute in range(6 * 60, 22 * 60, STEP_MIN):
                if minute % 60 == 0:
                    state = random_state(rng)
                _, pir, temp, hum, lux = world.sense(minute, state)
                abs_min = day.toordinal() * 1440 + minute
                ratio, since = motion_features(list(history), abs_min, pir)
                X.append(build_vector(minute, day.weekday(), slots, pir, temp, hum, lux, ratio, since))
                y.append(1 if world.people(minute + STEP_MIN)[0] > 0 else 0)
                day_idx.append(d)
                meta.append(pir)
                history.appendleft((abs_min, pir))
    return np.array(X, float), np.array(y), np.array(day_idx), X


def evaluate(y_true, y_pred, y_prob=None):
    out = dict(accuracy=accuracy_score(y_true, y_pred), precision=precision_score(y_true, y_pred),
               recall=recall_score(y_true, y_pred), f1=f1_score(y_true, y_pred))
    if y_prob is not None:
        out["roc_auc"] = roc_auc_score(y_true, y_prob)
    return {k: round(float(v), 4) for k, v in out.items()}


def train(days=100, verbose=True):
    if RandomForestClassifier is None:
        raise RuntimeError("scikit-learn is required to train the model, but could not be loaded on this system.")
    X, y, day_idx, _ = generate_dataset(days)
    split = int(days * 0.8)                       # chronological split: last 20 % of days = test
    tr, te = day_idx < split, day_idx >= split
    model = RandomForestClassifier(n_estimators=100, max_depth=12, min_samples_leaf=5,
                                   class_weight="balanced", n_jobs=-1, random_state=42)
    model.fit(X[tr], y[tr])
    prob = model.predict_proba(X[te])[:, 1]
    pred = (prob >= 0.5).astype(int)

    ix = {n: i for i, n in enumerate(FEATURES)}
    pir_only = (X[te][:, ix["motion_ratio_15m"]] > 0).astype(int)          # classic motion switch
    timetable_only = X[te][:, ix["scheduled_now"]].astype(int)             # schedule-based switch
    metrics = dict(
        samples_train=int(tr.sum()), samples_test=int(te.sum()), occupied_share=round(float(y.mean()), 3),
        random_forest=evaluate(y[te], pred, prob),
        baseline_motion_only=evaluate(y[te], pir_only),
        baseline_timetable_only=evaluate(y[te], timetable_only),
        confusion_matrix=confusion_matrix(y[te], pred).tolist(),
        feature_importance={n: round(float(v), 4) for n, v in
                            sorted(zip(FEATURES, model.feature_importances_), key=lambda t: -t[1])},
        features=FEATURES,
    )
    model.n_jobs = 1
    joblib.dump(model, MODEL_PATH)
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)
    if verbose:
        print(json.dumps(metrics, indent=2))
    return metrics


if __name__ == "__main__":
    train()
