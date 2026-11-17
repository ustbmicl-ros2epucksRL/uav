"""盲区诊断与补点补设备建议。

盲区 = 低空空域内未被任何设备覆盖的体素；三维连通聚类后逐块给出
位置、规模、所属重点区覆盖情况，以及补点位置与设备类型建议。
"""
from dataclasses import dataclass, asdict
import numpy as np
from scipy import ndimage


@dataclass
class BlindRegion:
    rid: int
    centroid_world: tuple        # 盲区质心世界坐标 (x, y, z)，米
    voxel_count: int
    volume_m3: float
    bbox_world: tuple            # (xmin,ymin,zmin, xmax,ymax,zmax)
    suggest_type: str            # 建议补设备类型
    suggest_count: int           # 建议补设备数量
    note: str


def _suggest(centroid, voxel_count, voxels_per_device):
    """简单补点规则：按盲区高度与规模建议设备类型与数量。"""
    z = centroid[2]
    if z >= 150:                 # 中高空盲区：远程雷达更合适
        stype, note = "RADAR", "中高空盲区，建议增设远程雷达提升探测距离"
    elif z >= 80:
        stype, note = "RF", "中空盲区，建议增设无线电监测补盲（绕射能力强）"
    else:                        # 低空近地、易受遮挡：光电+无线电
        stype, note = "EO", "近地遮挡盲区，建议增设光电并配合无线电监测"
    count = int(np.ceil(voxel_count / max(voxels_per_device, 1)))
    return stype, max(count, 1), note


def diagnose_blind(fusion_union, grid, env, voxels_per_device=400, min_region=8):
    """返回盲区块列表（已按规模降序）。"""
    air = grid.airspace_mask()
    blind = air & (~fusion_union)

    structure = ndimage.generate_binary_structure(3, 2)   # 26-连通
    labels, n = ndimage.label(blind, structure=structure)

    X, Y, Z = grid.centers()
    regions = []
    for rid in range(1, n + 1):
        mask = labels == rid
        cnt = int(mask.sum())
        if cnt < min_region:
            continue
        cx, cy, cz = float(X[mask].mean()), float(Y[mask].mean()), float(Z[mask].mean())
        bbox = (float(X[mask].min()), float(Y[mask].min()), float(Z[mask].min()),
                float(X[mask].max()), float(Y[mask].max()), float(Z[mask].max()))
        stype, scount, note = _suggest((cx, cy, cz), cnt, voxels_per_device)
        regions.append(BlindRegion(
            rid=rid, centroid_world=(round(cx, 1), round(cy, 1), round(cz, 1)),
            voxel_count=cnt, volume_m3=round(cnt * grid.voxel_volume(), 1),
            bbox_world=tuple(round(v, 1) for v in bbox),
            suggest_type=stype, suggest_count=scount, note=note,
        ))
    regions.sort(key=lambda r: r.voxel_count, reverse=True)
    return regions


def regions_to_dict(regions):
    return [asdict(r) for r in regions]
