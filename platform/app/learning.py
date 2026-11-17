"""Independent course library and study records; no scene or simulation dependencies."""
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from . import store

router = APIRouter()
LIBRARY = store.ROOT / 'resources' / 'courses' / 'hku-data8010'


DEFAULT_COURSE = 'hku-data8010'


def course_directory(course_id):
    if not re.fullmatch(r'[a-z0-9-]+', course_id):
        raise HTTPException(404, '课程不存在')
    directory = LIBRARY.parent / course_id
    if not (directory / 'catalog.json').is_file():
        raise HTTPException(404, '课程不存在')
    return directory


def catalog(course_id=DEFAULT_COURSE):
    return json.loads((course_directory(course_id) / 'catalog.json').read_text())


def lesson_for(lesson_id, course_id=DEFAULT_COURSE):
    lesson = next((x for x in catalog(course_id)['lessons'] if x['id'] == lesson_id), None)
    if not lesson:
        raise HTTPException(404, '章节不存在')
    return lesson


def resource_for(lesson, resource_id):
    resource = next((x for x in lesson['resources'] if x['id'] == resource_id), None)
    if not resource:
        raise HTTPException(404, '资料不存在')
    return resource


@contextmanager
def database():
    store.DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(store.DATA / 'learning.sqlite3', timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS study_records ('
                     'lesson_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        yield conn
        conn.commit()
    finally:
        conn.close()


class StudyRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['not_started', 'studying', 'completed'] = 'not_started'
    resource: str = Field(default='original', max_length=40)
    page: int = Field(default=1, ge=1, strict=True)
    notes: str = Field(default='', max_length=30000)


def records(course_id=DEFAULT_COURSE):
    with database() as conn:
        rows = conn.execute('SELECT * FROM study_records').fetchall()
    prefix = '' if course_id == DEFAULT_COURSE else course_id + '::'
    return {row['lesson_id'][len(prefix):]: json.loads(row['payload']) for row in rows
            if (row['lesson_id'].startswith(prefix) if prefix else '::' not in row['lesson_id'])}


@router.get('/learning')
@router.get('/learning/')
def learning_page():
    return FileResponse(store.ROOT / 'static' / 'learning.html')


@router.get('/api/learning/catalog')
def get_catalog(course: str = DEFAULT_COURSE):
    result = catalog(course)
    for lesson in result['lessons']:
        for resource in lesson['resources']:
            resource['available'] = (course_directory(course) / resource.pop('file')).is_file()
    result['records'] = records(course)
    return result


@router.get('/api/learning/files/{lesson_id}/{resource_id}')
def course_file(lesson_id: str, resource_id: str, download: bool = Query(False), course: str = DEFAULT_COURSE):
    resource = resource_for(lesson_for(lesson_id, course), resource_id)
    directory = course_directory(course).resolve()
    path = (directory / resource['file']).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(404, '本地资料暂不可用')
    return FileResponse(path, media_type='application/pdf', filename=path.name,
                        content_disposition_type='attachment' if download else 'inline')


@router.put('/api/learning/records/{lesson_id}')
def save_record(lesson_id: str, record: StudyRecord, course: str = DEFAULT_COURSE):
    resource = resource_for(lesson_for(lesson_id, course), record.resource)
    if record.page > resource['pages']:
        raise HTTPException(422, '页码超出当前资料范围')
    payload = {**record.model_dump(), 'updated_at': store.now()}
    with database() as conn:
        conn.execute('INSERT INTO study_records VALUES (?, ?) '
                     'ON CONFLICT(lesson_id) DO UPDATE SET payload=excluded.payload',
                     (lesson_id if course == DEFAULT_COURSE else course + '::' + lesson_id,
                      json.dumps(payload, ensure_ascii=False)))
    return payload


@router.get('/api/learning/export')
def export_records(course: str = DEFAULT_COURSE):
    return JSONResponse({'course': catalog(course)['id'], 'exported_at': store.now(), 'records': records(course)},
                        headers={'Content-Disposition': 'attachment; filename="study-notes.json"'})


@router.get('/api/learning/courses')
def list_courses():
    courses = []
    for path in sorted(LIBRARY.parent.glob('*/catalog.json')):
        item = catalog(path.parent.name)
        courses.append({key: item.get(key, '') for key in
                        ('id', 'title', 'english_title', 'instructor', 'term', 'source', 'description', 'availability')})
        courses[-1]['lessons'] = [{key: lesson.get(key, '') for key in ('id', 'title', 'module', 'goal', 'kind')}
                                  for lesson in item['lessons']]
        courses[-1]['resource_count'] = sum(len(x['resources']) for x in item['lessons'])
    courses.sort(key=lambda x: (x['id'] != DEFAULT_COURSE, x['id']))
    references_path = LIBRARY.parent / 'references.json'
    references = json.loads(references_path.read_text()) if references_path.exists() else []
    return {'courses': courses, 'references': references}


@router.get('/api/learning/translation-status')
def translation_status():
    path = store.ROOT.parent / 'outputs/course-translations/status.json'
    if not path.exists():
        return {'state': 'not_started'}
    result = json.loads(path.read_text())
    # Paths and internal error traces are not exposed to the browser.
    return {key: result[key] for key in ('state', 'total_documents', 'completed_documents',
            'total_source_pages', 'completed_pages', 'title', 'source_page', 'updated_at', 'quality') if key in result}


@router.get('/api/learning/study/{lesson_id}')
def study_page(lesson_id: str, page: int = Query(1, ge=1), course: str = DEFAULT_COURSE):
    lesson = lesson_for(lesson_id, course)
    original = resource_for(lesson, 'original')
    if page > original['pages']:
        raise HTTPException(422, '页码超出原讲义范围')
    directory = course_directory(course).resolve()
    study_file = (directory / lesson.get('study_file', f'{lesson_id}-study.json')).resolve()
    if not study_file.is_relative_to(directory):
        raise HTTPException(404, '学习译稿不存在')
    if study_file.is_file():
        data = json.loads(study_file.read_text())
        item = data['pages'][page - 1]
        return {'available': True, 'complete': True, 'source_page': page,
                'quality': data['quality'], 'content': {k: item.get(k, '') for k in
                ('title', 'translation', 'summary', 'annotation', 'method')}}
    # Completed page drafts are readable while the remaining pages are being translated.
    cache = store.ROOT.parent / 'outputs/course-translations' / course / lesson_id / f'page-{page:03}.json'
    if cache.is_file():
        item = json.loads(cache.read_text())
        return {'available': True, 'complete': False, 'source_page': page,
                'quality': '机器辅助学习译稿，未逐句人工校对；本讲 PDF 尚在制作',
                'content': {k: item.get(k, '') for k in ('title', 'translation', 'summary', 'annotation', 'method')}}
    return {'available': False, 'complete': False, 'source_page': page,
            'quality': '该页尚未完成翻译，英文原稿可以正常阅读。'}
