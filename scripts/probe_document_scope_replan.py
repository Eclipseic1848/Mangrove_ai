"""显式真实模型探针：只发送合成页码信息，不创建任务或读写业务数据库。"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from src.agentic_runtime.coverage import CoverageContractDraft, freeze_contract
from src.llm.provider import get_chat_model


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-real-model', action='store_true', required=True)
    parser.add_argument('--case', choices=('all', 'normal', 'replan'), default='all')
    args = parser.parse_args()
    assert args.allow_real_model
    model = get_chat_model('deepseek', model='deepseek-flash', max_tokens=2000)
    model.request_timeout = 60
    model.max_retries = 0
    tool = model.bind_tools([CoverageContractDraft])
    instruction = ('提交覆盖契约。authorized_scope是整个任务的检索范围，不是首批读取页面。'
        '用户未明确限定页码时省略unit_ids。第N个对象不等于第N页。'
        '不得仅提高confidence绕过确认。只用合成来源synthetic，已检查页面synthetic:page:1至synthetic:page:12。')
    user = HumanMessage(content='分析文件中第5个报销审批单，按人名组织，包含出差明细、小计、发票合计与结算金额。')
    bad = dict(authorized_scope={'source_ids': ['synthetic'], 'unit_ids': ['synthetic:page:1']},
        result_cardinality='ordinal', result_ordinal=5, completeness='strict', ordering='文档顺序',
        object_boundary='完整审批单', stop_semantics='读完第5张审批单', interpretation='第5张审批单', confidence='low')
    for recovery in (False, True):
        if args.case != 'all' and args.case != ('replan' if recovery else 'normal'):
            continue
        messages = [SystemMessage(content=instruction), user]
        if recovery:
            messages += [AIMessage(content='', additional_kwargs={'reasoning_content': '先提交范围供工具校验。'}, tool_calls=[{'id': 'rejected', 'name': 'CoverageContractDraft', 'args': bad}]),
                ToolMessage(tool_call_id='rejected', content='尚未确认页码范围，本次未冻结。用户未限定时省略authorized_scope.unit_ids，重新规划；不得仅提高confidence绕过确认。')]
        if recovery:
            # SDK 消息转换会丢掉供应商要求的 reasoning_content；探针直接传输合成历史。
            raw = await asyncio.wait_for(model.async_client.create(model=model.model_name, max_tokens=2000,
                tools=[convert_to_openai_tool(CoverageContractDraft)], messages=[
                    {'role': 'system', 'content': instruction}, {'role': 'user', 'content': user.content},
                    {'role': 'assistant', 'content': '', 'reasoning_content': '先提交范围供工具校验。',
                     'tool_calls': [{'id': 'rejected', 'type': 'function', 'function': {'name': 'CoverageContractDraft', 'arguments': json.dumps(bad, ensure_ascii=False)}}]},
                    {'role': 'tool', 'tool_call_id': 'rejected', 'content': messages[-1].content},
                ]), timeout=70)
            response = AIMessage(content='', tool_calls=[{'id': c.id, 'name': c.function.name, 'args': json.loads(c.function.arguments)} for c in (raw.choices[0].message.tool_calls or [])], response_metadata={'model_name': raw.model})
        else:
            response = await asyncio.wait_for(tool.ainvoke(messages), timeout=70)
        assert len(response.tool_calls) == 1, '未返回唯一契约'
        draft = CoverageContractDraft.model_validate(response.tool_calls[0]['args'])
        assert not draft.authorized_scope.unit_ids, '仍错误限制页面'
        assert draft.result_cardinality.value == 'ordinal' and draft.result_ordinal == 5
        _, ledger = freeze_contract(draft, bound_source_ids={'synthetic'},
            inspected_unit_ids=[f'synthetic:page:{p}' for p in range(1, 13)])
        assert len(ledger.authorized_unit_ids) == 12
        print(json.dumps({'case': 'replan' if recovery else 'normal', 'passed': True,
            'model': response.response_metadata.get('model_name'), 'usage': response.usage_metadata}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
