"""异构感知设备统一描述与参数建档（统一导入 光电/雷达/无线电/报文）。"""
from dataclasses import dataclass, field
from typing import Literal
import json

SensorType = Literal["EO", "RADAR", "RF", "ADSB"]


@dataclass
class Sensor:
    sid: str
    type: SensorType
    pos: tuple                      # 挂载点本地坐标 (x, y, z)，z=铁塔挂载高度+地面高程
    az_center: float = 0.0          # 主瓣方位中心（度，北=0，顺时针）
    el_center: float = 10.0         # 主瓣俯仰中心（度）
    h_fov: float = 360.0            # 水平视场（度），360=全向
    v_fov: float = 30.0             # 垂直视场（度）
    max_range: float = 3000.0       # 最大作用距离（米）
    min_range: float = 0.0          # 最小作用距离（米）
    need_los: bool = True           # 是否需视线（光电/雷达需要，RF/报文绕射可不需）
    status: str = "online"          # online / offline / test
    extra: dict = field(default_factory=dict)   # 频段/功率/灵敏度/RCS 等差异化参数

    # ---- 各模态的工程默认参数模板 ----
    @classmethod
    def make(cls, sid, type, pos, **kw):
        defaults = {
            "EO":    dict(h_fov=60, v_fov=40, max_range=2000, el_center=8,  need_los=True),
            "RADAR": dict(h_fov=120, v_fov=40, max_range=5000, el_center=10, need_los=True),
            "RF":    dict(h_fov=360, v_fov=90, max_range=8000, el_center=0,  need_los=False),
            "ADSB":  dict(h_fov=360, v_fov=90, max_range=20000, el_center=0, need_los=False),
        }[type]
        defaults.update(kw)
        return cls(sid=sid, type=type, pos=tuple(pos), **defaults)


def import_sensors(payload):
    """统一导入接口：接收 dict 列表或 JSON 字符串，返回 Sensor 列表。

    每条记录至少包含 sid, type, pos；其余字段缺省走模态模板。
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    sensors = []
    for r in payload:
        r = dict(r)
        sid, stype, pos = r.pop("sid"), r.pop("type"), r.pop("pos")
        sensors.append(Sensor.make(sid, stype, pos, **r))
    return sensors
