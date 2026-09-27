"""隔离子进程采集探针：只用合成来源，真实落盘进度与进程退出。"""
import asyncio
import json
import os
from pathlib import Path
import sys

from src.collectors.base import CollectResult, CollectedItem
from src.conductor import evidence_collection as flow
from src.config.user_ctx import user_overrides_context
from tests.test_evidence_collection_flow import spec_for


def main():
    root, phase = Path(sys.argv[1]), sys.argv[2]
    flow.settings.data_prep_artifact_root = str(root / 'artifacts')
    flow.settings.semantic_execution_root = str(root / 'execution')
    flow.execution_owner = lambda: 'isolated-owner'

    def record(value):
        with (root / 'calls.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    async def collect(self, request):
        record(['discover'])
        return CollectResult(True, 'synthetic', items=[CollectedItem(content='合成服务规则',
            metadata={'note_id': str(i), 'note_type': 'normal'}) for i in range(3)],
            coverage={'pages': [{'has_more': False}]})

    async def assess(note, scope, state):
        operation = [note['metadata']['note_id'], state['_assessment_stage']]
        record(operation)
        if phase == 'crash' and operation == ['1', 'extract']:
            # 不执行finally，模拟进程在请求已发送、响应尚未确认时消失。
            os._exit(23)
        return {'relevant': True, 'records': []}

    async def images(*args):
        raise AssertionError('合成来源没有图片')

    flow.SocialMediaCollector.collect = collect
    spec_path = root / 'frozen-spec.json'
    if phase == 'crash':
        spec = spec_for('设备维护规则', target_count=3)
        spec_path.write_text(spec.model_dump_json(), encoding='utf-8')
    else:
        from src.conductor.task_spec import TaskSpec
        spec = TaskSpec.model_validate_json(spec_path.read_text(encoding='utf-8'))
    with user_overrides_context({'mc_cookie_xhs': 'synthetic-new' if phase == 'rotate' else 'synthetic-old'}):
        try:
            result = asyncio.run(flow.collect_evidence({'task_id': 'process-resume', 'task_spec': spec},
                scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
                assess=assess, exclude=flow.exclude_topic, read_images=images))
        except ValueError as error:
            if phase != 'rotate' or '版本不一致' not in str(error):
                raise
            result = {'rotation_rejected': True}
    (root / (phase + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
