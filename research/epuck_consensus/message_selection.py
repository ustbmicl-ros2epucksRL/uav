"""Bandwidth-constrained factor selection policies under shared sensor biases."""

from dataclasses import dataclass
import numpy as np

from .linear_consensus import (
    LinearFactor,
    deduplicate_idempotent_factors,
    schur_marginal_info,
    solve_linear,
)

PACKET_HEADER_BYTES = 4
SOURCE_HASH_BYTES = 4


def factor_wire_bytes(factor: LinearFactor):
    """Realistic serialized byte size of a compressed scalar/vector LinearFactor packet."""
    m_rows, k_vars = factor.a.shape
    # header(4) + source_id_hash(4 * n_sources) + key_ids(2 * k_vars) + float32(a + b)
    n_src = max(len(factor.sources), 1)
    payload = 2 * k_vars + 4 * (m_rows * k_vars + m_rows)
    return int(PACKET_HEADER_BYTES + SOURCE_HASH_BYTES * n_src + payload)


def beacon_wire_bytes(n_target_dim, n_bias_dim, rank_r=1):
    """Serialized byte size of a float16 Confounding-Beacon (u_w, c_w, sigma_w^2)."""
    floats = rank_r * (n_target_dim + n_bias_dim + 1)
    return int(PACKET_HEADER_BYTES + 2 * floats)


@dataclass(frozen=True)
class ConfoundingBeacon:
    """Compact need-sketch broadcast by node i describing its weakest position direction and bias coupling."""

    sender_id: int
    target_keys: tuple
    bias_keys: tuple
    u_weak: np.ndarray      # quantized float16->float64 unit vector in R^{d_x}
    c_confound: np.ndarray  # quantized float16->float64 confounding loading in R^{d_b}
    weak_var: float         # variance along u_weak
    wire_bytes: int


def build_confounding_beacon(
    node_id,
    local_factors,
    target_keys,
    bias_keys,
    other_nuisance_keys=(),
    prior_var_envelope=4.0,
):
    """Compute and float16-quantize node_id's weakest bias-marginalized position mode."""
    target_keys = tuple(target_keys)
    bias_keys = tuple(bias_keys)
    other_nuisance_keys = tuple(other_nuisance_keys)

    # Exclude pure-bias zero-mean prior factors when extracting observation confounding geometry
    # so pure confounding modes have exact 1:1 loading without prior shrinkage distortion.
    obs_only_factors = [
        f for f in local_factors if not (set(f.keys) <= set(bias_keys))
    ]
    all_nuisance = bias_keys + other_nuisance_keys
    info = schur_marginal_info(
        obs_only_factors if obs_only_factors else local_factors,
        target_keys=target_keys,
        nuisance_keys=all_nuisance,
        prior_var_envelope=prior_var_envelope,
    )
    # Weakest eigenvector of S_xx corresponds to smallest eigenvalue (index 0 from eigh)
    u_raw = info["eigenvectors"][:, 0]
    min_eig = float(info["eigenvalues"][0])
    weak_var = float(min(prior_var_envelope, 1.0 / max(min_eig, 1.0 / prior_var_envelope)))

    # Extract only shared-bias rows of confounding_loading (first len(bias_keys) rows)
    nb = len(bias_keys)
    c_raw = info["confounding_loading"][:nb, :] @ u_raw if nb > 0 else np.zeros(0, dtype=float)

    # Quantize to float16 on the wire so scoring uses ONLY what actually crossed the channel
    u_q = np.asarray(u_raw, dtype=np.float16).astype(float)
    nrm = float(np.linalg.norm(u_q))
    if nrm > 1e-8:
        u_q = u_q / nrm
    c_q = np.asarray(c_raw, dtype=np.float16).astype(float)
    var_q = float(np.asarray(weak_var, dtype=np.float16))

    return ConfoundingBeacon(
        sender_id=int(node_id),
        target_keys=target_keys,
        bias_keys=bias_keys,
        u_weak=u_q,
        c_confound=c_q,
        weak_var=max(var_q, 1e-6),
        wire_bytes=beacon_wire_bytes(len(target_keys), len(bias_keys), rank_r=1),
    )


