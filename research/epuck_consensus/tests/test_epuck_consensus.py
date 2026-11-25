"""Unit and integration tests for e-puck Factor Graph + Edge-Separable Consensus ADMM."""

import numpy as np
import pytest

from research.epuck_consensus.benchmark import (
    evaluate_nonlinear_consensus_vs_baselines,
    run_gatekeeper_benchmark,
)
from research.epuck_consensus.linear_consensus import (
    EdgeSeparableADMM,
    LinearFactor,
    marginalize_linear,
    schur_marginal_info,
    solve_linear,
)
from research.epuck_consensus.message_selection import (
    build_confounding_beacon,
    score_candidate_with_beacon,
)
from research.epuck_consensus.nonlinear_admm import MultiRobotSE2ADMM, RobotNodeGraph
from research.epuck_consensus.se2_math import (
    AnchorRangeFactor,
    FixedSE2Prior,
    InterRobotRangeFactor,
    preintegrate_wheel_imu,
    se2_retract,
)


def _finite_diff_se2(fun, pose, eps=1e-6):
    r0 = fun(pose)
    jac = np.zeros((len(r0), 3), dtype=float)
    for k in range(3):
        step = np.zeros(3)
        step[k] = eps
        rp = fun(se2_retract(pose, step))
        rm = fun(se2_retract(pose, -step))
        jac[:, k] = (rp - rm) / (2.0 * eps)
    return jac


def test_wheel_imu_and_uwb_analytical_jacobians():
    rng = np.random.default_rng(7)
    samples = np.column_stack([
        np.full(20, 0.05),
        0.25 + rng.normal(0.0, 0.01, 20),
        rng.normal(0.0, 0.005, 20),
        0.30 + rng.normal(0.0, 0.01, 20),
    ])
    odom = preintegrate_wheel_imu(samples, "x0", "x1", "bg", nom_gyro_bias=0.01, source_id="odom:test")
    pa = np.array([1.2, -0.7, 0.65])
    pb = np.array([1.45, -0.52, 0.98])
    bg = 0.025

    _, (ja, jb, jbg) = odom.evaluate(pa, pb, bg)
    num_ja = _finite_diff_se2(lambda x: odom.evaluate(x, pb, bg)[0], pa)
    num_jb = _finite_diff_se2(lambda x: odom.evaluate(pa, x, bg)[0], pb)
    num_jbg = (odom.evaluate(pa, pb, bg + 1e-6)[0] - odom.evaluate(pa, pb, bg - 1e-6)[0])[:, None] / 2e-6

    assert np.allclose(ja, num_ja, atol=1e-6)
    assert np.allclose(jb, num_jb, atol=1e-6)
    assert np.allclose(jbg, num_jbg, atol=1e-5)

    # Check AnchorRangeFactor & InterRobotRangeFactor analytical Jacobians
    anc = AnchorRangeFactor(
        pose_key="x0",
        anchor_pos=np.array([0.0, 2.0]),
        measurement=2.6,
        sigma=0.05,
        lever_arm=np.array([0.04, -0.02]),
        bias_key="b0",
        bias_loading=1.0,
        sources=frozenset({"anc:test"}),
    )
    _, _, jp, jbias = anc.evaluate(pa, 0.3)
    assert np.allclose(jp, _finite_diff_se2(lambda x: anc.evaluate(x, 0.3)[0], pa), atol=1e-6)
    num_jbias = (anc.evaluate(pa, 0.3 + 1e-6)[0] - anc.evaluate(pa, 0.3 - 1e-6)[0])[:, None] / 2e-6
    assert np.allclose(jbias, num_jbias, atol=1e-6)

    p2p = InterRobotRangeFactor(
        pose_i_key="x0",
        pose_j_key="x1",
        measurement=0.4,
        sigma=0.04,
        lever_i=np.array([0.03, 0.01]),
        lever_j=np.array([0.03, -0.01]),
        bias_key="b_p2p",
        sources=frozenset({"p2p:test"}),
    )
    _, _, ji, jj, jbp = p2p.evaluate(pa, pb, -0.1)
    assert np.allclose(ji, _finite_diff_se2(lambda x: p2p.evaluate(x, pb, -0.1)[0], pa), atol=1e-6)
    assert np.allclose(jj, _finite_diff_se2(lambda x: p2p.evaluate(pa, x, -0.1)[0], pb), atol=1e-6)
    num_jbp = (p2p.evaluate(pa, pb, -0.1 + 1e-6)[0] - p2p.evaluate(pa, pb, -0.1 - 1e-6)[0])[:, None] / 2e-6
    assert np.allclose(jbp, num_jbp, atol=1e-6)


