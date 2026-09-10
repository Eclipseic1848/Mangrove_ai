"""固定内部Driver宿主候选；网络资格不由客户端参数授予。"""
import asyncio,hashlib,json,secrets,threading,time
from contextlib import contextmanager,asynccontextmanager
from weakref import WeakValueDictionary
from typing import Annotated
from fastapi import APIRouter,Depends,Header,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from src.api.auth import get_execution_user,get_current_user
from src.api.execution import execution_to_thread

class Empty(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Verify(Empty):
    generation:str=Field(min_length=1,max_length=100)

class DriverHost:
    def __init__(self,repository,images,drivers,*,authorize,continuation=None,source_entries=None):
        self.repository=repository;self.images=images;self.drivers=dict(drivers)
        self.authorize=authorize;self.continuation=continuation;self.source_entries=dict(source_entries or {})
        self.sessions={};self.reservations=set();self.lock=threading.RLock();self.request_locks=WeakValueDictionary()
        self.loop=None;self.jobs={};self.poll_interval=1.0;self.stopping=False
        with repository._db() as db:
            db.execute('SELECT owner,request_id,action,idempotency_key,fingerprint,state,receipt_json FROM authenticated_source_commands LIMIT 0')
    @asynccontextmanager
    async def lifespan(self,app):
        self.loop=asyncio.get_running_loop();self.stopping=False
        try:yield
        finally:
            self.stopping=True
            jobs=list(self.jobs.values())
            for job in jobs:job.cancel()
            # execution_to_thread等真实Driver线程退出，不能取消Future后声称已关闭。
            await asyncio.gather(*jobs,return_exceptions=True)
            for owner,identity in list(self.sessions):
                await execution_to_thread(self._stop_session,owner,identity)
            self.loop=None
    def _stop_session(self,owner,identity):
        with self._serial(owner,identity):
            with self.repository._db() as db:
                db.execute("UPDATE authenticated_source_commands SET receipt_json=NULL WHERE owner=? AND request_id=? AND state='claimed'",(owner,identity))
            self._close(owner,identity)
    def schedule(self,owner,identity,key):
        if self.loop is None or self.stopping:return
        def enqueue():
            if self.stopping or (owner,identity) in self.jobs:return
            job=asyncio.create_task(self._drive_pending(owner,identity,key))
            self.jobs[(owner,identity)]=job
            def finished(done):
                self.jobs.pop((owner,identity),None)
                if not done.cancelled():done.exception()
            job.add_done_callback(finished)
        # 保留发起请求的冻结执行Context；每一轮仍重核持久授权。
        self.loop.call_soon_threadsafe(enqueue)
    async def _drive_pending(self,owner,identity,key):
        try:
            while not self.stopping:
                result=await execution_to_thread(self.command,owner,identity,'verify',key)
                if result['state']=='pending':
                    await asyncio.sleep(self.poll_interval)
                    result=await execution_to_thread(self.poll_pending,owner,identity,key)
                    if result['state']=='pending':continue
                if result['state']=='verified' and not self.stopping:
                    request=await execution_to_thread(self._request,owner,identity)
                    if request['binding']:
                        await self.resume(owner,identity,'auto:'+identity)
                return
        except asyncio.CancelledError:raise
        except Exception:
            # 命令已持久领取；未知控制面不重发，不将异常正文写入回执。
            return
        finally:
            await execution_to_thread(self._stop_session,owner,identity)
    @contextmanager
    def _serial(self,owner,identity):
        # 全局锁仅保护内存索引；外部Driver调用只串行同一待办。
        with self.lock:
            lock=self.request_locks.get((owner,identity))
            if lock is None:
                lock=threading.RLock();self.request_locks[(owner,identity)]=lock
        with lock:yield
    def _receipt(self,row,owner,identity):
        if row['state']=='completed':return json.loads(row['receipt_json'])
        if row['receipt_json'] and (owner,identity) in self.sessions:
            return json.loads(row['receipt_json'])
        return {'state':'unknown','continuation_state':'unknown'}
    def _request(self,owner,identity):
        if self.stopping:raise ValueError('authentication_host_stopping')
        request=self.repository.get_request(owner,identity)
        self.authorize(owner,request)
        return request
    def _driver(self,request):
        driver=self.drivers.get(request['website'])
        if driver is None or driver.website!=request['website'] or self.repository.drivers.get(driver.website)!=driver.version:
            raise ValueError('driver_unavailable')
        return driver
    def _claim(self,owner,identity,action,key,payload):
        digest=hashlib.sha256(json.dumps([identity,payload],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.repository._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT fingerprint,state,receipt_json FROM authenticated_source_commands WHERE owner=? AND action=? AND idempotency_key=?',(owner,action,key)).fetchone()
            if row:
                if row['fingerprint']!=digest:raise ValueError('idempotency_conflict')
                return self._receipt(row,owner,identity)
            prior=db.execute('SELECT 1 FROM authenticated_source_commands WHERE owner=? AND request_id=? AND action=?',(owner,identity,action)).fetchone()
            if prior:raise ValueError('original_command_required')
            db.execute('INSERT INTO authenticated_source_commands VALUES (?,?,?,?,?,\'claimed\',NULL)',(owner,identity,action,key,digest))
        return None
    def _finish(self,owner,action,key,result):
        with self.repository._db() as db:
            db.execute("UPDATE authenticated_source_commands SET state='completed',receipt_json=? WHERE owner=? AND action=? AND idempotency_key=? AND state='claimed'",(json.dumps(result),owner,action,key))
        return result
    def cancel(self,owner,identity):
        request=self.repository.get_request(owner,identity)
        state=self.repository.cancel(owner,identity)
        if state=='claimed' and set(request['binding'])=={'attempt_id'} and self.continuation is not None:
            result=self.continuation.repository.cancel_attempt(owner,request['binding']['attempt_id'])
            return {'state':state,'continuation_state':result['status']}
        # 先撤回持久待办再等待宿主清理，使迟到验证无法覆盖取消。
        with self._serial(owner,identity):self._close(owner,identity)
        return {'state':state,'cleanup_state':'closed'}
    def command(self,owner,identity,action,key):
        self._request(owner,identity)
        with self.repository._db() as db:
            row=db.execute('SELECT state,receipt_json FROM authenticated_source_commands WHERE owner=? AND request_id=? AND action=? AND idempotency_key=?',(owner,identity,action,key)).fetchone()
            if row is None:raise PermissionError('command_not_found')
            return self._receipt(row,owner,identity)
    def _close(self,owner,identity):
        session=self.sessions.get((owner,identity))
        if session:
            session['driver'].close(session['handle'])
            self.sessions.pop((owner,identity),None)
        self.images.discard(owner,identity)
    def _prune(self):
        for owner,identity in list(self.sessions):
            request=self.repository.get_request(owner,identity)
            if request['state']!='pending' or request['expires']<=time.time():
                with self.lock:
                    lock=self.request_locks.get((owner,identity))
                    if lock is None:
                        lock=threading.RLock();self.request_locks[(owner,identity)]=lock
                # 不等待另一待办，避免清扫与取消形成交叉锁等待。
                if lock.acquire(blocking=False):
                    try:self._close(owner,identity)
                    finally:lock.release()
    def start(self,owner,identity,key):
        with self._serial(owner,identity):
            request=self._request(owner,identity);driver=self._driver(request)
            # 先查询原命令，不能在重启后把丢失挑战当作未曾启动。
            with self.repository._db() as db:
                existing=db.execute('SELECT 1 FROM authenticated_source_commands WHERE owner=? AND action=? AND idempotency_key=?',(owner,'start',key)).fetchone()
            if existing:return self._claim(owner,identity,'start',key,{})
            with self.repository._db() as db:
                prior=db.execute("SELECT 1 FROM authenticated_source_commands WHERE owner=? AND request_id=? AND action='start'",(owner,identity)).fetchone()
            if prior:raise ValueError('original_challenge_required')
            self._prune()
            if request['state']!='pending' or request['expires']<=time.time():raise ValueError('request_not_pending')
            with self.lock:
                if (owner,identity) in self.sessions or (owner,identity) in self.reservations or len(self.sessions)+len(self.reservations)>=32:raise ValueError('challenge_busy')
                self.reservations.add((owner,identity))
            try:
                receipt=self._claim(owner,identity,'start',key,{})
            except BaseException:
                # 尚未调用Driver，无资源副作用，可以释放预留。
                with self.lock:self.reservations.discard((owner,identity))
                raise
            if receipt is not None:
                with self.lock:self.reservations.discard((owner,identity))
                return receipt
            # Driver创建结果未知时保留预留，不能用错误返回假称资源不存在。
            handle=driver.start(owner,request)
            generation=secrets.token_urlsafe(24)
            with self.lock:
                self.sessions[(owner,identity)]={'driver':driver,'handle':handle,'generation':generation}
                self.reservations.discard((owner,identity))
            try:
                image=driver.challenge_image(handle)
                challenge_expires_at=None
                if image is not None:
                    image_record=self.images.publish(owner,identity,image['content'],image['media_type'])
                    generation=image_record['generation']
                    challenge_expires_at=image_record['expires']
                    self.sessions[(owner,identity)]['generation']=generation
                self._request(owner,identity)
                if self.repository.get_request(owner,identity)['state']!='pending':raise ValueError('request_not_pending')
                return self._finish(owner,'start',key,{'state':'challenge','generation':generation,'request_id':identity,'challenge_expires_at':challenge_expires_at})
            except BaseException:
                self._close(owner,identity);raise
    def verify(self,owner,identity,key,generation):
        with self._serial(owner,identity):
            request=self._request(owner,identity)
            with self.repository._db() as db:
                existing=db.execute('SELECT 1 FROM authenticated_source_commands WHERE owner=? AND action=? AND idempotency_key=?',(owner,'verify',key)).fetchone()
            if existing:return self._claim(owner,identity,'verify',key,{'generation':generation})
            self._prune();session=self.sessions.get((owner,identity))
            if session is None or session['generation']!=generation:raise ValueError('challenge_unavailable')
            receipt=self._claim(owner,identity,'verify',key,{'generation':generation})
            if receipt is not None:return receipt
            return self._poll(owner,identity,key,session)
    def poll_pending(self,owner,identity,key):
        # 仅固定宿主调度调用；HTTP查询和同键重放不触发网络读取。
        with self._serial(owner,identity):
            self._request(owner,identity)
            with self.repository._db() as db:
                row=db.execute("SELECT state,receipt_json FROM authenticated_source_commands WHERE owner=? AND request_id=? AND action='verify' AND idempotency_key=?",(owner,identity,key)).fetchone()
            if row is None:raise PermissionError('command_not_found')
            receipt=self._receipt(row,owner,identity)
            if receipt.get('state')!='pending':return receipt
            return self._poll(owner,identity,key,self.sessions[(owner,identity)])
    def _poll(self,owner,identity,key,session):
            keep_open=False
            try:
                # 下一次外部调用开始即撤回pending回执；异常后不能自动再发。
                with self.repository._db() as db:
                    db.execute("UPDATE authenticated_source_commands SET receipt_json=NULL WHERE owner=? AND request_id=? AND action='verify' AND idempotency_key=? AND state='claimed'",(owner,identity,key))
                request=self._request(owner,identity)
                if request['state']!='pending' or request['expires']<=time.time():
                    outcome='expired' if request['expires']<=time.time() else request['state']
                    self._close(owner,identity)
                    return self._finish(owner,'verify',key,{'state':outcome,'request_id':identity})
                polled=session['driver'].poll(session['handle'])
                self._request(owner,identity)
                if not isinstance(polled,dict) or polled.get('status') not in ('pending','verified','expired','blocked'):raise ValueError('driver_poll_invalid')
                if polled['status']=='pending':
                    result={'state':'pending','request_id':identity}
                    with self.repository._db() as db:
                        db.execute("UPDATE authenticated_source_commands SET receipt_json=? WHERE owner=? AND request_id=? AND action='verify' AND idempotency_key=? AND state='claimed'",(json.dumps(result),owner,identity,key))
                    keep_open=True
                    return result
                if polled['status']!='verified':
                    outcome=polled['status']
                    self._close(owner,identity)
                    return self._finish(owner,'verify',key,{'state':outcome,'request_id':identity})
                state=polled.get('state')
                if not isinstance(state,dict):raise ValueError('driver_poll_invalid')
                real_driver=session['driver']
                host=self
                class AuthorizedDriver:
                    website=real_driver.website
                    version=real_driver.version
                    def verify(self,*args):
                        result=real_driver.verify(*args)
                        host._request(owner,identity)
                        return result
                outcome=self.repository.confirm(owner,identity,state,AuthorizedDriver(),now=time.time)
                # 秘密状态只传内部Vault边界；回执只允许状态枚举。
            finally:
                if not keep_open:self._close(owner,identity)
            # 清理完成后才能保存已知回执；close失败保留命令unknown。
            return self._finish(owner,'verify',key,{'state':outcome,'request_id':identity})
    def refresh(self,owner,identity,key):
        with self._serial(owner,identity):return self._refresh_locked(owner,identity,key)
    def _refresh_locked(self,owner,identity,key):
        request=self._request(owner,identity)
        entry=self.source_entries.get(request['website'])
        if entry is None:raise ValueError('refresh_unavailable')
        with self.repository._db() as db:
            exists=db.execute("SELECT 1 FROM authenticated_source_commands WHERE owner=? AND action='refresh' AND idempotency_key=?",(owner,key)).fetchone()
        if not exists:
            with self.repository._db() as db:
                db.execute('BEGIN IMMEDIATE')
                unknown=db.execute("SELECT 1 FROM authenticated_source_commands WHERE owner=? AND request_id=? AND action IN ('start','verify') AND state='claimed' AND receipt_json IS NULL",(owner,identity)).fetchone()
                if unknown:raise ValueError('refresh_result_unknown')
                started=db.execute("SELECT receipt_json FROM authenticated_source_commands WHERE owner=? AND request_id=? AND action='start' AND state='completed'",(owner,identity)).fetchone()
                expires=json.loads(started[0]).get('challenge_expires_at') if started else None
                # 使用服务端持久的图像期限，不把未扫码待办十分钟误当二维码寿命。
                if type(expires) in (int,float) and expires<=time.time():
                    db.execute("UPDATE source_reauthentication_requests SET expires=MIN(expires,?) WHERE owner=? AND request_id=? AND state='pending'",(expires,owner,identity))
            request=self.repository.get_request(owner,identity)
        if not exists and (request['state'] not in ('pending','expired') or request['expires']>time.time()):raise ValueError('refresh_not_allowed')
        receipt=self._claim(owner,identity,'refresh',key,{})
        if receipt is not None:return receipt
        with self._serial(owner,identity):self._close(owner,identity)
        renewed=entry.refresh_expired(owner,identity,key)
        return self._finish(owner,'refresh',key,{'request_id':renewed['request_id'],'state':renewed['state']})
    async def resume(self,owner,identity,key):
        request=self._request(owner,identity)
        if not request['binding']:return {'state':request['state'],'continuation_state':'not_applicable'}
        if set(request['binding'])!={'attempt_id'} or self.continuation is None:raise ValueError('continuation_unavailable')
        receipt=self._claim(owner,identity,'resume',key,{})
        if receipt is not None:return receipt
        result=await self.continuation.resume(owner,identity)
        # 不将reader原结果正文存入命令回执；只持久原attempt与真实终态。
        receipt={'attempt_id':result['attempt_id'],'continuation_state':result['continuation_state'],'state':result['result']['status'],'snapshot_id':result['result'].get('snapshot_id')}
        if 'run_receipts' in result:receipt['run_receipts']=[{field:item[field] for field in ('task_id','revision','run_id','status')} for item in result['run_receipts']]
        return self._finish(owner,'resume',key,receipt)

def router_for(host):
    router=APIRouter(prefix='/api/authenticated-sources')
    def public_error(exc):
        # Driver异常可能含Cookie或带凭据URL；仅固定业务码可以外发。
        known={'idempotency_conflict','original_command_required','original_challenge_required','request_not_pending','challenge_busy','challenge_unavailable','driver_unavailable','refresh_unavailable','refresh_not_allowed','continuation_unavailable'}
        return str(exc) if str(exc) in known else 'authentication_operation_unknown'
    def convert(call):
        try:return call()
        except PermissionError:raise HTTPException(404,'认证待办不存在') from None
        except ValueError as exc:raise HTTPException(409,public_error(exc)) from None
        except Exception:raise HTTPException(409,'authentication_operation_unknown') from None
    @router.post('/requests/{identity}/cancel')
    def cancel(identity:str,body:Empty,user=Depends(get_current_user)):
        return convert(lambda:host.cancel(user['user_id'],identity))
    @router.get('/requests/{identity}/commands/{action}/{key}')
    def command(identity:str,action:str,key:str,user=Depends(get_execution_user)):
        return convert(lambda:host.command(user['user_id'],identity,action,key))
    @router.post('/requests/{identity}/start')
    def start(identity:str,body:Empty,key:Annotated[str,Header(alias='Idempotency-Key',min_length=1,max_length=200)],user=Depends(get_execution_user)):
        return convert(lambda:host.start(user['user_id'],identity,key))
    @router.post('/requests/{identity}/verify')
    def verify(identity:str,body:Verify,key:Annotated[str,Header(alias='Idempotency-Key',min_length=1,max_length=200)],user=Depends(get_execution_user)):
        result=convert(lambda:host.verify(user['user_id'],identity,key,body.generation))
        if result.get('state') in ('pending','verified'):host.schedule(user['user_id'],identity,key)
        return result
    @router.post('/requests/{identity}/refresh')
    def refresh(identity:str,body:Empty,key:Annotated[str,Header(alias='Idempotency-Key',min_length=1,max_length=200)],user=Depends(get_execution_user)):
        return convert(lambda:host.refresh(user['user_id'],identity,key))
    @router.post('/requests/{identity}/resume')
    async def resume(identity:str,body:Empty,key:Annotated[str,Header(alias='Idempotency-Key',min_length=1,max_length=200)],user=Depends(get_execution_user)):
        try:return await host.resume(user['user_id'],identity,key)
        except PermissionError:raise HTTPException(404,'认证待办不存在') from None
        except ValueError as exc:raise HTTPException(409,public_error(exc)) from None
        except Exception:raise HTTPException(409,'authentication_operation_unknown') from None
    return router


def combined_router(host):
    from src.source_acquisition.authenticated_http import router_for as read_router
    result=APIRouter(lifespan=host.lifespan)
    reads=read_router(host.repository,host.images)
    # 保留既有安全查询/图像与真实站点unavailable；取消必须经宿主清理。
    reads.routes=[route for route in reads.routes if not route.path.endswith('/cancel')]
    result.include_router(reads);result.include_router(router_for(host))
    return result
