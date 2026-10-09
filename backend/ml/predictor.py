"""Machine learning inference engine for classroom occupancy probability estimation.

Loads serialized Random Forest model artifacts and performs high-speed vectorized
probabilistic inference with pure NumPy fallback support.
"""
from typing import Any, Dict, List, Sequence, Union
import importlib
import json
import logging
import os
import threading

import joblib
import joblib.numpy_pickle
import numpy as np

from config import METRICS_PATH, MODEL_PATH

logger = logging.getLogger("smart_classroom.ml")

_model: Any = None
_lock = threading.Lock()


class PureNumpyTree:
    """Zero-dependency NumPy binary decision tree evaluator."""

    def __init__(self, nodes: np.ndarray, values: np.ndarray) -> None:
        self.left = nodes["left_child"]
        self.right = nodes["right_child"]
        self.feature = nodes["feature"]
        self.thresh = nodes["threshold"]
        val = values[:, 0, :]
        self.probs = val / val.sum(axis=1, keepdims=True)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Evaluate tree decision paths and output class probabilities."""
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
    """Ensemble aggregator for PureNumpyTree estimators."""

    def __init__(self, trees: List[PureNumpyTree]) -> None:
        self.trees = trees
        self.n_jobs = 1

    def predict_proba(self, X: Union[Sequence[Sequence[float]], np.ndarray]) -> np.ndarray:
        """Compute mean prediction probabilities across all decision trees."""
        X_arr = np.asarray(X, dtype=float)
        if len(X_arr) == 0:
            return np.empty((0, 2))
        all_p = [t.predict_proba(X_arr) for t in self.trees]
        return np.mean(all_p, axis=0)


class _Dummy:
    """Placeholder stub for safe unpickling without external C-extensions."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, dict):
            self.__dict__.update(state)
        elif isinstance(state, tuple):
            self.__dict__["state_tuple"] = state


def _safe_find_class(self: Any, module: str, name: str) -> Any:
    if module.startswith("sklearn") or module.startswith("scipy"):
        return type(name, (_Dummy,), {})
    return getattr(importlib.import_module(module), name)


def _load_numpy_forest(path: str) -> PureNumpyForest:
    saved_fc = joblib.numpy_pickle.NumpyUnpickler.find_class
    joblib.numpy_pickle.NumpyUnpickler.find_class = _safe_find_class
    try:
        raw = joblib.load(path)
        trees = [PureNumpyTree(e.tree_.nodes, e.tree_.values) for e in raw.estimators_]
        return PureNumpyForest(trees)
    finally:
        joblib.numpy_pickle.NumpyUnpickler.find_class = saved_fc


def get_model() -> Any:
    """Retrieve or initialize the singleton Random Forest classifier model."""
    global _model
    with _lock:
        if _model is None:
            if not os.path.exists(MODEL_PATH):
                logger.info("No trained model found at %s - initiating training...", MODEL_PATH)
                from ml.train_model import train
                train(verbose=False)
            try:
                _model = joblib.load(MODEL_PATH)
                _model.n_jobs = 1
            except Exception as exc:
                logger.warning("Falling back to pure numpy forest due to: %s", exc)
                _model = _load_numpy_forest(MODEL_PATH)
    return _model


def predict(rows: Sequence[Sequence[float]]) -> List[float]:
    """Calculate P(occupied) class probability for a batch of 12D feature vectors."""
    if not rows:
        return []
    return get_model().predict_proba(np.array(rows, float))[:, 1].tolist()


def metrics() -> Dict[str, Any]:
    """Load model evaluation metrics (F1 score, precision, recall, confusion matrix)."""
    if os.path.exists(METRICS_PATH):
        try:
            with open(METRICS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to read metrics file: %s", exc)
    return {}
