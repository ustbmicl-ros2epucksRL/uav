"""Nonlinear R^2 x SO(2) x R^q Multi-Robot Factor Graph with Edge-Separable Consensus ADMM."""

import copy
import numpy as np

from .linear_consensus import LinearFactor, marginalize_linear
from .se2_math import (
    AnchorRangeFactor,
    FixedSE2Prior,
    InterRobotRangeFactor,
    WheelIMUFactor,
    se2_minus,
    se2_retract,
    wrap_angle,
)


class RobotNodeGraph:
    """Local factor graph for a single e-puck robot, supporting sliding-window SVD marginalization
    and ephemeral ADMM Biased Priors without contaminating permanent physical history.
    """

    def __init__(self, robot_id, lever_arm=(0.03, 0.0)):
        self.robot_id = int(robot_id)
        self.lever_arm = np.asarray(lever_arm, dtype=float)
        self.var_kinds = {}
        self.values = {}
        self.odom_factors: list[WheelIMUFactor] = []
        self.anchor_factors: list[AnchorRangeFactor] = []
        self.p2p_factors: list[InterRobotRangeFactor] = []
        self.permanent_priors: list[FixedSE2Prior] = []
        self.ephemeral_admm_priors: list[FixedSE2Prior] = []
        self.seen_sources = set()

    def ensure_se2(self, key, init_pose):
        if key not in self.var_kinds:
            self.var_kinds[key] = "se2"
            self.values[key] = np.asarray(init_pose, dtype=float).copy()

    def ensure_scalar(self, key, init_val=0.0):
        if key not in self.var_kinds:
            self.var_kinds[key] = "scalar"
            self.values[key] = float(init_val)

    def _register_sources(self, sources):
        overlap = self.seen_sources & sources
        if overlap:
            raise ValueError(f"Robot {self.robot_id} duplicate physical source: {sorted(overlap)}")
        self.seen_sources.update(sources)

    def add_prior(self, prior: FixedSE2Prior):
        if prior.ephemeral:
            self.ephemeral_admm_priors.append(prior)
            return
        if prior.sources <= self.seen_sources:
            return
        self._register_sources(prior.sources)
        for k, kind in prior.var_specs:
            if kind == "se2":
                self.ensure_se2(k, prior.ref_values[k])
            else:
                self.ensure_scalar(k, prior.ref_values[k])
        self.permanent_priors.append(prior)

    def add_odom(self, factor: WheelIMUFactor):
        self._register_sources(factor.sources)
        self.odom_factors.append(factor)

    def add_anchor_range(self, factor: AnchorRangeFactor):
        if factor.sources <= self.seen_sources:
            return
        self._register_sources(factor.sources)
        if factor.bias_key is not None:
            self.ensure_scalar(factor.bias_key, 0.0)
        self.anchor_factors.append(factor)

    def add_p2p_range(self, factor: InterRobotRangeFactor, neighbor_init_pose=None):
        if factor.sources <= self.seen_sources:
            return
        self._register_sources(factor.sources)
        if neighbor_init_pose is not None:
            if factor.pose_i_key not in self.var_kinds:
                self.ensure_se2(factor.pose_i_key, neighbor_init_pose)
            if factor.pose_j_key not in self.var_kinds:
                self.ensure_se2(factor.pose_j_key, neighbor_init_pose)
        if factor.bias_key is not None:
            self.ensure_scalar(factor.bias_key, 0.0)
        self.p2p_factors.append(factor)

    def clear_ephemeral_admm(self):
        self.ephemeral_admm_priors.clear()

    def variable_layout(self, keys=None):
        ordered = tuple(sorted(self.var_kinds.keys())) if keys is None else tuple(keys)
        offsets = {}
        dims = {}
        cursor = 0
        for k in ordered:
            kind = self.var_kinds[k]
            d = 3 if kind == "se2" else 1
            offsets[k] = slice(cursor, cursor + d)
            dims[k] = d
            cursor += d
        return ordered, offsets, dims, cursor

    def assemble(self, values=None, include_ephemeral=True, subset_factors=None):
        """Assemble total nonlinear cost, stacked whitened residual r, and Jacobian J."""
        vals = self.values if values is None else values
        ordered, offsets, _, total_dim = self.variable_layout()
        rows_r = []
        rows_j = []
        total_cost = 0.0

        def append_block(res_vec, cost_val, key_jac_pairs):
            nonlocal total_cost
            total_cost += float(cost_val)
            rows_r.append(res_vec)
            row = np.zeros((len(res_vec), total_dim), dtype=float)
            for k, j_mat in key_jac_pairs:
                if j_mat is not None:
                    row[:, offsets[k]] += j_mat
            rows_j.append(row)

        priors = list(self.permanent_priors)
        if include_ephemeral:
            priors.extend(self.ephemeral_admm_priors)

        for p in priors:
            if subset_factors is not None and id(p) not in subset_factors:
                continue
            res, cost, j_full = p.evaluate(vals)
            pairs = []
            c = 0
            for k, kind in p.var_specs:
                d = 3 if kind == "se2" else 1
                pairs.append((k, j_full[:, c : c + d]))
                c += d
            append_block(res, cost, pairs)

        for f in self.odom_factors:
            if subset_factors is not None and id(f) not in subset_factors:
                continue
            res, (ja, jb, jbg) = f.evaluate(vals[f.pose_a_key], vals[f.pose_b_key], vals[f.gyro_bias_key])
            append_block(
                res,
                0.5 * float(np.dot(res, res)),
                [(f.pose_a_key, ja), (f.pose_b_key, jb), (f.gyro_bias_key, jbg)],
            )

        for f in self.anchor_factors:
            if subset_factors is not None and id(f) not in subset_factors:
                continue
            b_val = vals[f.bias_key] if f.bias_key is not None else 0.0
            res, cost, jp, jb = f.evaluate(vals[f.pose_key], b_val)
            pairs = [(f.pose_key, jp)]
            if f.bias_key is not None:
                pairs.append((f.bias_key, jb))
            append_block(res, cost, pairs)

        for f in self.p2p_factors:
            if subset_factors is not None and id(f) not in subset_factors:
                continue
            b_val = vals[f.bias_key] if f.bias_key is not None else 0.0
            res, cost, ji, jj, jb = f.evaluate(vals[f.pose_i_key], vals[f.pose_j_key], b_val)
            pairs = [(f.pose_i_key, ji), (f.pose_j_key, jj)]
            if f.bias_key is not None:
                pairs.append((f.bias_key, jb))
            append_block(res, cost, pairs)

        if not rows_r:
            return 0.0, np.zeros(0, dtype=float), np.zeros((0, total_dim), dtype=float), ordered, offsets
        return float(total_cost), np.concatenate(rows_r), np.vstack(rows_j), ordered, offsets

    def optimize_local(self, max_iters=6, damping=1e-6):
        """Damped Gauss-Newton with backtracking line search on R^2 x SO(2) x R^q."""
        accepted = 0
        for _ in range(max_iters):
            old_cost, res, jac, ordered, offsets = self.assemble(include_ephemeral=True)
            if jac.shape[0] == 0:
                break
            n_dim = jac.shape[1]
            aug_a = np.vstack([jac, np.sqrt(damping) * np.eye(n_dim)])
            aug_b = np.concatenate([-res, np.zeros(n_dim)])
            # Column-equilibrated least squares step
            norms = np.linalg.norm(aug_a, axis=0)
            scale = np.where(norms > 0.0, 1.0 / norms, 1.0)
            z_step = np.linalg.lstsq(aug_a * scale[None, :], aug_b, rcond=None)[0]
            delta = scale * z_step
            if np.linalg.norm(delta) < 1e-8:
                break

            step_ok = False
            for alpha in (1.0, 0.5, 0.25, 0.125, 0.0625):
                trial = {}
                for k in ordered:
                    sl = offsets[k]
                    d_k = alpha * delta[sl]
                    if self.var_kinds[k] == "se2":
                        d_k = np.clip(d_k, [-1.0, -1.0, -0.5], [1.0, 1.0, 0.5])
                        trial[k] = se2_retract(self.values[k], d_k)
                    else:
                        trial[k] = float(self.values[k] + np.clip(d_k[0], -2.0, 2.0))
                try:
                    new_cost, _, _, _, _ = self.assemble(values=trial, include_ephemeral=True)
                except ValueError:
                    continue
                if np.isfinite(new_cost) and new_cost <= old_cost + 1e-9:
                    self.values = trial
                    accepted += 1
                    step_ok = True
                    break
            if not step_ok:
                break
        return accepted

    def marginalize_private_pose(self, pose_key, protected_shared_keys=frozenset()):
        """Eliminate an old private SE(2) pose into a FixedSE2Prior via column-equilibrated SVD.

        Refuses to eliminate variables participating in cross-robot ADMM consensus, and strictly
        excludes all ephemeral ADMM priors from entering the permanent historical prior.
        """
        if pose_key in protected_shared_keys:
            raise ValueError(f"Cannot marginalize shared ADMM variable: {pose_key}")
        if self.var_kinds.get(pose_key) != "se2":
            raise ValueError(f"Variable {pose_key} is not an active SE(2) pose")

        # Collect only permanent physical factors touching pose_key
        touched_priors = [p for p in self.permanent_priors if pose_key in p.keys]
        touched_odom = [f for f in self.odom_factors if pose_key in (f.pose_a_key, f.pose_b_key)]
        touched_anc = [f for f in self.anchor_factors if f.pose_key == pose_key]
        touched_p2p = [f for f in self.p2p_factors if pose_key in (f.pose_i_key, f.pose_j_key)]
        all_touched = touched_priors + touched_odom + touched_anc + touched_p2p
        subset_ids = {id(obj) for obj in all_touched}
        if not subset_ids:
            del self.var_kinds[pose_key]
            del self.values[pose_key]
            return None

        involved_keys = set()
        combined_sources = set()
        for p in touched_priors:
            involved_keys.update(p.keys)
            combined_sources.update(p.sources)
        for f in touched_odom:
            involved_keys.update([f.pose_a_key, f.pose_b_key, f.gyro_bias_key])
            combined_sources.update(f.sources)
        for f in touched_anc:
            involved_keys.add(f.pose_key)
            if f.bias_key is not None:
                involved_keys.add(f.bias_key)
            combined_sources.update(f.sources)
        for f in touched_p2p:
            involved_keys.update([f.pose_i_key, f.pose_j_key])
            if f.bias_key is not None:
                involved_keys.add(f.bias_key)
            combined_sources.update(f.sources)

        retain_vars = tuple(sorted(involved_keys - {pose_key}))
        _, res, jac_full, ordered, offsets = self.assemble(
            include_ephemeral=False, subset_factors=subset_ids
        )

        # Build scalar coordinate keys for SVD marginalize_linear
        def expand_coord_keys(vkeys):
            coords = []
            for vk in vkeys:
                if self.var_kinds[vk] == "se2":
                    coords.extend([(vk, 0), (vk, 1), (vk, 2)])
                else:
                    coords.append((vk, 0))
            return tuple(coords)

        elim_coords = expand_coord_keys((pose_key,))
        retain_coords = expand_coord_keys(retain_vars)
        all_vars = (pose_key,) + retain_vars
        cols = [jac_full[:, offsets[vk]] for vk in all_vars]
        a_sub = np.hstack(cols)
        # Around current linearization point X_0, r(X) ≈ res + a_sub @ (X (-) X_0) = a_sub @ dx - (-res)
        lin_factor = LinearFactor(
            keys=elim_coords + retain_coords,
            a=a_sub,
            b=-res,
            sources=frozenset(combined_sources),
            ephemeral=False,
        )
        prior_lin, info = marginalize_linear([lin_factor], elim_coords, retain_coords)

        var_specs = tuple((vk, self.var_kinds[vk]) for vk in retain_vars)
        ref_vals = {vk: copy.deepcopy(self.values[vk]) for vk in retain_vars}
        new_prior = FixedSE2Prior(
            var_specs=var_specs,
            ref_values=ref_vals,
            a_mat=prior_lin.a,
            b_vec=prior_lin.b,
            sources=prior_lin.sources,
            ephemeral=False,
        )

        self.permanent_priors = [p for p in self.permanent_priors if id(p) not in subset_ids] + [new_prior]
        self.odom_factors = [f for f in self.odom_factors if id(f) not in subset_ids]
        self.anchor_factors = [f for f in self.anchor_factors if id(f) not in subset_ids]
        self.p2p_factors = [f for f in self.p2p_factors if id(f) not in subset_ids]
        del self.var_kinds[pose_key]
        del self.values[pose_key]
        return info

    def physical_covariance(self, target_keys, prior_var_envelope=25.0):
        """Compute honest linearized covariance envelope using ONLY permanent physical factors."""
        _, _, jac_phys, ordered, offsets = self.assemble(include_ephemeral=False)
        n_dim = jac_phys.shape[1]
        h_phys = jac_phys.T @ jac_phys if jac_phys.shape[0] > 0 else np.zeros((n_dim, n_dim))
        eigvals, eigvecs = np.linalg.eigh(0.5 * (h_phys + h_phys.T))
        tol = max(n_dim, 1) * np.finfo(float).eps * max(float(eigvals.max()), 1.0) * 100.0
        obs = eigvals > tol
        inv_eigs = np.where(obs, 1.0 / np.maximum(eigvals, tol), float(prior_var_envelope))
        cov_full = (eigvecs * inv_eigs[None, :]) @ eigvecs.T
        idx = []
        for k in target_keys:
            sl = offsets[k]
            idx.extend(range(sl.start, sl.stop))
        return cov_full[np.ix_(idx, idx)]


