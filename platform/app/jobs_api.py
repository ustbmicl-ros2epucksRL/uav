"""HTTP contract for durable engineering computations."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from . import jobs, store

router = APIRouter()


class JobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['volume', 'plan']
    parameters: dict = Field(default_factory=dict)


@router.get('/computations')
def computations_page():
    return FileResponse(store.ROOT / 'static' / 'computations.html')


@router.post('/api/scenes/{scene_id}/jobs', status_code=202)
def submit_job(scene_id: str, request: JobRequest):
    try:
        return jobs.submit(scene_id, request.kind, request.parameters)
    except (ValidationError, ValueError) as error:
        raise HTTPException(422, str(error)) from error


@router.get('/api/jobs')
def list_jobs(scene: str | None = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    jobs.manager.start()
    with store.connection() as conn:
        rows = conn.execute('SELECT id,scene,kind,status,created_at,started_at,finished_at,error,parent_id FROM computation_jobs '
                            'WHERE (? IS NULL OR scene=?) ORDER BY created_at DESC LIMIT ? OFFSET ?',
                            (scene, scene, limit, offset)).fetchall()
    return {'jobs': [dict(row) for row in rows]}


@router.get('/api/jobs/{job_id}')
def job_detail(job_id: str):
    return jobs.get(job_id)


@router.get('/api/jobs/{job_id}/export')
def export_job(job_id: str):
    job = jobs.get(job_id)
    return JSONResponse(job, headers={'Content-Disposition': f'attachment; filename="computation-{job["id"]}.json"'})


@router.post('/api/jobs/{job_id}/cancel')
def cancel_job(job_id: str):
    return jobs.cancel(job_id)


@router.post('/api/jobs/{job_id}/retry', status_code=202)
def retry_job(job_id: str):
    return jobs.retry(job_id)


@router.post('/api/jobs/{job_id}/publish')
def publish_job(job_id: str):
    return jobs.publish(job_id)
