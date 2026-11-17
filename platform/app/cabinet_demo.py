"""Authenticated cabinet API; local users, committed organisation permissions."""
import os
import json
import secrets
import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from . import store

ROOT = store.PROJECT / 'research' / 'data_security_cabinet'
# Prototype imports its sibling state_guard on error paths.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from security_cabinet import Denied, IntegrityError, transform
import container_contract
import template_registry as templates
import media_attachments as media
import chunked_attachments as chunks
import hashlib

from . import cabinet_keys as keys
from . import cabinet_persistence as persistence
from . import cabinet_auth as auth
from trustee_broker import preview_request
from state_guard import StateGuard, RemoteCheckpoint, StateIntegrityError, CheckpointUnavailable


def configured_guard():
    try:
        if keys.selected_mode() == 'hardware':
            return keys.hardware_guard()
    except keys.KeyConfigurationError as error:
        raise HTTPException(503, str(error)) from None
    return demo_guard(os.environ.get('SENSE_CABINET_CHECKPOINT_CONFIG'))


def demo_guard(path):
    if not path:
        if (store.DATA/'cabinet-demo'/'persistent.db').exists():
            raise HTTPException(503, '持久安全柜缺少检查点配置，禁止降级。')
        return None
    try:
        config = json.loads(Path(path).read_text())
        token = Path(config['token_file']).read_bytes()
        key = Path(config['state_key_file']).read_bytes()
        if len(token) != 32 or len(key) != 32:
            raise ValueError('Expected 32-byte checkpoint credentials')
        remote = RemoteCheckpoint(config['url'], config['namespace'], token.hex(), config['ca_file'])
        return StateGuard(config['namespace'], key, remote)
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, '独立检查点配置无效，已停止建立会话；不会降级。') from None



class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            # Cross-worker serialization: contention is retryable, never a stale fork.
            import fcntl
            folder = store.DATA/'cabinet-demo'
            folder.mkdir(parents=True, exist_ok=True)
            lockfile = open(folder/'platform.lock', 'a')
            # Browsers can issue a seek Range while their permission poll is in
            # flight. Briefly queue, without blocking the event loop, rather than
            # failing a healthy video read immediately. Long contention still fails.
            import asyncio
            deadline=asyncio.get_running_loop().time()+5
            try:
                while True:
                    try:
                        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if asyncio.get_running_loop().time()>=deadline:
                            lockfile.close()
                            return JSONResponse({'detail':'安全柜忙，请重试。'}, status_code=503, headers={'Cache-Control':'no-store','Retry-After':'1'})
                        await asyncio.sleep(0.02)
            except BaseException:
                lockfile.close()
                raise
            try:
                response = await original(request)
            except CheckpointUnavailable:
                response = JSONResponse({'detail':'独立检查点不可用或拒绝请求，已停止访问；不会降级。'}, status_code=503)
            except media.MediaUnavailable as error:
                response = JSONResponse({'detail':str(error)}, status_code=503)
            except StateIntegrityError:
                response = JSONResponse({'detail':'授权状态校验失败：可能被篡改或回滚，已停止访问。'}, status_code=409)
            except HTTPException as error:
                if error.status_code in (403, 404, 408, 413, 415, 416, 422) and hasattr(request.state, 'cabinet_actor'):
                    cab, uid = request.state.cabinet_actor
                    try:
                        with cab._transaction() as db:
                            cab._audit(db, 'api_denied', uid, status=error.status_code,
                                       endpoint=getattr(request.scope.get('route'), 'path', 'unknown'))
                    except StateIntegrityError:
                        return JSONResponse({'detail':'拒绝请求；审计状态无法验证'}, status_code=503, headers={'Cache-Control':'no-store'})
                response = JSONResponse({'detail':error.detail}, status_code=error.status_code)
            except RequestValidationError as error:
                response = JSONResponse({'detail':[{'loc':list(e['loc']), 'type':e['type'], 'msg':e['msg']} for e in error.errors()]}, status_code=422)
            except Denied as error:
                if hasattr(request.state, 'cabinet_actor'):
                    cab, uid = request.state.cabinet_actor
                    try:
                        with cab._transaction() as db:
                            cab._audit(db, 'api_denied', uid, endpoint=getattr(request.scope.get('route'), 'path', 'unknown'))
                    except StateIntegrityError:
                        return JSONResponse({'detail':'拒绝请求；审计状态无法验证'}, status_code=503, headers={'Cache-Control':'no-store'})
                response = JSONResponse({'detail':str(error)}, status_code=403)
            except IntegrityError:
                response = JSONResponse({'detail':'数据完整性校验失败'}, status_code=409)
            finally:
                lockfile.close()
            response.headers['Cache-Control'] = 'no-store'
            return response
        if self.path.endswith(('/stream', '/preview', '/streams/{stream_id}')):
            async def traced_preview(request):
                request_id = secrets.token_hex(16)
                token = preview_request.set(request_id)
                try:
                    if self.path.endswith('/streams/{stream_id}') and os.environ.get('SENSE_CABINET_KEY_MODE') == 'hardware':
                        from starlette.concurrency import run_in_threadpool
                        try:
                            same_origin(request)
                            request.state.range_guard = await run_in_threadpool(configured_guard)
                        except HTTPException as exc:
                            return JSONResponse({'detail':exc.detail}, status_code=exc.status_code,
                                headers={'X-Cabinet-Preview-ID':request_id,'Cache-Control':'no-store'})
                    response = await handler(request)
                    response.headers['X-Cabinet-Preview-ID'] = request_id
                    return response
                finally:
                    preview_request.reset(token)
            return traced_preview
        return handler


