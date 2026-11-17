import numpy as np

from geom import identity_of, solve
from imm import filter_track
from reid import Scorer
from sim import STATIONS, look


def test_noiseless_radar_returns_the_target_point():
    station = STATIONS[0]["xyz"]
    point = np.array([820.0, 640.0, 90.0])
    slant, azimuth, elevation = look(station, point)
    estimate = solve([{
        "type": "RADAR", "station": station, "range_m": slant,
        "azimuth": azimuth, "elevation": elevation, "xyz": None, "cooperative_id": None,
    }])
    assert np.linalg.norm(estimate - point) < 1.0


def test_identity_follows_the_reported_remote_id_only():
    assert identity_of([{"cooperative_id": None}, {"cooperative_id": "TASK-01"}])[0] == "合作"
    assert identity_of([{"cooperative_id": None}])[0] == "身份待核验"
    assert identity_of([{"cooperative_id": "A"}, {"cooperative_id": "B"}])[0] == "身份待核验"


def test_match_scorer_loss_falls_on_a_separable_set():
    rng = np.random.default_rng(3)
    positive = rng.normal(0, 0.2, (80, 12))
    negative = rng.normal(2.5, 0.2, (80, 12))
    features = np.vstack([positive, negative])
    labels = np.array([1.0] * 80 + [0.0] * 80)
    scorer = Scorer(1)
    history = scorer.fit(features, labels, epochs=12, lr=0.05, seed=1)
    assert history[-1] < history[0] * 0.5


def test_imm_stays_near_a_straight_noisy_track():
    truth = np.array([[100.0 + 12.0 * t, 400.0, 80.0] for t in range(20)])
    rng = np.random.default_rng(4)
    measured = [point + rng.normal(0, 8, 3) for point in truth]
    filtered = filter_track(list(range(20)), measured, measurement_std=8)
    error = np.linalg.norm(np.asarray(filtered) - truth, axis=1)
    assert error.mean() < 12