def candidate_innovation_gate(
    candidate: LinearFactor,
    local_reference_factors,
    prior_var_envelope=1.0,
    tau_hard=3.0,
):
    """GLRT / Tukey biweight innovation gate in [0, 1] against honest physical envelope.

    Tests H_0 (declared bias group) vs H_1 (candidate carries an unmodeled spawned bias b_new).
    Under H_1 (|r_whitened| >= tau_hard, 99.7% 3-sigma envelope), a scalar factor with a free
    private bias has exact zero Schur position gain, so omega = 0 prevents transmitting outliers.
    """
    ref_keys = tuple(sorted({k for f in local_reference_factors for k in f.keys} | set(candidate.keys)))
    fit = solve_linear(
        local_reference_factors,
        ref_keys,
        include_ephemeral=False,
        prior_var_envelope=prior_var_envelope,
    )
    key_map = {k: i for i, k in enumerate(ref_keys)}
    idx = [key_map[k] for k in candidate.keys]
    theta_hat = fit["solution"][idx]
    cov_sub = fit["covariance_envelope"][np.ix_(idx, idx)]

    res = candidate.a @ theta_hat - candidate.b
    pred_cov = candidate.a @ cov_sub @ candidate.a.T + np.eye(len(candidate.b))
    try:
        chol = np.linalg.cholesky(0.5 * (pred_cov + pred_cov.T))
        whitened = np.linalg.solve(chol, res)
        maha = float(np.linalg.norm(whitened))
    except np.linalg.LinAlgError:
        maha = float(np.linalg.norm(res))
    if maha >= tau_hard:
        return 0.0
    ratio_sq = (maha / tau_hard) ** 2
    return float((1.0 - ratio_sq) ** 2)


def score_candidate_with_beacon(
    candidate: LinearFactor,
    beacon: ConfoundingBeacon,
    sender_base_factors,
    use_cert_gate=True,
    prior_var_envelope=1.0,
):
    """Closed-form sender-side disentanglement variance reduction per byte for a receiver's beacon."""
    t_map = {k: i for i, k in enumerate(beacon.target_keys)}
    b_map = {k: i for i, k in enumerate(beacon.bias_keys)}

    # Project candidate rows onto [u_weak; -c_confound]
    # Note: if candidate touches private sender variables not in (target, bias), project after local conditioning
    extra_keys = tuple(k for k in candidate.keys if k not in t_map and k not in b_map)
    eff_factor = candidate
    if extra_keys:
        sender_touch = [f for f in sender_base_factors if set(f.keys) & set(extra_keys)]
        if sender_touch:
            # Marginalize sender's private intermediate variables locally
            from .linear_consensus import marginalize_linear
            retain = tuple(k for k in sorted({k for f in sender_touch + [candidate] for k in f.keys}) if k not in extra_keys)
            eff_factor, _ = marginalize_linear(sender_touch + [candidate], extra_keys, retain)
        else:
            # Unconstrained extra variable leaves zero information on target
            return 0.0

    proj_vec = np.zeros(len(eff_factor.b), dtype=float)
    for col, k in enumerate(eff_factor.keys):
        if k in t_map:
            proj_vec += eff_factor.a[:, col] * beacon.u_weak[t_map[k]]
        elif k in b_map:
            proj_vec -= eff_factor.a[:, col] * beacon.c_confound[b_map[k]]

    gamma = float(np.dot(proj_vec, proj_vec))
    row_norm_sq = float(np.sum(eff_factor.a ** 2))
    # Account for float16 beacon quantization floor (~1e-3 relative) so collinear confounded factors score 0
    if gamma <= max(1e-12, 1e-5 * row_norm_sq):
        return 0.0

    omega = (
        candidate_innovation_gate(candidate, sender_base_factors, prior_var_envelope=prior_var_envelope)
        if use_cert_gate
        else 1.0
    )
    eff_gamma = omega * gamma
    w_var = beacon.weak_var
    delta_var = (w_var * w_var * eff_gamma) / (1.0 + w_var * eff_gamma)
    return float(delta_var / factor_wire_bytes(candidate))