router = APIRouter(route_class=PrivateRoute)
COOKIE = 'cabinet_account'


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)


class Login(Body):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class Grant(Body):
    asset_id: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=3, max_length=80)
    purpose: str


class NewUser(Login):
    role: str


class Enabled(Body):
    enabled: bool


class Password(Body):
    old: str = Field(min_length=1, max_length=128)
    new: str = Field(min_length=12, max_length=128)


def cabinet(guard=None):
    if guard is None:
        guard = configured_guard()
    if guard is None:
        raise HTTPException(503, '账号服务要求独立检查点配置，不允许软件降级')
    cab = persistence.open_cabinet(store.DATA/'cabinet-demo', guard)
    auth.require_schema(cab)
    return cab


def same_origin(request):
    expected = os.environ.get('SENSE_CABINET_ORIGIN', f'{request.url.scheme}://{request.url.netloc}')
    if request.headers.get('sec-fetch-site') == 'cross-site' or request.headers.get('origin', expected) != expected:
        raise HTTPException(403, '拒绝跨站请求')
    if request.method != 'GET' and request.headers.get('X-Cabinet-Request') != '1':
        raise HTTPException(403, '缺少同源请求头')


def context(request):
    same_origin(request)
    cab = cabinet(getattr(request.state,'range_guard',None))
    sid = request.cookies.get(COOKIE, '')
    user = auth.authenticate(cab, sid)
    request.state.cabinet_actor = (cab, user['id'])
    if request.method != 'GET' and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), auth.csrf(cab, sid)):
        raise HTTPException(403, 'CSRF 校验失败')
    return cab, user, sid


def owner(user):
    if user['role'] != 'owner': raise HTTPException(403, '需要数据所有者权限')


def checked_grant(cab, user, gid):
    with cab._transaction() as db:
        row = db.execute('SELECT g.* FROM grants g JOIN assets a ON a.id=g.asset JOIN auth_users u ON u.id=a.owner JOIN auth_orgs o ON o.id=u.org WHERE g.id=? AND g.subject=? AND u.enabled=1 AND o.enabled=1 AND u.role=\'owner\'', (gid, user['id'])).fetchone()
        if row is None: raise HTTPException(404, '授权不存在或不可访问')
        auth.assert_purpose(user, row['purpose'])
        return dict(row)


@router.get('/data-cabinet')
def page():
    return FileResponse(store.ROOT/'static'/'data-cabinet.html')


@router.api_route('/api/cabinet-demo/{path:path}', methods=['GET', 'POST'])
def retired(path: str):
    raise HTTPException(410, '演示身份接口已关闭，请使用独立账号登录')


@router.post('/api/cabinet/login')
def login(body: Login, request: Request):
    same_origin(request)
    cab = cabinet()
    sid = auth.login(cab, body.username, body.password, request.cookies.get(COOKIE))
    response = JSONResponse({'csrf':auth.csrf(cab, sid)})
    response.set_cookie(COOKIE, sid, httponly=True, samesite='strict',
                        secure=os.environ.get('SENSE_CABINET_ALLOW_HTTP') != '1', max_age=auth.SESSION_TTL)
    response.delete_cookie('cabinet_demo')
    return response


