"""只接受当前用户独立提交的明确记忆命令，不让模型决定删除范围。"""
import hashlib
import re
import uuid

from src.conversation_steering.models import ContextDelta, DeltaConfidence, TurnIntent


def memory_command(text, store, repository, *, before_call):
    # 冒号明确区分命令与“记住是什么意思”等咨询；不扫描历史、附件或模型输出。
    match = re.fullmatch(r'(?:请)?(?:帮我)?(记住|忘记)\s*[：:]\s*([^\r\n]+)', text.strip())
    if not match or re.search(r'[?？]|(?:吗|么|呢)[。！!]*$', text.strip()):
        return None
    return MemoryCommand(match[1], match[2].strip(), store, repository, before_call)


class MemoryCommand:
    def __init__(self, action, text, store, repository, before_call):
        self.action, self.text = action, text
        self.store, self.repository, self.before_call = store, repository, before_call

    async def rewrite(self, turn, request):
        from src.task_context import _validate_context_advice, _safe_summary
        self.before_call()
        answer = None
        if not self.text or len(self.text) > 4000:
            answer = '请提供一条不超过4000字的完整记忆。'
        elif self.action == '记住':
            try:
                _validate_context_advice(self.text)
                if _safe_summary(self.text, limit=4000) != ' '.join(self.text.split()):
                    raise ValueError('敏感内容')
            except ValueError:
                answer = '这段内容包含敏感信息或控制指令，未保存。请只提供工作偏好，不要包含密码或密钥。'
        if answer is None:
            # 持久占位防止响应中断后再次修改，尤其不能误删用户后来重新添加的同文记忆。
            _, claimed = self.repository.claim_idempotency(
                turn.owner_id, 'memory-command:' + turn.turn_id,
                request_hash=hashlib.sha256(turn.text.encode('utf-8')).hexdigest(),
                proposed_task_id=turn.task_id,
            )
            if not claimed:
                answer = '这条记忆操作已提交，请到“记忆”页面核对结果；不会重复修改。'
            elif self.action == '记住':
                self.store.memory_add(turn.owner_id, self.text, source='conversation')
                answer = '已记住，仅用于你后续的任务；本次明确要求仍优先。可在“记忆”页面修改或删除。'
            else:
                matches = [item for item in self.store.memory_list(turn.owner_id)
                           if item['purpose'] == 'general' and item['text'] == self.text]
                if len(matches) != 1:
                    answer = '未找到唯一、完全一致的个人记忆，未删除任何内容。请复制完整记忆，或到“记忆”页面选择删除。'
                elif self.store.memory_delete(turn.owner_id, matches[0]['id'], expected_text=self.text):
                    answer = '已忘记这条个人记忆，后续新任务不再使用；已有任务和历史结果保持不变。'
                else:
                    answer = '这条记忆已发生变化，未删除。请到“记忆”页面核对后重试。'
        return ContextDelta(
            delta_id='delta_' + uuid.uuid4().hex[:16], owner_id=turn.owner_id,
            task_id=turn.task_id, inherited_revision=turn.revision,
            source_turn_ids=(*[item.turn_id for item in request.relevant_turns], turn.turn_id),
            intent=TurnIntent.STATUS_QUESTION, confidence=DeltaConfidence.HIGH,
            normalized_text=turn.text, direct_answer=answer,
        )
