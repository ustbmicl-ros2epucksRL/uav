"""Offline training and scoring. Not imported by the ground station or the planning service."""
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from geom import cluster, solve
from imm import filter_track
from reid import Scorer, features, fragment
from sim import episode


def oracle_track(record, target_id):
    series = []
    for frame in record["frames"]:
        hits = [item for item in frame["measurements"] if item["target_id"] == target_id]
        series.append(solve(hits) if hits else None)
    return series


def _fragments(tracks, left_start=14):
    built = []
    for track in tracks:
        left = fragment(track, left_start)
        right = fragment(track, left_start + 12)
        if left is None or right is None:
            return None
        built.append((left, right))
    return built


def collect_pairs(records):
    rows, labels = [], []
    for record in records:
        tracks = [oracle_track(record, target["id"]) for target in record["targets"]]
        for left_start in range(4, 18):
            built = _fragments(tracks, left_start)
            if built is None:
                continue
            for index in range(2):
                rows.append(features(built[index][0], built[index][1]))
                labels.append(1.0)
                rows.append(features(built[index][0], built[1 - index][1]))
                labels.append(0.0)
    return np.asarray(rows, float), np.asarray(labels, float)


def _match_rate(costs):
    left, right = linear_sum_assignment(costs)
    return float(np.mean(left == right))


def association_rates(records, scorer):
    raw, extrapolated, trained, used = [], [], [], 0
    for record in records:
        tracks = [oracle_track(record, target["id"]) for target in record["targets"]]
        built = _fragments(tracks)
        if built is None:
            continue
        used += 1
        gap = built[0][1]["t0"] - built[0][0]["t1"]
        position = np.zeros((2, 2))
        motion = np.zeros((2, 2))
        learned = np.zeros((2, 2))
        for i in range(2):
            for j in range(2):
                earlier, later = built[i][0], built[j][1]
                position[i, j] = np.linalg.norm(later["p0"] - earlier["p1"])
                motion[i, j] = np.linalg.norm(later["p0"] - (earlier["p1"] + earlier["v"] * gap))
                learned[i, j] = -float(scorer.logits(features(earlier, later)[None, :])[0])
        raw.append(_match_rate(position))
        extrapolated.append(_match_rate(motion))
        trained.append(_match_rate(learned))
    return {
        "episodes": used,
        "position_match": float(np.mean(raw)) if raw else None,
        "extrapolation_match": float(np.mean(extrapolated)) if extrapolated else None,
        "trained_match": float(np.mean(trained)) if trained else None,
    }


def localization_errors(records):
    oracle, automatic, matched_truth, truths, false_tracks, merged, frames = [], [], 0, 0, 0, 0, 0
    cooperative_correct, cooperative_seen = 0, 0
    pending_correct, pending_seen = 0, 0
    for record in records:
        for frame_index, frame in enumerate(record["frames"]):
            frames += 1
            for target in record["targets"]:
                hits = [item for item in frame["measurements"] if item["target_id"] == target["id"]]
                estimate = solve(hits) if hits else None
                if estimate is not None:
                    oracle.append(np.linalg.norm(estimate - target["points"][frame_index]))
            estimates = cluster(frame["measurements"])
            taken = set()
            for target in record["targets"]:
                truths += 1
                order = sorted(range(len(estimates)), key=lambda i: np.linalg.norm(estimates[i]["position"] - target["points"][frame_index]))
                choice = next((i for i in order if i not in taken and np.linalg.norm(estimates[i]["position"] - target["points"][frame_index]) < 80), None)
                if choice is None:
                    continue
                taken.add(choice)
                matched_truth += 1
                automatic.append(np.linalg.norm(estimates[choice]["position"] - target["points"][frame_index]))
                label = estimates[choice]["identity"]
                if target["cooperative_id"]:
                    cooperative_seen += 1
                    cooperative_correct += int(label == "合作")
                else:
                    pending_seen += 1
                    pending_correct += int(label == "身份待核验")
            false_tracks += len(estimates) - len(taken)
            merged += sum(1 for item in estimates if {"T1", "T2"} <= {tid for tid in item["target_ids"] if tid})
    def rmse(values):
        values = np.asarray(values, float)
        return None if values.size == 0 else float(np.sqrt(np.mean(values ** 2)))
    return {
        "oracle_rmse_m": rmse(oracle),
        "oracle_median_m": None if len(oracle) == 0 else float(np.median(oracle)),
        "oracle_p90_m": None if len(oracle) == 0 else float(np.percentile(oracle, 90)),
        "found_within_80m_rmse_m": rmse(automatic),
        "truth_found": matched_truth / truths if truths else None,
        "false_tracks_per_frame": false_tracks / frames if frames else None,
        "merged_clusters": merged,
        "cooperative_called_cooperative": cooperative_correct / cooperative_seen if cooperative_seen else None,
        "noncooperative_called_pending": pending_correct / pending_seen if pending_seen else None,
    }


def smoothing_errors(records):
    raw, smoothed = [], []
    for record in records:
        for target in record["targets"]:
            series = oracle_track(record, target["id"])
            times = [frame["time"] for frame in record["frames"]]
            filtered = filter_track(times, series)
            for index, truth in enumerate(target["points"]):
                if series[index] is not None:
                    raw.append(np.linalg.norm(series[index] - truth))
                smoothed.append(np.linalg.norm(filtered[index] - truth))
    raw, smoothed = np.asarray(raw), np.asarray(smoothed)
    return {
        "measurement_rmse_m": float(np.sqrt(np.mean(raw ** 2))),
        "imm_rmse_m": float(np.sqrt(np.mean(smoothed ** 2))),
    }


def make_split(n_train=160, n_val=48, seed=20261007):
    rng = np.random.default_rng(seed)
    def draw(count, start):
        records = []
        for offset in range(count):
            records.append(episode(
                int(rng.integers(0, 1_000_000_000)),
                crossing=offset % 2 == 0,
                interference=False,
            ))
        return records
    return draw(n_train, 0), draw(n_val, n_train)


def run(n_train=160, n_val=48, epochs=30, seed=20261007):
    train_records, val_records = make_split(n_train, n_val, seed)
    features_, labels = collect_pairs(train_records)
    scorer = Scorer(seed)
    history = scorer.fit(features_, labels, epochs=epochs, seed=seed)
    crossing = [item for item in val_records if item["crossing"]]
    ordinary = [item for item in val_records if not item["crossing"]]
    stress = [episode(seed + 1000 + index, crossing=index % 2 == 0, interference=True) for index in range(24)]
    metrics = {
        "samples": int(len(labels)),
        "positive_fraction": float(labels.mean()),
        "loss_first": float(history[0]),
        "loss_last": float(history[-1]),
        "crossing": association_rates(crossing, scorer),
        "ordinary": association_rates(ordinary, scorer),
        "localization": localization_errors(val_records),
        "smoothing": smoothing_errors(val_records),
        "interference": localization_errors(stress),
    }
    return scorer, metrics


def main():
    scorer, metrics = run()
    out = Path(__file__).resolve().parent / "outputs"
    out.mkdir(exist_ok=True)
    np.savez(out / "reid-weights.npz", w1=scorer.w1, b1=scorer.b1, w2=scorer.w2, b2=scorer.b2, mean=scorer.mean, std=scorer.std)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
