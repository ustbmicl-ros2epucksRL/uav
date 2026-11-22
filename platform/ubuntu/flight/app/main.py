from pathlib import Path
import asyncio
from contextlib import asynccontextmanager, AsyncExitStack
from fastapi.staticfiles import StaticFiles
from typing import Literal
from . import mavlink_link, station, route_plan
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from . import follow, sim, business

ROOT = Path(__file__).resolve().parent
@asynccontextmanager
async def lifespan(app):
    async def clock():
        while True:
            await asyncio.sleep(1)
            if station.CLOCK['running']:
                sim.tick_steps(station.CLOCK['speed'])
                if not sim.moving():
                    station.CLOCK['running']=False
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(planning_app.router.lifespan_context(planning_app))
        task=asyncio.create_task(clock())
        try:
            yield
        finally:
            compute_runtime.shutdown()
            sitl_demo.CANCEL.set()
            sitl.stop()
            mavlink_link.stop()
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass

app = FastAPI(title="低空区域飞行管理系统 · 地面站",lifespan=lifespan)
app.include_router(business.router)
from . import incidents
app.include_router(incidents.router)
app.include_router(incidents.airspace_router)
from . import traffic
app.include_router(traffic.router)
from . import patrol
app.include_router(patrol.router)
from . import device_adapter
app.include_router(device_adapter.router)
from . import test_video
app.include_router(test_video.router)
from . import sitl
app.include_router(sitl.router)
from . import sitl_demo
app.include_router(sitl_demo.router)
from . import twin
app.include_router(twin.router)
from . import dynamic_demo
app.include_router(dynamic_demo.router)
from . import algorithm_api
app.include_router(algorithm_api.router)
from . import compute_api, compute_runtime
app.include_router(compute_api.router)
from . import stability_api
app.include_router(stability_api.router)
from . import dual_sitl
app.include_router(dual_sitl.router)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')


