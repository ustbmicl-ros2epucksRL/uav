"""Read-only research problem pages; illustrative experiments are separate from evidence."""
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from . import store

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
    return json.loads((store.ROOT/'static'/'research-tasks.json').read_text())

@router.get('/api/research-analysis/proposal')
def proposal():
    path=store.PROJECT/'项目申报书-面向低空经济的多模态感知网络智能规划关键技术研究及示范应用.pdf'
    if not path.is_file():raise HTTPException(404,'当前部署未包含申请书 PDF')
    return FileResponse(path,media_type='application/pdf')