def test_linear_admm_matches_centralized_sync_and_async_and_rejects_ephemeral_marginalization():
    # 3 robots estimating shared formation variables & shared anchor bias b
    f0 = [
        LinearFactor(("x0", "b"), np.array([[10.0, 10.0]]), np.array([15.0]), frozenset({"s0"})),
        LinearFactor(("x0", "x1"), np.array([[20.0, -20.0]]), np.array([-10.0]), frozenset({"s01"})),
    ]
    f1 = [
        LinearFactor(("x1", "b"), np.array([[10.0, -10.0]]), np.array([5.0]), frozenset({"s1"})),
        LinearFactor(("x1", "x2"), np.array([[20.0, -20.0]]), np.array([-20.0]), frozenset({"s12"})),
    ]
    f2 = [
        LinearFactor(("x2", "b"), np.array([[8.0, 4.0]]), np.array([22.0]), frozenset({"s2"})),
    ]
    keys = ("b", "x0", "x1", "x2")
    central = solve_linear(f0 + f1 + f2, keys)

    admm_sync = EdgeSeparableADMM({0: f0, 1: f1, 2: f2}, [(0, 1), (1, 2), (0, 2)], rho=35.0)
    admm_sync.run(max_iters=200, tol=1e-11)
    # Each node holds its own state + shared neighbors; shared bias 'b' agrees across all 3 nodes
    est_sync = np.array([
        admm_sync.node_estimates[0]["b"],
        admm_sync.node_estimates[0]["x0"],
        admm_sync.node_estimates[1]["x1"],
        admm_sync.node_estimates[2]["x2"],
    ])
    assert np.allclose(est_sync, central["solution"], atol=1e-7)
    assert admm_sync.node_estimates[0]["b"] == pytest.approx(admm_sync.node_estimates[2]["b"], abs=1e-7)

    admm_async = EdgeSeparableADMM({0: f0, 1: f1, 2: f2}, [(0, 1), (1, 2), (0, 2)], rho=35.0)
    admm_async.run(max_iters=300, tol=1e-10, rng_async_seed=123)
    est_async = np.array([
        admm_async.node_estimates[1]["b"],
        admm_async.node_estimates[0]["x0"],
        admm_async.node_estimates[1]["x1"],
        admm_async.node_estimates[2]["x2"],
    ])
    assert np.allclose(est_async, central["solution"], atol=1e-6)

    # Ephemeral ADMM priors must be blocked from permanent historical marginalization
    ephem = admm_sync._ephemeral_priors_for_node(0)
    assert len(ephem) > 0
    with pytest.raises(ValueError, match="Ephemeral ADMM factors must NEVER be marginalized"):
        marginalize_linear(f0 + ephem, ("x0",), ("x1", "b"))


