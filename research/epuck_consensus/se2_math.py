"""Planar R^2 x SO(2) manifold operations, Wheel+2D-IMU preintegration, UWB factors, and FixedSE2Prior."""

from dataclasses import dataclass
import numpy as np

J_CROSS = np.array([[0.0, -1.0], [1.0, 0.0]], dtype=float)


def wrap_angle(theta):
    """Wrap angle(s) to (-pi, pi]."""
    return np.arctan2(np.sin(theta), np.cos(theta))


def rot2(theta):
    """2D rotation matrix for scalar angle theta."""
    c = float(np.cos(theta))
    s = float(np.sin(theta))
    return np.array([[c, -s], [s, c]], dtype=float)


def se2_retract(pose, delta):
    """Retract pose = [x, y, theta] on R^2 x SO(2) by tangent increment delta = [dx, dy, dtheta]."""
    pose = np.asarray(pose, dtype=float)
    delta = np.asarray(delta, dtype=float)
    if pose.shape != (3,) or delta.shape != (3,):
        raise ValueError("Pose and increment must have shape (3,)")
    return np.array([
        pose[0] + delta[0],
        pose[1] + delta[1],
        float(wrap_angle(pose[2] + delta[2])),
    ], dtype=float)


def se2_minus(target, reference, check_boundary=True):
    """Chart difference target (-) reference on R^2 x SO(2)."""
    target = np.asarray(target, dtype=float)
    reference = np.asarray(reference, dtype=float)
    if target.shape != (3,) or reference.shape != (3,):
        raise ValueError("Target and reference poses must have shape (3,)")
    dtheta = float(wrap_angle(target[2] - reference[2]))
    if check_boundary and abs(dtheta) > np.pi - 1e-4:
        raise ValueError("Angle difference reaches SO(2) principal chart cut")
    return np.array([target[0] - reference[0], target[1] - reference[1], dtheta], dtype=float)


def huber_weight_and_cost(e, delta=2.5):
    """Return (sqrt_weight, scalar_cost) for standardized scalar residual e."""
    abs_e = abs(float(e))
    if abs_e <= delta:
        return 1.0, 0.5 * abs_e * abs_e
    w = delta / max(abs_e, 1e-12)
    return float(np.sqrt(w)), float(delta * (abs_e - 0.5 * delta))


@dataclass
class WheelIMUFactor:
    """Preintegrated e-puck wheel-encoder + 2D MEMS gyro factor between two SE(2) poses."""

    pose_a_key: str
    pose_b_key: str
    gyro_bias_key: str
    dt_total: float
    nom_gyro_bias: float
    delta_p: np.ndarray
    delta_theta: float
    jac_bg: np.ndarray
    whitening: np.ndarray
    sources: frozenset

    def corrected(self, gyro_bias):
        dbg = float(gyro_bias) - self.nom_gyro_bias
        dp = self.delta_p + self.jac_bg[:2] * dbg
        dth = self.delta_theta + float(self.jac_bg[2]) * dbg
        return dp, dth

    def predict(self, pose_a, gyro_bias):
        pose_a = np.asarray(pose_a, dtype=float)
        dp, dth = self.corrected(gyro_bias)
        p_b = pose_a[:2] + rot2(pose_a[2]) @ dp
        th_b = float(wrap_angle(pose_a[2] + dth))
        return np.array([p_b[0], p_b[1], th_b], dtype=float)

    def evaluate(self, pose_a, pose_b, gyro_bias):
        """Return whitened residual (3,) and analytical Jacobians (J_xi_a (3,3), J_xi_b (3,3), J_bg (3,1))."""
        pose_a = np.asarray(pose_a, dtype=float)
        pose_b = np.asarray(pose_b, dtype=float)
        dp, dth = self.corrected(gyro_bias)
        rt = rot2(pose_a[2]).T
        diff_p = pose_b[:2] - pose_a[:2]
        err = np.empty(3, dtype=float)
        err[:2] = rt @ diff_p - dp
        err[2] = float(wrap_angle(pose_b[2] - pose_a[2] - dth))

        j_a = np.zeros((3, 3), dtype=float)
        j_b = np.zeros((3, 3), dtype=float)
        j_a[:2, :2] = -rt
        j_a[:2, 2] = -(J_CROSS @ (rt @ diff_p))
        j_a[2, 2] = -1.0

        j_b[:2, :2] = rt
        j_b[2, 2] = 1.0

        j_bg = -self.jac_bg[:, None]
        w = self.whitening
        return w @ err, (w @ j_a, w @ j_b, w @ j_bg)