@router.get('/api/cabinet/security-mode')
def security_mode():
    # Public configuration label only. Readiness requires authenticated /me.
    try:
        return {'mode':keys.selected_mode()}
    except keys.KeyConfigurationError:
        return JSONResponse({'mode':'invalid'}, status_code=503)


@router.get('/api/cabinet/me')
def me(request: Request):
    cab, user, sid = context(request)
    return {'user':auth.public(user), 'csrf':auth.csrf(cab, sid), 'state_guard_enabled':True, 'hardware_verified':False,
            'key_mode':cab.key_mode, 'remote_keys_released':cab.key_mode == 'hardware'}


@router.post('/api/cabinet/logout')
def logout(request: Request):
    cab, user, sid = context(request)
    auth.logout(cab, sid, user)
    response = JSONResponse({'logged_out':True}); response.delete_cookie(COOKIE)
    return response


@router.post('/api/cabinet/password')
def password(body: Password, request: Request):
    cab, user, _ = context(request)
    auth.change_password(cab, user, body.old, body.new)
    response = JSONResponse({'login_required':True}); response.delete_cookie(COOKIE)
    return response


@router.get('/api/cabinet/members')
def members(request: Request):
    cab, user, _ = context(request)
    if user['role'] != 'org_admin': raise HTTPException(403, '需要组织管理员权限')
    with cab._transaction() as db:
        return [dict(r) for r in db.execute('SELECT id,username,org,role,enabled FROM auth_users WHERE org=? ORDER BY username', (user['org'],))]


@router.post('/api/cabinet/members')
def create_member(body: NewUser, request: Request):
    cab, user, _ = context(request)
    try:
        uid = auth.add_user(cab, user['org'], body.username, body.password, body.role, admin=user)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    return {'id':uid}


@router.post('/api/cabinet/members/{uid}/enabled')
def member_enabled(uid: str, body: Enabled, request: Request):
    cab, user, _ = context(request)
    auth.set_enabled(cab, user, uid, body.enabled)
    return {'enabled':body.enabled}


@router.get('/api/cabinet/state')
def state(request: Request):
    cab, user, _ = context(request)
    with cab._transaction() as db:
        if user['role']=='owner':
            assets = [dict(r) for r in db.execute('SELECT id,owner,name,version,sha256 FROM assets WHERE owner=?', (user['id'],))]
            grants = [dict(r) for r in db.execute('SELECT g.* FROM grants g JOIN assets a ON a.id=g.asset WHERE a.owner=?', (user['id'],))]
        else:
            grants = [dict(r) for r in db.execute('SELECT * FROM grants WHERE subject=?', (user['id'],))]
            assets = [dict(r) for r in db.execute('SELECT DISTINCT a.id,a.owner,a.name,a.version,a.sha256 FROM assets a JOIN grants g ON g.asset=a.id WHERE g.subject=? AND g.revoked=0 AND g.expires>?', (user['id'], cab.clock()))]
        jobs = [dict(r) for r in db.execute('SELECT id,grant_id,asset,purpose FROM jobs WHERE subject=?', (user['id'],))]
        if templates.installed(db):
            for asset in assets:
                binding = db.execute('SELECT template_id,version,digest FROM asset_templates WHERE asset=?',(asset['id'],)).fetchone()
                asset['template_binding'] = dict(binding) if binding else None
        aids={a['id'] for a in assets}; gids={g['id'] for g in grants}
        audit=[]
        for row in db.execute('SELECT event FROM audit ORDER BY seq DESC'):
            event=json.loads(row[0]); details=event.get('details',{})
            if event['actor']==user['id'] or (user['role']=='owner' and (details.get('asset') in aids or details.get('grant') in gids)):
                audit.append(event)
            if len(audit)==50: break
        return {'assets':assets,'grants':grants,'jobs':jobs,'audit':audit,'user':auth.public(user)}


