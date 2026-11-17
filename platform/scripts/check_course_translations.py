"""Validate published translations and optionally rebuild from saved page drafts."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from translate_courses import LIBRARY, WORK, RECIPE, publish


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    report = {'documents': [], 'issues': [], 'source_pages': 0}
    for cat in sorted(LIBRARY.glob('*/catalog.json')):
        course = json.loads(cat.read_text())
        for lesson in course['lessons']:
            original = next(r for r in lesson['resources'] if r['id'] == 'original')
            folder = WORK/course['id']/lesson['id']
            study_path = cat.parent / f"{lesson['id']}-study.json"
            if args.rebuild:
                if course['id']=='hku-data8010' and lesson['id']=='lecture-01':
                    pages=json.loads(study_path.read_text())['pages']
                else:
                    paths=[folder/f'page-{n:03}.json' for n in range(1,original['pages']+1)]
                    if not all(p.exists() for p in paths):
                        if args.allow_partial: continue
                        report['issues'].append(f"Missing page draft: {course['id']}/{lesson['id']}")
                        continue
                    pages=[json.loads(p.read_text()) for p in paths]
                    stale=[i for i,row in enumerate(pages,1) if row.get('recipe')!=RECIPE and not row.get('review_note')]
                    if stale:
                        if not args.allow_partial: report['issues'].append(f'Stale translation draft: {course["id"]}/{lesson["id"]}: {stale}')
                        continue
                folder.mkdir(parents=True,exist_ok=True)
                publish(course,lesson,folder,pages,(cat.parent/original['file']).resolve())
            latest=json.loads(cat.read_text())
            item=next(l for l in latest['lessons'] if l['id']==lesson['id'])
            zh=next((r for r in item['resources'] if r['id']=='zh-study'),None)
            key=course['id']+'/'+lesson['id']
            if not zh or not study_path.is_file():
                if not args.allow_partial: report['issues'].append(f'Missing Chinese version: {key}')
                continue
            pdf=cat.parent/zh['file']; data=json.loads(study_path.read_text())
            if not pdf.read_bytes().startswith(b'%PDF'):report['issues'].append(f'Invalid PDF: {key}')
            digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
            if digest!=zh['sha256']:report['issues'].append(f'Hash mismatch: {key}')
            pages=int(re.search(r'Pages:\s+(\d+)',subprocess.check_output(['pdfinfo',str(pdf)],text=True))[1])
            if pages!=zh['pages']:report['issues'].append(f'PDF page count mismatch: {key}')
            if len(data['pages'])!=original['pages']:report['issues'].append(f'Translation count mismatch: {key}')
            mapping=zh['source_page_map']; expected=[str(n) for n in range(1,original['pages']+1)]
            if set(mapping)!=set(expected):report['issues'].append(f'Source map incomplete: {key}')
            values=[mapping.get(n,0) for n in expected]
            if values!=sorted(set(values)) or min(values)<1 or max(values)>pages:report['issues'].append(f'Invalid mapping: {key}')
            source_digest=hashlib.sha256((cat.parent/original['file']).read_bytes()).hexdigest()
            analysis_path=folder/'source-analysis.json'
            hidden_pages=set(json.loads(analysis_path.read_text()).get('hidden_ocr_pages',[])) if analysis_path.exists() else set()
            for n, row in enumerate(data['pages'],1):
                if n in hidden_pages and row.get('ocr_revision')!=2 and not row.get('review_note'):report['issues'].append(f'Uncorrected hidden OCR layer: {key}/{n}')
                if row.get('source_sha256') and row['source_sha256']!=source_digest:report['issues'].append(f'Source hash mismatch: {key}/{n}')
                if row.get('source_page')!=n:report['issues'].append(f'Source index mismatch: {key}/{n}')
                for field in ['title','translation','summary','annotation']:
                    if not isinstance(row.get(field),str) or not row[field].strip():report['issues'].append(f'Missing {field}: {key}/{n}')
                if row['summary']==row['annotation']:report['issues'].append(f'Duplicate learning note: {key}/{n}')
            log_path=folder/'中文学习版.log'
            warnings=[]
            if log_path.exists():
                log=log_path.read_text(errors='replace')
                warnings=sorted(set(re.findall(r'Missing character:.*|Overfull .*',log)))
                if warnings:report['issues'].append({'document':key,'layout_warnings':warnings})
            report['documents'].append({'course':course['id'],'lesson':lesson['id'],'source_pages':original['pages'],'chinese_pages':pages,'sha256':digest})
            report['source_pages']+=original['pages']
            print('CHECK',key,original['pages'],pages,'warnings',len(warnings),flush=True)
    destination=WORK/'validation.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('Documents',len(report['documents']),'Source pages',report['source_pages'],'Issues',len(report['issues']))
    if report['issues']:raise SystemExit(1)


if __name__=='__main__':main()
