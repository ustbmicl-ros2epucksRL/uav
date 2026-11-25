"""e-puck Sparse-Anchor UWB/IMU Cooperative Localization Simulator & Gatekeeper Benchmarks."""

import json
from pathlib import Path
import numpy as np
from scipy.stats import chi2

from .linear_consensus import (
    EdgeSeparableADMM,
    LinearFactor,
    deduplicate_idempotent_factors,
    schur_marginal_info,
    solve_linear,
)
from .message_selection import select_messages_under_budget
from .nonlinear_admm import MultiRobotSE2ADMM, RobotNodeGraph
from .se2_math import (
    AnchorRangeFactor,
    FixedSE2Prior,
    InterRobotRangeFactor,
    preintegrate_wheel_imu,
    rot2,
    wrap_angle,
)

CHI2_2D_95 = float(chi2.ppf(0.95, df=2))
CHI2_1D_95 = float(chi2.ppf(0.95, df=1))


def simulate_epuck_squad(
    seed=42,
    n_robots=4,
    n_steps=6,
    dt_keyframe=0.8,
    substeps=16,
    shared_anchor_biases=(0.55, -0.40),
):
    """Simulate a 4-robot e-puck team in a 5m x 5m arena with ONLY 2 UWB anchors (with shared bias),
    wheel+2D-IMU preintegration, inter-robot UWB ranging, and one robot briefly sighting a calibrated
    short-range docking marker (breaking the shared-anchor bias gauge for the whole team via ADMM).
    """
    rng = np.random.default_rng(seed)
    anchors = np.array([[0.0, 0.0], [0.0, 4.0]], dtype=float)  # Collinear along x=0 wall -> x-bias confounding!
    lever = np.array([0.03, 0.0], dtype=float)
    dt_sub = dt_keyframe / substeps

    # Initial poses of 4 e-pucks
    base_starts = [
        np.array([1.5, 0.8, 0.25]),
        np.array([2.8, 1.1, 1.35]),
        np.array([1.8, 3.0, -0.45]),
        np.array([3.2, 3.2, -1.75]),
    ]
    gyro_biases_true = rng.normal(0.0, 0.025, size=n_robots)
    truth_poses = {r: [base_starts[r].copy()] for r in range(n_robots)}
    odom_factors = {r: [] for r in range(n_robots)}

    vel_sigma = 0.035
    lat_sigma = 0.012
    gyro_sigma = 0.015
    range_sigma = 0.045
    p2p_sigma = 0.040

    for r in range(n_robots):
        for t in range(n_steps - 1):
            cur = truth_poses[r][-1].copy()
            raw_samples = []
            for s in range(substeps):
                v_cmd = 0.22 + 0.05 * np.sin(0.7 * t + r)
                w_cmd = 0.35 * np.cos(0.6 * t + 0.8 * r)
                cur[:2] += rot2(cur[2]) @ np.array([v_cmd * dt_sub, 0.0])
                cur[2] = float(wrap_angle(cur[2] + w_cmd * dt_sub))
                v_m = v_cmd + rng.normal(0.0, vel_sigma / np.sqrt(dt_sub))
                v_lat_m = rng.normal(0.0, lat_sigma / np.sqrt(dt_sub))
                w_m = w_cmd + gyro_biases_true[r] + rng.normal(0.0, gyro_sigma / np.sqrt(dt_sub))
                raw_samples.append([dt_sub, v_m, v_lat_m, w_m])
            truth_poses[r].append(cur)
            odom_factors[r].append(
                preintegrate_wheel_imu(
                    raw_samples,
                    pose_a_key=f"x_{r}_{t}",
                    pose_b_key=f"x_{r}_{t+1}",
                    gyro_bias_key=f"bg_{r}",
                    nom_gyro_bias=0.0,
                    vel_noise_density=vel_sigma,
                    lat_noise_density=lat_sigma,
                    gyro_noise_density=gyro_sigma,
                    source_id=f"odom:{r}:{t}",
                )
            )

    # Sparse anchor UWB ranges (every robot ranges to the 2 wall anchors, corrupted by shared_anchor_biases)
    anchor_factors = {r: [] for r in range(n_robots)}
    for r in range(n_robots):
        for t in range(n_steps):
            pose = truth_poses[r][t]
            tag = pose[:2] + rot2(pose[2]) @ lever
            for a_idx in range(len(anchors)):
                true_d = float(np.linalg.norm(tag - anchors[a_idx]))
                z = true_d + shared_anchor_biases[a_idx] + rng.normal(0.0, range_sigma)
                anchor_factors[r].append(
                    AnchorRangeFactor(
                        pose_key=f"x_{r}_{t}",
                        anchor_pos=anchors[a_idx],
                        measurement=float(z),
                        sigma=range_sigma,
                        lever_arm=lever,
                        bias_key=f"b_anc_{a_idx}",
                        bias_loading=1.0,
                        sources=frozenset({f"anc:{r}:{t}:{a_idx}"}),
                    )
                )

    # Robot 0 gets 2 short-range bias-free docking beacon observations at t=0, t=3 (calibrating anchor bias!)
    dock_pos = np.array([1.2, 2.0], dtype=float)
    for t_cal in (0, 3):
        pose0 = truth_poses[0][t_cal]
        tag0 = pose0[:2] + rot2(pose0[2]) @ lever
        z_cal = float(np.linalg.norm(tag0 - dock_pos) + rng.normal(0.0, 0.05))
        anchor_factors[0].append(
            AnchorRangeFactor(
                pose_key=f"x_0_{t_cal}",
                anchor_pos=dock_pos,
                measurement=z_cal,
                sigma=0.05,
                lever_arm=lever,
                bias_key=None,
                sources=frozenset({f"dock_cal:0:{t_cal}"}),
            )
        )

    # Inter-robot P2P UWB ranges across ring edges (0-1, 1-2, 2-3, 3-0)
    comm_edges = [(0, 1), (1, 2), (2, 3), (0, 3)]
    p2p_factors = []
    for t in range(n_steps):
        for u, v in comm_edges:
            pu, pv = truth_poses[u][t], truth_poses[v][t]
            qu = pu[:2] + rot2(pu[2]) @ lever
            qv = pv[:2] + rot2(pv[2]) @ lever
            z_uv = float(np.linalg.norm(qu - qv) + rng.normal(0.0, p2p_sigma))
            p2p_factors.append(
                (
                    u,
                    v,
                    InterRobotRangeFactor(
                        pose_i_key=f"x_{u}_{t}",
                        pose_j_key=f"x_{v}_{t}",
                        measurement=z_uv,
                        sigma=p2p_sigma,
                        lever_i=lever,
                        lever_j=lever,
                        bias_key=None,
                        sources=frozenset({f"p2p:{u}:{v}:{t}"}),
                    ),
                )
            )

    # Dead-reckoning initial trajectory (NO truth injected into solver initialization; rough noisy start prior)
    init_poses = {}
    startup_priors = {}
    for r in range(n_robots):
        noisy_p0 = truth_poses[r][0].copy() + np.array([
            rng.normal(0.35, 0.20),
            rng.normal(-0.25, 0.20),
            rng.normal(0.0, 0.12),
        ])
        noisy_p0[2] = float(wrap_angle(noisy_p0[2]))
        init_poses[r] = [noisy_p0]
        for t in range(n_steps - 1):
            init_poses[r].append(odom_factors[r][t].predict(init_poses[r][-1], 0.0))
        # Weak uninformative prior on initial pose (sigma = 1.5m pos, 0.5 rad heading) & gyro bias (0.08 rad/s)
        startup_priors[r] = [
            FixedSE2Prior(
                var_specs=((f"x_{r}_0", "se2"),),
                ref_values={f"x_{r}_0": noisy_p0},
                a_mat=np.diag([1.0 / 1.5, 1.0 / 1.5, 1.0 / 0.5]),
                b_vec=np.zeros(3),
                sources=frozenset({f"init_pose:{r}"}),
            ),
            FixedSE2Prior(
                var_specs=((f"bg_{r}", "scalar"),),
                ref_values={f"bg_{r}": 0.0},
                a_mat=np.array([[1.0 / 0.08]]),
                b_vec=np.zeros(1),
                sources=frozenset({f"init_bg:{r}"}),
            ),
        ]

    return dict(
        n_robots=n_robots,
        n_steps=n_steps,
        anchors=anchors,
        lever=lever,
        shared_anchor_biases=np.asarray(shared_anchor_biases, dtype=float),
        gyro_biases_true=gyro_biases_true,
        truth_poses=truth_poses,
        init_poses=init_poses,
        startup_priors=startup_priors,
        odom_factors=odom_factors,
        anchor_factors=anchor_factors,
        p2p_factors=p2p_factors,
        comm_edges=comm_edges,
    )


