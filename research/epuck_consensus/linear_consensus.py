"""Linear Factor Graph with column-equilibrated SVD elimination and Edge-Separable Consensus ADMM."""

from dataclasses import dataclass
import numpy as np


@dataclass
class LinearFactor:
    """Whitened linear factor: residual(x) = a @ x_keys - b, cost = 0.5 * ||a @ x - b||_2^2."""

    keys: tuple
    a: np.ndarray
    b: np.ndarray
    sources: frozenset
    ephemeral: bool = False

    def __post_init__(self):
        self.keys = tuple(self.keys)
        self.a = np.array(self.a, dtype=float, copy=True)
        self.b = np.array(self.b, dtype=float, copy=True)
        self.sources = frozenset(self.sources)
        self.ephemeral = bool(self.ephemeral)
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("Duplicate variable keys in LinearFactor")
        if self.b.ndim != 1 or self.a.ndim != 2 or self.a.shape != (len(self.b), len(self.keys)):
            raise ValueError("Invalid LinearFactor matrix/vector shape")
        if not np.isfinite(self.a).all() or not np.isfinite(self.b).all():
            raise ValueError("Nonfinite entries in LinearFactor")
        if not self.ephemeral and not self.sources:
            raise ValueError("Permanent physical factor must carry non-empty source IDs")


def deduplicate_idempotent_factors(factors):
    """Merge a collection of physical factors idempotently, dropping exact duplicate source packets."""
    accepted = []
    seen_sources = set()
    for factor in factors:
        if factor.ephemeral:
            accepted.append(factor)
            continue
        overlap = seen_sources & factor.sources
        if overlap == factor.sources:
            # Exact packet already present (e.g. arrived via another path in a loopy network)
            continue
        if overlap:
            raise ValueError(f"Partial overlapping provenance detected: {sorted(overlap)}")
        seen_sources.update(factor.sources)
        accepted.append(factor)
    return accepted


def assemble_linear(factors, keys, include_ephemeral=True):
    """Assemble stacked whitened system A @ x - b with strict provenance deduplication."""
    keys = tuple(keys)
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate assembled variable keys")
    index = {k: i for i, k in enumerate(keys)}
    seen_sources = set()
    mats = []
    rhs = []
    for factor in factors:
        if factor.ephemeral and not include_ephemeral:
            continue
        if not factor.ephemeral:
            if seen_sources & factor.sources:
                raise ValueError("Original observation counted more than once")
            seen_sources.update(factor.sources)
        block = np.zeros((len(factor.b), len(keys)), dtype=float)
        for col_idx, key in enumerate(factor.keys):
            if key not in index:
                raise ValueError(f"Factor variable omitted from key list: {key}")
            block[:, index[key]] = factor.a[:, col_idx]
        mats.append(block)
        rhs.append(factor.b)
    if not mats:
        return np.zeros((0, len(keys)), dtype=float), np.zeros(0, dtype=float), frozenset()
    return np.vstack(mats), np.concatenate(rhs), frozenset(seen_sources)


def equilibrated_svd(a_mat, rel_tol=None):
    """Column-equilibrated SVD decomposition returning scaled spectrum and rank."""
    if a_mat.size == 0 or a_mat.shape[0] == 0:
        n_cols = a_mat.shape[1] if a_mat.ndim == 2 else 0
        return (
            np.zeros((0, 0)),
            np.zeros(0),
            np.eye(n_cols),
            np.ones(n_cols),
            0,
            0.0,
        )
    norms = np.linalg.norm(a_mat, axis=0)
    scale = np.ones(a_mat.shape[1], dtype=float)
    np.divide(1.0, norms, out=scale, where=norms > 0.0)
    scaled = a_mat * scale[None, :]
    u, s, vt = np.linalg.svd(scaled, full_matrices=True)
    if rel_tol is None:
        tol = max(a_mat.shape) * np.finfo(float).eps * (float(s[0]) if len(s) else 0.0)
    else:
        tol = float(rel_tol) * (float(s[0]) if len(s) else 0.0)
    rank = int(np.sum(s > tol))
    return u, s, vt, scale, rank, float(tol)


