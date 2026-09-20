"""显式真实模型与PDF验证，业务数据仅在临时隔离库中保存。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from pathlib import Path
import socket
import sys
import tempfile
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from reportlab.pdfgen import canvas
import uvicorn
from src.agentic_runtime.document_retrieval import DocumentRetrievalModule
from src.agentic_runtime.document_tools import DocumentToolBroker
from src.agentic_runtime.models import PiRuntimeRequest, PiRuntimeCheckpoint, SourceInput, RuntimeTaskConfig, RuntimeVersion, RuntimeStatus
from src.agentic_runtime.pi_runtime import PiRuntime
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.routes import document_tools
from src.api.routes import model_relay
from src.model_connections import ConnectionBroker
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from src.llm.provider import get_provider
from tests.database_migration_helpers import migrated_webui_database
from tests.account_execution_helpers import seed_execution_owner
from src.account_execution import execution_context
from src.api.execution import execution_validation
from src.api.store import WebUIStore
from src.candidate_verification import CandidateVerificationService, CurrentVerifierRulesetResolver, SqliteCandidateVerificationRepository
from src.api.semantic_workspace_runtime import _CandidateVerificationBrokerAdapter
from src.runtime_routing import runtime_routing_is_p0_blocked


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-real-model', action='store_true', required=True)
    parser.add_argument('--draft-review', action='store_true', help='验证真实初稿暂停及同运行继续核对')
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='mangrove-scope-pdf-'))
    print(f'isolated_root={root}', flush=True)
    source = root / 'synthetic.pdf'
    pdf = canvas.Canvas(str(source))
    pdf.drawString(72, 720, 'Cover page. Six expense approval forms follow.')
    pdf.showPage()
    for number in range(1, 7):
        pdf.drawString(72, 720, f'Expense Approval Form {number}')
        pdf.drawString(72, 690, f'Person: Synthetic{number}; Travel: City{number}; Amount: {number * 100}')
        pdf.drawString(72, 660, f'Subtotal: {number * 100}; Invoice total: {number * 100}; Settlement: {number * 100}')
        pdf.showPage()
    pdf.save()
    database = migrated_webui_database(root / 'isolated.db')
    repository = AgenticRuntimeRepository(database)
    authorization = seed_execution_owner(database, 'synthetic-owner')
    connection = get_provider().resolve_model('deepseek', model='deepseek-flash')
    models = ConnectionBroker(repository=ModelConnectionRepository(str(database)), vault=FernetCredentialVault.generate())
    configured = await models.configure_personal(owner_user_id='synthetic-owner', preset_id='deepseek',
        api_key=connection.api_key, model=connection.model)
    binding = models.freeze_connection('synthetic-owner', str(configured['connection_id']))
    broker = DocumentToolBroker(retriever=DocumentRetrievalModule(execution_root=root / 'cache'), state_store=repository)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    approved = False
    runtime = PiRuntime(execution_root=root / 'runs', state_store=repository,
        draft_review_required=(lambda _: not approved) if args.draft_review else None,
        document_tool_broker=broker, document_relay_base_url=f'http://127.0.0.1:{port}/internal/document-tools',
        connection_broker=models, relay_base_url=f'http://127.0.0.1:{port}/internal/model-relay', timeout_seconds=420)
    runtime.bind_candidate_verification(CandidateVerificationService(
        repository=SqliteCandidateVerificationRepository(database),
        ruleset_resolver=CurrentVerifierRulesetResolver(Path(__file__).resolve().parents[1]),
        p0_reader=lambda _: runtime_routing_is_p0_blocked(database),
        broker_adapter=_CandidateVerificationBrokerAdapter(),
        event_writer=lambda event, attempt: print(event, attempt.status, flush=True),
        provider_grant_revoker=lambda grant, reason: models.revoke_grant(grant, reason)))
    app = FastAPI()
    app.include_router(document_tools.router)
    app.include_router(model_relay.router)
    app.dependency_overrides[model_relay.get_connection_broker] = lambda: models
    server = uvicorn.Server(uvicorn.Config(app, host='0.0.0.0', port=port, log_level='error'))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(.1)
    assert server.started
    request = PiRuntimeRequest(user_id='synthetic-owner', task_id='synthetic-scope', revision=1,
        objective_text='提取文件中第5个报销审批单（Expense Approval Form），按人名组织JSON，包含出差信息、小计、发票合计和结算金额。只输出该单，不要其他单据。',
        requested_output_formats=('json',), sources=(SourceInput(upload_id='synthetic', original_name=source.name,
            host_path=source, sha256=hashlib.sha256(source.read_bytes()).hexdigest(), media_type='application/pdf'),),
        model_connection_id=binding.connection_id, model_connection_version=binding.connection_version,
        model_connection_model=binding.model, external_api_confirmed=True)
    async def event_sink(event):
        print(event.event_type, event.summary, flush=True)
    try:
        with execution_context(authorization), execution_validation(WebUIStore(str(database)).require_account_authorization):
            repository.register(RuntimeTaskConfig(user_id=request.user_id, task_id=request.task_id, revision=1,
                runtime_version=RuntimeVersion.PI, model_connection_id=binding.connection_id,
                model_connection_version=binding.connection_version, model_connection_model=binding.model,
                external_api_confirmed=True))
            result = await runtime.start(request, on_event=event_sink)
            if args.draft_review:
                assert result.status is RuntimeStatus.NEEDS_INPUT
                assert result.verification is None and result.clarification['draft_review_id']
                assert result.session_file and (result.workspace_root / result.session_file).is_file()
                print('PASS: 真实初稿已暂停且会话保留，未进入独立核对', flush=True)
                approved = True
                class ResumeObserved(Exception):
                    pass
                async def resume_sink(event):
                    await event_sink(event)
                    if event.event_type == 'provider.usage':
                        # 验证第一条真实恢复响应后结束探针，不额外执行整轮核对。
                        raise ResumeObserved
                try:
                    await runtime.resume(request, checkpoint=PiRuntimeCheckpoint(
                        run_id=result.run_id, workspace_root=result.workspace_root,
                        container_name=result.container_name, session_file=result.session_file), on_event=resume_sink)
                except ResumeObserved:
                    assert not runtime._operations and not runtime._grants
                    print('PASS: 明确批准后恢复原会话，真实模型返回，探针资源已清理', flush=True)
                    return
                raise AssertionError('未观察到原运行的真实恢复响应')
        assert len(result.candidates) == 1
        content = result.candidates[0].host_path.read_text(encoding='utf-8')
        assert 'Synthetic5' in content and '500' in content
        assert not any(f'Synthetic{n}' in content for n in (1, 2, 3, 4, 6))
        assert result.verification is not None and result.verification.status.value == 'passed'
        print('PASS: PDF第五单据读取、结果内容及独立校验', flush=True)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == '__main__':
    asyncio.run(main())
