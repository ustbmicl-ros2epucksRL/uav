"""观测段落对一个已声明问题的增量。分数只由记录计算。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

NEAR_M = 50.0


class Segment(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    holder: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    kind: Literal['position', 'identity', 'coverage']
    t0: float = Field(ge=0, le=86400)
    t1: float = Field(ge=0, le=86400)
    x: float | None = Field(default=None, ge=0, le=2000)
    y: float | None = Field(default=None, ge=0, le=2000)
    z: float | None = Field(default=None, ge=0, le=300)
    nbytes: int = Field(ge=1, le=20_000_000)


def _overlaps(a: Segment, b: Segment) -> bool:
    return a.t0 <= b.t1 and b.t0 <= a.t1


def _near(a: Segment, b: Segment) -> bool:
    if None in (a.x, a.y, a.z, b.x, b.y, b.z):
        return False
    return (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2 <= NEAR_M ** 2


def increment(segment: Segment, kind: str, t0: float, t1: float, held: list[Segment]) -> float:
    if segment.t1 < segment.t0 or segment.kind != kind or segment.t1 < t0 or segment.t0 > t1:
        return 0.0
    if kind == 'position':
        if None in (segment.x, segment.y, segment.z):
            return 0.0
        if any(h.kind == 'position' and _overlaps(segment, h) and _near(segment, h) for h in held):
            return 0.0
        return 1.0
    if any(h.kind == kind and _overlaps(segment, h) and h.holder == segment.holder for h in held):
        return 0.0
    return 1.0


def select(segments: list[Segment], kind: str, t0: float, t1: float, budget_bytes: int) -> dict:
    held: list[Segment] = []
    pool = list(segments)
    chosen = []
    remaining = budget_bytes
    while True:
        scored = []
        for segment in pool:
            gain = increment(segment, kind, t0, t1, held)
            if gain > 0 and segment.nbytes <= remaining:
                scored.append((gain / segment.nbytes, segment))
        if not scored:
            break
        scored.sort(key=lambda item: (-item[0], item[1].id))
        best = scored[0][1]
        held.append(best)
        pool.remove(best)
        remaining -= best.nbytes
        chosen.append({'id': best.id, 'holder': best.holder, 'kind': best.kind,
                       'increment': 1.0, 'nbytes': best.nbytes})
    return {'chosen': chosen, 'used_bytes': budget_bytes - remaining, 'budget_bytes': budget_bytes,
            'near_m': NEAR_M, 'evidence_status': 'record_only'}