def preintegrate_wheel_imu(
    samples,
    pose_a_key,
    pose_b_key,
    gyro_bias_key,
    nom_gyro_bias=0.0,
    vel_noise_density=0.04,
    lat_noise_density=0.015,
    gyro_noise_density=0.015,
    source_id="odom:0",
):
    """Preintegrate sequence of [dt, v_fwd, v_lat, omega_m] samples on R^2 x SO(2).

    Uses midpoint heading integration with exact analytical sensitivity wrt gyro bias.
    """
    samples = np.asarray(samples, dtype=float)
    if samples.ndim != 2 or samples.shape[1] != 4 or len(samples) == 0:
        raise ValueError("Samples must have shape (K, 4): [dt, v_fwd, v_lat, omega_m]")
    if np.any(samples[:, 0] <= 0.0) or not np.isfinite(samples).all():
        raise ValueError("Invalid preintegration sample durations or values")

    dp = np.zeros(2, dtype=float)
    dth = 0.0
    j_bg = np.zeros(3, dtype=float)
    cov = np.zeros((3, 3), dtype=float)
    elapsed = 0.0

    q_step_diag = np.array([vel_noise_density**2, lat_noise_density**2, gyro_noise_density**2], dtype=float)

    for dt, v_fwd, v_lat, omega_m in samples:
        v_body = np.array([v_fwd, v_lat], dtype=float)
        omega = float(omega_m) - float(nom_gyro_bias)
        mid_th = dth + 0.5 * omega * dt
        r_mid = rot2(mid_th)

        # Sensitivity of mid_th wrt gyro_bias is -(elapsed + 0.5 * dt)
        d_midth_dbg = -(elapsed + 0.5 * dt)
        j_bg[:2] += (r_mid @ (J_CROSS @ v_body)) * d_midth_dbg * dt
        j_bg[2] -= dt

        # First-order discrete error covariance propagation on [dp_x, dp_y, dtheta]
        a_mat = np.eye(3, dtype=float)
        a_mat[:2, 2] = (r_mid @ (J_CROSS @ v_body)) * dt
        b_mat = np.zeros((3, 3), dtype=float)
        b_mat[:2, :2] = r_mid * dt
        b_mat[:2, 2] = 0.5 * (r_mid @ (J_CROSS @ v_body)) * (dt * dt)
        b_mat[2, 2] = dt
        # Continuous-to-discrete noise scaling: variance of average rate over dt is density^2 / dt,
        # so b_mat @ diag(density^2 / dt) @ b_mat.T scales linearly with dt.
        q_rate = np.diag(q_step_diag / dt)
        cov = a_mat @ cov @ a_mat.T + b_mat @ q_rate @ b_mat.T

        dp += (r_mid @ v_body) * dt
        dth += omega * dt
        elapsed += dt

    cov = 0.5 * (cov + cov.T) + 1e-12 * np.eye(3)
    chol = np.linalg.cholesky(cov)
    whitening = np.linalg.solve(chol, np.eye(3))
    return WheelIMUFactor(
        pose_a_key=pose_a_key,
        pose_b_key=pose_b_key,
        gyro_bias_key=gyro_bias_key,
        dt_total=float(elapsed),
        nom_gyro_bias=float(nom_gyro_bias),
        delta_p=dp,
        delta_theta=float(dth),
        jac_bg=j_bg,
        whitening=whitening,
        sources=frozenset({source_id}),
    )


