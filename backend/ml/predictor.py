"""Loads the trained model and returns occupancy probabilities."""
import importlib
import json
import os
import threading

import joblib
import joblib.numpy_pickle
import numpy as np

from config import METRICS_PATH, MODEL_PATH

_model = None
_lock = threading.Lock()


class PureNumpyTree:
    def __init__(self, nodes, values):
        self.left = nodes["left_child"]
        self.right = nodes["right_child"]
        self.feature = nodes["feature"]
        self.thresh = nodes["threshold"]
        val = values[:, 0, :]
        self.probs = val / val.sum(axis=1, keepdims=True)

    def predict_proba(self, X):
        n = len(X)
        out = np.zeros((n, 2))
        for i in range(n):
            node = 0
            x = X[i]
            while self.left[node] != -1:
                if x[self.feature[node]] <= self.thresh[node]:
                    node = self.left[node]
                else:
                    node = self.right[node]
            out[i] = self.probs[node]
        return out


class PureNumpyForest:
    def __init__(self, trees):
        self.trees = trees
        self.n_jobs = 1

    def predict_proba(self, X):
        X = np.asarray(X, dtype=float)
        if len(X) == 0:
            return np.empty((0, 2))
        all_p = [t.predict_proba(X) for t in self.trees]
        return np.mean(all_p, axis=0)


class _Dummy:
    def __init__(self, *args, **kwargs):
        pass

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update(state)
        elif isinstance(state, tuple):
            self.__dict__["state_tuple"] = state


def _safe_find_class(self, module, name):
    if module.startswith("sklearn") or module.startswith("scipy"):
        return type(name, (_Dummy,), {})
    return getattr(importlib.import_module(module), name)


def _load_numpy_forest(path):
    saved_fc = joblib.numpy_pickle.NumpyUnpickler.find_class
    joblib.numpy_pickle.NumpyUnpickler.find_class = _safe_find_class
    try:
        raw = joblib.load(path)
        trees = [PureNumpyTree(e.tree_.nodes, e.tree_.values) for e in raw.estimators_]
        return PureNumpyForest(trees)
    finally:
        joblib.numpy_pickle.NumpyUnpickler.find_class = saved_fc


def get_model():
    global _model
    with _lock:
        if _model is None:
            if not os.path.exists(MODEL_PATH):
                print("[ml] No trained model found - training one now ...")
                from ml.train_model import train
                train(verbose=False)
            try:
                _model = joblib.load(MODEL_PATH)
                _model.n_jobs = 1
            except Exception as e:
                # Fallback to pure numpy tree inference if C-extensions are blocked (e.g. Windows Smart App Control)
                _model = _load_numpy_forest(MODEL_PATH)
    return _model


def predict(rows):
    """rows: list of feature vectors -> list of P(occupied next interval)."""
    return get_model().predict_proba(np.array(rows, float))[:, 1].tolist()


def metrics():
    if os.path.exists(METRICS_PATH):
        with open(METRICS_PATH) as f:
            return json.load(f)
    return {}

