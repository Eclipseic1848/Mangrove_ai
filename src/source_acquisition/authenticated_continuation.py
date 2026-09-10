"""原来源attempt恢复装配；仅固定内部Driver可提供受控fetcher。"""
import json
from src.source_acquisition.service import SourceAcquisitionRequest,SourceAcquisitionService
from src.api.execution import execution_to_thread
resume_authenticated = SourceAcquisitionService.resume_authenticated

class SourceContinuation:
    def __init__(self,repository,authentication,drivers):
        self.repository=repository;self.authentication=authentication;self.drivers=drivers
    def authorize(self,owner,request):
        binding=request['binding']
        if set(binding)!={'attempt_id'}:raise ValueError('source_binding_required')
        with self.repository._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            digest=self.repository.auth_digest(db,owner,binding['attempt_id'])
            saved=db.execute('SELECT authorization_digest FROM source_reauthentication_requests WHERE owner=? AND request_id=?',(owner,request['request_id'])).fetchone()
            if saved is None or saved[0]!=digest:raise PermissionError('authorization_changed')
    async def resume(self,owner,identity):
        request=self.authentication.get_request(owner,identity);self.authorize(owner,request)
        attempt_id=request['binding']['attempt_id']
        with self.repository._connect() as db:
            row=db.execute('SELECT * FROM source_acquisition_attempts WHERE owner_id=? AND attempt_id=?',(owner,attempt_id)).fetchone()
            auth=db.execute('SELECT expected_version FROM source_reauthentication_requests WHERE owner=? AND request_id=?',(owner,identity)).fetchone()
            normalized=SourceAcquisitionRequest.from_frozen_attempt(row)
        driver=self.drivers.get(request['website'])
        if driver is None:raise ValueError('driver_unavailable')
        if normalized.scope_kind=='public_search' and getattr(driver,'search_provider',None)!=normalized.search_provider:raise ValueError('driver_version_changed')
        service=SourceAcquisitionService(self.repository,None)
        async def reader(owner_id,attempt,frozen):
            self.authorize(owner_id,request)
            state=await execution_to_thread(self.authentication.load_verified_state,owner_id,request['website'],request['account'],expected_version=auth['expected_version']+1,driver=driver)
            if state is None:raise ValueError('authentication_not_verified')
            # 工厂只来自固定注册Driver；不能由HTTP提供URL/代码/回调覆盖原冻结请求。
            if frozen.scope_kind=="public_search":return await driver.read_source(self.repository,owner_id,attempt,frozen,state)
            fetcher=driver.source_fetcher(state,frozen)
            bound_service=SourceAcquisitionService(self.repository,fetcher)
            return await bound_service._read(owner_id,attempt,frozen)
        service._authenticated_reader=reader
        return await resume_authenticated(service,owner_id=owner,attempt_id=attempt_id,request_id=identity,request=normalized)
