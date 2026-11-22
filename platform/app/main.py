"""Application composition; HTTP routes and domain logic live in separate modules."""
from contextlib import asynccontextmanager
import platform
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from . import store
from .scene_service import scene_for, CACHE, DEFAULT_RULES, DEFAULT_REGISTRY
from .core_api import router as core_router, Replay
from .engineering_api import router as engineering_router
from .tiles_api import router as tiles_router
from .planning_portal import router as planning_portal_router
from .demonstration import router as demonstration_router
from .research_analysis import router as research_analysis_router
from .research_agent import router as research_agent_router
from .baseline_comparison import router as baseline_comparison_router
from .jobs_api import router as jobs_router
from .cabinet_demo import router as cabinet_router
from .learning import router as learning_router
from .acceptance_api import router as acceptance_router
from .twin_api import router as twin_router
from .jobs import manager
from .observability import install_observability


@asynccontextmanager
async def lifespan(app):
    store.init()
    manager.start()
    try:
        yield
    finally:
        manager.stop()


app = FastAPI(title="多模态感知网络智能规划平台", lifespan=lifespan)
app.mount('/static', StaticFiles(directory=store.ROOT / 'static'), name='static')
for router in (core_router, engineering_router, tiles_router, planning_portal_router,
               demonstration_router, research_analysis_router, research_agent_router,
               baseline_comparison_router, jobs_router, learning_router, cabinet_router, acceptance_router, twin_router):
    app.include_router(router)
install_observability(app)


@app.get('/health')
def health():
    with store.connection() as conn:
        conn.execute('SELECT 1').fetchone()
    try:
        system = platform.freedesktop_os_release().get('NAME', 'unknown')
    except OSError:
        system = platform.system()
    return {'service': 'planning', 'status': 'ok', 'database': 'ok', 'os': system}