def solve_linear(factors, keys, include_ephemeral=True, prior_var_envelope=25.0):
    """Solve linear factor system via column-equilibrated SVD without ridge-pinning nullspace."""
    keys = tuple(keys)
    a_mat, b_vec, sources = assemble_linear(factors, keys, include_ephemeral=include_ephemeral)
    if a_mat.shape[0] == 0:
        n = len(keys)
        return dict(
            solution=np.zeros(n, dtype=float),
            cost=0.0,
            rank=0,
            nullity=n,
            covariance_envelope=np.eye(n, dtype=float) * prior_var_envelope,
            identifiable_projector=np.zeros((n, n), dtype=float),
            singular_values=np.zeros(0, dtype=float),
            sources=sources,
        )

    norms = np.linalg.norm(a_mat, axis=0)
    scale = np.ones(len(keys), dtype=float)
    np.divide(1.0, norms, out=scale, where=norms > 0.0)
    a_scaled = a_mat * scale[None, :]
    u, s, vt = np.linalg.svd(a_scaled, full_matrices=False)
    tol = max(a_mat.shape) * np.finfo(float).eps * (float(s[0]) if len(s) else 0.0)
    rank = int(np.sum(s > tol))
    z = vt[:rank].T @ ((u[:, :rank].T @ b_vec) / s[:rank]) if rank > 0 else np.zeros(len(keys))
    sol = scale * z

    # Physical information H = A^T A; construct honest covariance envelope on unidentifiable nullspace
    h_phys = a_mat.T @ a_mat
    eigvals, eigvecs = np.linalg.eigh(0.5 * (h_phys + h_phys.T))
    h_tol = max(len(keys), 1) * np.finfo(float).eps * max(float(eigvals.max()), 1.0) * 100.0
    obs_mask = eigvals > h_tol
    inv_eigs = np.where(obs_mask, 1.0 / np.maximum(eigvals, h_tol), float(prior_var_envelope))
    cov_env = (eigvecs * inv_eigs[None, :]) @ eigvecs.T
    proj_obs = eigvecs[:, obs_mask] @ eigvecs[:, obs_mask].T if np.any(obs_mask) else np.zeros((len(keys), len(keys)))

    return dict(
        solution=sol,
        cost=0.5 * float(np.linalg.norm(a_mat @ sol - b_vec) ** 2),
        rank=int(np.sum(obs_mask)),
        nullity=int(len(keys) - np.sum(obs_mask)),
        covariance_envelope=0.5 * (cov_env + cov_env.T),
        identifiable_projector=proj_obs,
        information=h_phys,
        singular_values=s,
        sources=sources,
    )


def marginalize_linear(factors, eliminate_keys, retain_keys):
    """Eliminate specified variables from permanent physical factors via column-equilibrated SVD.

    Strictly rejects ephemeral ADMM priors so dual penalties can never leak into historical priors.
    """
    if any(f.ephemeral for f in factors):
        raise ValueError("Ephemeral ADMM factors must NEVER be marginalized into permanent priors")
    eliminate_keys = tuple(eliminate_keys)
    retain_keys = tuple(retain_keys)
    if set(eliminate_keys) & set(retain_keys):
        raise ValueError("Eliminated and retained variable sets overlap")

    a_mat, b_vec, sources = assemble_linear(factors, eliminate_keys + retain_keys, include_ephemeral=False)
    n_elim = len(eliminate_keys)
    ae = a_mat[:, :n_elim]
    ar = a_mat[:, n_elim:]
    u, s, _, _, rank, tol = equilibrated_svd(ae)
    u_perp = u[:, rank:]
    prior_factor = LinearFactor(
        keys=retain_keys,
        a=u_perp.T @ ar,
        b=u_perp.T @ b_vec,
        sources=sources,
        ephemeral=False,
    )
    return prior_factor, dict(
        eliminated_rank=rank,
        eliminated_nullity=n_elim - rank,
        tolerance=tol,
        singular_values=s.tolist(),
    )