@dataclass
class AnchorRangeFactor:
    """Sparse UWB anchor range factor with robot lever arm and optional shared bias variable."""

    pose_key: str
    anchor_pos: np.ndarray
    measurement: float
    sigma: float
    lever_arm: np.ndarray
    bias_key: str | None = None
    bias_loading: float = 1.0
    robust_delta: float | None = None
    sources: frozenset = frozenset()

    def __post_init__(self):
        self.anchor_pos = np.asarray(self.anchor_pos, dtype=float).copy()
        self.lever_arm = np.asarray(self.lever_arm, dtype=float).copy()
        self.sources = frozenset(self.sources)
        if self.anchor_pos.shape != (2,) or self.lever_arm.shape != (2,) or self.sigma <= 0.0:
            raise ValueError("Invalid AnchorRangeFactor geometry or sigma")
        if not self.sources:
            raise ValueError("AnchorRangeFactor requires non-empty sources")

    def evaluate(self, pose, bias_val=0.0):
        """Return (irls_residual (1,), cost_scalar, J_pose (1,3), J_bias (1,1) or None)."""
        pose = np.asarray(pose, dtype=float)
        r_mat = rot2(pose[2])
        tag_pos = pose[:2] + r_mat @ self.lever_arm
        diff = tag_pos - self.anchor_pos
        dist = float(np.sqrt(np.dot(diff, diff) + 1e-18))
        los = diff / dist
        pred = dist + (self.bias_loading * float(bias_val) if self.bias_key is not None else 0.0)
        e = (pred - self.measurement) / self.sigma

        if self.robust_delta is not None:
            sqrt_w, cost = huber_weight_and_cost(e, self.robust_delta)
        else:
            sqrt_w, cost = 1.0, 0.5 * e * e

        d_tag_dtheta = r_mat @ (J_CROSS @ self.lever_arm)
        j_pose = np.array([[los[0], los[1], float(np.dot(los, d_tag_dtheta))]], dtype=float) * (sqrt_w / self.sigma)
        j_bias = (
            np.array([[self.bias_loading * sqrt_w / self.sigma]], dtype=float)
            if self.bias_key is not None
            else None
        )
        return np.array([sqrt_w * e], dtype=float), cost, j_pose, j_bias


@dataclass
class InterRobotRangeFactor:
    """Peer-to-peer UWB range factor coupling two robots' SE(2) poses and optional shared bias."""

    pose_i_key: str
    pose_j_key: str
    measurement: float
    sigma: float
    lever_i: np.ndarray
    lever_j: np.ndarray
    bias_key: str | None = None
    bias_loading: float = 1.0
    robust_delta: float | None = None
    sources: frozenset = frozenset()

    def __post_init__(self):
        self.lever_i = np.asarray(self.lever_i, dtype=float).copy()
        self.lever_j = np.asarray(self.lever_j, dtype=float).copy()
        self.sources = frozenset(self.sources)
        if self.lever_i.shape != (2,) or self.lever_j.shape != (2,) or self.sigma <= 0.0:
            raise ValueError("Invalid InterRobotRangeFactor lever arm or sigma")
        if not self.sources:
            raise ValueError("InterRobotRangeFactor requires non-empty sources")

    def evaluate(self, pose_i, pose_j, bias_val=0.0):
        pose_i = np.asarray(pose_i, dtype=float)
        pose_j = np.asarray(pose_j, dtype=float)
        ri = rot2(pose_i[2])
        rj = rot2(pose_j[2])
        qi = pose_i[:2] + ri @ self.lever_i
        qj = pose_j[:2] + rj @ self.lever_j
        diff = qi - qj
        dist = float(np.sqrt(np.dot(diff, diff) + 1e-18))
        los = diff / dist
        pred = dist + (self.bias_loading * float(bias_val) if self.bias_key is not None else 0.0)
        e = (pred - self.measurement) / self.sigma

        if self.robust_delta is not None:
            sqrt_w, cost = huber_weight_and_cost(e, self.robust_delta)
        else:
            sqrt_w, cost = 1.0, 0.5 * e * e

        scale = sqrt_w / self.sigma
        ji = np.array([[los[0], los[1], float(np.dot(los, ri @ (J_CROSS @ self.lever_i)))]], dtype=float) * scale
        jj = np.array([[-los[0], -los[1], -float(np.dot(los, rj @ (J_CROSS @ self.lever_j)))]], dtype=float) * scale
        jb = np.array([[self.bias_loading * scale]], dtype=float) if self.bias_key is not None else None
        return np.array([sqrt_w * e], dtype=float), cost, ji, jj, jb