@router.post('/api/cabinet/ingest')
def ingest(request: Request, body: dict | None = None):
    cab, user, _ = context(request); owner(user)
    try:
        if body and body.get('format') == templates.FORMAT:
            with cab._transaction() as db:
                row, definition = templates.resolve(db, body, user['org'])
                for purpose in definition['purposes']:
                    if purpose=='preview': continue
                    templates.mapped_result(definition, body['content'], purpose)
            raw = container_contract.canonical(body)
            name = definition['name']
        else:
            with cab._transaction() as db:
                if db.execute("SELECT 1 FROM assets WHERE owner=? AND name='urban-synthetic-features'", (user['id'],)).fetchone():
                    raise HTTPException(409, '已有内置示例资产；可用注册模板导入其他资产')
            raw = container_contract.pack(body if body else container_contract.example())
            transform(raw, 'dataset')
            name = 'urban-synthetic-features'
        return cab.put(user['token'], name, raw)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise HTTPException(422, '模板或数据校验失败，请检查发布版本、字段类型、映射与分组') from None



@router.post('/api/cabinet/grants')
def grant(body: Grant, request: Request):
    cab, user, _ = context(request); owner(user)
    with cab._transaction() as db:
        target = db.execute('SELECT u.* FROM auth_users u JOIN auth_orgs o ON o.id=u.org WHERE u.username=? AND u.enabled=1 AND o.enabled=1', (body.username.strip().lower(),)).fetchone()
        if target is None: raise HTTPException(404, '目标账号不可授权')
        auth.assert_purpose(target, body.purpose)
        if not db.execute('SELECT 1 FROM assets WHERE id=? AND owner=?', (body.asset_id,user['id'])).fetchone():
            raise HTTPException(404, '资产不存在或不可访问')
        if db.execute('SELECT 1 FROM grants WHERE asset=? AND subject=? AND purpose=?', (body.asset_id,target['id'],body.purpose)).fetchone():
            raise HTTPException(409, '该资产已有此主体的授权，不能通过重复授权恢复额度或撤销')
        expires=cab.clock()+1800
    return {'grant_id':cab.grant(user['token'], body.asset_id, target['id'], body.purpose, expires=expires, quota=5)}


@router.post('/api/cabinet/grants/{gid}/revoke')
def revoke(gid: str, request: Request):
    cab, user, _ = context(request); owner(user)
    cab.revoke(user['token'], gid)
    return {'revoked':True}


@router.post('/api/cabinet/grants/{gid}/run')
def run(gid: str, request: Request):
    cab, user, _ = context(request)
    checked_grant(cab, user, gid)
    job=cab.run(user['token'],gid)
    return {'job_id':job,'result':cab.read_result(user['token'],job)}


@router.get('/api/cabinet/results/{jid}')
def result(jid: str, request: Request):
    cab, user, _ = context(request)
    with cab._transaction() as db:
        job=db.execute('SELECT grant_id FROM jobs WHERE id=? AND subject=?', (jid,user['id'])).fetchone()
        if job is None: raise HTTPException(404, '结果不存在或不可访问')
    checked_grant(cab, user, job['grant_id'])
    return {'job_id':jid,'result':cab.read_result(user['token'],jid)}


class RoleChange(Body):
    role: str


@router.post('/api/cabinet/members/{uid}/role')
def member_role(uid: str, body: RoleChange, request: Request):
    cab,user,_=context(request)
    auth.set_role(cab,user,uid,body.role)
    return {'role':body.role,'previous_grants_revoked':True}


@router.get('/api/cabinet/container-template')
def container_template(request: Request):
    context(request)
    return {'contract':container_contract.describe(), 'example':container_contract.example()}


class TemplateRegistration(Body):
    template_id: str | None = Field(default=None, max_length=64)
    definition: dict


def registry_ready(cab):
    with cab._transaction() as db:
        if not templates.installed(db):
            raise HTTPException(503, '请先执行离线模板注册表迁移')


@router.get('/api/cabinet/templates')
def template_list(request: Request):
    cab,user,_=context(request); registry_ready(cab)
    with cab._transaction() as db:
        rows=[]
        for r in db.execute('SELECT * FROM container_versions WHERE org=? ORDER BY template_id,version DESC',(user['org'],)):
            if user['role'] in ('owner','org_admin') or r['status']=='published':
                item=dict(r);item['definition']=json.loads(item['definition']);rows.append(item)
        return rows