def schur_marginal_info(factors, target_keys, nuisance_keys, prior_var_envelope=25.0):
    """Compute bias/nuisance-eliminated physical information S_xx = H_xx - H_xb H_bb^+ H_bx.

    Uses only permanent physical factors (include_ephemeral=False) and SVD pseudo-inverse so
    rank-deficient shared biases never cause singular matrix failures or fake precision.
    """
    target_keys = tuple(target_keys)
    nuisance_keys = tuple(nuisance_keys)
    if set(target_keys) & set(nuisance_keys):
        raise ValueError("Target and nuisance keys must be disjoint")
    all_keys = target_keys + nuisance_keys
    a_mat, b_vec, _ = assemble_linear(factors, all_keys, include_ephemeral=False)
    nx = len(target_keys)
    nb = len(nuisance_keys)
    if a_mat.shape[0] == 0:
        s_xx = np.zeros((nx, nx), dtype=float)
        cov_xx = np.eye(nx, dtype=float) * prior_var_envelope
        return dict(
            schur_info=s_xx,
            covariance_envelope=cov_xx,
            confounding_loading=np.zeros((nb, nx), dtype=float),
            position_mean=np.zeros(nx, dtype=float),
            rank=0,
        )

    ax = a_mat[:, :nx]
    ab = a_mat[:, nx:]
    if nb > 0:
        # Project out column space of ab via SVD for superior numerical conditioning over H_bb^-1
        u_b, s_b, vt_b, scale_b, rank_b, _ = equilibrated_svd(ab)
        u_b_active = u_b[:, :rank_b]
        ax_perp = ax - u_b_active @ (u_b_active.T @ ax)
        b_perp = b_vec - u_b_active @ (u_b_active.T @ b_vec)
        s_xx = ax_perp.T @ ax_perp
        rhs_x = ax_perp.T @ b_perp
        # Confounding loading C = H_bb^+ H_bx mapping x perturbation to compensating bias shift
        if rank_b > 0:
            ab_pinv = (scale_b[:, None] * vt_b[:rank_b].T) @ (
                u_b_active.T / s_b[:rank_b][:, None]
            )
            confounding = ab_pinv @ ax
        else:
            confounding = np.zeros((nb, nx), dtype=float)
    else:
        s_xx = ax.T @ ax
        rhs_x = ax.T @ b_vec
        confounding = np.zeros((0, nx), dtype=float)

    s_xx = 0.5 * (s_xx + s_xx.T)
    eigvals, eigvecs = np.linalg.eigh(s_xx)
    h_tol = max(nx, 1) * np.finfo(float).eps * max(float(eigvals.max()), 1.0) * 100.0
    obs_mask = eigvals > h_tol
    pos_mean = (
        eigvecs[:, obs_mask] @ ((eigvecs[:, obs_mask].T @ rhs_x) / eigvals[obs_mask])
        if np.any(obs_mask)
        else np.zeros(nx, dtype=float)
    )
    inv_eigs = np.where(obs_mask, 1.0 / np.maximum(eigvals, h_tol), float(prior_var_envelope))
    cov_env = (eigvecs * inv_eigs[None, :]) @ eigvecs.T
    return dict(
        schur_info=s_xx,
        covariance_envelope=0.5 * (cov_env + cov_env.T),
        confounding_loading=confounding,
        position_mean=pos_mean,
        rank=int(np.sum(obs_mask)),
        eigenvalues=eigvals,
        eigenvectors=eigvecs,
    )


