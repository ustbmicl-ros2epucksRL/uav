"""Repair duplicated summaries/annotations in saved drafts without changing translations."""
import difflib
import json
from pathlib import Path
import urllib.request
from translate_courses import WORK, atomic

http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
fixed=0
for path in sorted(WORK.glob('*/*/page-*.json')):
    page=json.loads(path.read_text())
    summary=page['summary'].strip();annotation=page['annotation'].strip()
    if difflib.SequenceMatcher(None,summary,annotation).ratio()<.90:
        continue
    body={'model':'qwen3:8b','think':False,'stream':False,
          'messages':[{'role':'user','content':'以下是课程译文和已有总结。请给出一个具体、有针对性的中文自测问题，或指出一个需要留意的适用条件。不得重复总结，不要给答案，不要编造原文之外的实验结论。只输出这一句，80字以内。\n译文：'+page['translation'][:6000]+'\n已有总结：'+summary}],
          'options':{'temperature':.1,'num_ctx':16384,'num_predict':200},'keep_alive':'10m'}
    request=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    result=json.load(http.open(request,timeout=180));text=result['message']['content'].strip()
    if result.get('done_reason')=='length' or not text or difflib.SequenceMatcher(None,summary,text).ratio()>.85:
        print('REVIEW NEEDED',path,flush=True);continue
    page['annotation']=text;page['note_repair']='qwen3:8b: replaced duplicated annotation with a specific study question'
    atomic(path,page);fixed+=1;print('REPAIRED',path.parent.parent.name,path.parent.name,page['source_page'],flush=True)
print('Repaired',fixed)
