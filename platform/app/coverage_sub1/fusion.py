"""多设备融合覆盖（多塔交叉互补 + 重叠增强分析）。"""
import numpy as np
from .models import coverage_single


def coverage_fusion(sensors, grid, env, by_type=False):
    """融合覆盖。

    返回 dict：
      union   : 融合覆盖布尔体素（多塔交叉互补 = 并集）
      count   : 重叠度（≥2 为交叉增强区）
      per_type: {type: 该模态并集}（by_type=True 时）
      singles : {sid: 单设备覆盖}
    """
    union = np.zeros(grid.shape, dtype=bool)
    count = np.zeros(grid.shape, dtype=np.int16)
    singles, per_type = {}, {}

    for s in sensors:
        m = coverage_single(s, grid, env)
        singles[s.sid] = m
        union |= m
        count += m.astype(np.int16)
        if by_type:
            per_type.setdefault(s.type, np.zeros(grid.shape, dtype=bool))
            per_type[s.type] |= m

    return {"union": union, "count": count, "per_type": per_type, "singles": singles}
