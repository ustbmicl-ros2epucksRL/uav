"""Sequential browser and SITL acceptance with one persisted report per batch."""
import html
import json
import shutil
import sys
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from test_dual_sitl_ui import main as exercise


def evidence(data,folder):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    frames=data['frames']
    fig,(map_ax,age_ax)=plt.subplots(1,2,figsize=(10,4))
    for vehicle in range(2):
        pts=[f['after'][vehicle] for f in frames]
        map_ax.plot(*zip(*[p[:2] for p in pts]),label=f'SITL {vehicle+1}')
    age_ax.plot([f['slot'] for f in frames],[f['mean_age_s'] for f in frames])
    entries=[]
    for original in (f for f in frames if f['dispatch']=='blocked'):
        restored=next(f for f in frames if f.get('blocked_slot')==original['slot'])
        for f,color,style in [(original,'red','--'),(restored,'green','-')]:
            for t,b in zip(f['telemetry'],f['targets']):
                map_ax.plot([t['xyz'][0],b[0]],[t['xyz'][1],b[1]],style,color=color,linewidth=1)
            age_ax.scatter(f['slot'],f['mean_age_s'],color=color)
        xyz=original['safety']['contacts'][0]['position']
        map_ax.scatter(*xyz[:2],marker='x',color='red')
        entries.append(f'<li>{html.escape(original["injection"])} · 时隙 {original["slot"]} 拦截并悬停，'
                       f'{restored["slot"]} 重规划恢复 · 冲突位置 ENU {tuple(round(v,2) for v in xyz)} m · '
                       f'AoI {original["mean_age_s"]:.2f} → {restored["mean_age_s"]:.2f} 模型秒</li>')
    map_ax.set(xlabel='East (m)',ylabel='North (m)',title='Telemetry and proposed segments')
    map_ax.legend()
    age_ax.set(xlabel='Slot',ylabel='Mean AoI (model s)')
    fig.tight_layout()
    fig.savefig(folder/'overview.png',dpi=125)
    plt.close(fig)
    shots=''.join(f'<a href="{folder.name}/{p.name}"><img width="290" src="{folder.name}/{p.name}" alt="{p.stem}"></a>' for p in sorted(folder.glob('slot-*.png')))
    return ('<ul>'+''.join(entries)+'</ul><p>红虚线为被拦截的拟航段，绿色为恢复航段，叉号为预测冲突位置。</p>'
            +f'<img width="800" src="{folder.name}/overview.png"><div>{shots}</div>')


def main(job,rounds,fault=0,resume=1,parent_id=None):
    job.mkdir(parents=True)
    started=time.time()
    rows=[]
    sections=[]
    origin=''
    previous_tasks=set()
    if parent_id:
        parent=job.parent/parent_id
        prior=json.loads((parent/'summary.json').read_text())
        previous_tasks.update(prior.get('previous_tasks',[]))
        previous_tasks.update(row['task_id'] for row in prior['rounds'] if row.get('task_id'))
        origin=f'<p>从第 {resume} 轮重试 · <a href="/api/stability/{parent_id}/files/summary.html">原失败记录、截图与回放</a></p>'
        for row in prior['rounds'][:resume-1]:
            index=row['round']
            folder=f'round-{index}'
            shutil.copytree(parent/folder,job/folder)
            shutil.copy2(parent/f'{folder}-runner.log',job/f'{folder}-runner.log')
            rows.append(row)
            if 'sections' in prior:
                sections.append(prior['sections'][index-1])
            else:
                sections.append(f'<p>第 {index} 轮 · {row["task_id"]} · 已通过，证据见原报告</p>')
        (job/'summary.html').write_text('<meta charset="utf-8">'+origin+'重试运行中')
    for index in range(resume,rounds+1):
        folder=job/f'round-{index}'
        folder.mkdir()
        begin=time.time()
        error=None
        body=''
        with (job/f'round-{index}-runner.log').open('w') as log,redirect_stdout(log):
            try:
                data=exercise(True,folder)
                assert all(row['task_id']!=data['id'] for row in rows),'重复使用任务编号'
                assert data['id'] not in previous_tasks,'重复使用原尝试的任务编号'
                body=evidence(data,folder)
                if index==fault:
                    raise RuntimeError('QA-CONTROLLED：双机档案和回放截图保存后注入验收故障')
            except Exception:
                error=traceback.format_exc()
                print(error)
        flow=folder/'flow.json'
        task=json.loads(flow.read_text())['task_id'] if flow.exists() else None
        result='failed' if error else 'passed'
        duration=round(time.time()-begin,2)
        row=dict(round=index,status=result,task_id=task,duration_s=duration,error=error)
        rows.append(row)
        (folder/'report.json').write_text(json.dumps(row,ensure_ascii=False))
        replay=f'<a href="/?dual={task}">{task} · 打开回放</a>' if task else ''
        sections.append(f'<section><h2>第 {index} 轮 · {result} · {duration} 秒</h2>{replay} · '
                        f'<a href="round-{index}-runner.log">日志</a>{body}<pre>{html.escape(error or "")}</pre></section>')
        status=result if error or index==rounds else 'running'
        summary=dict(status=status,requested_rounds=rounds,started_at=started,rounds=rows,sections=sections,previous_tasks=sorted(previous_tasks),retry_of=parent_id)
        for name,content in [('summary.json',json.dumps(summary,ensure_ascii=False)),
                             ('summary.html','<!doctype html><meta charset="utf-8"><title>双机演练回归</title>'
                              +f'<h1>双机禁飞区与航线交会 · {index}/{rounds} · {status}</h1>'+origin+''.join(sections))]:
            tmp=job/(name+'.tmp')
            tmp.write_text(content)
            tmp.replace(job/name)
        if error:
            return 1
    return 0


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('job',type=Path)
    parser.add_argument('rounds',type=int)
    parser.add_argument('fault',type=int,nargs='?',default=0)
    parser.add_argument('resume',type=int,nargs='?',default=1)
    parser.add_argument('parent_id',nargs='?')
    sys.exit(main(**vars(parser.parse_args())))
