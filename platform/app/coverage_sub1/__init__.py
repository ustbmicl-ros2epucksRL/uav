"""
子模块一：多模态三维感知覆盖数字映射与可视化（计算内核）

内置时空三维网格架构与个体设备覆盖数学模型，支持异构设备参数导入与
复杂物理环境映射；输出单塔独立覆盖、多塔交叉互补覆盖、环境遮挡盲区，
以及全域覆盖热力层、重点防控区覆盖率、三维盲区位置与补点补设备建议。
"""

from .grid import Grid3D
from .environment import Environment
from .sensors import Sensor
from .models import coverage_single, COVERAGE_MODELS
from .fusion import coverage_fusion
from .blind import diagnose_blind
from .metrics import coverage_rate, coverage_heatmap

__all__ = [
    "Grid3D", "Environment", "Sensor",
    "coverage_single", "COVERAGE_MODELS",
    "coverage_fusion", "diagnose_blind",
    "coverage_rate", "coverage_heatmap",
]
