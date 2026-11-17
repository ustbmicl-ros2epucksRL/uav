"""个体设备三维覆盖数学模型（按模态分发）。

通用流程：距离筛选 → 主瓣视场筛选 → 模态衰减/阈值 → 视线遮挡（按需）。
返回布尔体素掩码，形状 = grid.shape。
"""
import numpy as np
from . import geometry as geo


def _base_coverage(sensor, grid, env, attenuate=None):
    """通用覆盖：距离 + 视场 + （可选衰减）+ LoS。"""
    X, Y, Z = grid.centers()
    sx, sy, sz = sensor.pos
    az, el, dist = geo.azimuth_elevation(sx, sy, sz, X, Y, Z)

    cand = (dist >= sensor.min_range) & (dist <= sensor.max_range)
    cand &= geo.fov_mask(az, el, sensor)

    if attenuate is not None:                 # 模态相关的有效作用距离修正
        cand &= attenuate(dist)

    result = np.zeros(grid.shape, dtype=bool)
    if not cand.any():
        return result

    if sensor.need_los:
        idx = np.argwhere(cand)               # (M,3) 候选体素索引
        targets = np.stack([X[cand], Y[cand], Z[cand]], axis=1)
        vis = geo.los_visible(sensor.pos, targets, grid, env)
        keep = idx[vis]
        result[keep[:, 0], keep[:, 1], keep[:, 2]] = True
    else:
        result[cand] = True
    return result


# ---------- 光电（视场锥 + 能见度衰减 + 视线遮挡） ----------
def coverage_eo(sensor, grid, env):
    eff_range = min(sensor.max_range, env.visibility_km * 1000.0)
    return _base_coverage(sensor, grid, env, attenuate=lambda d: d <= eff_range)


# ---------- 雷达（雷达方程→有效距离 + 降雨衰减 + 视线遮挡） ----------
def coverage_radar(sensor, grid, env):
    # 简化雷达方程：有效距离 ∝ (RCS)^(1/4)，基准 RCS=1 m²
    rcs = float(sensor.extra.get("rcs_m2", 1.0))
    eff = sensor.max_range * (rcs ** 0.25)
    if env.rain_db_per_km > 0:                 # 降雨使有效距离按 dB 预算缩减
        eff *= 10 ** (-(env.rain_db_per_km) / 40.0)
    return _base_coverage(sensor, grid, env, attenuate=lambda d: d <= eff)


# ---------- 无线电（链路预算阈值，绕射，不强制视线） ----------
def coverage_rf(sensor, grid, env):
    # 自由空间路径损耗阈值已折算为 max_range；此处仅做距离+全向扇区
    return _base_coverage(sensor, grid, env)


# ---------- ADS-B/报文（球形接收范围，不需视线） ----------
def coverage_adsb(sensor, grid, env):
    return _base_coverage(sensor, grid, env)


COVERAGE_MODELS = {
    "EO": coverage_eo,
    "RADAR": coverage_radar,
    "RF": coverage_rf,
    "ADSB": coverage_adsb,
}


def coverage_single(sensor, grid, env):
    """单设备三维覆盖（单塔/单设备独立覆盖边界）。"""
    if sensor.status != "online":
        return np.zeros(grid.shape, dtype=bool)
    return COVERAGE_MODELS[sensor.type](sensor, grid, env)