def test_unidentifiable_shared_bias_preserves_honest_uncertainty_despite_admm_consensus():
    """Two nodes measuring z_i = x + b + eps_i reach consensus residual ~0 via ADMM,
    yet physical_uncertainty_summary() must NOT treat ADMM penalty rho as physical information.
    """
    n0 = [LinearFactor(("x", "b"), np.array([[25.0, 25.0]]), np.array([12.5]), frozenset({"m0"}))]
    n1 = [LinearFactor(("x", "b"), np.array([[25.0, 25.0]]), np.array([12.8]), frozenset({"m1"}))]
    admm = EdgeSeparableADMM({0: n0, 1: n1}, [(0, 1)], rho=600.0, prior_var_envelope=9.0)
    stats = admm.run(max_iters=120, tol=1e-10)
    assert stats["final_residual"] < 1e-9

    phys = admm.physical_uncertainty_summary()
    assert phys["rank"] == 1
    assert phys["nullity"] == 1
    # Marginal variance of x must remain large (~0.5 * 9.0 = 4.5), NOT 1 / 50.0!
    assert phys["covariance_envelope"][0, 0] > 4.0

    # Confounding beacon must assign zero score to another x + b measurement, and positive to x_only
    bcn = build_confounding_beacon(0, n0, target_keys=("x",), bias_keys=("b",), prior_var_envelope=9.0)
    same_source_cand = LinearFactor(("x", "b"), np.array([[40.0, 40.0]]), np.array([20.0]), frozenset({"c_same"}))
    indep_cand = LinearFactor(("x",), np.array([[5.0]]), np.array([0.2]), frozenset({"c_ind"}))
    assert score_candidate_with_beacon(same_source_cand, bcn, n0) == pytest.approx(0.0, abs=1e-6)
    assert score_candidate_with_beacon(indep_cand, bcn, n0) > 0.05


def test_sliding_window_private_marginalization_and_nonlinear_epuck_admm():
    res = evaluate_nonlinear_consensus_vs_baselines(seed=42)
    # Joint Pose+Bias ADMM must closely match Centralized Joint Batch NLS and beat Bias-Blind ADMM
    assert res["admm_vs_central_gap_m"] < 0.03
    assert res["rmse_joint_admm_m"] < res["rmse_bias_blind_admm_m"] * 0.45
    assert res["rmse_joint_admm_m"] < res["rmse_isolated_m"] * 0.35

    # Verify sliding-window private pose marginalization refuses shared ADMM keys and preserves cost on private keys
    g = RobotNodeGraph(0)
    g.ensure_se2("x0", [0.0, 0.0, 0.0])
    g.ensure_se2("x1", [0.2, 0.0, 0.0])
    g.ensure_scalar("bg", 0.0)
    g.add_prior(
        FixedSE2Prior(
            (("x0", "se2"), ("bg", "scalar")),
            {"x0": np.zeros(3), "bg": 0.0},
            np.eye(4),
            np.zeros(4),
            frozenset({"p0"}),
        )
    )
    samples = np.tile([0.05, 0.2, 0.0, 0.0], (10, 1))
    g.add_odom(preintegrate_wheel_imu(samples, "x0", "x1", "bg", source_id="odom01"))
    with pytest.raises(ValueError, match="Cannot marginalize shared ADMM variable"):
        g.marginalize_private_pose("x0", protected_shared_keys=frozenset({"x0"}))
    info = g.marginalize_private_pose("x0", protected_shared_keys=frozenset({"x1"}))
    assert info["eliminated_rank"] == 3
    assert "x0" not in g.var_kinds and "x1" in g.var_kinds


def test_gatekeeper_benchmark_smoke():
    summary = run_gatekeeper_benchmark(n_trials=20, byte_budget=132)
    # 1. Under sparse_independent, beacon_cert must strongly beat bias_blind_info and random
    assert summary["sparse_independent"]["beacon_cert"]["weak_x_rmse_cm"] < (
        summary["sparse_independent"]["bias_blind_info"]["weak_x_rmse_cm"] * 0.4
    )
    # 2. Under cross_node_synergy, beacon_cert must strongly beat local_schur (which misses cross-node b_0 synergy)
    assert summary["cross_node_synergy"]["beacon_cert"]["weak_x_rmse_cm"] < (
        summary["cross_node_synergy"]["local_schur"]["weak_x_rmse_cm"] * 0.4
    )
    # 3. Under misattributed_bias, beacon_cert must strongly beat un-gated beacon_no_cert and centralized_oracle
    assert summary["misattributed_bias"]["beacon_cert"]["weak_x_rmse_cm"] < (
        summary["misattributed_bias"]["beacon_no_cert"]["weak_x_rmse_cm"] * 0.3
    )
