"""Collect text QA candidates; findings need contextual review, not automatic deletion."""
import json
import re
from translate_courses import WORK

units={'k':1e3,'m':1e6,'b':1e9,'thousand':1e3,'million':1e6,'billion':1e9}
report={'numeric_units':[],'english_lines':[]}
for path in sorted(WORK.glob('*/*/page-*.json')):
    page=json.loads(path.read_text());source=page.get('source_text','')+'\n'+page.get('recognized_text','')
    ocr=path.parent/f"source-{page['source_page']:03}.ocr.txt"
    if ocr.exists():source+='\n'+ocr.read_text()
    if page.get('ocr_revision',0)>=1 and page.get('recognized_text'):source=page['recognized_text']
    key='/'.join(path.parts[-3:]);values=[]
    for match in re.finditer(r'(?<![\w.])(\d+(?:\.\d+)?)\s*(thousand|million|billion|[KMB])\b',source,re.I):
        values.append(float(match[1])*units[match[2].lower()])
    for field in ['translation','summary','annotation']:
        text=page[field]
        for match in re.finditer(r'(\d+(?:\.\d+)?)\s*(万|亿)',text):
            value=float(match[1])*(1e4 if match[2]=='万' else 1e8)
            if values and not any(abs(value-v)<=max(1,abs(v)*1e-7) for v in values):
                report['numeric_units'].append({'page':key,'field':field,'quantity':match[0],
                    'source_quantities':values,'context':text[max(0,match.start()-35):match.end()+35]})
        if field!='translation':continue
        for line in text.splitlines():
            if re.search(r'[\u4e00-\u9fff]|https?://|\bURL\b|\.org/|\.com/|^```|^\s*(from |import |def |class |pip |conda |sudo |#)|\bet al\b',line):continue
            if len(re.findall('[A-Za-z]{2,}',line))>=9:
                report['english_lines'].append({'page':key,'line':line})
output=WORK/'qa/text-review-candidates.json';output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('Numeric candidates',len(report['numeric_units']),'English-line candidates',len(report['english_lines']))