@app.middleware("http")
async def revalidate_pages(request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/station"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def mount_planning():
    """感知网络规划与地面站同一进程、同一端口，页面仍分开。"""
    import importlib.util
    import sys
    platform = Path(__file__).resolve().parents[3]
    name = "sensing_plan"
    pkg_dir = platform / "app"
    if f"{name}.main" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, pkg_dir / "__init__.py", submodule_search_locations=[str(pkg_dir)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
        main_spec = importlib.util.spec_from_file_location(
            f"{name}.main", pkg_dir / "main.py")
        module = importlib.util.module_from_spec(main_spec)
        sys.modules[f"{name}.main"] = module
        main_spec.loader.exec_module(module)
    module = sys.modules[f"{name}.main"]
    app.mount("/network", module.app)
    return module.app


planning_app = mount_planning()
from .planning_bridge import router as planning_bridge_router
app.include_router(planning_bridge_router)


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid",allow_inf_nan=False)
    x: float = Field(ge=-500, le=5000)
    y: float = Field(ge=-500, le=5000)
    z: float = Field(ge=0, le=500)


class Mission(BaseModel):
    model_config = ConfigDict(extra="forbid",allow_inf_nan=False)
    waypoints: list[Point] = Field(min_length=2, max_length=20)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid",allow_inf_nan=False)
    cmd: Literal["arm","disarm","start","auto","loiter","guided","rtl","land","stop"]


class Gimbal(BaseModel):
    model_config = ConfigDict(extra="forbid",allow_inf_nan=False)
    pitch: float = Field(ge=-90,le=20)
    yaw: float = Field(ge=-180,le=180)


def vehicle_or_404(vehicle_id):
    vehicle = sim.get(vehicle_id)
    if vehicle is None:
        raise HTTPException(404, "飞机不存在")
    return vehicle


def host_os():
    release = Path("/etc/os-release")
    if release.exists():
        for line in release.read_text(encoding="utf-8").splitlines():
            if line.startswith("NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


@app.get("/health")
def health():
    return {"service": "flight-control", "os": host_os(), "vehicles": len(sim.VEHICLES)}


@app.get("/station")
def station_page(request: Request):
    if "twin_experiment" in request.query_params:
        return FileResponse(ROOT / "static" / "planning-replay.html")
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/")
def index(request: Request):
    # Existing map embeds and deep links open the original station view.
    station_params = {"view", "embed", "source", "record", "task", "report", "transfer",
                      "dual", "assessment", "round", "step", "autoplay", "result"}
    if station_params.intersection(request.query_params) or request.headers.get("sec-fetch-dest") == "iframe":
        return station_page(request)
    return FileResponse(ROOT / "static" / "unified-workspace.html")


@app.post("/api/reset")
def reset():
    station.CLOCK["running"]=False
    station.EVENTS.clear()
    sim.reset()
    return {"ok": True}


@app.get("/api/vehicles")
def list_vehicles():
    return {"vehicles": [sim.snapshot(v) for v in sim.VEHICLES.values()]}


@app.get("/api/vehicles/{vehicle_id}")
def one_vehicle(vehicle_id: str):
    return sim.snapshot(vehicle_or_404(vehicle_id))


@app.get("/api/vehicles/{vehicle_id}/telemetry")
def telemetry(vehicle_id: str):
    return sim.snapshot(vehicle_or_404(vehicle_id))


@app.get("/api/vehicles/{vehicle_id}/track")
def track(vehicle_id: str):
    return sim.track_of(vehicle_or_404(vehicle_id))


@app.post("/api/vehicles/{vehicle_id}/gimbal")
def gimbal(vehicle_id: str, body: Gimbal):
    vehicle = vehicle_or_404(vehicle_id)
    try:
        sim.set_gimbal(vehicle, body.pitch, body.yaw)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return sim.snapshot(vehicle)


@app.post("/api/vehicles/{vehicle_id}/mission")
def upload(vehicle_id: str, mission: Mission):
    vehicle = vehicle_or_404(vehicle_id)
    try:
        with sim.LOCK:
            business.require_unowned(vehicle)
            sim.load_mission(vehicle, [[p.x, p.y, p.z] for p in mission.waypoints])
        station.ack(vehicle_id,"mission",f"航线上传成功 · {len(mission.waypoints)} 个航点")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return sim.snapshot(vehicle)


@app.post("/api/vehicles/{vehicle_id}/command")
def command(vehicle_id: str, body: Command):
    vehicle = vehicle_or_404(vehicle_id)
    try:
        with sim.LOCK:
            if body.cmd in {'arm', 'start', 'auto'}:
                business.require_unowned(vehicle)
            if body.cmd in {'rtl', 'land'}:
                from . import disposition
                with business.connect() as db:
                    space = incidents.airspace(db)
                buildings = route_plan.scene_record(getattr(vehicle, 'business_scene', 'urban'))['buildings']
                goal = list(vehicle.home) if body.cmd == 'rtl' else [*vehicle.position[:2], 0]
                result = disposition.check([vehicle.position, goal], disposition.obstacles(buildings, space['zones']), sim.FENCE, vehicle.speed, vehicle.battery)
                if not result['passed']:
                    raise HTTPException(409, dict(message='直接处置航线不安全；请在预警详情检查替代方案', check=result))
            sim.command(vehicle, body.cmd)
        if body.cmd=="stop":
            station.CLOCK["running"]=False
        station.ack(vehicle_id,"command",f"指令 {body.cmd} 已执行，模式 {vehicle.mode}")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return sim.snapshot(vehicle)


@app.post("/api/tick")
def tick(steps: int = 1, vehicle_id: str | None = None):
    if station.CLOCK["running"]:raise HTTPException(409,"实时仿真正在运行，请先暂停再手动推进")
    if steps < 1 or steps > 500:
        raise HTTPException(422, "steps 应为 1–500")
    if vehicle_id and sim.get(vehicle_id) is None:
        raise HTTPException(404, "飞机不存在")
    sim.tick_steps(steps, vehicle_id)
    return {"vehicles": [sim.snapshot(v) for v in sim.VEHICLES.values()]}


@app.get('/api/station')
def station_metadata():
    return {'name':'低空平台 · 地面站','mode':'simulation','map':station.config(),
            'clock':station.CLOCK,'coordinate_frame':'local ENU / WGS84 illustrative anchor',
            'transport':{'active':'simulator','mavlink_connected':mavlink_link.status()['connected'],'mavlink_commands':False},
            'map_library':{'name':'Leaflet','version':'1.9.4','license':'BSD-2-Clause'},
            'capabilities':['fleet','map','mission-editor','obstacle-route','telemetry','attitude','geofence','gimbal-footprint','track-replay','command-ack','target-follow'],
            'limits':{'waypoints':20,'low_battery_percent':20,'fence':sim.FENCE}}

@app.put('/api/station/map')
def map_configuration(body:station.MapConfig):
    if any(v.mode in {'AUTO','RTL','LAND'} for v in sim.VEHICLES.values()):
        raise HTTPException(409,'请先结束飞行再变更坐标锚点')
    station.save_config(body);return body

@app.get('/api/station/state')
def station_state():
    anchor=station.config();vehicles=[]
    for v in sim.VEHICLES.values():
        data=sim.snapshot(v);data['geographic_position']=station.geo(data['position'],anchor)
        data['speed_mps']=v.speed if v.mode in {'AUTO','RTL','LAND'} else 0
        vehicles.append(data)
    return {'vehicles':vehicles,'clock':station.CLOCK,'events':list(station.EVENTS),'mavlink':mavlink_link.status(),'follow':follow.public()}


class MavlinkConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(default='', max_length=80)
    listen: bool = False


@app.get('/api/mavlink')
def mavlink_status():
    return mavlink_link.status()


@app.put('/api/mavlink')
def mavlink_configure(body: MavlinkConfig):
    try:
        return mavlink_link.configure(body.url, body.listen)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

class SimulationClock(BaseModel):
    model_config=ConfigDict(extra='forbid')
    running:bool
    speed:Literal[1,2,5]=1

@app.put('/api/simulation')
def simulation_clock(body:SimulationClock):
    station.CLOCK.update(body.model_dump());return station.CLOCK

class RouteGoal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    goal_x: float = Field(ge=0, le=2000)
    goal_y: float = Field(ge=0, le=2000)
    altitude_m: float = Field(ge=30, le=300)
    scene: Literal["urban", "corridor", "electromagnetic"] = "urban"


@app.get("/api/obstacles")
def obstacles(scene: Literal["urban", "corridor", "electromagnetic"] = "urban"):
    record = route_plan.scene_record(scene)
    return {"scene": scene, "name": record["name"], "buildings": record["buildings"],
            "note": "合成建筑体块，用于航线绕障，不是测绘障碍。"}


@app.post("/api/vehicles/{vehicle_id}/mission/plan")
def plan_mission(vehicle_id: str, body: RouteGoal):
    vehicle = vehicle_or_404(vehicle_id)
    try:
        record = route_plan.scene_record(body.scene)
        result = route_plan.plan(vehicle.home[:2], (body.goal_x, body.goal_y), body.altitude_m,
                                 record["buildings"], sim.FENCE)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    result["scene"] = body.scene
    result["scene_name"] = record["name"]
    result["waypoints"] = [{"x": p[0], "y": p[1], "z": p[2]} for p in result["waypoints"]]
    return result


@app.post('/api/vehicles/{vehicle_id}/mission/preview')
def preview_mission(vehicle_id:str, mission:Mission,
                    scene: Literal["urban", "corridor", "electromagnetic"] | None = None):
    v=vehicle_or_404(vehicle_id)
    result = station.preview([[p.x,p.y,p.z] for p in mission.waypoints],v.position,v.speed,sim.FENCE)
    if scene:
        buildings = route_plan.scene_record(scene)["buildings"]
        flown = [tuple(v.position)] + [(p.x, p.y, p.z) for p in mission.waypoints]
        result["building_hits"] = route_plan.crossings(flown, buildings)
        result["scene"] = scene
        result["note"] = "直线航段及恒速估算。已检查所选场景的合成建筑；未检查空域许可或动力学可飞性。"
    return result


@app.get('/api/device-integration')
def device_integration():
    """Capability declaration, never an implicit switch to hardware control."""
    return {
        'active_control': 'simulator',
        'sitl_adapter': {'status':'/api/sitl','commands':'/api/sitl/commands','mission':'/api/sitl/patrol/upload','managed_local_only':True},
        'test_video': test_video.status(),
        'simulation_adapter': {'telemetry':'/api/devices/{vehicle_id}/telemetry','commands':'/api/devices/{vehicle_id}/commands','video_sessions':'/api/devices/{vehicle_id}/video/sessions'},
        'flight_controller': {
            'telemetry_endpoint': '/api/mavlink',
            'connection_method': 'PUT /api/mavlink',
            'received_fields': ['heartbeat', 'system_id', 'armed', 'latitude', 'longitude', 'relative_altitude_m','roll_deg','pitch_deg','yaw_deg','groundspeed_mps','climb_mps','battery_percent','voltage_v','gps_fix','satellites'],
            'status': mavlink_link.status(),
            'real_commands_available': False,
            'command_contract': {'method': 'POST', 'path': '/api/hardware/{vehicle_id}/commands',
                                 'implemented': False,
                                 'required': ['command_id', 'vehicle_id', 'system_id', 'command', 'parameters', 'expires_at']},
        },
        'video': {
            'available': False, 'source': None,
            'session_contract': {'method': 'POST', 'path': '/api/video/{vehicle_id}/sessions', 'implemented': False},
            'planned_browser_transports': ['WebRTC', 'HLS'],
            'note': 'RTSP requires a media gateway; no real camera stream is configured.',
        },
    }

from . import flight_records
app.include_router(flight_records.router)

from . import sitl_business
app.include_router(sitl_business.router)

# One product entrance; the service module is mounted on the same origin.
def mount_service():
    import importlib.util
    import sys
    name = 'lowair_service'
    directory = Path(__file__).resolve().parents[2] / 'lowair' / 'app'
    if name + '.main' not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, directory / '__init__.py', submodule_search_locations=[str(directory)])
        package = importlib.util.module_from_spec(spec);sys.modules[name] = package;spec.loader.exec_module(package)
        spec = importlib.util.spec_from_file_location(name + '.main', directory / 'main.py')
        module = importlib.util.module_from_spec(spec);sys.modules[name + '.main'] = module;spec.loader.exec_module(module)
    app.mount('/service', sys.modules[name + '.main'].app)
mount_service()
