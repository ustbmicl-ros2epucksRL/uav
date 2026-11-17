"""Two-model interacting filter on already localized positions.

Both models share a position-velocity state. One expects gentle motion and the
other allows a turn. Angle measurements are solved before this filter, so this
is an IMM Kalman filter, not an angle UKF.
"""
import numpy as np


def filter_track(times, positions, measurement_std=18.0):
    state = np.zeros((2, 6))
    if positions[0] is not None:
        state[:, :3] = positions[0]
    covariance = np.stack([
        np.diag([400.0, 400.0, 400.0, 64.0, 64.0, 25.0]),
        np.diag([400.0, 400.0, 400.0, 400.0, 400.0, 100.0]),
    ])
    probability = np.array([0.7, 0.3])
    transition = np.array([[0.94, 0.06], [0.10, 0.90]])
    process = (2.0, 50.0)
    observation = np.zeros((3, 6))
    observation[:, :3] = np.eye(3)
    noise = np.eye(3) * measurement_std ** 2
    output = []
    for index, position in enumerate(positions):
        if index:
            dt = max(0.2, float(times[index] - times[index - 1]))
            motion = np.eye(6)
            motion[:3, 3:] = np.eye(3) * dt
            mixed_weight = transition * probability[:, None]
            normalizer = mixed_weight.sum(axis=0)
            mixed_weight = mixed_weight / normalizer
            mixed_state = np.zeros_like(state)
            mixed_covariance = np.zeros_like(covariance)
            for model in range(2):
                mixed_state[model] = sum(mixed_weight[source, model] * state[source] for source in range(2))
                for source in range(2):
                    delta = state[source] - mixed_state[model]
                    mixed_covariance[model] += mixed_weight[source, model] * (covariance[source] + np.outer(delta, delta))
            gain = np.vstack([np.eye(3) * dt * dt / 2, np.eye(3) * dt])
            likelihood = np.ones(2)
            for model in range(2):
                state[model] = motion @ mixed_state[model]
                covariance[model] = motion @ mixed_covariance[model] @ motion.T + gain @ gain.T * process[model]
            if position is not None:
                measured = np.asarray(position, float)
                for model in range(2):
                    innovation = measured - observation @ state[model]
                    innovation_cov = observation @ covariance[model] @ observation.T + noise
                    kalman = covariance[model] @ observation.T @ np.linalg.inv(innovation_cov)
                    state[model] = state[model] + kalman @ innovation
                    covariance[model] = (np.eye(6) - kalman @ observation) @ covariance[model]
                    sign, logdet = np.linalg.slogdet(innovation_cov)
                    if sign <= 0:
                        likelihood[model] = 1e-12
                    else:
                        quad = float(innovation @ np.linalg.solve(innovation_cov, innovation))
                        likelihood[model] = float(np.exp(np.clip(-0.5 * quad - 0.5 * logdet, -40, 40)))
            probability = normalizer * likelihood
            probability = np.maximum(probability, 1e-12)
            probability /= probability.sum()
        output.append((probability @ state)[:3].copy())
    return output