class FixedSE2Prior:
    """Fixed-reference square-root prior over an arbitrary list of SE(2) poses and scalar variables.

    Residual is r(X) = A @ (X (-) X_ref) - b.
    Because SO(2) is Abelian, d/d(delta) ((X (+) delta) (-) X_ref) = I everywhere inside (-pi, pi).
    """

    def __init__(self, var_specs, ref_values, a_mat, b_vec, sources, ephemeral=False):
        """
        var_specs: tuple of (key, kind) where kind in ('se2', 'scalar')
        ref_values: dict mapping key -> np.ndarray(3,) for 'se2' or float for 'scalar'
        a_mat: (m, total_dim) square-root information matrix
        b_vec: (m,) right-hand side target in r = a_mat @ coords - b_vec
        """
        self.var_specs = tuple(var_specs)
        self.ephemeral = bool(ephemeral)
        self.sources = frozenset(sources)
        if not self.ephemeral and not self.sources:
            raise ValueError("Permanent physical prior must retain non-empty source provenance")
        self.ref_values = {}
        total_dim = 0
        for key, kind in self.var_specs:
            if kind == "se2":
                val = np.asarray(ref_values[key], dtype=float).copy()
                if val.shape != (3,) or not np.isfinite(val).all():
                    raise ValueError(f"Invalid SE(2) reference for {key}")
                self.ref_values[key] = val
                total_dim += 3
            elif kind == "scalar":
                val = float(ref_values[key])
                if not np.isfinite(val):
                    raise ValueError(f"Invalid scalar reference for {key}")
                self.ref_values[key] = val
                total_dim += 1
            else:
                raise ValueError(f"Unknown variable kind: {kind}")

        self.a_mat = np.array(a_mat, dtype=float, copy=True)
        self.b_vec = np.array(b_vec, dtype=float, copy=True)
        if self.a_mat.ndim != 2 or self.a_mat.shape[1] != total_dim or self.b_vec.shape != (self.a_mat.shape[0],):
            raise ValueError("Mismatched FixedSE2Prior matrix/vector dimensions")
        if not np.isfinite(self.a_mat).all() or not np.isfinite(self.b_vec).all():
            raise ValueError("Nonfinite FixedSE2Prior parameters")

    @property
    def keys(self):
        return tuple(k for k, _ in self.var_specs)

    def local_coordinates(self, current_values, check_boundary=True):
        parts = []
        for key, kind in self.var_specs:
            if kind == "se2":
                parts.append(se2_minus(current_values[key], self.ref_values[key], check_boundary=check_boundary))
            else:
                parts.append(np.array([float(current_values[key]) - self.ref_values[key]], dtype=float))
        return np.concatenate(parts) if parts else np.zeros(0, dtype=float)

    def evaluate(self, current_values, check_boundary=True):
        coords = self.local_coordinates(current_values, check_boundary=check_boundary)
        res = self.a_mat @ coords - self.b_vec
        return res, 0.5 * float(np.dot(res, res)), self.a_mat.copy()
