"""前端可渲染格式导出：设备覆盖圆顶、覆盖热力层(GeoJSON)、盲区/补点(GeoJSON)。

设计取舍：
- 三维覆盖体（百万级体素）不直接序列化，而是导出"设备覆盖圆顶"参数，由前端
  Cesium 用 ellipsoid 图元渲染——这正是图中"单设备/融合覆盖图"的彩色半球效果，
  既轻量又直观。
- 覆盖热力层、盲区、补点、重点区导出为 GeoJSON，Cesium 原生 GeoJsonDataSource 可读。
- 超大规模（真三维体表面）可后续接入 glTF / 3D Tiles，本接口预留升级位。
"""
import numpy as np
from .georef import enu_to_lonlat

# 各模态颜色（与图中一致：光电橙、雷达红、无线电紫、报文绿）
TYPE_COLOR = {"EO": "#ff8c00", "RADAR": "#e23030", "RF": "#8a2be2", "ADSB": "#1fbf6b"}


def device_domes(sensors, lon0=None, lat0=None):
    """设备覆盖圆顶参数（供前端 Cesium ellipsoid 渲染）。"""
    kw = _geo_kw(lon0, lat0)
    out = []
    for s in sensors:
        lon, lat = enu_to_lonlat(s.pos[0], s.pos[1], **kw)
        out.append({
            "sid": s.sid, "type": s.type, "status": s.status,
            "lon": lon, "lat": lat, "alt": float(s.pos[2]),
            "radius": float(s.max_range),
            "az_center": s.az_center, "h_fov": s.h_fov,
            "color": TYPE_COLOR.get(s.type, "#ffffff"),
        })
    return out


def heatmap_geojson(hm, grid, lon0=None, lat0=None, only_positive=True):
    """全域覆盖热力层 → GeoJSON 栅格面，属性 value 为归一化覆盖叠加度。"""
    kw = _geo_kw(lon0, lat0)
    ox, oy, _ = grid.origin
    cell = grid.cell
    vmax = float(hm.max()) or 1.0
    feats = []
    for ix in range(grid.nx):
        for iy in range(grid.ny):
            v = float(hm[ix, iy])
            if only_positive and v <= 0:
                continue
            x0, y0 = ox + ix * cell, oy + iy * cell
            x1, y1 = x0 + cell, y0 + cell
            ring = [enu_to_lonlat(x0, y0, **kw), enu_to_lonlat(x1, y0, **kw),
                    enu_to_lonlat(x1, y1, **kw), enu_to_lonlat(x0, y1, **kw),
                    enu_to_lonlat(x0, y0, **kw)]
            feats.append({
                "type": "Feature",
                "properties": {"value": round(v / vmax, 3)},
                "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]},
            })
    return {"type": "FeatureCollection", "features": feats}


def blind_geojson(regions, lon0=None, lat0=None):
    """盲区与补点建议 → GeoJSON 点（含补设备类型/数量/说明）。"""
    kw = _geo_kw(lon0, lat0)
    feats = []
    for r in regions:
        cx, cy, cz = r.centroid_world
        lon, lat = enu_to_lonlat(cx, cy, **kw)
        feats.append({
            "type": "Feature",
            "properties": {
                "rid": r.rid, "volume_m3": r.volume_m3, "alt": cz,
                "suggest_type": r.suggest_type, "suggest_count": r.suggest_count,
                "note": r.note, "color": TYPE_COLOR.get(r.suggest_type, "#ffffff"),
            },
            "geometry": {"type": "Point", "coordinates": [lon, lat, cz]},
        })
    return {"type": "FeatureCollection", "features": feats}


def coverage_voxels(union, count, grid, lon0=None, lat0=None,
                    stride=4, max_points=20000):
    """真实融合覆盖体素 → 降采样三维点云（供前端 Cesium 点云渲染）。

    每点带归一化重叠度 cn（用于着色：低=蓝、高=红），相比"圆顶近似"能真实
    反映覆盖体形态与交叉增强区。stride 控制降采样密度，max_points 控制上限。
    """
    kw = _geo_kw(lon0, lat0)
    air = grid.airspace_mask()
    mask = union & air
    idx = np.argwhere(mask)
    if len(idx) == 0:
        return {"points": [], "count_max": 0}

    sub = (idx[:, 0] % stride == 0) & (idx[:, 1] % stride == 0) & (idx[:, 2] % stride == 0)
    idx = idx[sub]
    if len(idx) > max_points:                       # 再均匀抽稀到上限
        idx = idx[:: int(np.ceil(len(idx) / max_points))]

    X, Y, Z = grid.centers()
    i, j, k = idx[:, 0], idx[:, 1], idx[:, 2]
    lon, lat = enu_to_lonlat(X[i, j, k], Y[i, j, k], **kw)
    alt = Z[i, j, k]
    cnt = count[i, j, k].astype(int)
    cmax = int(count.max()) or 1
    pts = [{"lon": float(lo), "lat": float(la), "alt": float(al),
            "c": int(c), "cn": round(c / cmax, 3)}
           for lo, la, al, c in zip(lon, lat, alt, cnt)]
    return {"points": pts, "count_max": cmax}


def coverage_isosurface(union, grid, lon0=None, lat0=None, ds=2, smooth=1.0):
    """真实融合覆盖体 → 等值面三角网格（marching cubes），输出实体表面。

    返回 positions=[lon,lat,alt,...]（展平）、indices=[...]（三角面），前端用
    Cesium 自定义 Geometry 渲染半透明实体覆盖体。ds 为降采样倍率（控制网格规模）。
    """
    from skimage import measure
    from scipy import ndimage

    kw = _geo_kw(lon0, lat0)
    vol = union[::ds, ::ds, ::ds].astype(np.float32)
    if vol.sum() < 8 or vol.size - vol.sum() < 8:
        return {"positions": [], "indices": [], "n_vertices": 0, "n_faces": 0}
    if smooth > 0:
        vol = ndimage.gaussian_filter(vol, sigma=smooth)

    spacing = (grid.cell * ds, grid.cell * ds, grid.cell * ds)
    verts, faces, _, _ = measure.marching_cubes(vol, level=0.5, spacing=spacing)

    ox, oy, oz = grid.origin
    wx, wy, wz = verts[:, 0] + ox, verts[:, 1] + oy, verts[:, 2] + oz
    lon, lat = enu_to_lonlat(wx, wy, **kw)

    positions = np.empty(verts.shape[0] * 3, dtype=float)
    positions[0::3] = lon
    positions[1::3] = lat
    positions[2::3] = wz
    return {"positions": positions.tolist(),
            "indices": faces.astype(int).ravel().tolist(),
            "n_vertices": int(verts.shape[0]), "n_faces": int(faces.shape[0])}


def zone_geojson(box, lon0=None, lat0=None):
    """重点防控区矩形 → GeoJSON 多边形。box=(x0,y0,x1,y1)。"""
    kw = _geo_kw(lon0, lat0)
    x0, y0, x1, y1 = box
    ring = [enu_to_lonlat(x0, y0, **kw), enu_to_lonlat(x1, y0, **kw),
            enu_to_lonlat(x1, y1, **kw), enu_to_lonlat(x0, y1, **kw),
            enu_to_lonlat(x0, y0, **kw)]
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"name": "重点防控区"},
        "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}}]}


def _geo_kw(lon0, lat0):
    kw = {}
    if lon0 is not None:
        kw["lon0"] = lon0
    if lat0 is not None:
        kw["lat0"] = lat0
    return kw
