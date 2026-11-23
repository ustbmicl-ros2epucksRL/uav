"""Formal proposal document: local-only PDF, page anchors and read-only passage search.

The repository is public, so the PDF and its text never enter Git; only page numbers do.
"""
import hashlib
import math
import os
import re
import threading
from pathlib import Path

from . import store

VERSION = 'end-1124'
FILE_NAME = '雄安新区科技创新专项储备项目申报书end-1124.pdf'
# File page order; the printed page number is two less.
PRINTED_OFFSET = 2
KPI_TABLE_PAGES = [19, 20, 21, 22]
KPI_PAGES = dict(objectives=19, platform=19, devices=20, parameters=20, coverage=20, interference=20,
                 planning=21, demo=22, outputs=22, training=18)
TASK_PAGES = {1: [20, 29, 36, 37, 38], 2: [20, 21, 30, 39, 40, 41], 3: [21, 31, 42, 43, 44],
              4: [19, 20, 32, 45, 46, 47], 5: [22, 33, 34, 48, 49, 50, 51]}
WINDOW, STEP, CONTEXT = 80, 20, 80
TOKEN = re.compile(r'[\u4e00-\u9fff]|[A-Za-z0-9.%≥≤]+')
# Question wording, not proposal content.
QUESTION = re.compile(r'申请书|申报书|申请|请问|是多少|多少|哪些|什么|怎么|如何|是否|要求|里面|中的|有没有')
_cache = {}
_lock = threading.Lock()



def page_label(pages):
    """[20, 29, 36, 37, 38] -> '20、29、36–38'."""
    runs = []
    for page in pages:
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return '、'.join(str(a) if a == b else f'{a}–{b}' for a, b in runs)


def path():
    return Path(os.getenv('SENSE_PROPOSAL_PDF') or store.PROJECT / FILE_NAME)


def index():
    """Page passages of the current file, rebuilt only when the file changes."""
    source = path()
    if not source.is_file():
        return None
    stat = source.stat()
    key = (str(source), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _cache.get('key') != key:
            from pypdf import PdfReader
            reader = PdfReader(source)
            texts, passages = [], []
            for number, page in enumerate(reader.pages, 1):
                text = re.sub(r'\s+', '', page.extract_text() or '')
                texts.append(text)
                for start in range(0, max(len(text) - WINDOW // 2, 1), STEP):
                    passages.append((number, start, text[start:start + WINDOW]))
            grams = [terms(text) for _, _, text in passages]
            frequency = {}
            for found in grams:
                for gram in found:
                    frequency[gram] = frequency.get(gram, 0) + 1
            _cache.clear()
            _cache.update(key=key, sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                          pages=len(reader.pages), texts=texts, passages=passages, grams=grams,
                          frequency=frequency)
        return _cache


def terms(text):
    tokens = TOKEN.findall(text)
    return {a + b for a, b in zip(tokens, tokens[1:])} | {t for t in tokens if len(t) > 1}


def search(query, limit=5):
    data = index()
    if data is None:
        return dict(available=False, version=VERSION, file=FILE_NAME, query=query, passages=[],
                    note='当前部署未包含正式申请书 PDF，缺少原文证据。')
    wanted = terms(QUESTION.sub(' ', query or ''))
    total = len(data['passages'])
    scored = []
    for (page, start, text), found in zip(data['passages'], data['grams']):
        hits = wanted & found
        if hits:
            score = sum(math.log(1 + total / data['frequency'][gram]) for gram in hits)
            scored.append((score, page, start, text))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    chosen = []
    for item in scored:
        if len(chosen) == limit:
            break
        if all(item[1] != other[1] or abs(item[2] - other[2]) >= WINDOW + 2 * CONTEXT for other in chosen):
            chosen.append(item)
    return dict(available=True, version=VERSION, file=FILE_NAME, sha256=data['sha256'], pages=data['pages'],
                query=query, page_numbering=f'PDF 文件页序，正文印刷页码减 {PRINTED_OFFSET}',
                passages=[dict(page=page, printed_page=page - PRINTED_OFFSET if page > PRINTED_OFFSET else None,
                               text=data['texts'][page - 1][max(start - CONTEXT, 0):start + WINDOW + CONTEXT],
                               score=round(score, 3),
                               source_path=f'/api/research-analysis/proposal#page={page}')
                          for score, page, start, _ in chosen])
