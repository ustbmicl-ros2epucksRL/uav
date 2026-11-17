"""Bounded local process orchestration, independent of the MAVLink reader.

HEFT remains the upstream placement method. Actual local IPC timings are measured;
configured radio ranges gate dispatch, not falsely reported as measured WAN links.
"""
import copy
import json
import multiprocessing
import queue
import threading
import time
import uuid
from .algorithm_compute import evaluate, transfer
from .compute_worker import worker, DEPENDENCIES
from . import compute_store
from .compute_feedback import Feedback
from .compute_network import Network


class Runtime:
    def __init__(self, binding, record_id, interval_s=2, timeout_s=30, max_attempts=3, workload='video'):
        config = binding['algorithm']['compute']
        if {t['id']: sorted(t['parents']) for t in config['tasks']} != {k: sorted(v) for k, v in DEPENDENCIES.items()}:
            raise ValueError('实际执行仅支持 preprocess/detect/segment/fusion/alert 固定任务链')
        self.workload = workload
        if workload == 'video':
            from .video_workload import available
            if not available():raise ValueError('测试视频/模型未就绪，请运行 scripts/setup_video_workload.py')
        self.video = None
        self.id = uuid.uuid4().hex
        self.binding = copy.deepcopy(binding)
        self.record_id = record_id
        self.config = copy.deepcopy(config)
        self.interval_s, self.timeout_s, self.max_attempts = interval_s, timeout_s, max_attempts
        self.lock = threading.Lock()
        self.latest = None
        self.cached = None
        self.faults = {}
        self.node_requests = []
        self.network_requests = []
        self.demo = None
        self.demo_request = None
        self.network = Network(config['links'])
        self.network_nodes = {n['id']:copy.deepcopy(n) for n in config['nodes']}
        self.network_timeout_s = 8
        self.offline = {}
        self.feedback = Feedback()
        self.scheduling_events = []
        self.last_assignment = {}
        self.stop_event = threading.Event()
        self.ctx = multiprocessing.get_context('spawn')
        self.events = []
        self.nodes = {}
        self.active = []
        self.recent = None
        self.counts = dict(completed=0, failed=0, cancelled=0, retries=0)
        self.next_batch = 0
        self.state = 'starting'
        self.reason = ''
        self.started_at = time.time()
        self.thread = threading.Thread(target=self._run, name='compute-orchestrator', daemon=True)

    def start(self):
        self.thread.start()

    def offer(self, sample, terminal=False):
        # Called by MAVLink reader: no computation, disk IO, waits or process joins.
        with self.lock:
            self.latest = dict(sample=copy.deepcopy(sample), terminal=terminal)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.cached)

    def inject(self, task, mode):
        with self.lock:
            if self.stop_event.is_set() or self.state in ('completed', 'stopped', 'error'):
                raise ValueError('计算执行已结束')
            self.faults[task] = mode

    def node_action(self, node_id, action, duration_s=10):
        with self.lock:
            if node_id not in {n['id'] for n in self.config['nodes']}:
                raise ValueError('未知节点')
            if self.stop_event.is_set() or self.state in ('completed', 'stopped', 'error'):
                raise ValueError('计算执行已结束')
            if len(self.node_requests) >= 16:raise ValueError('节点请求过多')
            self.node_requests.append((node_id, action, duration_s))

    def network_action(self, profile, duration_s=10, mbps=1, latency_ms=200):
        with self.lock:
            if self.stop_event.is_set() or self.state in ('completed','stopped','error'):
                raise ValueError('计算执行已结束')
            if len(self.network_requests)>=16:raise ValueError('链路请求过多')
            self.network_requests.append((profile,duration_s,mbps,latency_ms))

    def demo_action(self, action):
        with self.lock:
            if self.stop_event.is_set() or self.state in ('completed', 'stopped', 'error'):
                raise ValueError('计算执行已结束')
            if action != 'cancel' and (self.demo_request or self.demo and self.demo['state'] == 'running'):
                raise ValueError('演示正在进行')
            self.demo_request = action

    def _demo_tick(self, now):
        with self.lock:
            request, self.demo_request = self.demo_request, None
        if request in ('start', 'short_transfer'):
            self.demo = dict(state='running', phase='normal', started_at=now, phase_at=now,
                             evidence=[], completed_before=self.counts['completed'], reason='等待正常检测结果')
            if request == 'short_transfer':
                self.demo['short_probe_pending'] = True
            self.network_action('normal')
            self._event('demo_phase', now, phase='normal')
        d = self.demo
        if not d or d['state'] != 'running':return
        def restore(state, reason):
            self.network_action('normal')
            for node in list(self.offline):self.node_action(node, 'recover')
            d.update(state=state, phase='restored', reason=reason, finished_at=now)
            self._event('demo_finished', now, state=state, reason=reason)
        if request == 'cancel':
            restore('cancelled', '演示已中止，恢复链路与节点');return
        if now-d['started_at'] > 100:
            restore('incomplete', '等待执行证据超时；未将场景注入视为验证成功');return
        for e in self.scheduling_events:
            if e['at']>=d['started_at'] and e['kind'] in ('task_migrated','task_recovered','transfer_failed','node_offline','node_recovered') and e not in d['evidence']:
                d['evidence'].append(copy.deepcopy(e))
        phase=d['phase']
        next_phase=None
        if d.get('short_probe_pending') and self.counts['completed'] > d['completed_before']:
            token = uuid.uuid4().hex
            probe = self.network.begin(token, 'device', 'edge-a', 64, self.network_nodes, now)
            if probe['status'] == 'moving':
                d.update(short_probe_pending=False, short_token=token, transfer_token=token)
                self.network_action('weak', 60, .1, 200)
                d.update(phase='weak', phase_at=now)
            else:
                self.network.flows.pop(token, None)
            return
        moving = [flow for flow in self.network.flows.values() if flow['status'] == 'moving']
        if phase == 'normal' and self.counts['completed'] > d['completed_before'] and moving:
            d['transfer_token'] = max(moving, key=lambda flow: flow['remaining_mbit'])['token']
            self.network_action('weak', 60, .1, 200)
            next_phase='weak'
        elif phase == 'weak' and self.network.profile == 'weak':
            flow = self.network.flows.get(d['transfer_token'])
            if flow and flow['token'] == d.get('short_token') and flow['status'] == 'moving':
                return
            if flow and flow['status'] == 'moving':
                d['weak_transfer'] = copy.deepcopy(flow)
                d['weak_observed_at'] = now
                self.network_action('disconnected', 4)
                next_phase = 'disconnected'
            else:
                if flow and flow['token'] == d.get('short_token'):
                    if flow['status'] == 'completed':
                        d['short_completed'] = dict(flow, finished_at=now)
                    self.network.flows.pop(flow['token'], None)
                # Local scheduling under weak links may never start another remote transfer.
                self.network_action('normal')
                next_phase = 'normal'
        elif phase == 'disconnected' and now-d['phase_at'] >= 5:
            self.network_action('normal')
            next_phase='node_wait'
        elif phase == 'node_wait':
            target=next((ident for ident,n in self.nodes.items() if n['current'] and ident not in self.offline),None)
            if target:
                self.node_action(target,'offline',5)
                d['node']=target
                next_phase='node_offline'
        elif phase == 'node_offline' and now-d['phase_at'] >= 6:
            next_phase='recovery'
        elif phase == 'recovery':
            events=d['evidence']
            recovered=any(e['kind']=='task_recovered' for e in events)
            migrated=any(e['kind']=='task_migrated' for e in events)
            broken=any(e['kind']=='transfer_failed' for e in events)
            node_failed=any(e.get('error')=='node_offline' and e.get('recovered') for e in self.events)
            if recovered and migrated and broken and node_failed and not self.offline and self.counts['completed']>d['completed_before']+1:
                restore('completed','场景完成，已记录传输失败、任务迁移及重试成功');return
        if next_phase:
            d.update(phase=next_phase,phase_at=now,reason='等待该阶段执行证据')
            self._event('demo_phase',now,phase=next_phase)

    def _network_tick(self, now, sample):
        if sample and sample.get('status')=='ok':
            self.network_nodes['device']['position']=sample['position']
        # Advance old interval before applying a new link profile.
        self.network.advance(now,self.network_nodes)
        self._demo_tick(now)
        with self.lock:
            requests,self.network_requests=self.network_requests,[]
        if self.network.until and now>=self.network.until:
            requests.insert(0,('normal',10,1,200))
        for profile,duration,mbps,latency in requests:
            self.network.configure(profile,now,duration,mbps,latency)
            self._event('link_changed',now,transfer_token=(self.demo or {}).get('transfer_token') if profile in ('weak','disconnected') else None,profile=profile,mbps=mbps,latency_ms=latency,
                        revision=self.network.revision,restore_at=self.network.until)
        self.network.advance(now,self.network_nodes)
        for ident,node in self.nodes.items():
            current=node['current']
            if not current or current.get('phase') not in ('upload','download'):continue
            flow=self.network.flows.get(current['token'])
            if not flow:continue
            if flow['status']=='moving' and now-current['phase_at']>self.network_timeout_s:
                flow.update(status='failed',error='network_timeout')
            if flow['status']=='moving':continue
            batch,job=self._job(current['token'])
            if not job:
                self.network.flows.pop(current['token'],None);node['current']=None;continue
            if job:
                job['attempts'][-1].setdefault('transfers',[]).append(dict(flow,phase=current['phase'],finished_at=now,elapsed_s=now-current['phase_at']))
            self.network.flows.pop(current['token'],None)
            current['network_wait_s']+=max(0,now-current['phase_at'])
            if flow['status']=='failed':
                self._event('transfer_failed',now,transfer_token=current['token'],node=ident,batch_id=batch['id'] if batch else None,
                            task=job['id'] if job else None,reason=flow['error'],phase=current['phase'])
                self._finish_attempt(ident,dict(token=current['token'],status='failed',node=ident,
                    pid=node['process'].pid,error=flow['error']),now)
            elif current['phase']=='upload':
                current.update(phase='compute',worker_sent_at=now)
                node['inbox'].put_nowait(current.pop('payload'))
            else:
                self._finish_attempt(ident,current.pop('response'),now)

    def _event(self, kind, now, **details):
        self.scheduling_events.append(dict(kind=kind, at=now, **details))
        self.scheduling_events = self.scheduling_events[-64:]

    def _down(self, ident, now, duration, reason):
        node = self.nodes[ident]
        if ident not in self.offline:
            self._kill(ident)
            if node['current']:
                self._finish_attempt(ident, dict(token=node['current']['token'], status='failed',
                    error=reason, node=ident, pid=node['process'].pid), now)
        self.offline[ident] = now+duration
        self._event('node_offline', now, node=ident, reason=reason, recover_at=now+duration)

    def _node_changes(self, now):
        with self.lock:
            requests, self.node_requests = self.node_requests, []
        for ident, action, duration in requests:
            if action == 'offline':self._down(ident, now, duration, 'node_offline')
            elif ident in self.offline:self.offline[ident] = now
        for ident, until in list(self.offline.items()):
            if now >= until:
                self._spawn(ident)
                del self.offline[ident]
                self._event('node_recovered', now, node=ident, pid=self.nodes[ident]['process'].pid)

    def _loads(self, now):
        result = {}
        for ident, node in self.nodes.items():
            node['intervals'] = [(a,b) for a,b in node['intervals'] if b > now-10]
            intervals = node['intervals'] + ([(node['current']['sent_at'],now)] if node['current'] else [])
            result[ident] = min(1, sum(max(0,min(b,now)-max(a,now-10)) for a,b in intervals)/min(10,max(.001,now-self.started_at)))
        return result

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=8)
        if self.thread.is_alive():
            raise RuntimeError('计算线程仍在退出，请稍后查询状态')

    def _spawn(self, ident):
        inbox = self.ctx.Queue(maxsize=2)
        outbox = self.ctx.Queue(maxsize=2)
        process = self.ctx.Process(target=worker, args=(ident, inbox, outbox), daemon=True)
        process.start()
        old = self.nodes.get(ident, {})
        self.nodes[ident] = dict(process=process, inbox=inbox, outbox=outbox, current=None,
                                 intervals=old.get('intervals', []), restarts=old.get('restarts', -1)+1)

    def _kill(self, ident):
        node = self.nodes[ident]
        if node.get('closed'):return
        node['closed'] = True
        if node['process'].is_alive():node['process'].terminate()
        node['process'].join(timeout=1)
        if node['process'].is_alive():
            node['process'].kill();node['process'].join(timeout=1)
        node['inbox'].cancel_join_thread();node['inbox'].close()
        node['outbox'].cancel_join_thread();node['outbox'].close()

    def _fresh(self, sample, now):
        return bool(sample and sample.get('status') == 'ok' and 0 <= now-sample.get('telemetry_at', 0) < 3)

    def _new_batch(self, sample, now):
        self.active.append(dict(id=uuid.uuid4().hex, created_at=now, telemetry_at=sample['telemetry_at'],
            position=sample['position'], status='running', outputs={},
            video=self.video.capture() if self.video else None,
            tasks={k:dict(id=k, status='waiting', attempts=[], ready_at=None) for k in DEPENDENCIES}))
        self.next_batch = now + self.interval_s

    def _job(self, token):
        for batch in self.active:
            for job in batch['tasks'].values():
                if job.get('token') == token and job['status'] == 'running':return batch, job
        return None, None

    def _finish_attempt(self, node_id, response, now):
        node = self.nodes[node_id]; token = response['token']
        if not node['current'] or node['current']['token'] != token:return
        current=node['current']
        sent = current['sent_at'];node['current'] = None
        interrupted=self.network.flows.pop(token,None)
        if interrupted:current['network_wait_s']+=max(0,now-current['phase_at'])
        node['intervals'].append((sent, now))
        batch, job = self._job(token)
        if not job:return  # Late response from a cancelled/older attempt cannot commit.
        attempt = job['attempts'][-1]
        if interrupted:
            attempt.setdefault('transfers',[]).append(dict(interrupted,status='cancelled',error=response.get('error'),
                phase=current['phase'],finished_at=now,elapsed_s=now-current['phase_at']))
        attempt.update({k:v for k,v in response.items() if k not in ('output','token')})
        attempt.update(received_at=now, roundtrip_s=max(0, now-sent),network_wait_s=current.get('network_wait_s',0),
                       worker_roundtrip_s=response.get('worker_roundtrip_s'))
        if response['status'] != 'completed':
            self.events.append(dict(at=now,batch_id=batch['id'],task=job['id'],node=node_id,attempt=len(job['attempts']),error=response.get('error'),will_retry=len(job['attempts'])<self.max_attempts))
            self.events=self.events[-8:]
        if response['status'] == 'completed':
            self.feedback.observe(job['id'], node_id, response, response.get('worker_roundtrip_s',max(0, now-sent)))
            for event in self.events:
                if event['batch_id']==batch['id'] and event['task']==job['id']:
                    event.update(recovered=True,recovered_at=now)
            if len(job['attempts']) > 1:
                self._event('task_recovered',now,batch_id=batch['id'],task=job['id'],node=node_id,frame_sha256=attempt.get('frame_sha256'))
            job.update(status='completed', completed_at=now, node=node_id, output_sha256=response['sha256'])
            batch['outputs'][job['id']] = response['output']
        elif len(job['attempts']) < self.max_attempts:
            job.update(status='retry_wait', retry_at=now+.2*len(job['attempts']))
            self.counts['retries'] += 1
        else:
            job.update(status='failed', completed_at=now)

    def _collect(self, now):
        for ident, node in list(self.nodes.items()):
            if ident in self.offline:continue
            while True:
                try:result = node['outbox'].get_nowait()
                except queue.Empty:break
                current=node['current']
                if not current or current['token']!=result['token'] or current.get('phase')!='compute':continue
                result['worker_roundtrip_s']=max(0,now-current['worker_sent_at'])
                if result['status']!='completed':self._finish_attempt(ident,result,now)
                else:
                    current.update(phase='download',phase_at=now,response=result)
                    self.network.begin(result['token'],ident,'device',result['output_bytes'],self.network_nodes,now)
        for ident, node in list(self.nodes.items()):
            if ident in self.offline:continue
            current = node['current']
            if current and (not node['process'].is_alive() or (current.get('phase')=='compute' and now-current['worker_sent_at'] > self.timeout_s)):
                error = 'worker_exit' if not node['process'].is_alive() else 'timeout'
                response = dict(token=current['token'], status='failed', error=error, node=ident, pid=node['process'].pid)
                self._down(ident,now,1,error)
            elif not current and not node['process'].is_alive():
                self._down(ident,now,1,'worker_exit')

    def _dispatch(self, sample, now):
        config = copy.deepcopy(self.config)
        config['links']=self.network.links()
        costs, evidence = self.feedback.matrix(config,self._loads(now))
        for n in config['nodes']:
            current = self.nodes[n['id']]['current']
            n['busy_s'] = max(.03, current.get('estimated_s',.1)-(now-current['sent_at'])) if current else 0
        plan = evaluate(config, sample['position'],costs=costs,unavailable=self.offline)
        assignments = {r['id']:r['node'] for r in plan['rows']}
        nodes = {n['id']:dict(n) for n in config['nodes']}
        nodes['device']['position'] = sample['position']
        for batch in self.active:
            for ident, parents in DEPENDENCIES.items():
                job = batch['tasks'][ident]
                if job['status'] not in ('waiting','retry_wait') or now < job.get('retry_at',0):continue
                if any(batch['tasks'][p]['status'] in ('failed','cancelled') for p in parents):
                    job.update(status='cancelled', error='dependency_failed', completed_at=now);continue
                if not all(p in batch['outputs'] for p in parents):continue
                if job['ready_at'] is None:job['ready_at'] = now
                target = assignments.get(ident)
                recovery = None
                if job['attempts'] and target==job['attempts'][-1]['node'] and target!='device' and 'device' not in self.offline:
                    target='device';recovery='local_fallback'
                if not target or target in self.offline or self.nodes[target]['current']:continue
                # Parent results are checkpointed at the coordinator; offline producer is not required.
                sources = ['device']
                if any(transfer(source,target,0,nodes,config['links'])[0] is None for source in sources):continue
                token = uuid.uuid4().hex
                with self.lock:fault = self.faults.pop(ident,None)
                inputs = {p:batch['outputs'][p] for p in parents}
                if ident == 'preprocess' and batch.get('video'):inputs['video'] = batch['video']
                payload = dict(token=token,task=ident,inputs=inputs,position=batch['position'],fault=fault,workload=self.workload)
                previous = job['attempts'][-1]['node'] if job['attempts'] else self.last_assignment.get(ident)
                reason = 'failure_migration' if job['attempts'] else 'measured_cost_load_telemetry'
                decision = dict(policy='HEFT+EWMA',revision=self.feedback.revision,previous_node=previous,
                    target=target,reason=reason,candidates=copy.deepcopy(evidence[ident]),offline=sorted(self.offline),network_revision=self.network.revision,network_profile=self.network.profile)
                if previous != target or job['attempts']:
                    self._event('task_migrated' if job['attempts'] else 'placement_changed',now,batch_id=batch['id'],
                        task=ident,previous_node=previous,node=target,reason=reason,revision=self.feedback.revision,transfer_token=job['attempts'][-1].get('token') if job['attempts'] else None)
                self.last_assignment[ident] = target
                attempt = dict(token=token,decision=decision,number=len(job['attempts'])+1,node=target,sent_at=now,status='running',
                               telemetry_at=sample['telemetry_at'],dispatch_position=sample['position'],fault=fault,recovery=recovery,
                               queue_s=max(0,now-job['ready_at']),
                               frame_sha256=(batch.get('video') or {}).get('frame',{}).get('sha256'))
                job['attempts'].append(attempt)
                job.update(status='running',token=token,node=target,predicted_s=costs[ident][target])
                self.nodes[target]['current'] = dict(token=token,sent_at=now,estimated_s=costs[ident][target],
                    phase='upload',phase_at=now,payload=payload,network_wait_s=0)
                self.network.begin(token,'device',target,len(json.dumps(payload).encode()),self.network_nodes,now)

    def _archive_batches(self, now):
        for batch in list(self.active):
            if now-batch['created_at'] > max(15, self.timeout_s * self.max_attempts + 15):
                for job in batch['tasks'].values():
                    if job['status'] not in ('completed','failed','cancelled'):
                        job.update(status='failed',error='batch_deadline',completed_at=now)
                        if job['attempts'] and job['attempts'][-1]['status']=='running':job['attempts'][-1].update(status='cancelled',error='batch_deadline',received_at=now)
            if all(j['status'] in ('completed','failed','cancelled') for j in batch['tasks'].values()):
                batch['status'] = 'completed' if all(j['status']=='completed' for j in batch['tasks'].values()) else 'failed'
                batch.update(finished_at=now,latency_s=now-batch['created_at'],output=batch['outputs'].get('alert'))
                self.counts[batch['status']] += 1
                self.active.remove(batch)
                self.recent = {k:copy.deepcopy(v) for k,v in batch.items() if k not in ('outputs','video')}
                self._publish(now, self.recent)

    def _publish(self, now, batch=None):
        nodes=[]
        for ident,n in self.nodes.items():
            n['intervals'] = [(a,b) for a,b in n['intervals'] if b>now-10]
            intervals=n['intervals']+([(n['current']['sent_at'],now)] if n['current'] else [])
            busy=sum(max(0,min(b,now)-max(a,now-10)) for a,b in intervals)
            nodes.append(dict(id=ident,pid=n['process'].pid,alive=n['process'].is_alive(),
                              busy=bool(n['current']),busy_fraction_10s=min(1,busy/min(10,max(.001,now-self.started_at))),restarts=n['restarts'],offline=ident in self.offline,recover_at=self.offline.get(ident)))
        current = self.active[-1] if self.active else None
        public = {k:copy.deepcopy(v) for k,v in current.items() if k not in ('outputs','video')} if current else None
        snapshot = dict(runtime_id=self.id,record_id=self.record_id,execution_id=self.binding['execution_id'],
                        task_id=self.binding['task_id'],experiment_id=self.binding['algorithm']['experiment_id'],
                        at=now,state=self.state,reason=self.reason,counts=dict(self.counts),nodes=nodes,events=copy.deepcopy(self.events),
                        demo=copy.deepcopy(self.demo),network=self.network.snapshot(),scheduling_events=copy.deepcopy(self.scheduling_events),feedback_revision=self.feedback.revision,
                        current=public,last_batch=copy.deepcopy(self.recent),source='local-process-execution',workload=self.workload)
        try:
            compute_store.save(snapshot,batch)
        except Exception as exc:
            snapshot['storage_error']=str(exc)
            self.reason='执行证据写入失败';self.stop_event.set()
        with self.lock:self.cached=snapshot

    def _run(self):
        last_publish=0
        last_phase=None
        try:
            if self.workload == 'video':
                from .video_workload import Source
                self.video = Source()
            for n in self.config['nodes']:self._spawn(n['id'])
            while not self.stop_event.is_set():
                now=time.time()
                with self.lock:latest=copy.deepcopy(self.latest)
                sample=latest['sample'] if latest else None
                terminal=bool(latest and latest['terminal'])
                fresh=self._fresh(sample,now)
                self._node_changes(now)
                self._network_tick(now,sample)
                self._collect(now)
                now=time.time()
                self.state='draining' if terminal else 'running' if fresh else 'paused'
                self.reason='飞行结束，等待已接收计算完成' if terminal else '' if fresh else '遥测过期/缺失，暂停新任务派发'
                if fresh and not terminal and now>=self.next_batch and len(self.active)<2:self._new_batch(sample,now)
                if fresh:
                    self._dispatch(sample,now)
                    # A short upload can finish before the next coordinator tick.
                    if self.demo and self.demo['phase'] == 'normal' and self.network.flows:
                        self._network_tick(now, sample)
                self._archive_batches(now)
                if terminal and not self.active:
                    self.state='completed';self.reason='飞行与计算均已结束';break
                phase_key=(self.network.revision, (self.demo or {}).get('phase'), (self.demo or {}).get('state'))
                if now-last_publish>=.5 or phase_key!=last_phase:
                    self._publish(now);last_publish=now;last_phase=phase_key
                self.stop_event.wait(.03)
        except Exception as exc:
            self.state='error';self.reason=str(exc)
        finally:
            if self.demo and self.demo['state']=='running':
                self.demo.update(state='incomplete',reason='计算执行结束，演示未完成',finished_at=time.time())
            self.network.configure('normal',time.time())
            if self.video:self.video.close()
            now=time.time()
            for b in self.active:
                for j in b['tasks'].values():
                    if j['status'] not in ('completed','failed','cancelled'):
                        j.update(status='cancelled',error='runtime_stopped',completed_at=now)
                        if j['attempts'] and j['attempts'][-1]['status']=='running':j['attempts'][-1].update(status='cancelled',error='runtime_stopped',received_at=now)
                b.update(status='cancelled',finished_at=now,latency_s=now-b['created_at'])
                self.counts['cancelled']+=1
                self.recent={k:copy.deepcopy(v) for k,v in b.items() if k not in ('outputs','video')}
                self._publish(now,self.recent)
            self.active.clear()
            self.network.flows.clear()
            for ident in self.nodes:
                self._kill(ident)
                self.nodes[ident]['current']=None
            if self.state not in ('completed','error'):self.state='stopped'
            self._publish(time.time())



GUARD=threading.Lock()
CURRENT=None


def start(binding,record_id):
    global CURRENT
    with GUARD:
        if CURRENT and CURRENT.thread.is_alive():
            if CURRENT.binding['execution_id']==binding['execution_id']:return CURRENT
            raise ValueError('已有其他任务计算中，请先停止')
        CURRENT=Runtime(binding,record_id)
        CURRENT.start()
        return CURRENT


def observe(state,now):
    runtime=CURRENT
    if not runtime or not runtime.thread.is_alive():return
    binding=state.get('execution_binding',{})
    if binding.get('execution_id')!=runtime.binding['execution_id']:
        runtime.offer(None,terminal=True);return
    sample=state.get('algorithm_live')
    runtime.offer(sample,terminal=state.get('recording_complete',False))


def snapshot(execution_id=None):
    runtime=CURRENT
    if not runtime or (execution_id and runtime.binding['execution_id']!=execution_id):return None
    return runtime.snapshot()


def shutdown():
    if CURRENT:CURRENT.stop()
