"""先证明浏览器收口，再复用原快照finalize；最多三份实际HTML字节。"""
import asyncio,base64,hashlib,threading
from bs4 import BeautifulSoup

async def read_source(driver,repository,owner,attempt,request,state):
    from src.api.execution import execution_to_thread
    from src.source_acquisition.service import SourceAcquisitionService,_FetchedPage,_FetchFailure,_now
    attempt_id=attempt['attempt_id'];session_holder=[];close_lock=threading.Lock();closed=False;cancelled=threading.Event()
    def check():
        if cancelled.is_set():raise asyncio.CancelledError()
        repository.check_execution(owner,attempt_id)
        if repository.cancellation_requested(owner,attempt_id):raise asyncio.CancelledError()
    def close():
        nonlocal closed
        with close_lock:
            if closed:return
            if not session_holder:raise ValueError('browser_cleanup_unknown')
            session_holder[0].close();closed=True
    def prepare():
        check();session=driver.factory();session_holder.append(session)
        try:
            check()
            result=session.call(driver._payload('verify' if state is not None else 'observe',**({'state':state} if state is not None else {})))
            if state is not None:
                if result.get('status')!='valid':raise ValueError('authentication_not_verified')
                check();result=session.call({'action':'search'})
            if result.get('status')!='not_required':raise ValueError('site_read_not_ready')
            candidates=result.get('candidates')
            if not isinstance(candidates,list) or not 1<=len(candidates)<=request.page_limit:raise ValueError('candidate_scope_changed')
            identities=[driver.content_identity(item['url']) for item in candidates]
            if len(set(identities))!=len(identities):raise ValueError('candidate_scope_changed')
            pages={}
            for candidate,identity in zip(candidates,identities):
                check();result=session.call({'action':'read','identity':identity});check()
                if result.get('status')!='read':
                    # 只封存已知且可结束的页失败；关闭成功不等于未知读取已取得确定结果。
                    status=result.get('status');http_status=result.get('http_status')
                    if status=='timeout':code='timeout'
                    elif status=='site_refused' and type(http_status) is int and 400<=http_status<=599:code='site_refused'
                    elif status=='scope_denied':raise ValueError('source_scope_denied')
                    else:raise ValueError('browser_read_unknown')
                    pages[identity]=_FetchFailure(code,'站点未返回可用正文',final_url=identity);continue
                if result.get('identity')!=identity or result.get('media_type') not in ('text/html','application/xhtml+xml'):raise ValueError('source_identity_changed')
                raw=base64.b64decode(result['content'],validate=True)
                if len(raw)>2*1024*1024:raise ValueError('content_too_large')
                soup=BeautifulSoup(raw,'html.parser')
                for node in soup(['script','style','noscript']):node.decompose()
                text=' '.join(soup.get_text(' ',strip=True).split())
                if not text:raise ValueError('content_unreadable')
                pages[identity]=_FetchedPage(request_url=identity,final_url=identity,read_at=_now(),media_type=result['media_type'],content=raw,content_sha256=hashlib.sha256(raw).hexdigest(),title=str(result.get('title',''))[:300],text_preview=text[:4000])
            check();return [{'url':url,'title':str(item.get('title',''))[:300]} for url,item in zip(identities,candidates)],pages
        finally:close()
    reading=asyncio.create_task(execution_to_thread(prepare))
    try:
        candidates,pages=await asyncio.shield(reading)
    except asyncio.CancelledError:
        cancelled.set()
        # 精确停止浏览器能解除正在等待的RPC；同时必须等待原IO线程实际退出。
        cleanup_error=None
        try:
            if session_holder:await execution_to_thread(close)
        except Exception as exc:cleanup_error=exc
        while not reading.done():
            try:await asyncio.shield(reading)
            except asyncio.CancelledError:continue
            except Exception:break
        if cleanup_error is not None:raise ValueError('browser_cleanup_unknown') from None
        if not closed:raise ValueError('browser_cleanup_unknown')
        raise
    check()
    class BufferedRead:
        provider=driver.search_provider
        async def search(self,query,*,time_range,domains,limit):
            if (query,time_range,tuple(domains),limit)!=(request.query,request.time_range,request.domains,request.page_limit):raise ValueError('source_scope_changed')
            return candidates
        async def fetch(self,url):
            if url not in pages:raise ValueError('source_scope_changed')
            value=pages[url]
            if isinstance(value,Exception):raise value
            return value
    buffered=BufferedRead()
    # 这里复用真实字节和原Repository complete_batch，不把浏览器回执当发布或快照成功。
    return await SourceAcquisitionService(repository,buffered,search_client=buffered)._read(owner,attempt,request)
