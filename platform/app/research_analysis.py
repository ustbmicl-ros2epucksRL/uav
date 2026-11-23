"""Read-only research problem pages; illustrative experiments are separate from evidence."""
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from . import proposal_source, store

router=APIRouter()

@router.get('/research')
def index(request:Request):
    return RedirectResponse(request.scope.get('root_path','')+'/research/1')

@router.get('/research/{task_id}')
def page(task_id:int,request:Request):
    if task_id not in range(1,6):raise HTTPException(404,'研究子任务不存在')
    html=(store.ROOT/'static'/'research.html').read_text()
    return HTMLResponse(html.replace('<head>','<head><base href="'+request.scope.get('root_path','')+'/">'))

@router.get('/api/research-analysis/tasks')
def tasks():
    tasks=json.loads((store.ROOT/'static'/'research-tasks.json').read_text())
    for task in tasks:task['pages']=proposal_source.TASK_PAGES[task['id']]
    return tasks

@router.get('/api/research-analysis/proposal')
def proposal():
    path=proposal_source.path()
    if not path.is_file():raise HTTPException(404,'当前部署未包含正式申请书 PDF')
    return FileResponse(path,media_type='application/pdf')