class EdgeSeparableADMM:
    """Distributed Edge-Separable Consensus ADMM on linear factor graphs (MESA linear engine).

    Strictly separates:
      - Primal optimization penalties (ephemeral Biased Priors per incident edge)
      - Physical Fisher information & uncertainty reporting (permanent physical factors only)
    """

    def __init__(self, node_factors, edges, rho=20.0, adaptive_rho=False, prior_var_envelope=25.0):
        """
        node_factors: dict[node_id -> list[LinearFactor]]
        edges: list of (node_i, node_j) undirected communication links
        """
        self.node_ids = tuple(sorted(node_factors.keys()))
        self.node_factors = {
            nid: deduplicate_idempotent_factors(list(node_factors[nid])) for nid in self.node_ids
        }
        self.adaptive_rho = bool(adaptive_rho)
        self.prior_var_envelope = float(prior_var_envelope)
        self.node_keys = {
            nid: tuple(sorted({k for f in self.node_factors[nid] for k in f.keys}))
            for nid in self.node_ids
        }
        self.node_estimates = {
            nid: {k: 0.0 for k in self.node_keys[nid]} for nid in self.node_ids
        }

        self.edges = []
        self.rho = {}
        self.z_edge = {}
        self.dual = {}
        self.edge_dual_res = {}
        for u, v in edges:
            e = (min(u, v), max(u, v))
            shared = tuple(sorted(set(self.node_keys[e[0]]) & set(self.node_keys[e[1]])))
            if not shared or e in self.rho:
                continue
            self.edges.append(e)
            self.rho[e] = float(rho)
            self.z_edge[e] = np.zeros(len(shared), dtype=float)
            self.dual[(e[0], e)] = np.zeros(len(shared), dtype=float)
            self.dual[(e[1], e)] = np.zeros(len(shared), dtype=float)
            self.edge_dual_res[e] = np.inf
            self._shared_keys_cache_set(e, shared)

    def _shared_keys_cache_set(self, edge, shared):
        if not hasattr(self, "edge_shared_keys"):
            self.edge_shared_keys = {}
        self.edge_shared_keys[edge] = shared

    def add_physical_factor(self, node_id, factor, default_rho=None):
        """Inject a received physical factor idempotently and refresh shared edge variables if needed."""
        self.node_factors[node_id] = deduplicate_idempotent_factors(
            self.node_factors[node_id] + [factor]
        )
        new_keys = tuple(sorted({k for f in self.node_factors[node_id] for k in f.keys}))
        for k in new_keys:
            if k not in self.node_estimates[node_id]:
                self.node_estimates[node_id][k] = 0.0
        self.node_keys[node_id] = new_keys

        # Refresh any incident edge shared key sets
        base_rho = float(default_rho if default_rho is not None else (next(iter(self.rho.values()), 2.0)))
        for idx, (u, v) in enumerate(list(self.edges)):
            if node_id not in (u, v):
                continue
            e = (u, v)
            shared = tuple(sorted(set(self.node_keys[u]) & set(self.node_keys[v])))
            old_shared = self.edge_shared_keys[e]
            if shared != old_shared:
                old_map = {k: i for i, k in enumerate(old_shared)}
                z_new = np.zeros(len(shared), dtype=float)
                d_u = np.zeros(len(shared), dtype=float)
                d_v = np.zeros(len(shared), dtype=float)
                for j, k in enumerate(shared):
                    if k in old_map:
                        oi = old_map[k]
                        z_new[j] = self.z_edge[e][oi]
                        d_u[j] = self.dual[(u, e)][oi]
                        d_v[j] = self.dual[(v, e)][oi]
                    else:
                        z_new[j] = 0.5 * (self.node_estimates[u][k] + self.node_estimates[v][k])
                self.edge_shared_keys[e] = shared
                self.z_edge[e] = z_new
                self.dual[(u, e)] = d_u
                self.dual[(v, e)] = d_v
                if e not in self.rho:
                    self.rho[e] = base_rho

    def _ephemeral_priors_for_node(self, node_id):
        priors = []
        if not hasattr(self, "edge_shared_keys"):
            return priors
        for e in self.edges:
            if node_id not in e:
                continue
            shared = self.edge_shared_keys[e]
            rho_e = self.rho[e]
            sqrt_rho = np.sqrt(rho_e)
            target = self.z_edge[e] - self.dual[(node_id, e)] / rho_e
            priors.append(
                LinearFactor(
                    keys=shared,
                    a=sqrt_rho * np.eye(len(shared), dtype=float),
                    b=sqrt_rho * target,
                    sources=frozenset(),
                    ephemeral=True,
                )
            )
        return priors

    def primal_update_node(self, node_id):
        keys = self.node_keys[node_id]
        factors = self.node_factors[node_id] + self._ephemeral_priors_for_node(node_id)
        res = solve_linear(factors, keys, include_ephemeral=True, prior_var_envelope=self.prior_var_envelope)
        for k, val in zip(keys, res["solution"]):
            self.node_estimates[node_id][k] = float(val)

    def edge_update(self, edge):
        u, v = edge
        shared = self.edge_shared_keys[edge]
        rho_e = self.rho[edge]
        z_prev = self.z_edge[edge]
        theta_u = np.array([self.node_estimates[u][k] for k in shared], dtype=float)
        theta_v = np.array([self.node_estimates[v][k] for k in shared], dtype=float)
        z_new = 0.5 * (
            theta_u + theta_v + (self.dual[(u, edge)] + self.dual[(v, edge)]) / rho_e
        )
        r_prim = float(np.sqrt(np.sum((theta_u - z_new) ** 2) + np.sum((theta_v - z_new) ** 2)))
        s_dual = float(np.sqrt(2.0) * rho_e * np.linalg.norm(z_new - z_prev))
        self.edge_dual_res[edge] = s_dual
        self.z_edge[edge] = z_new
        self.dual[(u, edge)] = self.dual[(u, edge)] + rho_e * (theta_u - z_new)
        self.dual[(v, edge)] = self.dual[(v, edge)] + rho_e * (theta_v - z_new)
        if self.adaptive_rho and s_dual > 0.0:
            if r_prim > 10.0 * s_dual and rho_e < 1e4:
                self.rho[edge] = rho_e * 2.0
            elif s_dual > 10.0 * r_prim and rho_e > 1e-2:
                self.rho[edge] = rho_e * 0.5

    def step_synchronous(self):
        for nid in self.node_ids:
            self.primal_update_node(nid)
        for edge in self.edges:
            self.edge_update(edge)
        return self.consensus_metrics()

    def step_asynchronous_edge(self, edge):
        """Pairwise asynchronous MESA step: only endpoints of `edge` solve & reconcile."""
        u, v = edge
        self.primal_update_node(u)
        self.primal_update_node(v)
        self.edge_update(edge)
        return self.consensus_metrics()

    def consensus_metrics(self):
        primal_sq = 0.0
        dual_sq = 0.0
        max_disagreement = 0.0
        edge_residuals = {}
        for edge in self.edges:
            u, v = edge
            shared = self.edge_shared_keys[edge]
            tu = np.array([self.node_estimates[u][k] for k in shared], dtype=float)
            tv = np.array([self.node_estimates[v][k] for k in shared], dtype=float)
            diff = tu - tv
            norm_diff = float(np.linalg.norm(diff))
            edge_residuals[edge] = norm_diff
            primal_sq += float(np.dot(diff, diff))
            dual_val = float(self.edge_dual_res.get(edge, 0.0))
            dual_sq += dual_val * dual_val
            max_disagreement = max(max_disagreement, norm_diff)
        return dict(
            primal_residual_norm=float(np.sqrt(primal_sq)),
            dual_residual_norm=float(np.sqrt(dual_sq)),
            max_edge_disagreement=max_disagreement,
            edge_residuals=edge_residuals,
        )

    def run(self, max_iters=150, tol=1e-9, rng_async_seed=None):
        history = []
        if rng_async_seed is None:
            for _ in range(max_iters):
                m = self.step_synchronous()
                history.append(m["primal_residual_norm"])
                if (
                    m["primal_residual_norm"] <= tol
                    and m["dual_residual_norm"] <= tol * 10.0
                    and len(history) >= 3
                ):
                    break
        else:
            rng = np.random.default_rng(rng_async_seed)
            if not self.edges:
                for nid in self.node_ids:
                    self.primal_update_node(nid)
            else:
                for _ in range(max_iters * max(len(self.edges), 1)):
                    e = self.edges[int(rng.integers(len(self.edges)))]
                    m = self.step_asynchronous_edge(e)
                    history.append(m["primal_residual_norm"])
                    if (
                        m["primal_residual_norm"] <= tol
                        and m["dual_residual_norm"] <= tol * 20.0
                        and len(history) >= len(self.edges) * 3
                    ):
                        break
        return dict(iterations=len(history), final_residual=history[-1] if history else 0.0, history=history)

    def physical_uncertainty_summary(self):
        """Aggregate unique physical factors across the network to audit honest physical covariance.

        Demonstrates strict accounting separation: ADMM penalty rho NEVER enters covariance!
        """
        all_phys = deduplicate_idempotent_factors(
            [f for nid in self.node_ids for f in self.node_factors[nid] if not f.ephemeral]
        )
        all_keys = tuple(sorted({k for f in all_phys for k in f.keys}))
        return solve_linear(all_phys, all_keys, include_ephemeral=False, prior_var_envelope=self.prior_var_envelope)