def evaluate_nonlinear_consensus_vs_baselines(seed=42):
    """Compare 4 nonlinear estimation modes on the e-puck 2-anchor shared-bias squad:
      1. Single-robot isolated (no P2P, ignores shared anchor bias)
      2. Bias-blind Consensus ADMM (standard MESA-like pose consensus, ignores shared anchor bias)
      3. Joint Pose+Bias Consensus ADMM (proposed consensus framework)
      4. Centralized Joint Pose+Bias Batch NLS (exact global reference)
    """
    sim = simulate_epuck_squad(seed=seed)
    n_r, n_t = sim["n_robots"], sim["n_steps"]

    def compute_rmse(pose_lookup):
        sq_errs = []
        for r in range(n_r):
            for t in range(n_t):
                est = pose_lookup(r, t)[:2]
                tru = sim["truth_poses"][r][t][:2]
                sq_errs.append(float(np.sum((est - tru) ** 2)))
        return float(np.sqrt(np.mean(sq_errs)))

    # 1. Single-robot isolated (bias-blind)
    iso_graphs = {}
    for r in range(n_r):
        g = RobotNodeGraph(r, sim["lever"])
        g.ensure_scalar(f"bg_{r}", 0.0)
        for t in range(n_t):
            g.ensure_se2(f"x_{r}_{t}", sim["init_poses"][r][t])
        for p in sim["startup_priors"][r]:
            g.add_prior(p)
        for f in sim["odom_factors"][r]:
            g.add_odom(f)
        for f in sim["anchor_factors"][r]:
            # Strip shared bias key
            g.add_anchor_range(
                AnchorRangeFactor(
                    pose_key=f.pose_key,
                    anchor_pos=f.anchor_pos,
                    measurement=f.measurement,
                    sigma=f.sigma,
                    lever_arm=f.lever_arm,
                    bias_key=None,
                    sources=f.sources,
                )
            )
        g.optimize_local(max_iters=20)
        iso_graphs[r] = g
    rmse_isolated = compute_rmse(lambda r, t: iso_graphs[r].values[f"x_{r}_{t}"])

    # 2. Bias-blind Consensus ADMM (MESA-style pose splitting without shared anchor bias variables)
    bb_graphs = {}
    for r in range(n_r):
        g = RobotNodeGraph(r, sim["lever"])
        g.ensure_scalar(f"bg_{r}", 0.0)
        for t in range(n_t):
            g.ensure_se2(f"x_{r}_{t}", sim["init_poses"][r][t])
        for p in sim["startup_priors"][r]:
            g.add_prior(p)
        for f in sim["odom_factors"][r]:
            g.add_odom(f)
        for f in sim["anchor_factors"][r]:
            g.add_anchor_range(
                AnchorRangeFactor(
                    pose_key=f.pose_key,
                    anchor_pos=f.anchor_pos,
                    measurement=f.measurement,
                    sigma=f.sigma,
                    lever_arm=f.lever_arm,
                    bias_key=None,
                    sources=f.sources,
                )
            )
        bb_graphs[r] = g
    for u, v, pf in sim["p2p_factors"]:
        # Assign P2P factor to endpoint u (creates local copy of v's pose on u, reconciled via ADMM)
        t_idx = int(pf.pose_j_key.split("_")[-1])
        bb_graphs[u].add_p2p_range(pf, neighbor_init_pose=sim["init_poses"][v][t_idx])
    admm_bb = MultiRobotSE2ADMM(bb_graphs, sim["comm_edges"], rho_pos=40.0, rho_ang=80.0, rho_bias=45.0)
    admm_bb_stats = admm_bb.run(rounds=75, inner_gn_iters=4)
    rmse_bias_blind_admm = compute_rmse(lambda r, t: bb_graphs[r].values[f"x_{r}_{t}"])

    # 3. Joint Pose + Shared-Bias Consensus ADMM (Ours)
    jt_graphs = {}
    for r in range(n_r):
        g = RobotNodeGraph(r, sim["lever"])
        g.ensure_scalar(f"bg_{r}", 0.0)
        for t in range(n_t):
            g.ensure_se2(f"x_{r}_{t}", sim["init_poses"][r][t])
        for p in sim["startup_priors"][r]:
            g.add_prior(p)
        for f in sim["odom_factors"][r]:
            g.add_odom(f)
        for f in sim["anchor_factors"][r]:
            g.add_anchor_range(f)
        jt_graphs[r] = g
    for u, v, pf in sim["p2p_factors"]:
        t_idx = int(pf.pose_j_key.split("_")[-1])
        jt_graphs[u].add_p2p_range(pf, neighbor_init_pose=sim["init_poses"][v][t_idx])
    admm_jt = MultiRobotSE2ADMM(jt_graphs, sim["comm_edges"], rho_pos=40.0, rho_ang=80.0, rho_bias=45.0)
    admm_jt_stats = admm_jt.run(rounds=90, inner_gn_iters=4, tol=1e-5)
    rmse_joint_admm = compute_rmse(lambda r, t: jt_graphs[r].values[f"x_{r}_{t}"])
    est_biases_admm = [
        float(np.mean([jt_graphs[r].values[f"b_anc_{a}"] for r in range(n_r)])) for a in range(2)
    ]

    # 4. Centralized Joint Batch NLS
    central = RobotNodeGraph(99, sim["lever"])
    for r in range(n_r):
        central.ensure_scalar(f"bg_{r}", 0.0)
        for t in range(n_t):
            central.ensure_se2(f"x_{r}_{t}", sim["init_poses"][r][t])
        for p in sim["startup_priors"][r]:
            central.add_prior(p)
        for f in sim["odom_factors"][r]:
            central.add_odom(f)
        for f in sim["anchor_factors"][r]:
            central.add_anchor_range(f)
    for _, _, pf in sim["p2p_factors"]:
        central.add_p2p_range(pf)
    central.optimize_local(max_iters=35)
    rmse_central = compute_rmse(lambda r, t: central.values[f"x_{r}_{t}"])
    admm_vs_central_gap = float(
        np.sqrt(
            np.mean(
                [
                    np.sum((jt_graphs[r].values[f"x_{r}_{t}"][:2] - central.values[f"x_{r}_{t}"][:2]) ** 2)
                    for r in range(n_r)
                    for t in range(n_t)
                ]
            )
        )
    )

    return dict(
        seed=seed,
        rmse_isolated_m=rmse_isolated,
        rmse_bias_blind_admm_m=rmse_bias_blind_admm,
        rmse_joint_admm_m=rmse_joint_admm,
        rmse_centralized_joint_m=rmse_central,
        admm_vs_central_gap_m=admm_vs_central_gap,
        bias_blind_consensus_residual=admm_bb_stats["final_residual"],
        joint_consensus_residual=admm_jt_stats["final_residual"],
        true_anchor_biases_m=sim["shared_anchor_biases"].tolist(),
        estimated_anchor_biases_admm_m=est_biases_admm,
    )