@router.post('/api/cabinet/templates')
def template_register(body: TemplateRegistration, request: Request):
    cab,user,_=context(request); registry_ready(cab)
    try: return templates.register(cab,user,body.definition,body.template_id)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise HTTPException(422,'模板定义非法；只允许受控字段类型与内置处理器') from None


@router.post('/api/cabinet/templates/{tid}/{version}/{action}')
def template_transition(tid: str, version: int, action: str, request: Request):
    cab,user,_=context(request); registry_ready(cab)
    try: return templates.transition(cab,user,tid,version,action)
    except ValueError:
        raise HTTPException(409,'模板状态转换不允许，已发布版本不可编辑或重新启用') from None


@router.get('/api/cabinet/template-examples')
def template_examples(request: Request):
    context(request)
    return templates.examples()


@router.post('/api/cabinet/templates/{tid}/{version}/attachments/upload')
async def attachment_upload(tid: str, version: int, field: str, request: Request):
    import asyncio
    from starlette.concurrency import run_in_threadpool
    cab,user,_=context(request);owner(user)
    with cab._transaction() as db:
        _,spec=media.published_field(db,user,tid,version,field)
        limit=min(media.MAX_BYTES,spec['misc']['max_bytes'])
    mime=request.headers.get('content-type','').split(';')[0].strip().lower()
    if mime not in spec['misc']['mime']: raise HTTPException(415,'附件类型不符合模板字段')
    try:
        length=request.headers.get('content-length')
        if length is not None and (int(length)<1 or int(length)>limit): raise HTTPException(413,'附件大小超出限制')
    except ValueError:
        raise HTTPException(400,'Content-Length 无效') from None
    raw=bytearray()
    try:
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(raw)+len(chunk)>limit: raise HTTPException(413,'附件大小超出限制')
                raw.extend(chunk)
    except TimeoutError:
        raise HTTPException(408,'上传超时') from None
    try:
        return await run_in_threadpool(media.upload,cab,user,tid,version,field,bytes(raw),mime)
    except (ValueError,OverflowError):
        raise HTTPException(422,'附件格式、完整性或暂存限额校验失败') from None


@router.post('/api/cabinet/attachments/{mid}/discard')
def attachment_discard(mid: str, request: Request):
    cab,user,_=context(request);owner(user)
    media.discard(cab,user,mid)
    return {'discarded':True}


def attachment_asset(cab,user,gid):
    grant=checked_grant(cab,user,gid)
    if grant['purpose']!='preview': raise HTTPException(403,'需要独立 preview 授权；数据集或统计权限不能预览原始附件')
    return grant['asset']


def media_response(request,raw,mime):
    extension={'image/png':'png','image/jpeg':'jpg','video/mp4':'mp4','video/webm':'webm'}[mime]
    return Response(raw,media_type=mime,headers={'Cache-Control':'no-store','Pragma':'no-cache',
        'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'",
        'Content-Disposition':f'inline; filename="preview.{extension}"','Accept-Ranges':'none',
        'Cross-Origin-Resource-Policy':'same-origin'})


@router.get('/api/cabinet/grants/{gid}/attachments')
def attachments_for_grant(gid: str, request: Request):
    cab,user,_=context(request)
    return media.listing(cab,user,attachment_asset(cab,user,gid),gid)


@router.post('/api/cabinet/grants/{gid}/attachments/{mid}/preview')
def attachment_preview_grant(gid: str, mid: str, request: Request):
    cab,user,_=context(request);asset=attachment_asset(cab,user,gid)
    if request.headers.get('range'):
        media.listing(cab,user,asset,gid)
        raise HTTPException(416,'当前仅支持有界完整预览，不支持 Range')
    raw,mime=media.preview(cab,user,asset,mid,gid)
    return media_response(request,raw,mime)


@router.get('/api/cabinet/assets/{aid}/attachments')
def attachments_for_owner(aid: str, request: Request):
    cab,user,_=context(request)
    return media.listing(cab,user,aid)


@router.post('/api/cabinet/assets/{aid}/attachments/{mid}/preview')
def attachment_preview_owner(aid: str, mid: str, request: Request):
    cab,user,_=context(request)
    if request.headers.get('range'):
        media.listing(cab,user,aid)
        raise HTTPException(416,'当前仅支持有界完整预览，不支持 Range')
    raw,mime=media.preview(cab,user,aid,mid)
    return media_response(request,raw,mime)


