"""复杂物理环境映射：地形 + 建筑 + 铁塔，融合为 2.5D 地表高度图。

地表高度图 surface[ix, iy] 表示该水平网格处的最高遮挡面高程（地形 + 建筑），
用于视线遮挡（LoS）判定。气象等要素以衰减系数形式作用于覆盖模型。
"""
from dataclasses import dataclass, field
import numpy as np


@dataclass
class Environment:
    surface: np.ndarray                 # 地表高度图 (nx, ny)，米
    visibility_km: float = 15.0         # 能见度（影响光电）
    rain_db_per_km: float = 0.0         # 降雨衰减（影响雷达/RF），dB/km
    _meta: dict = field(default_factory=dict)

    @classmethod
    def synthetic(cls, grid, seed=7, n_buildings=12):
        """生成示例环境：缓变地形 + 若干建筑块（演示/测试用）。"""
        rng = np.random.default_rng(seed)
        nx, ny = grid.nx, grid.ny
        xs = np.linspace(0, np.pi, nx)
        ys = np.linspace(0, np.pi, ny)
        terrain = 10.0 * np.outer(np.sin(xs), np.sin(ys))   # 0~10m 缓坡
        surface = terrain.copy()
        for _ in range(n_buildings):
            cx, cy = rng.integers(5, nx - 5), rng.integers(5, ny - 5)
            w = rng.integers(2, 6)
            h = rng.uniform(40, 120)                         # 建筑高度 40~120m
            surface[max(0, cx - w):cx + w, max(0, cy - w):cy + w] = (
                terrain[cx, cy] + h
            )
        return cls(surface=surface, _meta={"n_buildings": n_buildings})

    def surface_height(self, ix, iy):
        return self.surface[ix, iy]
