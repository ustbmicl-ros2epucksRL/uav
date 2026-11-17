"""几何与视线遮挡（LoS）计算工具。"""
import numpy as np


def azimuth_elevation(sx, sy, sz, X, Y, Z):
    """从传感器 (sx,sy,sz) 看向体素 (X,Y,Z) 的方位角与俯仰角（度）。"""
    dx, dy, dz = X - sx, Y - sy, Z - sz
    horiz = np.sqrt(dx * dx + dy * dy)
    az = np.degrees(np.arctan2(dx, dy))          # 北=+y → 0°，东=+x → 90°
    el = np.degrees(np.arctan2(dz, np.maximum(horiz, 1e-9)))
    dist = np.sqrt(horiz * horiz + dz * dz)
    return az, el, dist


def ang_diff(a, b):
    """方位角差，归一化到 [-180, 180]。"""
    return (a - b + 180.0) % 360.0 - 180.0


def fov_mask(az, el, sensor):
    """主瓣视场掩码。"""
    if sensor.h_fov >= 360.0:
        az_ok = np.ones_like(az, dtype=bool)
    else:
        az_ok = np.abs(ang_diff(az, sensor.az_center)) <= sensor.h_fov / 2.0
    if sensor.v_fov >= 180.0:
        el_ok = np.ones_like(el, dtype=bool)
    else:
        el_ok = np.abs(el - sensor.el_center) <= sensor.v_fov / 2.0
    return az_ok & el_ok


def los_visible(sensor_pos, targets, grid, env, steps=24, eps=1.0):
    """2.5D 视线遮挡：沿射线步进，被地表/建筑高度阻挡则不可见。

    targets: (M, 3) 体素中心坐标。返回 (M,) 布尔，True 表示可见。
    """
    s = np.asarray(sensor_pos, dtype=float)
    d = np.asarray(targets, dtype=float) - s          # (M,3)
    blocked = np.zeros(len(targets), dtype=bool)
    # 在 (0,1) 之间采样，避开端点（端点即传感器/目标自身）
    for t in np.linspace(0.08, 0.96, steps):
        p = s + t * d                                  # (M,3)
        ix, iy = grid.world_to_index(p[:, 0], p[:, 1])
        hs = env.surface_height(ix, iy)
        blocked |= p[:, 2] < (hs - eps)
    return ~blocked
