import copy
from fastapi import HTTPException
from . import store
from .scenes import SCENES, seed
from .sensing import execute

DEFAULT_RULES={"zone":{"x0":700,"y0":700,"x1":1300,"y1":1300,"z0":30,"z1":300},
               "altitude_limit":185,"loiter_radius":250,"interference":True}
DEFAULT_REGISTRY={"DEMO-LOGISTICS-01":{"status":"approved","route_y":900,"route_tolerance":120}}
CACHE={}


def scene_for(scene_id):
    if scene_id not in {s["id"] for s in SCENES}:
        raise HTTPException(404,"场景不存在")
    return store.get(f'scene:{scene_id}',seed(scene_id))


def settings(scene_id):
    return store.get(f'rules:{scene_id}',copy.deepcopy(DEFAULT_RULES)),store.get(f'registry:{scene_id}',copy.deepcopy(DEFAULT_REGISTRY))


def current(scene_id,mode="fusion"):
    scene=scene_for(scene_id);rules,registry=settings(scene_id)
    key=(scene_id,mode,scene["revision"],str(rules),str(registry))
    if key not in CACHE:
        CACHE.clear() if len(CACHE)>12 else None
        CACHE[key]=execute(scene,rules,registry,mode)
    return copy.deepcopy(CACHE[key])


