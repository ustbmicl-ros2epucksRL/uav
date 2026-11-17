"""Trainable scorer for whether two track fragments are the same aircraft.

The features are the constant-velocity extrapolation error and the heading change.
Training learns the match threshold; it does not learn a legal identity.
"""
import numpy as np


def fragment(track, start, length=5):
    window = track[start:start + length]
    if len(window) < length or any(item is None for item in window):
        return None
    first, last = window[0], window[-1]
    velocity = (last - first) / (length - 1)
    return {"t0": start, "t1": start + length - 1, "p0": first, "p1": last, "v": velocity}


def features(earlier, later):
    gap = later["t0"] - earlier["t1"]
    predicted = earlier["p1"] + earlier["v"] * gap
    error = later["p0"] - predicted
    turn = later["v"][:2] - earlier["v"][:2]
    earlier_speed = np.linalg.norm(earlier["v"][:2])
    later_speed = np.linalg.norm(later["v"][:2])
    heading = 0.0
    if earlier_speed > 1 and later_speed > 1:
        heading = float(np.dot(earlier["v"][:2], later["v"][:2]) / (earlier_speed * later_speed))
    return np.array([
        error[0] / 40.0, error[1] / 40.0, error[2] / 20.0,
        gap / 10.0,
        turn[0] / 5.0, turn[1] / 5.0,
        earlier_speed / 15.0, later_speed / 15.0,
        heading,
        np.linalg.norm(later["p0"] - earlier["p1"]) / 80.0,
        (later["p0"][2] - earlier["p1"][2]) / 20.0,
        gap / 8.0,
    ], dtype=float)


class Scorer:
    def __init__(self, seed=0):
        rng = np.random.default_rng(seed)
        self.w1 = rng.normal(0, 1 / np.sqrt(12), (12, 24))
        self.b1 = np.zeros(24)
        self.w2 = rng.normal(0, 1 / np.sqrt(24), 24)
        self.b2 = 0.0
        self.mean = np.zeros(12)
        self.std = np.ones(12)

    def _norm(self, features_):
        return (features_ - self.mean) / self.std

    def logits(self, features_):
        hidden = np.tanh(self._norm(features_) @ self.w1 + self.b1)
        return hidden @ self.w2 + self.b2

    def fit(self, features_, labels, epochs=30, lr=0.04, seed=0):
        features_ = np.asarray(features_, float)
        labels = np.asarray(labels, float)
        self.mean = features_.mean(axis=0)
        self.std = np.maximum(features_.std(axis=0), 1e-3)
        moment_w1 = np.zeros_like(self.w1)
        moment_b1 = np.zeros_like(self.b1)
        moment_w2 = np.zeros_like(self.w2)
        moment_b2 = 0.0
        velocity_w1 = np.zeros_like(self.w1)
        velocity_b1 = np.zeros_like(self.b1)
        velocity_w2 = np.zeros_like(self.w2)
        velocity_b2 = 0.0
        rng = np.random.default_rng(seed)
        history = []
        step = 0
        for epoch in range(epochs):
            order = rng.permutation(len(labels))
            total = 0.0
            seen = 0
            for start in range(0, len(order), 128):
                batch = order[start:start + 128]
                loss, grads = _batch(self, features_[batch], labels[batch])
                step += 1
                moment_w1, velocity_w1, self.w1 = _adam(self.w1, grads[0], moment_w1, velocity_w1, step, lr)
                moment_b1, velocity_b1, self.b1 = _adam(self.b1, grads[1], moment_b1, velocity_b1, step, lr)
                moment_w2, velocity_w2, self.w2 = _adam(self.w2, grads[2], moment_w2, velocity_w2, step, lr)
                moment_b2, velocity_b2, updated = _adam(np.array([self.b2]), np.array([grads[3]]), np.array([moment_b2]), np.array([velocity_b2]), step, lr)
                self.b2 = float(updated[0])
                moment_b2, velocity_b2 = float(moment_b2[0]), float(velocity_b2[0])
                total += loss * len(batch)
                seen += len(batch)
            history.append(total / seen)
        return history


def _adam(value, grad, moment, velocity, step, lr):
    moment = 0.9 * moment + 0.1 * grad
    velocity = 0.999 * velocity + 0.001 * (grad * grad)
    moment_hat = moment / (1 - 0.9 ** step)
    velocity_hat = velocity / (1 - 0.999 ** step)
    return moment, velocity, value - lr * moment_hat / (np.sqrt(velocity_hat) + 1e-8)


def _batch(model, features_, labels):
    normalized = model._norm(features_)
    hidden = np.tanh(normalized @ model.w1 + model.b1)
    logits = hidden @ model.w2 + model.b2
    probability = 1 / (1 + np.exp(-np.clip(logits, -30, 30)))
    residual = (probability - labels) / len(labels)
    hidden_grad = residual[:, None] * model.w2
    activation_grad = hidden_grad * (1 - hidden ** 2)
    loss = float(np.mean(np.maximum(logits, 0) - logits * labels + np.log1p(np.exp(-np.abs(logits)))))
    return loss, (
        normalized.T @ activation_grad,
        activation_grad.sum(axis=0),
        hidden.T @ residual,
        float(residual.sum()),
    )
