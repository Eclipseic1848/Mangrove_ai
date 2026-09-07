"""评测入口复用显式账号库，冻结授权覆盖完整重试链。"""
from contextlib import asynccontextmanager

from src.account_execution import ExecutionDenied, current_authorization, execution_context
from src.api.execution import execution_validation, running_execution
from src.api.store import WebUIStore


@asynccontextmanager
async def evaluation_execution(database, request):
    if request.revision != 1:
        raise ValueError("G1 入口只创建冻结的首个 Revision")
    store = WebUIStore(database)
    # 已有调用链不能借账号重新启用捕获新代；只有独立入口允许首次冻结。
    auth = current_authorization(required=False)
    if auth is None:
        auth = store.capture_account_execution(request.user_id)
    if auth.owner_user_id != request.user_id:
        raise ExecutionDenied("评测请求与冻结 Owner 不一致")
    store.require_account_authorization(auth)
    with execution_context(auth), execution_validation(lambda frozen: store.require_account_execution(frozen, "workspace", request.task_id)):
        sources = [{"upload_id": source.upload_id, "sha256": source.sha256}
                   for source in request.sources]
        contracts = [item.model_dump(mode="json") for item in request.table_output_contracts]
        existing = store.get_semantic_workspace_task(request.user_id, request.task_id)
        if existing is None:
            store.create_semantic_workspace_task(request.user_id, task_id=request.task_id,
                title=request.objective_text, objective_text=request.objective_text,
                upload_ids=[source.upload_id for source in request.sources],
                output_formats=list(request.requested_output_formats), provider="pi",
                model=request.model_connection_model or request.model,
                external_api_confirmed=request.external_api_confirmed,
                source_refs=sources, table_output_contracts=contracts)
        else:
            # 既有根不得补新代绑定，也不得把另一份输入借原任务发布。
            if (existing["objective_text"] != request.objective_text
                    or existing["output_formats"] != list(request.requested_output_formats)
                    or existing["source_refs"] != sources
                    or existing["table_output_contracts"] != contracts
                    or existing["model"] != (request.model_connection_model or request.model)
                    or existing["external_api_confirmed"] != request.external_api_confirmed):
                raise ValueError("评测任务与冻结输入不一致")
        store.require_account_execution(auth, "workspace", request.task_id)
        async with running_execution(store, "workspace", request.task_id):
            yield auth
