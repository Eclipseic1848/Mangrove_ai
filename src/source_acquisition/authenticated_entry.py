"""显式认证来源的原_read接缝；匿名HTTP失败不推断登录。"""
import asyncio,json,time,hashlib
from urllib.parse import urlsplit
from src.source_acquisition.service import SourceAcquisitionService,AnonymousWebFetcher

class DetectedSourceService(SourceAcquisitionService):
    def __init__(self,repository,authentication,driver,*,anonymous_fetcher=None):
        super().__init__(repository,None)
        self.authentication=authentication;self.driver=driver
        self.anonymous_fetcher=anonymous_fetcher if anonymous_fetcher is not None else AnonymousWebFetcher()
        if authentication.drivers.get(driver.website)!=driver.version:raise ValueError('driver_unavailable')
    async def acquire(self,*,owner_id,idempotency_key,request):
        if request.scope_kind=="public_search":self._scope(request)
        return await super().acquire(owner_id=owner_id,idempotency_key=idempotency_key,request=request)
    def _scope(self,request):
        normalized=request.normalized()
        if normalized.scope_kind=="public_search":return self.driver.validate_source(normalized)
        origin=urlsplit(normalized.url)
        if normalized.scope_kind!='current_page' or origin.scheme!='https' or origin.netloc not in self.driver.allowed_hosts:
            raise ValueError('authenticated_scope_unsupported')
        return normalized
    async def _read(self,owner,attempt,request):
        frozen=self._scope(request);attempt_id=attempt['attempt_id'];settled=False
        try:
            self.repository.check_execution(owner,attempt_id)
            detection=await self.driver.detect(owner,frozen)
            self.repository.check_execution(owner,attempt_id)
            if self.repository.cancellation_requested(owner,attempt_id):raise asyncio.CancelledError()
            if not isinstance(detection,dict) or detection.get('status') not in ('authentication_required','not_required','valid','unknown','blocked'):raise ValueError('invalid_detection')
            if detection['status']=='not_required':
                # 只有固定探测器的明确匿名回执可走此分支；未知错误不能推断无需登录。
                if frozen.scope_kind=="public_search":
                    try:result=await self.driver.read_source(self.repository,owner,attempt,frozen,None)
                    except asyncio.CancelledError:
                        # 固定浏览器reader仅在真实线程和资源已收口时抛取消；未知清理抛错误。
                        settled=True;raise
                else:result=await SourceAcquisitionService(self.repository,self.anonymous_fetcher)._read(owner,attempt,frozen)
                settled=True
                return result
            if detection['status']=='authentication_required':
                with self.repository._connect() as db:
                    db.execute('BEGIN IMMEDIATE');digest=self.repository.auth_digest(db,owner,attempt_id)
                created=self.authentication.request(owner,self.driver.website,detection.get('account_id'),'login_required',{'attempt_id':attempt_id},now=time.time(),ttl=600,authorization_digest=digest,idempotency_key='source-auth:'+attempt_id,observed_version=detection.get('observed_version'))
                try:self.repository.bind_auth_wait_locked(owner,attempt_id,created['request_id'])
                except BaseException:
                    self.authentication.cancel(owner,created['request_id']);raise
                settled=True
                return self.repository.get_attempt(owner,attempt_id)
            if detection['status']!='valid':
                settled=True
                return self.repository.complete_failure(owner,attempt_id,error_code='site_refused' if detection['status']=='blocked' else 'network_error',error_message='认证来源未取得确定可读状态')
            from src.api.execution import execution_to_thread
            state=await execution_to_thread(self.authentication.load_verified_state,owner,self.driver.website,detection.get('account_id'),expected_version=detection.get('observed_version'),driver=self.driver)
            if state is None:raise ValueError('authentication_not_verified')
            if frozen.scope_kind=="public_search":
                try:result=await self.driver.read_source(self.repository,owner,attempt,frozen,state)
                except asyncio.CancelledError:settled=True;raise
                settled=True
                return result
            service=SourceAcquisitionService(self.repository,self.driver.source_fetcher(state,frozen))
            result=await service._read(owner,attempt,frozen);settled=True
            return result
        finally:
            if not settled:
                # 未证明受控探测/读取清理完成时，现有取消/读取门必须保持未知。
                with self.repository._connect() as db:
                    db.execute("UPDATE source_acquisition_attempts SET error_code='auth_cleanup_unknown' WHERE owner_id=? AND attempt_id=? AND status='acquiring'",(owner,attempt_id))
    def refresh_expired(self,owner,identity,key):
        request=self.authentication.get_request(owner,identity)
        if request['website']!=self.driver.website or set(request['binding'])!={'attempt_id'}:raise ValueError('invalid_auth_binding')
        attempt_id=request['binding']['attempt_id']
        with self.repository.execution_lock(owner,attempt_id).acquire(timeout=0):
            with self.repository._connect() as db:
                db.execute('BEGIN IMMEDIATE');digest=self.repository.auth_digest(db,owner,attempt_id)
            operation_key='refresh:'+hashlib.sha256(json.dumps([identity,key],separators=(',',':')).encode()).hexdigest()
            with self.repository._connect() as db:
                existing=db.execute('SELECT request_id FROM source_reauthentication_requests WHERE owner=? AND idempotency_key=?',(owner,operation_key)).fetchone()
                current=db.execute('SELECT current_auth_request_id FROM source_acquisition_attempts WHERE owner_id=? AND attempt_id=?',(owner,attempt_id)).fetchone()
            if existing:
                if current[0]!=existing[0]:raise ValueError('refresh_result_unknown')
                return self.authentication.get_request(owner,existing[0])
            if request['state'] not in ('pending','expired') or request['expires']>time.time():raise ValueError('refresh_not_allowed')
            with self.repository._connect() as db:
                observed=db.execute('SELECT expected_version FROM source_reauthentication_requests WHERE owner=? AND request_id=?',(owner,identity)).fetchone()[0]
            # 延续原检测版本，不能把期间新登录版本直接判成需要重认证。
            created=self.authentication.request(owner,request['website'],request['account'],'login_required',request['binding'],now=time.time(),ttl=600,authorization_digest=digest,idempotency_key=operation_key,observed_version=observed)
            try:self.repository.bind_auth_wait_locked(owner,attempt_id,created['request_id'],previous=identity)
            except BaseException:
                self.authentication.cancel(owner,created['request_id']);raise
            return self.authentication.get_request(owner,created['request_id'])