def select_messages_under_budget(
    policy: str,
    node_base_factors: dict[int, list[LinearFactor]],
    node_candidates: dict[int, list[LinearFactor]],
    edges: list[tuple[int, int]],
    target_keys: tuple,
    bias_keys: tuple,
    byte_budget: int,
    admm_edge_residuals: dict | None = None,
    rng_seed: int = 0,
    prior_var_envelope: float = 4.0,
    cert_envelope: float = 0.64,
):
    """Select candidate factors to broadcast/exchange across nodes within a strict byte budget.

    Policies:
      - 'random': uniform random candidate packets until budget exhausted
      - 'max_admm_residual': prioritize candidates from endpoints of highest ADMM residual edges
      - 'bias_blind_info': local position variance reduction assuming shared biases are zero
      - 'local_schur': sender-only local Schur variance reduction (misses cross-node synergy & un-gated)
      - 'beacon_no_cert': Confounding-Beacon pull-push WITHOUT GLRT innovation gate (ablation)
      - 'beacon_cert': Confounding-Beacon pull-push WITH GLRT innovation gate (proposed)
      - 'centralized_oracle': privileged full-graph Schur variance reduction greedy reference (no truth used)
    """
    valid_policies = {
        "random",
        "max_admm_residual",
        "bias_blind_info",
        "local_schur",
        "beacon_no_cert",
        "beacon_cert",
        "centralized_oracle",
    }
    if policy not in valid_policies:
        raise ValueError(f"Unknown message selection policy: {policy}")

    rng = np.random.default_rng(rng_seed)
    node_ids = tuple(sorted(node_base_factors.keys()))
    neighbors = {nid: set() for nid in node_ids}
    for u, v in edges:
        neighbors[u].add(v)
        neighbors[v].add(u)

    # Flatten candidate pool as (sender_id, factor, wire_bytes), deduplicated by source_id
    all_base_sources = {
        src for nid in node_ids for f in node_base_factors[nid] for src in f.sources
    }
    pool = []
    seen_cand_sources = set()
    for sid in node_ids:
        for cand in node_candidates.get(sid, []):
            if cand.sources <= all_base_sources or (cand.sources & seen_cand_sources):
                continue
            seen_cand_sources.update(cand.sources)
            pool.append((sid, cand, factor_wire_bytes(cand)))

    bytes_spent = 0
    beacon_bytes = 0
    selected_packets = []

    if byte_budget <= 0 or not pool:
        return dict(
            selected=selected_packets,
            total_bytes=0,
            beacon_bytes=0,
            payload_bytes=0,
        )

    if policy == "random":
        order = rng.permutation(len(pool)).tolist()
        for idx in order:
            sid, cand, b_cost = pool[idx]
            if bytes_spent + b_cost <= byte_budget:
                selected_packets.append((sid, cand, b_cost))
                bytes_spent += b_cost

    elif policy == "max_admm_residual":
        node_urgency = {nid: 0.0 for nid in node_ids}
        if admm_edge_residuals:
            for (u, v), res_val in admm_edge_residuals.items():
                node_urgency[u] = max(node_urgency[u], float(res_val))
                node_urgency[v] = max(node_urgency[v], float(res_val))
        scored = []
        for idx, (sid, cand, b_cost) in enumerate(pool):
            # Tie-break by signal precision norm
            prec_norm = float(np.linalg.norm(cand.a) ** 2) / b_cost
            scored.append((node_urgency[sid], prec_norm, idx))
        scored.sort(reverse=True)
        for _, _, idx in scored:
            sid, cand, b_cost = pool[idx]
            if bytes_spent + b_cost <= byte_budget:
                selected_packets.append((sid, cand, b_cost))
                bytes_spent += b_cost

    elif policy == "bias_blind_info":
        # Treats bias_keys as known zero (condition_zero), scoring position trace variance drop locally
        scored = []
        for idx, (sid, cand, b_cost) in enumerate(pool):
            base_s = node_base_factors[sid]
            local_t = tuple(k for k in target_keys if any(k in f.keys for f in base_s + [cand]))
            if not local_t:
                scored.append((0.0, idx))
                continue
            # Strip bias columns to mimic bias-blind D/A-optimality
            def strip_bias(flist):
                out = []
                for f in flist:
                    keep = [i for i, k in enumerate(f.keys) if k not in bias_keys]
                    if keep:
                        out.append(LinearFactor(tuple(f.keys[i] for i in keep), f.a[:, keep], f.b, f.sources))
                return out

            other_keys = tuple(
                sorted({k for f in strip_bias(base_s + [cand]) for k in f.keys} - set(local_t))
            )
            before = np.trace(
                schur_marginal_info(strip_bias(base_s), local_t, other_keys, prior_var_envelope)["covariance_envelope"]
            )
            after = np.trace(
                schur_marginal_info(strip_bias(base_s + [cand]), local_t, other_keys, prior_var_envelope)[
                    "covariance_envelope"
                ]
            )
            scored.append((max(0.0, float(before - after)) / b_cost, idx))
        scored.sort(reverse=True)
        for gain, idx in scored:
            if gain <= 0.0:
                continue
            sid, cand, b_cost = pool[idx]
            if bytes_spent + b_cost <= byte_budget:
                selected_packets.append((sid, cand, b_cost))
                bytes_spent += b_cost

    elif policy == "local_schur":
        # Node-local Schur complement eliminating bias_keys using ONLY sender's own local graph
        scored = []
        for idx, (sid, cand, b_cost) in enumerate(pool):
            base_s = node_base_factors[sid]
            all_k = {k for f in base_s + [cand] for k in f.keys}
            local_t = tuple(k for k in target_keys if k in all_k)
            if not local_t:
                scored.append((0.0, idx))
                continue
            nuis = tuple(sorted(all_k - set(local_t)))
            before = np.trace(
                schur_marginal_info(base_s, local_t, nuis, prior_var_envelope)["covariance_envelope"]
            )
            after = np.trace(
                schur_marginal_info(base_s + [cand], local_t, nuis, prior_var_envelope)["covariance_envelope"]
            )
            scored.append((max(0.0, float(before - after)) / b_cost, idx))
        scored.sort(reverse=True)
        for gain, idx in scored:
            if gain <= 1e-12:
                continue
            sid, cand, b_cost = pool[idx]
            if bytes_spent + b_cost <= byte_budget:
                selected_packets.append((sid, cand, b_cost))
                bytes_spent += b_cost

    elif policy in ("beacon_cert", "beacon_no_cert"):
        use_cert = policy == "beacon_cert"
        # Stage 1: Each node with target keys broadcasts a compact Confounding-Beacon to neighbors
        beacons = {}
        for nid in node_ids:
            base_n = node_base_factors[nid]
            all_k = {k for f in base_n for k in f.keys}
            local_t = tuple(k for k in target_keys if k in all_k)
            if not local_t:
                continue
            local_b = tuple(k for k in bias_keys if k in all_k)
            other_nuis = tuple(sorted(all_k - set(local_t) - set(local_b)))
            bcn = build_confounding_beacon(
                nid,
                base_n,
                target_keys=local_t,
                bias_keys=local_b,
                other_nuisance_keys=other_nuis,
                prior_var_envelope=prior_var_envelope,
            )
            beacons[nid] = bcn
            beacon_bytes += bcn.wire_bytes

        bytes_spent += beacon_bytes
        if bytes_spent >= byte_budget:
            return dict(
                selected=[],
                total_bytes=bytes_spent,
                beacon_bytes=beacon_bytes,
                payload_bytes=0,
            )

        # Stage 2: Senders score local candidates against received neighbor (and self) beacons
        # Greedy submodular-style update of beacon weak_var after each chosen packet
        active_weak_var = {nid: beacons[nid].weak_var for nid in beacons}
        remaining = list(range(len(pool)))
        while remaining:
            best_score = 0.0
            best_pos = None
            best_impacts = None
            for pos_in_rem, idx in enumerate(remaining):
                sid, cand, b_cost = pool[idx]
                if bytes_spent + b_cost > byte_budget:
                    continue
                total_gain = 0.0
                impacts = {}
                # Evaluate benefit across neighborhood beacons
                receivers = (neighbors[sid] | {sid}) & set(beacons.keys())
                for rid in receivers:
                    bcn_r = beacons[rid]
                    updated_bcn = ConfoundingBeacon(
                        sender_id=bcn_r.sender_id,
                        target_keys=bcn_r.target_keys,
                        bias_keys=bcn_r.bias_keys,
                        u_weak=bcn_r.u_weak,
                        c_confound=bcn_r.c_confound,
                        weak_var=active_weak_var[rid],
                        wire_bytes=bcn_r.wire_bytes,
                    )
                    sc = score_candidate_with_beacon(
                        cand,
                        updated_bcn,
                        sender_base_factors=node_base_factors[sid],
                        use_cert_gate=use_cert,
                        prior_var_envelope=cert_envelope,
                    )
                    if sc > 0.0:
                        dv = sc * b_cost
                        total_gain += sc
                        impacts[rid] = dv
                if total_gain > best_score:
                    best_score = total_gain
                    best_pos = pos_in_rem
                    best_impacts = impacts

            if best_pos is None or best_score <= 1e-12:
                break
            chosen_idx = remaining.pop(best_pos)
            sid, cand, b_cost = pool[chosen_idx]
            selected_packets.append((sid, cand, b_cost))
            bytes_spent += b_cost
            for rid, dv in best_impacts.items():
                active_weak_var[rid] = max(1e-6, active_weak_var[rid] - dv)

    elif policy == "centralized_oracle":
        # Privileged global Jacobian Schur greedy benchmark (still NO ground truth x* or b* used!)
        current_global = deduplicate_idempotent_factors(
            [f for nid in node_ids for f in node_base_factors[nid]]
        )
        remaining = list(range(len(pool)))

        def global_target_trace(flist):
            all_k = {k for f in flist for k in f.keys}
            t_present = tuple(k for k in target_keys if k in all_k)
            nuis = tuple(sorted(all_k - set(t_present)))
            return float(
                np.trace(
                    schur_marginal_info(flist, t_present, nuis, prior_var_envelope)["covariance_envelope"]
                )
            )

        cur_trace = global_target_trace(current_global)
        while remaining:
            best_gain_per_byte = 0.0
            best_pos = None
            best_new_trace = cur_trace
            for pos_in_rem, idx in enumerate(remaining):
                sid, cand, b_cost = pool[idx]
                if bytes_spent + b_cost > byte_budget:
                    continue
                trial_trace = global_target_trace(current_global + [cand])
                gain_pb = (cur_trace - trial_trace) / b_cost
                if gain_pb > best_gain_per_byte:
                    best_gain_per_byte = gain_pb
                    best_pos = pos_in_rem
                    best_new_trace = trial_trace
            if best_pos is None or best_gain_per_byte <= 1e-12:
                break
            chosen_idx = remaining.pop(best_pos)
            sid, cand, b_cost = pool[chosen_idx]
            selected_packets.append((sid, cand, b_cost))
            current_global = deduplicate_idempotent_factors(current_global + [cand])
            bytes_spent += b_cost
            cur_trace = best_new_trace

    return dict(
        selected=selected_packets,
        total_bytes=bytes_spent,
        beacon_bytes=beacon_bytes,
        payload_bytes=bytes_spent - beacon_bytes,
    )
