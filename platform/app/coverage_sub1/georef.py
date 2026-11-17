"""本地 ENU 米坐标 → 经纬度（小范围等距近似），用于前端 Cesium 渲染。

默认原点取雄安新区附近，可按真实站址数据替换。
"""
import math

# 默认地理原点（雄安新区，示例值，可替换为真实测绘原点）
DEFAULT_LON0 = 115.95
DEFAULT_LAT0 = 39.00
_R = 6378137.0  # WGS84 长半轴


def enu_to_lonlat(x, y, lon0=DEFAULT_LON0, lat0=DEFAULT_LAT0):
    """x=东向(m), y=北向(m) → (lon, lat) 度。"""
    dlat = (y / _R) * (180.0 / math.pi)
    dlon = (x / (_R * math.cos(math.radians(lat0)))) * (180.0 / math.pi)
    return lon0 + dlon, lat0 + dlat
