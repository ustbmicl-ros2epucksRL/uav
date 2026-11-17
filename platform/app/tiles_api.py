import urllib.request
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from . import store

router = APIRouter()

IMAGERY_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ELEVATION_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def _tile_path(kind, z, x, y, suffix):
    if not 8 <= z <= 16:
        raise HTTPException(422, "缩放级别超出范围")
    limit = 1 << z
    if not (0 <= x < limit and 0 <= y < limit):
        raise HTTPException(422, "瓦片编号超出范围")
    return store.DATA / "map-tiles" / kind / str(z) / str(x) / f"{y}.{suffix}"


def _cached_tile(url, path):
    if path.is_file() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "sensing-plan/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = response.read()
    except Exception as error:
        raise HTTPException(502, "地图瓦片获取失败") from error
    if not data:
        raise HTTPException(502, "地图瓦片为空")
    path.write_bytes(data)
    return path


@router.get("/tiles/imagery/{z}/{x}/{y}")
def imagery_tile(z: int, x: int, y: int):
    path = _cached_tile(IMAGERY_URL.format(z=z, y=y, x=x), _tile_path("imagery", z, x, y, "jpg"))
    return FileResponse(path, media_type="image/jpeg")


@router.get("/tiles/elevation/{z}/{x}/{y}")
def elevation_tile(z: int, x: int, y: int):
    path = _cached_tile(ELEVATION_URL.format(z=z, x=x, y=y), _tile_path("elevation", z, x, y, "png"))
    return FileResponse(path, media_type="image/png")


