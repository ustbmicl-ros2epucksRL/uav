"""覆盖指标与可视化输出：全域覆盖热力层、重点防控区覆盖率。"""
import numpy as np


def coverage_rate(union, grid, zone_mask=None):
    """空域覆盖率（可限定在重点防控区 zone_mask 内）。"""
    air = grid.airspace_mask()
    region = air & zone_mask if zone_mask is not None else air
    total = int(region.sum())
    if total == 0:
        return 0.0
    return float((union & region).sum()) / total


def coverage_heatmap(count, grid):
    """全域覆盖热力层：空域内沿高度方向叠加重叠度，投影为 (nx, ny)。"""
    air = grid.airspace_mask()
    return (count * air).sum(axis=2).astype(float)


def zone_box_mask(grid, x0, y0, x1, y1):
    """根据世界坐标矩形生成重点防控区水平掩码（广播到三维）。"""
    X, Y, _ = grid.centers()
    return (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)
