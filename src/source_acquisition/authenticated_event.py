"""仅已返回NEEDS_INPUT的受控Adapter生成认证事件；不从模型文本推断暂停。"""
from src.agentic_runtime.models import RuntimeEvent,RuntimeStatus

def authentication_wait_event(request,binding,checkpoint,result,*,request_id,website,driver_version):
    if result.status!=RuntimeStatus.NEEDS_INPUT or result.candidates or result.verification is not None:
        raise ValueError('runtime_not_authentication_quiescent')
    if result.run_id!=binding.external_run_id or checkpoint.run_id!=binding.external_run_id or checkpoint.workspace_root!=result.workspace_root:
        raise ValueError('authentication_checkpoint_mismatch')
    if not all(isinstance(value,str) and 0<len(value)<=200 for value in (request_id,website,driver_version)):
        raise ValueError('invalid_authentication_identity')
    # 此函数仅由固定Driver宿主/Adapter内部调用；不注册HTTP输入。
    return RuntimeEvent(event_type='source.authentication_required',summary='来源需要重新认证',details={'authentication_request_id':request_id,'website':website,'driver_version':driver_version,'task_id':request.task_id,'revision':request.revision,'run_id':checkpoint.run_id,'binding_digest':binding.capability_digest})