def build_gatekeeper_scenario(seed, condition, sigma_b_prior=0.45):
    """Construct a 4-e-puck linearized cooperative localization instance under 4 test conditions:
      - 'all_confounded': all base & candidate observations share East-side x + b_0 confounding
      - 'sparse_independent': 2 noisy independent observations buried among 16 precise confounded ones
      - 'cross_node_synergy': NO independent observation exists; robots 0 & 1 sit East of Anchor 0
        (base: px + b_0), while robots 2 & 3 sit West of Anchor 0 (base: only P2P relative ranges, NO b_0)
        and hold West-side Anchor 0 candidates (px - b_0). Locally on robot 2/3, adding (px - b_0) has
        ZERO pure-observation Schur gain because b_0 is unseen in its base; globally via Beacon it cancels b_0!
      - 'misattributed_bias': 2 true noisy independent candidates + 2 high-precision poisoned NLOS
        candidates carrying +/-2.4m unmodeled reflection jump while mistakenly labeled as bias-free!
    """
    rng = np.random.default_rng(seed)
    n_nodes = 4
    edges = [(0, 1), (1, 2), (2, 3), (0, 3)]

    b0_true = float(rng.normal(0.0, sigma_b_prior))
    b1_true = float(rng.normal(0.0, sigma_b_prior))
    # Arbitrary room coordinates in [1.0, 4.0]m: NO artificial zero-mean position prior!
    p_true = {i: rng.uniform(1.0, 4.0, size=2) for i in range(n_nodes)}

    target_keys = tuple(f"p{i}_{axis}" for i in range(n_nodes) for axis in ("x", "y"))
    bias_keys = ("b_0", "b_1") if condition == "cross_node_synergy" else ("b_0",)

    node_base = {i: [] for i in range(n_nodes)}
    prior_b0_factor = LinearFactor(
        keys=("b_0",),
        a=np.array([[1.0 / sigma_b_prior]]),
        b=np.array([0.0]),
        sources=frozenset({"prior_b0"}),
    )
    prior_b1_factor = LinearFactor(
        keys=("b_1",),
        a=np.array([[1.0 / sigma_b_prior]]),
        b=np.array([0.0]),
        sources=frozenset({"prior_b1"}),
    )

    sigma_base = 0.05
    for i in range(n_nodes):
        # Lateral y-axis: in cross_node_synergy give moderate base noise (0.30m) so y-refinement
        # competes realistically for local Schur bandwidth when a node ignores cross-node b_0 cancellation!
        sigma_y = 0.30 if condition == "cross_node_synergy" else 0.06
        z_y = p_true[i][1] + rng.normal(0.0, sigma_y)
        node_base[i].append(
            LinearFactor(
                keys=(f"p{i}_y",),
                a=np.array([[1.0 / sigma_y]]),
                b=np.array([z_y / sigma_y]),
                sources=frozenset({f"base_y:{i}"}),
            )
        )
        if condition != "cross_node_synergy" or i in (0, 1):
            node_base[i].append(prior_b0_factor)
            z_x = p_true[i][0] + 1.0 * b0_true + rng.normal(0.0, sigma_base)
            node_base[i].append(
                LinearFactor(
                    keys=(f"p{i}_x", "b_0"),
                    a=np.array([[1.0 / sigma_base, 1.0 / sigma_base]]),
                    b=np.array([z_x / sigma_base]),
                    sources=frozenset({f"base_x:{i}"}),
                )
            )
        else:
            # West robots (2, 3) have local range to Secondary Anchor 1 (px + b_1), NOT Anchor 0
            node_base[i].append(prior_b0_factor)
            node_base[i].append(prior_b1_factor)
            z_x1 = p_true[i][0] + 1.0 * b1_true + rng.normal(0.0, sigma_base)
            node_base[i].append(
                LinearFactor(
                    keys=(f"p{i}_x", "b_1"),
                    a=np.array([[1.0 / sigma_base, 1.0 / sigma_base]]),
                    b=np.array([z_x1 / sigma_base]),
                    sources=frozenset({f"base_x_b1:{i}"}),
                )
            )

    # Inter-robot P2P relative factors along ring edges (lock formation relative geometry)
    sigma_rel = 0.03
    for u, v in edges:
        for axis_idx, axis in enumerate(("x", "y")):
            z_rel = p_true[u][axis_idx] - p_true[v][axis_idx] + rng.normal(0.0, sigma_rel)
            f_rel = LinearFactor(
                keys=(f"p{u}_{axis}", f"p{v}_{axis}"),
                a=np.array([[1.0 / sigma_rel, -1.0 / sigma_rel]]),
                b=np.array([z_rel / sigma_rel]),
                sources=frozenset({f"rel:{u}:{v}:{axis}"}),
            )
            node_base[u].append(f_rel)
            node_base[v].append(f_rel)

    # Candidate pool: 16 high-precision redundant candidates (East-side px + b_0 on 0/1, or y-axis)
    node_cands = {i: [] for i in range(n_nodes)}
    for k in range(16):
        owner = k % n_nodes
        sigma_red = 0.025
        if k % 2 == 0 and (condition != "cross_node_synergy" or owner in (0, 1)):
            z_c = p_true[owner][0] + 1.0 * b0_true + rng.normal(0.0, sigma_red)
            cand = LinearFactor(
                keys=(f"p{owner}_x", "b_0"),
                a=np.array([[1.0 / sigma_red, 1.0 / sigma_red]]),
                b=np.array([z_c / sigma_red]),
                sources=frozenset({f"cand_red_x:{k}"}),
            )
        else:
            z_c = p_true[owner][1] + rng.normal(0.0, sigma_red)
            cand = LinearFactor(
                keys=(f"p{owner}_y",),
                a=np.array([[1.0 / sigma_red]]),
                b=np.array([z_c / sigma_red]),
                sources=frozenset({f"cand_red_y:{k}"}),
            )
        node_cands[owner].append(cand)

    if condition == "all_confounded":
        for k in range(4):
            owner = k % n_nodes
            sigma_c = 0.02
            z_c = p_true[owner][0] + 1.0 * b0_true + rng.normal(0.0, sigma_c)
            node_cands[owner].append(
                LinearFactor(
                    keys=(f"p{owner}_x", "b_0"),
                    a=np.array([[1.0 / sigma_c, 1.0 / sigma_c]]),
                    b=np.array([z_c / sigma_c]),
                    sources=frozenset({f"cand_extra_conf:{k}"}),
                )
            )

    elif condition == "sparse_independent":
        for idx_c, owner in enumerate((1, 3)):
            sigma_ind = 0.10
            z_ind = p_true[owner][0] + rng.normal(0.0, sigma_ind)
            node_cands[owner].append(
                LinearFactor(
                    keys=(f"p{owner}_x",),
                    a=np.array([[1.0 / sigma_ind]]),
                    b=np.array([z_ind / sigma_ind]),
                    sources=frozenset({f"cand_ind:{idx_c}"}),
                )
            )

    elif condition == "cross_node_synergy":
        # West-side robots (2, 3) hold opposite-aspect ranges to Anchor 0: px - b_0 (sigma=0.10m).
        # Locally on robot 2/3, px is tied to b_1 (not b_0), so adding px - b_0 only averages b_1 and b_0
        # (~0.0040 var_drop/byte, losing to y-refinement's 0.0049/byte!). Globally via East robots' Beacon,
        # px - b_0 cancels East robots' px + b_0 and unlocks ALL 4 robots!
        for idx_c, owner in enumerate((2, 3)):
            sigma_syn = 0.10
            z_syn = p_true[owner][0] - 1.0 * b0_true + rng.normal(0.0, sigma_syn)
            node_cands[owner].append(
                LinearFactor(
                    keys=(f"p{owner}_x", "b_0"),
                    a=np.array([[1.0 / sigma_syn, -1.0 / sigma_syn]]),
                    b=np.array([z_syn / sigma_syn]),
                    sources=frozenset({f"cand_syn:{idx_c}"}),
                )
            )

    elif condition == "misattributed_bias":
        for idx_c, owner in enumerate((1, 3)):
            sigma_ind = 0.10
            z_ind = p_true[owner][0] + rng.normal(0.0, sigma_ind)
            node_cands[owner].append(
                LinearFactor(
                    keys=(f"p{owner}_x",),
                    a=np.array([[1.0 / sigma_ind]]),
                    b=np.array([z_ind / sigma_ind]),
                    sources=frozenset({f"cand_clean_ind:{idx_c}"}),
                )
            )
        for idx_bad, owner in enumerate((0, 2)):
            sigma_declared = 0.045
            unmodeled_nlos_jump = 2.4 * (1.0 if idx_bad == 0 else -1.0)
            z_bad = p_true[owner][0] + unmodeled_nlos_jump + rng.normal(0.0, 0.045)
            node_cands[owner].append(
                LinearFactor(
                    keys=(f"p{owner}_x",),
                    a=np.array([[1.0 / sigma_declared]]),
                    b=np.array([z_bad / sigma_declared]),
                    sources=frozenset({f"cand_misattr_bad:{idx_bad}"}),
                )
            )
    else:
        raise ValueError(f"Unknown condition: {condition}")

    truth_vec = np.array(
        [p_true[i][0] if ax == "x" else p_true[i][1] for i in range(n_nodes) for ax in ("x", "y")],
        dtype=float,
    )
    return dict(
        node_base=node_base,
        node_cands=node_cands,
        edges=edges,
        target_keys=target_keys,
        bias_keys=bias_keys,
        truth_target=truth_vec,
        b_true=np.array([b0_true]),
    )