@router.get('/api/cabinet/attachments/staged')
def staged_attachments(request: Request):
    cab,user,_=context(request);owner(user)
    with cab._transaction() as db:
        if not media.installed(db): raise media.MediaUnavailable('请先迁移附件表')
        return [dict(r) for r in db.execute('SELECT id,template_id,version,field,mime,size,expires FROM cabinet_attachments WHERE owner=? AND asset IS NULL ORDER BY expires',(user['id'],))]


class ChunkUpload(Body):
    field: str = Field(min_length=1,max_length=256)
    mime: str = Field(max_length=80)
    size: int = Field(gt=0,le=chunks.MAX_BYTES,strict=True)


@router.post('/api/cabinet/templates/{tid}/{version}/attachments/uploads')
def chunk_start(tid: str, version: int, body: ChunkUpload, request: Request):
    cab,user,_=context(request);owner(user)
    try: return chunks.start(cab,user,tid,version,body.field,body.mime,body.size)
    except ValueError as error: raise HTTPException(422,str(error)) from None


@router.get('/api/cabinet/uploads')
def chunk_list(request: Request):
    cab,user,_=context(request);owner(user)
    with cab._transaction() as db:
        chunks.ready(db)
        return [dict(r) for r in db.execute("SELECT id,template_id,version,field,mime,size,expires,state FROM attachment_uploads WHERE owner=? AND state='uploading' ORDER BY expires",(user['id'],))]


@router.get('/api/cabinet/uploads/{mid}')
def chunk_status(mid: str, request: Request):
    cab,user,_=context(request);owner(user)
    return chunks.status(cab,user,mid)


@router.post('/api/cabinet/uploads/{mid}/chunks/{index}')
async def chunk_put(mid: str, index: int, request: Request):
    import asyncio
    from starlette.concurrency import run_in_threadpool
    cab,user,_=context(request);owner(user)
    chunks.status(cab,user,mid)  # Authorize before reading even one request-body chunk.
    raw=bytearray()
    try:
        async with asyncio.timeout(30):
            async for part in request.stream():
                if len(raw)+len(part)>chunks.CHUNK_BYTES: raise HTTPException(413,'单分块最多 1 MiB')
                raw.extend(part)
    except TimeoutError: raise HTTPException(408,'分块上传超时，可重新提交相同分块') from None
    try: return await run_in_threadpool(chunks.put,cab,user,mid,index,bytes(raw))
    except ValueError as error: raise HTTPException(422,str(error)) from None


@router.post('/api/cabinet/uploads/{mid}/complete')
def chunk_complete(mid: str, request: Request):
    cab,user,_=context(request);owner(user)
    try: return chunks.finish(cab,user,mid)
    except ValueError as error: raise HTTPException(422,str(error)) from None


@router.post('/api/cabinet/uploads/{mid}/cancel')
def chunk_cancel(mid: str, request: Request):
    cab,user,_=context(request);owner(user);chunks.cancel(cab,user,mid)
    return {'cancelled':True}


def login_digest(sid): return hashlib.sha256(sid.encode()).hexdigest()


@router.post('/api/cabinet/assets/{aid}/attachments/{mid}/stream')
def stream_owner(aid: str, mid: str, request: Request):
    cab,user,sid=context(request)
    return chunks.open_stream(cab,user,aid,mid,login_digest(sid))


@router.post('/api/cabinet/grants/{gid}/attachments/{mid}/stream')
def stream_grant(gid: str, mid: str, request: Request):
    cab,user,sid=context(request)
    return chunks.open_stream(cab,user,attachment_asset(cab,user,gid),mid,login_digest(sid),gid)


@router.get('/api/cabinet/streams/{stream_id}')
def stream_read(stream_id: str, request: Request):
    cab,user,sid=context(request)
    try: raw,mime,start,end,total=chunks.read_stream(cab,user,stream_id,login_digest(sid),request.headers.get('range'))
    except chunks.InvalidRange as error: raise HTTPException(416,str(error)) from None
    response=media_response(request,raw,mime)
    response.status_code=206
    response.headers['Accept-Ranges']='bytes'
    response.headers['Content-Range']=f'bytes {start}-{end}/{total}'
    response.headers['Vary']='Cookie, Range'
    response.headers['X-Accel-Buffering']='no'
    return response
