"""统一时空三维网格架构（支持分区变分辨率：广域≤10m、重点区≤2m）。

为简洁，网格采用本地 ENU 米坐标系，经纬高 → 本地坐标的转换由数据层完成。
"""
from dataclasses import dataclass, field
import numpy as np


@dataclass
class Grid3D:
    nx: int                     # x 方向体素数
    ny: int                     # y 方向体素数
    nz: int                     # z 方向体素数
    cell: float = 10.0          # 体素边长（米），广域≤10m，重点区可另建≤2m子网格
    origin: tuple = (0.0, 0.0, 0.0)   # 本地坐标原点（米）
    z_air_min: float = 30.0     # 低空空域下界（米，AGL 近似）
    z_air_max: float = 300.0    # 低空空域上界（米）
    _centers: tuple = field(default=None, repr=False)

    @property
    def shape(self):
        return (self.nx, self.ny, self.nz)

    def centers(self):
        """返回体素中心坐标 X, Y, Z，形状均为 (nx, ny, nz)。带缓存。"""
        if self._centers is None:
            ox, oy, oz = self.origin
            xs = ox + (np.arange(self.nx) + 0.5) * self.cell
            ys = oy + (np.arange(self.ny) + 0.5) * self.cell
            zs = oz + (np.arange(self.nz) + 0.5) * self.cell
            X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
            self._centers = (X, Y, Z)
        return self._centers

    def airspace_mask(self):
        """低空空域体素掩码（仅在该范围内统计覆盖/盲区）。"""
        _, _, Z = self.centers()
        return (Z >= self.z_air_min) & (Z <= self.z_air_max)

    def world_to_index(self, x, y):
        """世界坐标 (x, y) → 网格水平索引（用于地表高度查询），自动裁剪。"""
        ox, oy, _ = self.origin
        ix = np.clip(((np.asarray(x) - ox) / self.cell).astype(int), 0, self.nx - 1)
        iy = np.clip(((np.asarray(y) - oy) / self.cell).astype(int), 0, self.ny - 1)
        return ix, iy

    def voxel_volume(self):
        return self.cell ** 3