def run_gatekeeper_benchmark(n_trials=80, byte_budget=132):
    """Run the decisive gatekeeper experiment across 4 conditions x 7 selection policies."""
    conditions = (
        "all_confounded",
        "sparse_independent",
        "cross_node_synergy",
        "misattributed_bias",
    )
    policies = (
        "random",
        "max_admm_residual",
        "bias_blind_info",
        "local_schur",
        "beacon_no_cert",
        "beacon_cert",
        "centralized_oracle",
    )
    prior_var_env = 0.45**2

    summary = {}
    for cond in conditions:
        summary[cond] = {}
        for pol in policies:
            weak_sq_errors = []
            full_sq_errors = []
            cover_weak_95 = []
            bytes_used = []

            for trial in range(n_trials):
                scen = build_gatekeeper_scenario(seed=10000 + trial, condition=cond, sigma_b_prior=0.45)
                admm_warm = EdgeSeparableADMM(
                    scen["node_base"], scen["edges"], rho=4.0, prior_var_envelope=prior_var_env
                )
                for _ in range(3):
                    warm_metrics = admm_warm.step_synchronous()

                sel = select_messages_under_budget(
                    policy=pol,
                    node_base_factors=scen["node_base"],
                    node_candidates=scen["node_cands"],
                    edges=scen["edges"],
                    target_keys=scen["target_keys"],
                    bias_keys=scen["bias_keys"],
                    byte_budget=byte_budget,
                    admm_edge_residuals=warm_metrics["edge_residuals"],
                    rng_seed=50000 + trial,
                    prior_var_envelope=prior_var_env,
                    cert_envelope=prior_var_env,
                )

                augmented_nodes = {nid: list(scen["node_base"][nid]) for nid in scen["node_base"]}
                for _, pkt_factor, _ in sel["selected"]:
                    for nid in augmented_nodes:
                        augmented_nodes[nid].append(pkt_factor)

                admm_final = EdgeSeparableADMM(
                    augmented_nodes, scen["edges"], rho=30.0, prior_var_envelope=prior_var_env
                )
                admm_final.run(max_iters=180, tol=1e-7)

                phys = admm_final.physical_uncertainty_summary()
                all_keys = tuple(
                    sorted({k for nid in augmented_nodes for f in augmented_nodes[nid] for k in f.keys})
                )
                kmap = {k: i for i, k in enumerate(all_keys)}
                t_idx = [kmap[k] for k in scen["target_keys"]]

                est_target = np.array(
                    [admm_final.node_estimates[k_idx // 2][k] for k_idx, k in enumerate(scen["target_keys"])]
                )
                err_vec = est_target - scen["truth_target"]
                x_indices = [0, 2, 4, 6]
                err_x = err_vec[x_indices]
                weak_sq_errors.append(float(np.mean(err_x**2)))
                full_sq_errors.append(float(np.mean(err_vec**2)))

                # For bias_blind_info, report honest physical coverage vs bias-blind overconfident coverage?
                # As specified in protocol: fixed estimator & fixed physical uncertainty model across all selection rules!
                cov_target = phys["covariance_envelope"][np.ix_(t_idx, t_idx)]
                var_p0x = float(cov_target[0, 0])
                cover_weak_95.append(float((err_vec[0] ** 2) / max(var_p0x, 1e-12) <= CHI2_1D_95))
                bytes_used.append(sel["total_bytes"])

            summary[cond][pol] = dict(
                weak_x_rmse_cm=round(float(np.sqrt(np.mean(weak_sq_errors))) * 100.0, 2),
                pos_2d_rmse_cm=round(float(np.sqrt(np.mean(full_sq_errors))) * 100.0, 2),
                coverage95_pct=round(float(np.mean(cover_weak_95)) * 100.0, 1),
                mean_bytes=round(float(np.mean(bytes_used)), 1),
            )
    return summary


def main_cli(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    nl_trials = [evaluate_nonlinear_consensus_vs_baselines(seed=40 + s) for s in range(10)]
    nl_agg = dict(
        n_seeds=len(nl_trials),
        mean_rmse_isolated_cm=round(float(np.mean([r["rmse_isolated_m"] for r in nl_trials])) * 100.0, 2),
        mean_rmse_bias_blind_admm_cm=round(
            float(np.mean([r["rmse_bias_blind_admm_m"] for r in nl_trials])) * 100.0, 2
        ),
        mean_rmse_joint_admm_cm=round(float(np.mean([r["rmse_joint_admm_m"] for r in nl_trials])) * 100.0, 2),
        mean_rmse_centralized_joint_cm=round(
            float(np.mean([r["rmse_centralized_joint_m"] for r in nl_trials])) * 100.0, 2
        ),
        mean_admm_vs_central_gap_cm=round(
            float(np.mean([r["admm_vs_central_gap_m"] for r in nl_trials])) * 100.0, 3
        ),
        per_seed=nl_trials,
    )
    gatekeeper = run_gatekeeper_benchmark(n_trials=80, byte_budget=132)
    payload = dict(
        note="Synthetic e-puck sparse-anchor UWB/IMU cooperative localization benchmark; not field hardware data.",
        nonlinear_se2_consensus=nl_agg,
        gatekeeper_message_selection=gatekeeper,
    )
    (out_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(dict(nonlinear=nl_agg["mean_rmse_joint_admm_cm"], gatekeeper=gatekeeper), indent=2))


if __name__ == "__main__":
    import sys

    target_dir = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path("/Users/duan/Developer/2026年度-感知网络规划/outputs/epuck-consensus-admm-20261124")
    )
    main_cli(target_dir)