class MultiRobotSE2ADMM:
    """Manifold Edge-based Separable ADMM for multi-e-puck SE(2) + Shared-Bias Factor Graphs."""

    def __init__(
        self,
        robot_graphs: dict[int, RobotNodeGraph],
        edges,
        rho_pos=40.0,
        rho_ang=80.0,
        rho_bias=45.0,
        adaptive_rho=False,
    ):
        self.robot_ids = tuple(sorted(robot_graphs.keys()))
        self.graphs = robot_graphs
        self.adaptive_rho = bool(adaptive_rho)

        self.edges = []
        self.edge_vars = {}
        self.edge_scale = {}
        self.z_edge = {}
        self.dual = {}
        self.edge_dual_res = {}
        self.base_rho_pos = float(rho_pos)
        self.base_rho_ang = float(rho_ang)
        self.base_rho_bias = float(rho_bias)
        for u, v in edges:
            e = (min(u, v), max(u, v))
            if e in self.edge_vars or u == v:
                continue
            shared = tuple(sorted(set(self.graphs[e[0]].var_kinds) & set(self.graphs[e[1]].var_kinds)))
            if not shared:
                continue
            self.edges.append(e)
            self.edge_vars[e] = shared
            self.edge_scale[e] = 1.0
            self.edge_dual_res[e] = np.inf
            self.z_edge[e] = {}
            self.dual[(e[0], e)] = {}
            self.dual[(e[1], e)] = {}
            for k in shared:
                kind = self.graphs[e[0]].var_kinds[k]
                if kind == "se2":
                    pu = self.graphs[e[0]].values[k]
                    pv = self.graphs[e[1]].values[k]
                    mid_th = float(wrap_angle(pu[2] + 0.5 * wrap_angle(pv[2] - pu[2])))
                    self.z_edge[e][k] = np.array([0.5 * (pu[0] + pv[0]), 0.5 * (pu[1] + pv[1]), mid_th])
                    self.dual[(e[0], e)][k] = np.zeros(3, dtype=float)
                    self.dual[(e[1], e)][k] = np.zeros(3, dtype=float)
                else:
                    su = float(self.graphs[e[0]].values[k])
                    sv = float(self.graphs[e[1]].values[k])
                    self.z_edge[e][k] = 0.5 * (su + sv)
                    self.dual[(e[0], e)][k] = 0.0
                    self.dual[(e[1], e)][k] = 0.0

    def shared_keys_for_robot(self, rid):
        out = set()
        for e in self.edges:
            if rid in e:
                out.update(self.edge_vars[e])
        return frozenset(out)

    def _rebuild_ephemeral_priors(self, rid):
        g = self.graphs[rid]
        g.clear_ephemeral_admm()
        for e in self.edges:
            if rid not in e:
                continue
            sc = self.edge_scale[e]
            rp, ra, rb = self.base_rho_pos * sc, self.base_rho_ang * sc, self.base_rho_bias * sc
            for k in self.edge_vars[e]:
                kind = g.var_kinds[k]
                z_val = self.z_edge[e][k]
                lam = self.dual[(rid, e)][k]
                if kind == "se2":
                    rho_vec = np.array([rp, rp, ra])
                    weights = np.sqrt(rho_vec)
                    a_mat = np.diag(weights)
                    b_vec = -lam / weights
                    g.add_prior(
                        FixedSE2Prior(
                            var_specs=((k, "se2"),),
                            ref_values={k: z_val},
                            a_mat=a_mat,
                            b_vec=b_vec,
                            sources=frozenset(),
                            ephemeral=True,
                        )
                    )
                else:
                    sqrt_rb = np.sqrt(rb)
                    a_mat = np.array([[sqrt_rb]], dtype=float)
                    b_vec = np.array([-float(lam) / sqrt_rb], dtype=float)
                    g.add_prior(
                        FixedSE2Prior(
                            var_specs=((k, "scalar"),),
                            ref_values={k: float(z_val)},
                            a_mat=a_mat,
                            b_vec=b_vec,
                            sources=frozenset(),
                            ephemeral=True,
                        )
                    )

    def _update_edge(self, e):
        u, v = e
        gu, gv = self.graphs[u], self.graphs[v]
        sc = self.edge_scale[e]
        rp, ra, rb = self.base_rho_pos * sc, self.base_rho_ang * sc, self.base_rho_bias * sc
        prim_sq = 0.0
        dual_sq = 0.0
        for k in self.edge_vars[e]:
            kind = gu.var_kinds[k]
            z_old = self.z_edge[e][k]
            if kind == "se2":
                rho_vec = np.array([rp, rp, ra])
                xu = gu.values[k]
                xv = gv.values[k]
                lu = self.dual[(u, e)][k]
                lv = self.dual[(v, e)][k]
                su_p = xu[:2] + lu[:2] / rho_vec[:2]
                sv_p = xv[:2] + lv[:2] / rho_vec[:2]
                su_th = float(wrap_angle(xu[2] + lu[2] / rho_vec[2]))
                sv_th = float(wrap_angle(xv[2] + lv[2] / rho_vec[2]))
                z_th = float(wrap_angle(su_th + 0.5 * wrap_angle(sv_th - su_th)))
                z_new = np.array([0.5 * (su_p[0] + sv_p[0]), 0.5 * (su_p[1] + sv_p[1]), z_th])
                du = se2_minus(xu, z_new, check_boundary=False)
                dv = se2_minus(xv, z_new, check_boundary=False)
                dz = se2_minus(z_new, z_old, check_boundary=False)
                prim_sq += float(np.dot(du, du) + np.dot(dv, dv))
                dual_sq += 2.0 * float(np.sum((rho_vec * dz) ** 2))
                self.z_edge[e][k] = z_new
                self.dual[(u, e)][k] = lu + rho_vec * du
                self.dual[(v, e)][k] = lv + rho_vec * dv
            else:
                xu = float(gu.values[k])
                xv = float(gv.values[k])
                lu = float(self.dual[(u, e)][k])
                lv = float(self.dual[(v, e)][k])
                z_new = 0.5 * (xu + xv + (lu + lv) / rb)
                prim_sq += (xu - z_new) ** 2 + (xv - z_new) ** 2
                dual_sq += 2.0 * (rb * (z_new - float(z_old))) ** 2
                self.z_edge[e][k] = z_new
                self.dual[(u, e)][k] = lu + rb * (xu - z_new)
                self.dual[(v, e)][k] = lv + rb * (xv - z_new)
        r_p = float(np.sqrt(prim_sq))
        s_d = float(np.sqrt(dual_sq))
        self.edge_dual_res[e] = s_d
        if self.adaptive_rho and s_d > 0.0:
            if r_p > 10.0 * s_d and sc < 64.0:
                self.edge_scale[e] = sc * 2.0
            elif s_d > 10.0 * r_p and sc > 1.0 / 64.0:
                self.edge_scale[e] = sc * 0.5

    def step_synchronous(self, inner_gn_iters=4):
        for rid in self.robot_ids:
            self._rebuild_ephemeral_priors(rid)
            self.graphs[rid].optimize_local(max_iters=inner_gn_iters)
        for e in self.edges:
            self._update_edge(e)
        return self.consensus_disagreement()

    def consensus_disagreement(self):
        sq_sum = 0.0
        dual_sq = 0.0
        max_diff = 0.0
        for e in self.edges:
            u, v = e
            dual_sq += float(self.edge_dual_res.get(e, 0.0)) ** 2
            for k in self.edge_vars[e]:
                if self.graphs[u].var_kinds[k] == "se2":
                    d = se2_minus(self.graphs[u].values[k], self.graphs[v].values[k], check_boundary=False)
                    nrm = float(np.linalg.norm(d))
                else:
                    nrm = abs(float(self.graphs[u].values[k]) - float(self.graphs[v].values[k]))
                sq_sum += nrm * nrm
                max_diff = max(max_diff, nrm)
        return dict(
            primal_residual=float(np.sqrt(sq_sum)),
            dual_residual=float(np.sqrt(dual_sq)),
            max_disagreement=max_diff,
        )

    def run(self, rounds=40, inner_gn_iters=4, tol=1e-6):
        history = []
        for _ in range(rounds):
            m = self.step_synchronous(inner_gn_iters=inner_gn_iters)
            history.append(m["primal_residual"])
            if m["primal_residual"] <= tol and m["dual_residual"] <= tol * 50.0 and len(history) >= 3:
                break
        return dict(rounds=len(history), final_residual=history[-1] if history else 0.0, history=history)
