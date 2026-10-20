"""银行权益主题必须有原文依据；未知和字段缺失不冒充无关。"""
import asyncio
import json

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.conductor import bank_benefits, evidence_collection as flow
from src.conductor.task_spec import TaskSpec


def bank_spec(**extra):
    return TaskSpec(intent="采集光大银行私行权益并分析", bank_benefits={
        "source_evidence": "小红书", "banks": [{"name": "光大银行", "keyword": "光大银行私行权益"}],
        "audience": "私行", "target_count": 1, **extra})


@pytest.fixture
def tag_only_capture():
    # 保留 run09 原始正文与响应，未读图片地址使用合成占位。
    return {
        "note": {"title": '各大行私人银行标准，交通最低？', "content": '#财经知识[话题]# #经济[话题]# #银行[话题]# #招商银行[话题]# #交通银行[话题]# #工商银行[话题]# #私人银行[话题]# #真诚分享[话题]# #平安银行[话题]# #每日分享[话题]#',
                 "metadata": {"note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
                              "image_urls": ["synthetic-image"]}, "images": []},
        "response": '{"relevant":false,"tag_stale":null,"tag_audience_mismatch":null,"credit_card_only":null,"rank_reason":"原文明确列举的银行为交通、招商、工商、平安，未提及光大银行，与目标银行不符。","evidence":{"relevant":{"source_id":"note","line_index":1},"tag_audience_mismatch":null}}'}


@pytest.mark.parametrize("reference_format", ["original_line_index", "quote"])
def test_tag_only_negative_cannot_exclude_unread_picture(monkeypatch, tag_only_capture, reference_format):
    note = tag_only_capture["note"]
    response = tag_only_capture["response"]
    if reference_format == "quote":
        parsed = json.loads(response)
        parsed["evidence"]["relevant"] = {"source_id": "note", "quote": note["content"]}
        response = json.dumps(parsed, ensure_ascii=False)
    async def model(*args, **kwargs):
        return response
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "screen"}))
    assert result["relevant"] is None and "relevant" not in result["judgment_evidence"]
    assert result["tag_audience_mismatch"] is None
    note["assessment"] = result
    assert bank_benefits._exclusion(note, spec.bank_benefits, before_ocr=True) is None


@pytest.mark.parametrize("reference_format", ["original_full_quote", "title_line_index"])
@pytest.mark.parametrize("body", ["original_topics", "empty"])
def test_title_and_topics_cannot_exclude_unread_picture(monkeypatch, tag_only_capture, reference_format, body):
    note = tag_only_capture["note"]
    # 保留 run11 原始全帖引用响应，未读图片仍用合成占位。
    response = '{"relevant":false,"tag_stale":null,"tag_audience_mismatch":null,"credit_card_only":null,"rank_reason":"原文明确列举其他银行，未提及光大银行，且含图片但文字已明示不符目标银行。","evidence":{"relevant":{"source_id":"note","quote":"各大行私人银行标准，交通最低？\\n#财经知识[话题]# #经济[话题]# #银行[话题]# #招商银行[话题]# #交通银行[话题]# #工商银行[话题]# #私人银行[话题]# #真诚分享[话题]# #平安银行[话题]# #每日分享[话题]#"}}}'
    if body == "empty" or reference_format == "title_line_index":
        parsed = json.loads(response)
        if body == "empty":
            note["content"] = ""
            parsed["evidence"]["relevant"] = {"source_id": "note", "quote": note["title"] + "\n"}
        if reference_format == "title_line_index":
            parsed["evidence"]["relevant"] = {"source_id": "note", "line_index": 0}
        response = json.dumps(parsed, ensure_ascii=False)
    async def model(*args, **kwargs):
        return response
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "screen"}))
    assert result["relevant"] is None and "relevant" not in result["judgment_evidence"]
    assert result["tag_audience_mismatch"] is None
    note["assessment"] = result
    assert bank_benefits._exclusion(note, spec.bank_benefits, before_ocr=True) is None


@pytest.mark.parametrize("text", ["只讲招商银行信用卡优惠，不涉及光大银行", "只讨论足球比赛比分，与银行业务无关"])
def test_title_topics_guard_preserves_explicit_body_counterevidence(monkeypatch, tag_only_capture, text):
    note = tag_only_capture["note"]
    note["content"] = text
    async def model(*args, **kwargs):
        return json.dumps({"relevant": False, "evidence": {
            "relevant": {"source_id": "note", "quote": note["title"] + "\n" + text}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "screen"}))
    assert result["relevant"] is False and "relevant" in result["judgment_evidence"]
    note["assessment"] = result
    assert bank_benefits._exclusion(note, spec.bank_benefits, before_ocr=True) == "unrelated"


@pytest.mark.parametrize("case", ["explicit_body", "residual_body", "no_images", "extract", "image_source", "positive"])
def test_tag_only_guard_preserves_other_topic_judgments(monkeypatch, tag_only_capture, case):
    note = tag_only_capture["note"]
    parsed = json.loads(tag_only_capture["response"])
    stage = "screen"
    if case == "explicit_body":
        note["content"] = "只讲招商银行信用卡优惠，不涉及光大银行"
    elif case == "residual_body":
        note["content"] += " 只讲招商银行信用卡优惠，不涉及光大银行"
    elif case == "no_images":
        note["metadata"]["image_urls"] = []
    elif case == "extract":
        stage = "extract"
        parsed["benefits"] = []
        note["images"] = [{"image_index": 1, "status": "recognized", "text": "仅招商银行信用卡活动"}]
    elif case == "image_source":
        note["images"] = [{"image_index": 1, "status": "recognized", "text": note["content"]}]
        parsed["evidence"]["relevant"] = {"source_id": "image:1", "line_index": 0}
    elif case == "positive":
        parsed["relevant"] = True
    async def model(*args, **kwargs):
        return json.dumps(parsed, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is (case == "positive")
    assert "relevant" in result["judgment_evidence"]


def test_tag_only_negative_does_not_erase_independent_audience_evidence(monkeypatch, tag_only_capture):
    note = tag_only_capture["note"]
    note["content"] += "\n光大银行私行客户可免费停车"
    parsed = json.loads(tag_only_capture["response"])
    parsed["tag_audience_mismatch"] = False
    parsed["evidence"]["tag_audience_mismatch"] = {"source_id": "note", "quote": "光大银行私行客户"}
    async def model(*args, **kwargs):
        return json.dumps(parsed, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "screen"}))
    assert result["relevant"] is None and "relevant" not in result["judgment_evidence"]
    assert result["tag_audience_mismatch"] is False
    assert result["judgment_evidence"]["tag_audience_mismatch"] == {
        "source_id": "note", "quote": "光大银行私行客户"}


def test_tag_only_negative_reaches_ocr_without_becoming_selected(monkeypatch, tmp_path, tag_only_capture):
    state = install_source(monkeypatch, tmp_path, tag_only_capture["note"]["content"], images=True)
    calls = []
    async def model(*args, **kwargs):
        return tag_only_capture["response"] if kwargs["max_tokens"] == 4096 else '{"relevant": null, "benefits": []}'
    async def images(*args):
        calls.append("ocr")
        return [{"image_index": 1, "status": "recognized", "text": "图片尚未证明目标银行及客群"}]
    monkeypatch.setattr(bank_benefits, "achat", model)
    monkeypatch.setattr(bank_benefits, "read_images", images)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert calls == ["ocr"]
    note = result["bank_collection"]["candidates"][0]
    assert note["status"] == "review_required" and note["reason"] == "relevance_unknown"
    assert result["collection_outcome"]["selected_count"] == 0
    assert not result["collection_outcome"]["target_complete"]


def test_recorded_mixed_references_cannot_prove_topic_or_audience(monkeypatch):
    # 保留真实混合响应；来源仅保留判断所需文本，不复制运行身份或网址。
    note = {'title': '光大银行立减金', 'content': '光大银行App主页“权益服务”——我的活动更多——私人银行专区，资产配置活动，随便关注一个光彩号，再去基金页面随便买个1块钱的基金，即可完成任务#立减金[话题]# #光大银行[话题]# #银行[话题]# #薅羊毛[话题]#', 'metadata': {}, 'images': [{'image_index': 1, 'status': 'recognized', 'text': '14:270\nK58\n活动详情\nEB\nPrivate Banking\nEVERBRIGHT BANK\n中国光大银行私人银行\n查看权益记录\n恭喜您，中奖啦\n18.00元\n18元支付宝立减金\n去兑换\n奖品可在「我的权益」中查看\n完成任务获得抽奖机会\n确认为持基客户①\n关注广发基金光彩号①\n已完成\n已完成\n关注富国基金光彩号①\n去完成'}, {'image_index': 2, 'status': 'recognized', 'text': '14:30  0\n自会58.\n羊羊羊9交流群(31)\n11:47\nAir_\n家人们，你们知道平安的立减金咋\n弄的吗\n11:56\nLUANSHIMIN\n家人们，你们知道平安的立减金咋\n弄的吗\n14:28\n麦芒502~卖萌五零贰\n光大银行App主页“权益服务”\n——我的活动更多——私人银行专\n区，资产配置活动，随便关注一个\n光彩号，再去基金页面随便买个1\n块钱的基金，即可完成任务\n⊕'}]}
    response = '{"relevant":true,"tag_stale":null,"tag_audience_mismatch":false,"credit_card_only":false,"tag_promo":false,"tag_resale":false,"tag_question":false,"customer_segment":"私人银行","rank_reason":"原文明确提及光大银行私人银行专区及资产配置活动，包含18元支付宝立减金抽奖权益及具体操作路径，符合私行客群。","evidence":{"relevant":{"source_id":"note","line_index":1,"quote":"光大银行App主页“权益服务”——我的活动更多——私人银行专区，资产配置活动"},"tag_audience_mismatch":{"source_id":"image:1","line_index":6,"quote":"中国光大银行私人银行"}},"benefits":[{"bank":"光大银行","customer_segment":"私人银行","benefit_category":"立减金","benefit_name":"18元支付宝立减金","quota":null,"threshold":"确认为持基客户① 关注广发基金光彩号① 已完成 已完成 关注富国基金光彩号① 去完成","point_system":null,"point_cost":null,"point_earn_rule":null,"valid_from":null,"valid_to":null,"app_path":"光大银行App主页“权益服务”——我的活动更多——私人银行专区，资产配置活动","activity_period":null,"registration_period":null,"redemption_period":null,"usage_period":null,"evidence":{"bank":{"source_id":"note","line_index":0,"quote":"光大银行立减金\\n"},"customer_segment":{"source_id":"image:1","line_index":6,"quote":"中国光大银行私人银行"},"benefit_category":{"source_id":"note","line_index":0,"quote":"光大银行立减金\\n"},"benefit_name":{"source_id":"image:1","line_index":10,"quote":"18元支付宝立减金"},"threshold":{"source_id":"image:1","line_index":14,"quote":"确认为持基客户①\\n关注广发基金光彩号①\\n已完成\\n已完成\\n关注富国基金光彩号①\\n去完成"},"app_path":{"source_id":"note","line_index":1,"quote":"光大银行App主页“权益服务”——我的活动更多——私人银行专区，资产配置活动"}}}],"image_types":[{"image_index":1,"type":"bank_app_screenshot","quote":"活动详情\\nEB\\nPrivate Banking\\nEVERBRIGHT BANK\\n中国光大银行私人银行\\n查看权益记录\\n恭喜您，中奖啦\\n18.00元\\n18元支付宝立减金\\n去兑换"},{"image_index":2,"type":"unknown","quote":"光大银行App主页“权益服务”\\n——我的活动更多——私人银行专\\n区，资产配置活动，随便关注一个\\n光彩号，再去基金页面随便买个1\\n块钱的基金，即可完成任务"}]}'
    async def model(messages, **kwargs):
        sources = json.loads(messages[1]["content"])["sources"]
        parsed = json.loads(response)
        for reference in parsed["evidence"].values():
            assert reference["quote"] in sources[reference["source_id"]][reference["line_index"]]["text"]
        return response
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "extract"}))
    assert result["relevant"] is None and result["tag_audience_mismatch"] is None
    assert "relevant" not in result["judgment_evidence"]
    assert "tag_audience_mismatch" not in result["judgment_evidence"]


@pytest.mark.parametrize("stage", ["screen", "extract"])
@pytest.mark.parametrize("relevant", [True, False])
@pytest.mark.parametrize("reference", [None, {"source_id": "note", "quote": "不存在的权益主题"},
                                    {"source_id": "missing", "quote": "光大银行私行卡面"}])
def test_topic_judgment_without_verified_quote_remains_unknown(monkeypatch, stage, relevant, reference):
    async def model(*args, **kwargs):
        response = {"relevant": relevant, "rank_reason": "未涉及具体私人银行权益细则", "benefits": []}
        if reference:
            response["evidence"] = {"relevant": reference}
        return json.dumps(response, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": "光大银行私行卡面，简约高级卡面", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is None
    assert "relevant" not in result["judgment_evidence"]


@pytest.mark.parametrize("stage", ["screen", "extract"])
@pytest.mark.parametrize("relevant", [True, False])
@pytest.mark.parametrize("quote", [" ", "\n"])
def test_blank_quote_cannot_prove_or_reject_topic(monkeypatch, stage, relevant, quote):
    async def model(*args, **kwargs):
        return json.dumps({"relevant": relevant, "benefits": [], "evidence": {
            "relevant": {"source_id": "note", "quote": quote}}})
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": " " if quote == " " else "", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is None
    assert "relevant" not in result["judgment_evidence"]


@pytest.mark.parametrize("stage", ["screen", "extract"])
def test_prompt_example_keeps_judgments_in_parseable_evidence_mapping(monkeypatch, stage):
    text = "光大银行私行客户可免费停车"
    async def model(messages, **kwargs):
        prompt = messages[0]["content"]
        format_marker = "顶层判断引用格式统一规则："
        assert prompt.count(format_marker) == 1
        earlier_rules, format_rules = prompt.split(format_marker, 1)
        assert "evidence仅为判断字段到{source_id,quote}" not in earlier_rules
        assert "evidence.relevant={" not in earlier_rules
        assert '每个引用仅用source_id和line_index两个键' in format_rules
        assert '顶层判断禁止输出quote或额外键' in format_rules
        assert '二选一' not in format_rules
        assert "benefits每项evidence只能使用source_id和quote两个键" in format_rules
        marker = "判断引用结构示例："
        assert marker in prompt
        example, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
        assert example["relevant"] is True and example["tag_audience_mismatch"] is False
        evidence = example["evidence"]
        assert set(evidence) == {"relevant", "tag_audience_mismatch"}
        assert evidence["relevant"] is not evidence["tag_audience_mismatch"]
        sources = json.loads(messages[1]["content"])["sources"]
        for reference in evidence.values():
            assert set(reference) == {"source_id", "line_index"}
            assert sources[reference["source_id"]][reference["line_index"]] == {"line_index": 0, "text": text}
        assert "禁止source_id、quote或line_index直接挂在顶层evidence下" in prompt
        assert "同一原文行可在不同判断字段下分别独立引用" in prompt
        assert "示例仅说明结构" in prompt and "实际有判断依据的来源行" in prompt
        return json.dumps({**example, "benefits": []}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": "光大银行权益见图", "metadata": {},
        "images": [{"image_index": 1, "status": "recognized", "text": text}]}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is True and result["tag_audience_mismatch"] is False
    assert result["judgment_evidence"] == {
        "relevant": {"source_id": "image:1", "quote": text},
        "tag_audience_mismatch": {"source_id": "image:1", "quote": text}}


def test_extract_prompt_keeps_topic_and_benefit_evidence_as_independent_objects(monkeypatch):
    text = "光大银行私行客户可免费停车"
    async def model(messages, **kwargs):
        assert (
            '顶层 evidence 专用于相关性等判断，relevant 为 true 或 false 时必须输出 '
            'evidence.relevant 对应的独立引用，格式仅按末尾统一规则；'
            '不能省略 evidence.relevant。benefits 每项的 evidence 仅用于权益字段，不能代替顶层 evidence。'
            '两者是独立对象，禁止混用或输出成列表。') in messages[0]["content"]
        return json.dumps({"relevant": True, "evidence": {"relevant": {"source_id": "note", "quote": text}},
            "benefits": [{"benefit_name": "免费停车", "evidence": {
                "benefit_name": {"source_id": "note", "quote": "免费停车"}}}]}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": text, "metadata": {}}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "extract"}))
    assert result["relevant"] is True
    assert result["judgment_evidence"]["relevant"] == {"source_id": "note", "quote": text}
    assert result["benefits"][0]["benefit_name"] == "免费停车"
    assert result["benefits"][0]["evidence"]["benefit_name"] == {"source_id": "note", "quote": "免费停车"}


def test_extract_prompt_requires_contiguous_quote_from_correct_source(monkeypatch):
    note_text = "<table><tr><td>光大银行</td><td>免费停车</td></tr></table>"
    image_text = "光大银行\n私人银行客户享机场贵宾服务"
    calls = []
    async def model(messages, **kwargs):
        calls.append(messages)
        prompt = messages[0]["content"]
        # 这里只验证发送给模型的契约，不代表真实模型已经遵守引用规则。
        assert "quote只取sources中一个键对应的短连续原文（尽量单行）" in prompt
        assert "原样保留引用中的换行、HTML标签和空格" in prompt
        assert "禁止删除换行或标签、跨HTML表格单元格或跨sources拼接" in prompt
        assert "图片OCR文字须选择对应image:<image_index>键作为source_id，不得写成note" in prompt
        assert "不可摘要或改写" in prompt
        sources = json.loads(messages[1]["content"])["sources"]
        assert sources["note"] == [{"line_index": 0, "text": "\n"}, {"line_index": 1, "text": note_text}]
        assert sources["image:1"] == [{"line_index": 0, "text": "光大银行\n"}, {"line_index": 1, "text": "私人银行客户享机场贵宾服务"}]
        return json.dumps({"relevant": True, "benefits": [], "evidence": {"relevant": {
            "source_id": "image:1", "quote": "私人银行客户享机场贵宾服务"}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": note_text, "metadata": {},
        "images": [{"image_index": 1, "status": "recognized", "text": image_text}]}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "extract"}))
    assert len(calls) == 1
    assert result["judgment_evidence"]["relevant"] == {
        "source_id": "image:1", "quote": "私人银行客户享机场贵宾服务"}


def test_extract_prompt_limits_image_type_quote_without_truncating_source(monkeypatch):
    image_text = "<table>\n<tr><td>私人银行客户权益表</td>" + "<td>停车服务</td>" * 200 + "</tr></table>"
    async def model(messages, **kwargs):
        prompt = messages[0]["content"]
        # 只约束图片类型引用的输出长度，完整OCR来源仍发送且核验不变。
        assert "quote仅为支持图片类型的最短必要连续OCR原文片段，最多120字，不能复制全文" in prompt
        assert "引用原样保留换行和HTML，并绑定image_index对应图片的OCR原文" in prompt
        assert "".join(row["text"] for row in json.loads(messages[1]["content"])["sources"]["image:1"]) == image_text
        return json.dumps({"relevant": True, "benefits": [], "evidence": {"relevant": {
            "source_id": "image:1", "quote": "私人银行客户权益表"}}, "image_types": [
                {"image_index": 1, "type": "benefit_table", "quote": "私人银行客户权益表"}]}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    image = {"image_index": 1, "status": "recognized", "text": image_text}
    result = asyncio.run(bank_benefits.assess_note({"metadata": {}, "images": [image]}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "extract"}))
    assert result["relevant"] is True
    assert image["text"] == image_text
    assert image["image_type"] == "benefit_table"
    assert image["image_type_evidence"] == "私人银行客户权益表"


@pytest.mark.parametrize("image_count", [0, 1])
def test_screen_receives_unread_image_count_and_defers_title_only_judgment(monkeypatch, image_count):
    async def model(messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        assert payload["unread_image_count"] == image_count
        prompt = messages[0]["content"]
        assert "unread_image_count>0" in prompt
        assert "不能证明整帖无关" in prompt and "相反银行/客群" in prompt
        return '{"relevant": null}'
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"title": "富人的身份卡你有吗", "content": "富人的身份卡你有吗",
        "metadata": {"image_urls": ["synthetic-image"] * image_count}}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "screen"}))
    assert result["relevant"] is None


@pytest.mark.parametrize("stage", ["screen", "extract"])
@pytest.mark.parametrize("relevant", [True, False])
def test_flat_topic_reference_keeps_verified_original_quote(monkeypatch, stage, relevant):
    async def model(*args, **kwargs):
        return json.dumps({"relevant": relevant, "benefits": [], "evidence": {
            "source_id": "note", "quote": "私人银行专区"}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": "光大银行权益服务活动，点击私人银行专区", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is relevant
    assert result["judgment_evidence"]["relevant"] == {"source_id": "note", "quote": "私人银行专区"}


@pytest.mark.parametrize("stage", ["screen", "extract"])
@pytest.mark.parametrize("relevant", [True, False])
@pytest.mark.parametrize("reference", [{"source_id": "note", "quote": " \n"},
    {"source_id": "missing", "quote": "私人银行专区"}, {"source_id": "note", "quote": "原文没有的主题"}])
def test_flat_topic_reference_still_requires_matching_nonblank_original(monkeypatch, stage, relevant, reference):
    async def model(*args, **kwargs):
        return json.dumps({"relevant": relevant, "benefits": [], "evidence": reference}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": " \n光大银行私人银行专区", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is None and "relevant" not in result["judgment_evidence"]


@pytest.mark.parametrize("stage", ["screen", "extract"])
@pytest.mark.parametrize("flag,value", [("tag_stale", True), ("tag_audience_mismatch", True),
                                      ("tag_audience_mismatch", False), ("credit_card_only", True)])
def test_flat_topic_reference_is_never_borrowed_by_other_judgments(monkeypatch, stage, flag, value):
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, flag: value, "benefits": [], "evidence": {
            "source_id": "note", "quote": "私人银行专区"}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": "光大银行私人银行专区", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is True and flag not in result["judgment_evidence"]
    assert result[flag] is None


@pytest.mark.parametrize("extra", [{"bank": "光大银行"}, {"relevant": None}])
def test_mixed_top_level_evidence_cannot_be_treated_as_single_topic_reference(monkeypatch, extra):
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "benefits": [], "evidence": {
            "source_id": "note", "quote": "私人银行专区", **extra}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": "光大银行私人银行专区", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": "extract"}))
    assert result["relevant"] is None and "relevant" not in result["judgment_evidence"]


def test_benefit_field_evidence_cannot_replace_missing_topic_reference(monkeypatch):
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "benefits": [{"benefit_name": "免费停车", "evidence": {
            "benefit_name": {"source_id": "note", "quote": "免费停车"}}}]}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": "光大银行私行客户可免费停车", "metadata": {}},
        spec.bank_benefits, {"task_spec": spec, "_assessment_stage": "extract"}))
    assert result["relevant"] is None and "relevant" not in result["judgment_evidence"]
    assert result["benefits"][0]["benefit_name"] == "免费停车"


def install_source(monkeypatch, tmp_path, text, *, images=False):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    async def collect(self, request):
        return CollectResult(True, "synthetic", items=[CollectedItem(content=text, metadata={
            "note_id": "one", "note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00",
            "image_urls": ["synthetic-image"] if images else []})], coverage={"pages": [{"has_more": False}]})
    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    return {"task_id": "bank-topic", "task_spec": bank_spec()}


@pytest.mark.parametrize("unknown_at", ["screen", "extract"])
def test_unknown_topic_is_retained_for_review_without_counting_target(monkeypatch, tmp_path, unknown_at):
    text = "光大银行私行权益见图"
    state = install_source(monkeypatch, tmp_path, text, images=unknown_at == "extract")
    async def model(*args, **kwargs):
        relevant = kwargs["max_tokens"] == 4096 and unknown_at == "extract"
        return json.dumps({"relevant": relevant, "benefits": [], "evidence": {
            "relevant": {"source_id": "note", "quote": text}} if relevant else {}})
    async def images(*args):
        return [{"image_index": 1, "status": "recognized", "text": "原文未给出权益内容"}]
    monkeypatch.setattr(bank_benefits, "achat", model)
    monkeypatch.setattr(bank_benefits, "read_images", images)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    note = result["bank_collection"]["candidates"][0]
    assert note["status"] == "review_required" and note["reason"] == "relevance_unknown"
    assert result["collection_outcome"]["selected_count"] == 0
    assert not result["collection_outcome"]["target_complete"]


def test_verified_unrelated_picture_note_is_rejected_before_ocr(monkeypatch, tmp_path):
    text = "光大私行卡面收藏，摆脱存款资产枷锁，简约高级卡面"
    state = install_source(monkeypatch, tmp_path, text, images=True)
    calls = []
    async def model(*args, **kwargs):
        calls.append(kwargs["max_tokens"])
        return json.dumps({"relevant": False, "benefits": [], "evidence": {
            "relevant": {"source_id": "note", "quote": text}}}, ensure_ascii=False)
    async def forbidden(*args):
        raise AssertionError("已证实主题无关的候选不能消耗OCR预算")
    monkeypatch.setattr(bank_benefits, "achat", model)
    monkeypatch.setattr(bank_benefits, "read_images", forbidden)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    note = result["bank_collection"]["candidates"][0]
    assert note["status"] == "excluded" and note["reason"] == "unrelated"
    assert calls == [4096]


@pytest.mark.parametrize("legacy_negative", [False, True])
def test_unknown_or_unproved_old_negative_picture_note_still_reaches_ocr(monkeypatch, tmp_path, legacy_negative):
    state = install_source(monkeypatch, tmp_path, "权益详见图片", images=True)
    text = "光大银行私行客户大额存款今年未收到中秋礼品"
    calls = []
    original_assess = bank_benefits.assess_note
    async def assess(note, scope, assessment_state):
        if legacy_negative and assessment_state["_assessment_stage"] == "screen":
            return {"relevant": False, "benefits": []}
        return await original_assess(note, scope, assessment_state)
    async def model(*args, **kwargs):
        if kwargs["max_tokens"] == 4096:
            return '{"relevant": null}'
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "benefits": [], "evidence": {
            "relevant": {"source_id": "image:1", "quote": text},
            "tag_audience_mismatch": {"source_id": "image:1", "quote": "私行客户"}}}, ensure_ascii=False)
    async def images(*args):
        calls.append("ocr")
        return [{"image_index": 1, "status": "recognized", "text": text}]
    monkeypatch.setattr(bank_benefits, "assess_note", assess)
    monkeypatch.setattr(bank_benefits, "achat", model)
    monkeypatch.setattr(bank_benefits, "read_images", images)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert calls == ["ocr"]
    assert result["bank_collection"]["candidates"][0]["status"] == "selected"
    assert result["collection_outcome"]["selected_count"] == 1


@pytest.mark.parametrize("complaint", [True, False])
def test_topic_evidence_accepts_experience_and_incomplete_benefit_fields(monkeypatch, tmp_path, complaint):
    text = "光大银行私行客户大额存款今年未收到中秋礼品" if complaint else "光大银行私行客户可免费停车"
    state = install_source(monkeypatch, tmp_path, text)
    async def model(messages, **kwargs):
        prompt = messages[0]["content"]
        assert "仅提银行、私行身份、卡面或卡号展示不算相关" in prompt
        assert "权益体验投诉" in prompt and "不要求" in prompt
        benefit = {"benefit_name": "免费停车", "evidence": {"benefit_name": {"source_id": "note", "quote": "免费停车"}}}
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "benefits": [] if complaint else [benefit], "evidence": {
            "relevant": {"source_id": "note", "quote": text},
            "tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    note = result["bank_collection"]["candidates"][0]
    assert note["status"] == "selected" and result["collection_outcome"]["selected_count"] == 1
    if complaint:
        assert note["assessment"]["benefits"] == []
    else:
        benefit = note["assessment"]["benefits"][0]
        assert benefit["benefit_name"] == "免费停车"
        assert benefit["threshold"] is None and benefit["valid_to"] is None


@pytest.mark.parametrize("stage", ["screen", "extract"])
@pytest.mark.parametrize("flat", [False, True])
def test_indexed_judgment_uses_exact_source_line_without_copying_whole_source(monkeypatch, stage, flat):
    image_text = "客户权益活动\r\n2️⃣稳定私行月月有礼<td>免费停车</td>\r\n"
    reference = {"source_id": "image:1", "line_index": 1}
    async def model(messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        assert payload["sources"]["image:1"] == [{"line_index": 0, "text": "客户权益活动\r\n"},
            {"line_index": 1, "text": "2️⃣稳定私行月月有礼<td>免费停车</td>\r\n"}]
        assert all(isinstance(lines, list) for lines in payload["sources"].values())
        assert "line_index" in messages[0]["content"] and "0" in messages[0]["content"]
        return json.dumps({"relevant": True, "evidence": reference if flat else {"relevant": reference},
            "benefits": [{"benefit_name": "免费停车", "threshold": "私行客户", "evidence": {
                "benefit_name": {"source_id": "image:1", "quote": "免费停车"},
                "threshold": reference}}]}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": "光大银行客户权益活动", "metadata": {},
        "images": [{"image_index": 1, "status": "recognized", "text": image_text}]}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is True
    assert result["judgment_evidence"]["relevant"] == {
        "source_id": "image:1", "quote": "2️⃣稳定私行月月有礼<td>免费停车</td>\r\n"}
    if stage == "extract":
        assert result["benefits"][0]["benefit_name"] == "免费停车"
        assert result["benefits"][0]["threshold"] is None


@pytest.mark.parametrize("images", [False, True])
def test_bank_birthday_without_proved_audience_stays_review_not_selected_or_excluded(monkeypatch, tmp_path, images):
    text = "光大银行今年生日礼换不到合适礼品"
    state = install_source(monkeypatch, tmp_path, text, images=images)
    calls = []
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "benefits": [], "evidence": {
            "relevant": {"source_id": "note", "quote": text}}}, ensure_ascii=False)
    async def read(*args):
        calls.append("ocr")
        return [{"image_index": 1, "status": "recognized", "text": "生日礼活动，原文未说明适用客群"}]
    monkeypatch.setattr(bank_benefits, "achat", model)
    monkeypatch.setattr(bank_benefits, "read_images", read)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    note = result["bank_collection"]["candidates"][0]
    assert calls == (["ocr"] if images else [])
    assert note["status"] == "review_required" and note["reason"] == "audience_unknown"
    assert note["assessment"]["tag_audience_mismatch"] is None
    assert result["collection_outcome"]["selected_count"] == 0
    assert not result["collection_outcome"]["target_complete"]


@pytest.mark.parametrize("stage", ["screen", "extract"])
@pytest.mark.parametrize("note,response", [({'title': '各大行私人银行标准', 'content': '#财经知识[话题]# #经济[话题]# #银行[话题]# #招商银行[话题]# #工商银行[话题]# #私人银行[话题]# #金融[话题]# #储蓄[话题]# #平安银行[话题]#', 'metadata': {}, 'images': [{'image_index': 1, 'status': 'recognized', 'text': '10:56\n！1\n<波哥的商业观察\n主要银行“私人银行”标准一览\nO\nR\n0\n<table><tr><td rowspan=1 colspan=1>银行名称</td><td rowspan=1 colspan=1>私人银行客户标准（万元）</td><td rowspan=1 colspan=1>2023年私行客户人数（户)</td><td rowspan=1 colspan=1>2023年管理规模（亿元）</td><td rowspan=1 colspan=1>2023年户均总资产（万元）</td></tr><tr><td rowspan=1 colspan=1>招商银行</td><td rowspan=1 colspan=1>≥1000万元</td><td rowspan=1 colspan=1>148842</td><td rowspan=1 colspan=1>40000</td><td rowspan=1 colspan=1>&gt;2800.00</td></tr><tr><td rowspan=1 colspan=1>工商银行</td><td rowspan=1 colspan=1>≥800万元</td><td rowspan=1 colspan=1>262900</td><td rowspan=1 colspan=1>30700</td><td rowspan=1 colspan=1>1167.74</td></tr><tr><td rowspan=1 colspan=1>中国银行</td><td rowspan=1 colspan=1>&gt;500万元</td><td rowspan=1 colspan=1>173700</td><td rowspan=1 colspan=1>25000</td><td rowspan=1 colspan=1>1548.65</td></tr><tr><td rowspan=1 colspan=1>农业银行</td><td rowspan=1 colspan=1>≥600万元</td><td rowspan=1 colspan=1>224000</td><td rowspan=1 colspan=1>25500</td><td rowspan=1 colspan=1>1183.04</td></tr><tr><td rowspan=1 colspan=1>建设银行</td><td rowspan=1 colspan=1>≥2600万元</td><td rowspan=1 colspan=1>212800</td><td rowspan=1 colspan=1>25200</td><td rowspan=1 colspan=1>118421</td></tr><tr><td rowspan=1 colspan=1>平安银行</td><td rowspan=1 colspan=1>&gt;500万元</td><td rowspan=1 colspan=1>90240</td><td rowspan=1 colspan=1>19155.15</td><td rowspan=1 colspan=1>2123.63</td></tr><tr><td rowspan=1 colspan=1>交通银行</td><td rowspan=1 colspan=1>≥600万元</td><td rowspan=1 colspan=1>3600</td><td rowspan=1 colspan=1>1664</td><td rowspan=1 colspan=1>139522</td></tr><tr><td rowspan=1 colspan=1>中信银行</td><td rowspan=1 colspan=1>≥600万元</td><td rowspan=1 colspan=1>7400</td><td rowspan=1 colspan=1>10312.7</td><td rowspan=1 colspan=1>1393.61</td></tr><tr><td rowspan=1 colspan=1>兴业银行</td><td rowspan=1 colspan=1>≥600万元</td><td rowspan=1 colspan=1>69158</td><td rowspan=1 colspan=1>8550.12</td><td rowspan=1 colspan=1>123632</td></tr><tr><td rowspan=1 colspan=1>民生银行</td><td rowspan=1 colspan=1>&gt;800万元</td><td rowspan=1 colspan=1>55906</td><td rowspan=1 colspan=1>7572.85</td><td rowspan=1 colspan=1>1354.57</td></tr><tr><td rowspan=1 colspan=1>光大银行</td><td rowspan=1 colspan=1>≥1000万元</td><td rowspan=1 colspan=1>63502</td><td rowspan=1 colspan=1>6378.08</td><td rowspan=1 colspan=1>1004.39</td></tr><tr><td rowspan=1 colspan=1>浦发银行</td><td rowspan=1 colspan=1>≥800万元</td><td rowspan=1 colspan=1>280000</td><td rowspan=1 colspan=1>5300</td><td rowspan=1 colspan=1>1892.86</td></tr><tr><td rowspan=1 colspan=1>宁波银行</td><td rowspan=1 colspan=1>≥500万元</td><td rowspan=1 colspan=1>17902</td><td rowspan=1 colspan=1>2174</td><td rowspan=1 colspan=1>1214.53</td></tr><tr><td rowspan=1 colspan=1>新商银行</td><td rowspan=1 colspan=1>≥2500万元</td><td rowspan=1 colspan=1>12189</td><td rowspan=1 colspan=1>1831.75</td><td rowspan=1 colspan=1>1502.79</td></tr></table>'}]}, '{"relevant":false,"tag_stale":null,"tag_audience_mismatch":null,"credit_card_only":null,"tag_promo":null,"tag_resale":null,"tag_question":null,"customer_segment":null,"rank_reason":"仅列出光大银行私人银行客户资产门槛及规模数据，未涉及任何具体权益或服务内容","evidence":{"relevant":{"source_id":"image:1","quote":"<tr><td rowspan=1 colspan=1>光大银行</td><td rowspan=1 colspan=1>≥1000万元</td><td rowspan=1 colspan=1>63502</td><td rowspan=1 colspan=1>6378.08</td><td rowspan=1 colspan=1>1004.39</td></tr>"}},"benefits":[],"image_types":[{"image_index":1,"type":"benefit_table","quote":"主要银行“私人银行”标准一览\\nO\\nR\\n0\\n<table><tr><td rowspan=1 colspan=1>银行名称</td><td rowspan=1 colspan=1>私人银行客户标准（万元）</td><td rowspan=1 colspan=1>2023年私行客户人数（户)</td><td rowspan=1 colspan=1>2023年管理规模（亿元）</td><td rowspan=1 colspan=1>2023年户均总资产（万元）</td></tr>"}]}'), ({'title': '【盘点】国内14家 🏦 私人银行达标门槛', 'content': '[零R][一R]工行：私人银行业务于2008年3月推出\n私银体验客户：月日均资产600万\n高净值客户：月日均资产800万\n超高净值客户：月日均资产2000万\n极高净值客户：月日均资产1亿\n\t\n[零R][二R]农行：私人银行业务于2010年5月推出\n月日均资产达到600万\n\t\n[零R][三R]中行：私人银行业务于2007年3月推出\n月日均资产达到600万\n\t\n[零R][四R]建行：私人银行业务于2008年7月推出\n月日均资产达到600万\n\t\n[零R][五R]交行：私人银行业务于2008年3月推出\n月日均资产达到600万\n\t\n[零R][六R]邮政：2023年1月明确将资产在 600 万以上的 “鼎福” 客户作为私人银行层级客户，月日均资产达到600万。\n\t\n[零R][七R]招行：私人银行业务于2007年8月推出\n标准私人银行客户：月日均资产1000万\n超高端私人银行客户：月日均资产5000万\n[星R]备注：500万以上准私行 权益跟私行基本一致\n\t\n[零R][八R]中信：私人银行业务于2007年8月推出\n白钻级客户：季度日均资产600万\n粉钻级客户：季度日均资产1000万\n紫钻级客户：季度日均资产2000万\n黑钻级客户：季度日均资产5000万\n\t\n[零R][九R]平安：私人银行业务于2013年11月推出\n私行客户：月日均达到600万\n私行客户：月日均达到3000万\n[星R]备注1：平安实行资产双算，集团内其他资产也计入，如证券，可参考之前笔记！\n[星R]备注2：平安是唯.一有运通私人银行的机构，享运通T1层级礼遇，可参考之前笔记！\n\t\n[一R][零R]民生：私人银行业务于2008年推出\n2022年3月由私银客户更名为钻石客户，资产月日均达到600万。\n\t\n[一R][一R]兴业：私人银行业务于2011年4月推出\n品尊级：月日均资产600万\n领尊级：月日均资产3000万\n顶尊级：月日均资产1亿\n\t\n[一R][二R]光大：私人银行业务于2011年推出\n月日均资产达到600万\n\t\n[一R][二R]浦发：私人银行业务于2011年12月推出\n私行体验客户 ：\nvvip1，月日均资产600万\n私行尊享客户：\nvvip2，月日均资产800万\nvvip3，月日均资产3000万\nvvip4，月日均资产1亿\n\t\n[一R][三R]华夏：私人银行业务于2021年10月推出\n月日均资产达到600万\n\t\n[一R][四R]广发：私人银行业务于2013年推出\n月日均资产达到600万\n\t\n#不懂就问有问必答[话题]# #私人银行[话题]#  #金融知识[话题]# #财富管理[话题]# #银行[话题]# #五月的张先生[话题]#', 'metadata': {}, 'images': [{'image_index': 1, 'status': 'recognized', 'text': '国内私人银行\n门槛汇总'}]}, '{"relevant":false,"tag_stale":null,"tag_audience_mismatch":null,"credit_card_only":null,"tag_promo":null,"tag_resale":null,"tag_question":null,"customer_segment":"私人银行","rank_reason":"仅提及光大银行私人银行业务推出时间及达标门槛，未涉及任何具体权益或服务内容。","evidence":{"relevant":{"source_id":"note","quote":"[一R][二R]光大：私人银行业务于2011年推出\\n月日均资产达到600万"}},"benefits":[],"image_types":[{"image_index":1,"type":"unknown","quote":"国内私人银行\\n门槛汇总"}]}')], ids=["image_admission", "body_admission"])
def test_original_admission_thresholds_receive_explicit_qualification_rule(monkeypatch, stage, note, response):
    async def model(messages, **kwargs):
        prompt = messages[0]["content"]
        assert "本场景query的“权益”包含私行办理资格、资产达标门槛、准入标准及服务体验" in prompt
        if stage == "screen":
            assert len(prompt) < 1600
        # 原始否定响应不改判，只核对发送给模型的资格定义契约。
        return response
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is False
    assert "relevant" in result["judgment_evidence"]


@pytest.mark.parametrize("stage", ["screen", "extract"])
def test_open_asset_tier_without_audience_counterevidence_cannot_be_rejected(monkeypatch, stage):
    text = "光大银行资产提升活动，80万元（含）以上可报名"
    async def model(messages, **kwargs):
        prompt = messages[0]["content"]
        assert "50/80万元以上" in prompt and "不能推断私行不适用" in prompt
        return json.dumps({"relevant": True, "tag_audience_mismatch": True, "benefits": [], "evidence": {
            "relevant": {"source_id": "note", "quote": text}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": text, "metadata": {}}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is True and result["tag_audience_mismatch"] is None


@pytest.mark.parametrize("stage", ["screen", "extract"])
@pytest.mark.parametrize("reference", [
    {"source_id": "note", "line_index": True},
    {"source_id": "note", "line_index": -1},
    {"source_id": "note", "line_index": 2},
    {"source_id": "note", "line_index": "1"},
    {"source_id": "missing", "line_index": 0},
    {"source_id": "note", "line_index": 0},
    {"source_id": ["note"], "line_index": 1},
    {"source_id": "note", "line_index": 1, "quote": "私行客户免费停车"},
])
def test_invalid_indexed_reference_cannot_prove_topic(monkeypatch, stage, reference):
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "benefits": [], "evidence": {"relevant": reference}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": "私行客户免费停车", "metadata": {}}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is None and "relevant" not in result["judgment_evidence"]


@pytest.mark.parametrize("stage", ["screen", "extract"])
@pytest.mark.parametrize("flag,value,text", [
    ("tag_stale", True, "该活动已结束"),
    ("tag_audience_mismatch", True, "仅适用财富客户，私行客户不可参加"),
    ("tag_audience_mismatch", False, "私行客户免费停车"),
    ("credit_card_only", True, "仅信用卡客户可领取"),
])
def test_each_exclusion_and_audience_match_keeps_its_own_indexed_evidence(monkeypatch, stage, flag, value, text):
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, flag: value, "benefits": [], "evidence": {
            "relevant": {"source_id": "note", "line_index": 1},
            flag: {"source_id": "note", "line_index": 1}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": text, "metadata": {}}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": stage}))
    assert result[flag] is value
    assert result["judgment_evidence"][flag] == {"source_id": "note", "quote": text}


def test_expired_private_event_is_excluded_only_with_its_own_deadline_evidence(monkeypatch, tmp_path):
    state = install_source(monkeypatch, tmp_path, "光大银行客户活动，权益见图片", images=True)
    state["task_spec"] = bank_spec(frozen_at="2026-10-13T12:00:00+08:00", require_current_rules=True)
    async def model(messages, **kwargs):
        prompt = messages[0]["content"]
        assert "原文明确活动截止早于scope.frozen_at" in prompt
        assert "混合活动的不同截止期不可合并，未给有效期保持null" in prompt
        if kwargs["max_tokens"] == 4096:
            return '{"relevant": null}'
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "tag_stale": True, "benefits": [],
            "evidence": {"relevant": {"source_id": "image:1", "line_index": 0},
                "tag_audience_mismatch": {"source_id": "image:1", "line_index": 0},
                "tag_stale": {"source_id": "image:1", "line_index": 1}}}, ensure_ascii=False)
    async def images(*args):
        return [{"image_index": 1, "status": "recognized", "text": "光大银行私行客户月月有礼\n活动截止2026年6月30日"}]
    monkeypatch.setattr(bank_benefits, "achat", model)
    monkeypatch.setattr(bank_benefits, "read_images", images)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    note = result["bank_collection"]["candidates"][0]
    assert note["status"] == "excluded" and note["reason"] == "expired_rule"
    assert note["assessment"]["judgment_evidence"]["tag_stale"]["quote"] == "活动截止2026年6月30日"
    assert result["collection_outcome"]["selected_count"] == 0


def test_image_can_prove_previously_unknown_audience_without_complete_benefit_fields(monkeypatch, tmp_path):
    state = install_source(monkeypatch, tmp_path, "光大银行权益见图", images=True)
    calls = []
    async def model(*args, **kwargs):
        evidence = {"relevant": {"source_id": "note", "line_index": 1}}
        if kwargs["max_tokens"] != 4096:
            evidence["tag_audience_mismatch"] = {"source_id": "image:1", "line_index": 0}
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "benefits": [], "evidence": evidence})
    async def images(*args):
        calls.append("ocr")
        return [{"image_index": 1, "status": "recognized", "text": "光大银行私行客户可免费停车"}]
    monkeypatch.setattr(bank_benefits, "achat", model)
    monkeypatch.setattr(bank_benefits, "read_images", images)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert calls == ["ocr"]
    assert result["bank_collection"]["candidates"][0]["status"] == "selected"
    assert result["collection_outcome"]["selected_count"] == 1


def test_raw_snapshot_preserves_previous_audience_unknown_behavior(monkeypatch, tmp_path):
    state = install_source(monkeypatch, tmp_path, "光大银行生日礼体验")
    state["task_spec"] = bank_spec(raw_only=True)
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "evidence": {
            "relevant": {"source_id": "note", "line_index": 1}}})
    monkeypatch.setattr(bank_benefits, "achat", model)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    note = result["bank_collection"]["candidates"][0]
    assert note["status"] == "selected"
    assert note["assessment"]["tag_audience_mismatch"] is None
    assert note["assessment"]["benefits"] == []


def test_original_invalid_expiry_and_generic_asset_reference_cannot_be_selected(monkeypatch, invalid_expiry_capture):
    from src.conductor.collection_results import candidate_review_details
    note = invalid_expiry_capture["note"]
    response = invalid_expiry_capture["response"]
    parsed = json.loads(response)
    assert parsed["tag_stale"] is True
    assert parsed["evidence"]["tag_stale"]["quote"] not in flow.source_context(note)["image:3"]
    async def model(*args, **kwargs):
        return response
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec(frozen_at="2026-10-13T12:00:00+08:00", require_current_rules=True)
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": "extract"}))
    assert result["tag_stale"] is None and "tag_stale" not in result["judgment_evidence"]
    assert result.get("invalid_negative_evidence") == ["tag_stale"]
    assert result["tag_audience_mismatch"] is None
    note["assessment"] = result
    for before_ocr in (False, True):
        assert bank_benefits._exclusion(note, spec.bank_benefits, before_ocr=before_ocr) == "negative_evidence_unknown"
    note.update(status="review_required", reason="negative_evidence_unknown")
    details = candidate_review_details(note, spec.bank_benefits.model_dump(mode="json"))
    assert details["reason"] == "适用条件的依据不完整，仍待核对"
    assert details["explanation"] is None
    assert parsed["evidence"]["tag_stale"] not in details["evidence"]


@pytest.mark.parametrize("flag,scope_key,active,inactive,confirmed", [
    ("tag_stale", "require_current_rules", True, False, "expired_rule"),
    ("tag_audience_mismatch", "audience", "私行", None, "audience_mismatch"),
    ("credit_card_only", "include_credit_card", False, True, "credit_card_only"),
])
@pytest.mark.parametrize("reference_valid", [False, True])
def test_negative_evidence_marker_only_blocks_its_enabled_scope(monkeypatch, flag, scope_key, active, inactive, confirmed, reference_valid):
    text = "仅适用财富客户，私行客户不可参加" if flag == "tag_audience_mismatch" else "光大银行私行客户活动"
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, flag: True, "benefits": [],
            "evidence": {"relevant": {"source_id": "note", "quote": text},
                "tag_audience_mismatch": {"source_id": "note", "quote": text},
                flag: {"source_id": "note", "quote": text if reference_valid else "原文不存在的否定依据"}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec(**{scope_key: active})
    note = {"content": text, "metadata": {"note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00"}}
    note["assessment"] = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits, {"task_spec": spec}))
    assert bank_benefits._exclusion(note, spec.bank_benefits) == (confirmed if reference_valid else "negative_evidence_unknown")
    if not reference_valid:
        assert note["assessment"].get("invalid_negative_evidence") == [flag]
        assert bank_benefits._exclusion(note, bank_spec(**{scope_key: inactive}).bank_benefits) is None


@pytest.mark.parametrize("replacement", ["quote_false", "index_false", "null", "null_with_ref", "false_without_ref", "quote_extra", "index_extra", "mixed"])
def test_screen_invalid_negative_reaches_ocr_and_only_verified_boolean_clears_marker(monkeypatch, tmp_path, replacement):
    text = "光大银行私行客户活动，权益见图片"
    state = install_source(monkeypatch, tmp_path, text, images=True)
    state["task_spec"] = bank_spec(require_current_rules=True)
    calls = []
    async def model(*args, **kwargs):
        evidence = {"relevant": {"source_id": "note", "quote": text},
                    "tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}
        stale = True if kwargs["max_tokens"] == 4096 else None if replacement.startswith("null") else False
        if kwargs["max_tokens"] == 4096:
            evidence["tag_stale"] = {"source_id": "note", "quote": "原文不存在的截止日期"}
        elif replacement not in {"null", "false_without_ref"}:
            ref = {"source_id": "image:1", "line_index": 0} if replacement.startswith("index") else {"source_id": "image:1", "quote": "活动长期有效"}
            if replacement.endswith("extra"):
                ref["unexpected"] = "不能解除旧标记"
            if replacement == "mixed":
                ref["line_index"] = 0
            evidence["tag_stale"] = ref
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "tag_stale": stale, "benefits": [], "evidence": evidence}, ensure_ascii=False)
    async def images(*args):
        calls.append("ocr")
        return [{"image_index": 1, "status": "recognized", "text": "活动长期有效"}]
    monkeypatch.setattr(bank_benefits, "achat", model)
    monkeypatch.setattr(bank_benefits, "read_images", images)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    note = result["bank_collection"]["candidates"][0]
    cleared = replacement in {"quote_false", "index_false"}
    assert calls == ["ocr"]
    assert note["status"] == ("selected" if cleared else "review_required")
    assert note["reason"] == (None if cleared else "negative_evidence_unknown")
    assert note["assessment"].get("invalid_negative_evidence") == (None if cleared else ["tag_stale"])
    assert result["collection_outcome"]["selected_count"] == int(cleared)
    assert result["collection_outcome"]["target_complete"] is cleared


def test_invalid_negative_without_picture_is_review_and_does_not_read_images(monkeypatch, tmp_path):
    text = "光大银行私行客户活动"
    state = install_source(monkeypatch, tmp_path, text)
    state["task_spec"] = bank_spec(require_current_rules=True)
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "tag_stale": True, "evidence": {
            "relevant": {"source_id": "note", "quote": text},
            "tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}, ensure_ascii=False)
    async def forbidden(*args):
        raise AssertionError("无图资料不能调用OCR")
    monkeypatch.setattr(bank_benefits, "achat", model)
    monkeypatch.setattr(bank_benefits, "read_images", forbidden)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    note = result["bank_collection"]["candidates"][0]
    assert note["status"] == "review_required" and note["reason"] == "negative_evidence_unknown"
    assert result["collection_outcome"]["selected_count"] == 0


@pytest.mark.parametrize("value", [None, False])
def test_unreported_expiry_and_untrusted_model_marker_keep_previous_behavior(monkeypatch, value):
    text = "光大银行私行客户可免费停车"
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "tag_stale": value, "tag_audience_mismatch": False,
            "invalid_negative_evidence": ["tag_stale"], "benefits": [], "evidence": {
                "relevant": {"source_id": "note", "quote": text},
                "tag_audience_mismatch": {"source_id": "note", "quote": "私行客户"}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec(require_current_rules=True)
    note = {"content": text, "metadata": {"note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00"},
            "assessment": {"invalid_negative_evidence": ["arbitrary", "relevant"]}}
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits, {"task_spec": spec}))
    assert result["tag_stale"] is value and "invalid_negative_evidence" not in result
    note["assessment"] = result
    assert bank_benefits._exclusion(note, spec.bank_benefits) is None


@pytest.mark.parametrize("quote,expected", [("80万元（含）以上", None), ("大额客户", None), ("高净值客户", None),
    ("私行客户", False), ("私人银行客户", False), ("私银客户", False), ("pRiVaTe BaNkInG", False)])
def test_private_audience_match_requires_private_cue_in_its_own_reference(monkeypatch, quote, expected):
    text = "光大银行私行客户可领取权益；" + quote
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "benefits": [
            {"customer_segment": "私行客户", "evidence": {"customer_segment": {"source_id": "note", "quote": "私行客户"}}}],
            "evidence": {"relevant": {"source_id": "note", "quote": text},
                         "tag_audience_mismatch": {"source_id": "note", "quote": quote}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": text, "metadata": {}}, spec.bank_benefits, {"task_spec": spec}))
    assert result["tag_audience_mismatch"] is expected
    assert ("tag_audience_mismatch" in result["judgment_evidence"]) is (expected is False)
    assert "invalid_negative_evidence" not in result


@pytest.mark.parametrize("audience", [None, "高净值客户", "非私行"])
def test_nonprivate_audience_reference_preserves_previous_matching_behavior(monkeypatch, audience):
    text = "光大银行高净值客户80万元以上活动"
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "benefits": [], "evidence": {
            "relevant": {"source_id": "note", "quote": text},
            "tag_audience_mismatch": {"source_id": "note", "quote": "80万元以上"}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec(audience=audience)
    result = asyncio.run(bank_benefits.assess_note({"content": text, "metadata": {}}, spec.bank_benefits, {"task_spec": spec}))
    assert result["tag_audience_mismatch"] is False


@pytest.mark.parametrize("stage", ["screen", "extract"])
def test_equity_definition_has_one_qualification_meaning_without_original_query_conflict(monkeypatch, stage):
    async def model(messages, **kwargs):
        prompt = messages[0]["content"]
        assert "相关性按原始query" not in prompt
        assert "本场景query的“权益”包含私行办理资格、资产达标门槛、准入标准及服务体验" in prompt
        assert "按此解释主题，无需具名福利" in prompt
        assert "其他范围按query/scope" in prompt
        assert "不能推断私行不适用，也不能单独证明私行适用" in prompt
        if stage == "screen":
            assert len(prompt) < 1600
        else:
            assert "资格信息允许benefits=[]，不得补造福利" in prompt
        return '{"relevant": null, "benefits": []}'
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    asyncio.run(bank_benefits.assess_note({"content": "光大银行办理资格", "metadata": {}}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": stage}))


@pytest.fixture
def invalid_expiry_capture():
    # 保留真实原文和原始响应；移除运行身份，图片地址仅表示已读图数量。
    return {'note': {'title': '光大银行活动避雷！', 'content': '做不起活动就别硬做，非得大字宣传，小字限制。\n1.  总共320份权益奖励，报名人数放开，完全不做限制，最终3700多报名。\n2. 8.28才收到活动通知（注定怎么也无法得到奖励）。按官方回复，8.15以后报名的就算完成所有任务，还是白瞎。\n最后，以后不会参加光大的任何资产提升活动，玩不起又想吸引大家的理财资金。\n#光大银行[话题]###金知了社区[话题]##', 'metadata': {'note_type': 'normal', 'publish_time': '2026-09-16T08:47:14+00:00', 'image_urls': ['synthetic-image-1', 'synthetic-image-2', 'synthetic-image-3']}, 'images': [{'image_index': 1, 'status': 'recognized', 'text': '14:12:32\n1 5 562%\n光大银行金知了社区\n资产冲段位，达标享好礼\n达标条件1\n点击图片下方“点击报名”按钮\n达标条件2\n8月月末时点金融资产较7月末提升至对应段位\n财富段位对照表\n黄金开拓者\n30万(含)-50万元(不含)\n28元微信立减金\n钻石掌局者\n50万(含)-80万元(不含)\n王者财富家\n50元微信立减金\n理财经理\n80万元（含）以上\n80元微信立减金'}, {'image_index': 2, 'status': 'recognized', 'text': '已开启验证码保护，禁止第三方应用读取\n16:07 中国移动22\n【光大银行】尊敬的客户：您反馈\n的金知了社区8月财富进阶资产提升\n活动活动，经核实您月末金融资产\n余额提升达标活动第一档位，您的\n报名时间为2026年8月30日，\n该活动以报名时间先后决定获奖资\n格，该档位最后一名获奖客户的报\n名时间为2026年8月15日10:53:06，\n故您未获奖。祝您生活愉快！\n查看应用\n三\n光大银行'}, {'image_index': 3, 'status': 'recognized', 'text': '16:35:36\nD1 5  570%\n优惠活动\n只想安稳存钱，选择什么产品好？\n本期看点：宝藏存款产品等你发现！点击下方立\n即查看，或许能帮到你哦！快来进入体验吧！金\n知了社区有更多话题等你发掘哦~\n立即查看\n2026/08/28 14:40:00\nLOADING\n财富进阶礼，有机会得最高80元微信立减\n金\n2026年8月31日23：59前点击前往金知了社区参\n与财富进阶资产提升活动，达标有机会得最高80\n元微信立减金，快来参与吧！如需拒收消息，可\n通过手机银行右上角-消息中心-设置-关闭接收消\n息。\n点击查看\n2026/08/28 10:05:14\n四季周转财富不断\n保本保息| 自动滚存 | 随用随取'}]}, 'response': '{"relevant":true,"tag_stale":true,"tag_audience_mismatch":false,"credit_card_only":false,"tag_promo":null,"tag_resale":null,"tag_question":null,"customer_segment":"80万元（含）以上","rank_reason":"涉及光大银行资产提升活动权益及投诉反馈，但非私人银行专属且活动已过期。","evidence":{"relevant":{"source_id":"note","quote":"总共320份权益奖励，报名人数放开"},"tag_stale":{"source_id":"image:3","quote":"2026年8月31日23：59前点击前往金知了社区参与财富进阶资产提升活动"},"tag_audience_mismatch":{"source_id":"image:1","quote":"80万元（含）以上"}},"benefits":[{"bank":"光大银行","customer_segment":"80万元（含）以上","benefit_category":"资产提升奖励","benefit_name":"微信立减金","quota":"320份","threshold":"8月月末时点金融资产较7月末提升至对应段位","point_system":null,"point_cost":null,"point_earn_rule":null,"valid_from":null,"valid_to":"2026年8月31日23：59前","app_path":"金知了社区","activity_period":"2026年8月31日23：59前","registration_period":null,"redemption_period":null,"usage_period":null,"evidence":{"bank":{"source_id":"note","quote":"光大银行活动避雷！\\n"},"customer_segment":{"source_id":"image:1","quote":"80万元（含）以上"},"benefit_category":{"source_id":"image:1","quote":"资产冲段位，达标享好礼"},"benefit_name":{"source_id":"image:1","quote":"80元微信立减金"},"quota":{"source_id":"note","quote":"总共320份权益奖励"},"threshold":{"source_id":"image:1","quote":"8月月末时点金融资产较7月末提升至对应段位"},"valid_to":{"source_id":"image:3","quote":"2026年8月31日23：59前点击前往金知了社区参与财富进阶资产提升活动"},"app_path":{"source_id":"image:3","quote":"点击前往金知了社区参与财富进阶资产提升活动"},"activity_period":{"source_id":"image:3","quote":"2026年8月31日23：59前点击前往金知了社区参与财富进阶资产提升活动"}}}],"image_types":[{"image_index":1,"type":"promotion","quote":"资产冲段位，达标享好礼\\n达标条件1\\n点击图片下方“点击报名”按钮\\n达标条件2\\n8月月末时点金融资产较7月末提升至对应段位\\n财富段位对照表"},{"image_index":2,"type":"offline_notice","quote":"【光大银行】尊敬的客户：您反馈\\n的金知了社区8月财富进阶资产提升\\n活动活动，经核实您月末金融资产\\n余额提升达标活动第一档位"},{"image_index":3,"type":"bank_app_screenshot","quote":"优惠活动\\n只想安稳存钱，选择什么产品好？\\n本期看点：宝藏存款产品等你发现！点击下方立\\n即查看"}]}'}


def test_recorded_asset_amount_is_not_private_audience_counterevidence(monkeypatch, invalid_expiry_capture):
    # 保留第二轮原始响应，金额引用不能代替客群排除依据。
    response = '{"relevant":true,"tag_stale":true,"tag_audience_mismatch":true,"credit_card_only":false,"tag_promo":null,"tag_resale":null,"tag_question":null,"customer_segment":null,"rank_reason":"涉及光大银行资产提升权益体验投诉及达标门槛，但客群为泛财富段位非私行，且活动已明确截止","evidence":{"relevant":{"source_id":"note","quote":"以后不会参加光大的任何资产提升活动，玩不起又想吸引大家的理财资金。"},"tag_stale":{"source_id":"image:3","quote":"2026年8月31日23：59前点击前往金知了社区参与财富进阶资产提升活动"},"tag_audience_mismatch":{"source_id":"image:1","quote":"80万元（含）以上\\n80元微信立减金"}},"benefits":[{"bank":"光大银行","customer_segment":null,"benefit_category":"资产提升奖励","benefit_name":"财富进阶礼-黄金开拓者","quota":"总共320份权益奖励","threshold":"30万(含)-50万元(不含)","point_system":null,"point_cost":null,"point_earn_rule":null,"valid_from":null,"valid_to":null,"app_path":"点击图片下方“点击报名”按钮","activity_period":"8月月末时点金融资产较7月末提升至对应段位","registration_period":null,"redemption_period":null,"usage_period":null,"evidence":{"bank":{"source_id":"note","quote":"光大银行活动避雷！"},"benefit_category":{"source_id":"image:1","quote":"资产冲段位，达标享好礼"},"benefit_name":{"source_id":"image:1","quote":"黄金开拓者"},"quota":{"source_id":"note","quote":"总共320份权益奖励，报名人数放开，完全不做限制，最终3700多报名。"},"threshold":{"source_id":"image:1","quote":"30万(含)-50万元(不含)"},"app_path":{"source_id":"image:1","quote":"点击图片下方“点击报名”按钮"},"activity_period":{"source_id":"image:1","quote":"8月月末时点金融资产较7月末提升至对应段位"}}},{"bank":"光大银行","customer_segment":null,"benefit_category":"资产提升奖励","benefit_name":"财富进阶礼-钻石掌局者","quota":"总共320份权益奖励","threshold":"50万(含)-80万元(不含)","point_system":null,"point_cost":null,"point_earn_rule":null,"valid_from":null,"valid_to":null,"app_path":"点击图片下方“点击报名”按钮","activity_period":"8月月末时点金融资产较7月末提升至对应段位","registration_period":null,"redemption_period":null,"usage_period":null,"evidence":{"bank":{"source_id":"note","quote":"光大银行活动避雷！"},"benefit_category":{"source_id":"image:1","quote":"资产冲段位，达标享好礼"},"benefit_name":{"source_id":"image:1","quote":"钻石掌局者"},"quota":{"source_id":"note","quote":"总共320份权益奖励，报名人数放开，完全不做限制，最终3700多报名。"},"threshold":{"source_id":"image:1","quote":"50万(含)-80万元(不含)"},"app_path":{"source_id":"image:1","quote":"点击图片下方“点击报名”按钮"},"activity_period":{"source_id":"image:1","quote":"8月月末时点金融资产较7月末提升至对应段位"}}},{"bank":"光大银行","customer_segment":null,"benefit_category":"资产提升奖励","benefit_name":"财富进阶礼-王者财富家","quota":"总共320份权益奖励","threshold":"80万元（含）以上","point_system":null,"point_cost":null,"point_earn_rule":null,"valid_from":null,"valid_to":null,"app_path":"点击图片下方“点击报名”按钮","activity_period":"8月月末时点金融资产较7月末提升至对应段位","registration_period":null,"redemption_period":null,"usage_period":null,"evidence":{"bank":{"source_id":"note","quote":"光大银行活动避雷！"},"benefit_category":{"source_id":"image:1","quote":"资产冲段位，达标享好礼"},"benefit_name":{"source_id":"image:1","quote":"王者财富家"},"quota":{"source_id":"note","quote":"总共320份权益奖励，报名人数放开，完全不做限制，最终3700多报名。"},"threshold":{"source_id":"image:1","quote":"80万元（含）以上"},"app_path":{"source_id":"image:1","quote":"点击图片下方“点击报名”按钮"},"activity_period":{"source_id":"image:1","quote":"8月月末时点金融资产较7月末提升至对应段位"}}}],"image_types":[{"image_index":1,"type":"promotion","quote":"光大银行金知了社区\\n资产冲段位，达标享好礼"},{"image_index":2,"type":"unknown","quote":"【光大银行】尊敬的客户：您反馈\\n的金知了社区8月财富进阶资产提升\\n活动活动，经核实您月末金融资产"},{"image_index":3,"type":"bank_app_screenshot","quote":"优惠活动\\n只想安稳存钱，选择什么产品好？\\n本期看点：宝藏存款产品等你发现！点击下方立\\n即查看"}]}'
    note = invalid_expiry_capture["note"]
    async def model(*args, **kwargs):
        return response
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec(frozen_at="2026-10-13T12:00:00+08:00", require_current_rules=True)
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits, {"task_spec": spec}))
    assert result["tag_audience_mismatch"] is None and result["tag_stale"] is None
    assert "tag_audience_mismatch" not in result["judgment_evidence"]
    assert result.get("invalid_negative_evidence") == ["tag_audience_mismatch", "tag_stale"]
    note["assessment"] = result
    assert bank_benefits._exclusion(note, spec.bank_benefits) == "negative_evidence_unknown"
    # 关闭有效期门后仍不能把资产下限认作客群不符，避免过期门遮盖误判。
    assert bank_benefits._exclusion(note, bank_spec(require_current_rules=False).bank_benefits) == "negative_evidence_unknown"


@pytest.mark.parametrize("reference_format", ["quote", "index"])
@pytest.mark.parametrize("text,expected", [("80万元（含）以上\n80元微信立减金", None), ("仅限80万元以上客户", None),
    ("仅限资产达标客户", None), ("财富客户", None), ("高净值客户", None), ("私行客户活动", None),
    ("非私行专属，80万元以上均可报名", None),
    ("非私行客户也可参加", None), ("本活动不排除私行客户", None), ("仅限普通客户和私人银行客户", None),
    ("非私行客户不可参加", None), ("并非私行客户不可参加", None), ("并非不适用私人银行客户", None),
    ("非 私行客户不可参加", None), ("并非\n私行客户不可参加", None), ("并非\n不适用私人银行客户", None),
    ("仅限财富客户", True), ("仅适用普通客户", True), ("私行客户不可参加", True), ("本活动不适用私人银行客户", True)])
def test_private_audience_exclusion_requires_explicit_counterevidence_in_own_reference(monkeypatch, reference_format, text, expected):
    async def model(*args, **kwargs):
        ref = {"source_id": "image:1", "quote": text} if reference_format == "quote" else {"source_id": "image:1", "line_index": 0}
        return json.dumps({"relevant": True, "tag_audience_mismatch": True, "benefits": [], "evidence": {
            "relevant": {"source_id": "note", "quote": "光大银行私行权益见图"}, "tag_audience_mismatch": ref}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note({"content": "光大银行私行权益见图", "metadata": {},
        "images": [{"image_index": 1, "status": "recognized", "text": text}]}, spec.bank_benefits, {"task_spec": spec}))
    assert result["tag_audience_mismatch"] is expected
    assert result.get("invalid_negative_evidence") == (["tag_audience_mismatch"] if expected is None else None)
    assert ("tag_audience_mismatch" in result["judgment_evidence"]) is (expected is True)


@pytest.mark.parametrize("stage", ["screen", "extract"])
def test_judgment_sources_expose_explicit_indexes_and_preserve_all_original_text(monkeypatch, stage):
    image_text = "主要银行“私人银行”标准一览\r\n\r\n" + "<td>光大银行≥1000万元</td>" * 200 + "\n"
    note = {"title": "资格表", "content": "光大银行资格标准", "metadata": {},
        "images": [{"image_index": 1, "status": "recognized", "text": image_text}]}
    async def model(messages, **kwargs):
        prompt = messages[0]["content"]
        assert 'sources各键为含line_index和text的原文行对象列表' in prompt
        assert '每个引用仅用source_id和line_index两个键' in prompt
        assert '顶层判断禁止输出quote或额外键' in prompt
        assert '二选一' not in prompt.split('顶层判断引用格式统一规则：', 1)[1]
        assert '“私行质感”“摆脱存款资产枷锁”等卡面风格词不证明办理资格' in prompt
        if stage == "screen":
            assert len(prompt) < 1600
        payload = json.loads(messages[1]["content"])
        original = flow.source_context(note)
        for key, text in original.items():
            rows = payload["sources"][key]
            assert [row["line_index"] for row in rows] == list(range(len(rows)))
            assert all(set(row) == {"line_index", "text"} for row in rows)
            assert "".join(row["text"] for row in rows) == text
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "benefits": [], "evidence": {
            "relevant": {"source_id": "image:1", "line_index": 2},
            "tag_audience_mismatch": {"source_id": "image:1", "line_index": 0}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits, {"task_spec": spec, "_assessment_stage": stage}))
    assert result["relevant"] is True and result["tag_audience_mismatch"] is False
    assert result["judgment_evidence"]["tag_audience_mismatch"]["quote"] == "主要银行“私人银行”标准一览\r\n"
    assert result["benefits"] == [] and note["images"][0]["text"] == image_text


@pytest.mark.parametrize("text,reference,expected", [
    ("非私行客户不可参加", {"source_id": "note", "quote": "私行客户不可参加"}, None),
    ("非\n私行客户不可参加", {"source_id": "note", "line_index": 2}, None),
    ("私行客户不可参加", {"source_id": "note", "quote": "私行客户不可参加"}, True),
    ("私行客户不可参加", {"source_id": "note", "line_index": 1}, True),
    ("仅限财富客户", {"source_id": "note", "quote": "仅限财富客户"}, True),
    ("仅限财富客户", {"source_id": "note", "line_index": 1}, True),
])
def test_audience_counterevidence_cannot_drop_same_source_clause_prefix(monkeypatch, text, reference, expected):
    async def model(*args, **kwargs):
        return json.dumps({"relevant": True, "tag_audience_mismatch": True, "benefits": [], "evidence": {
            "relevant": {"source_id": "note", "quote": text}, "tag_audience_mismatch": reference}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    note = {"content": text, "metadata": {"note_type": "normal", "publish_time": "2026-09-01T00:00:00+08:00"}}
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits, {"task_spec": spec}))
    assert result["tag_audience_mismatch"] is expected
    assert result.get("invalid_negative_evidence") == (["tag_audience_mismatch"] if expected is None else None)
    note["assessment"] = result
    assert bank_benefits._exclusion(note, spec.bank_benefits) == ("negative_evidence_unknown" if expected is None else "audience_mismatch")
    if expected is True:
        assert result["judgment_evidence"]["tag_audience_mismatch"] == {"source_id": "note", "quote": text}


@pytest.mark.parametrize("stage", ["screen", "extract"])
def test_extract_prompt_states_html_index_private_context_and_marketing_limits(monkeypatch, stage):
    # 只核对发送提示与输出预算，不冒充真实模型判断已改善。
    rules = ("HTML表格的<tr>不是原文换行", "引用前核对该下标确在该sources键的原文行列表内",
        "原文（含标题）若直接将本银行私行客群绑定实际办理、准入或服务体验",
        "可在各判断下独立引用同一原行", "不能借金额、话题标签或泛卡面推定",
        "“无需存款感受私行卡片魅力”、收藏卡面或号码及销售宣传不等于正常准入资格")
    async def model(messages, **kwargs):
        prompt = messages[0]["content"]
        assert kwargs["max_tokens"] == (4096 if stage == "screen" else 65536)
        if stage == "screen":
            assert len(prompt) == 1577 and len(prompt) < 1600
            assert not any(rule in prompt for rule in rules)
        else:
            assert all(rule in prompt for rule in rules)
        return '{"relevant": null, "benefits": []}'
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    asyncio.run(bank_benefits.assess_note({"content": "光大银行私行开户体验", "metadata": {}}, spec.bank_benefits,
        {"task_spec": spec, "_assessment_stage": stage}))


def test_html_row_does_not_create_an_index_or_supply_missing_topic_reference(monkeypatch):
    image_text = "银行办理条件\n<table><tr><td>光大银行私行开户</td></tr><tr><td>到店申请</td></tr></table>"
    note = {"title": "办理说明", "content": "见图", "metadata": {"note_type": "normal",
        "publish_time": "2026-09-01T00:00:00+08:00"},
        "images": [{"image_index": 1, "status": "recognized", "text": image_text}]}
    async def model(messages, **kwargs):
        rows = json.loads(messages[1]["content"])["sources"]["image:1"]
        assert rows == [{"line_index": 0, "text": "银行办理条件\n"},
            {"line_index": 1, "text": image_text.splitlines(keepends=True)[1]}]
        return json.dumps({"relevant": True, "tag_audience_mismatch": False, "benefits": [{
            "benefit_name": "私行开户", "evidence": {"benefit_name": {"source_id": "image:1", "quote": "私行开户"}}}],
            "evidence": {"relevant": {"source_id": "image:1", "line_index": 2},
                "tag_audience_mismatch": {"source_id": "image:1", "line_index": 1}}}, ensure_ascii=False)
    monkeypatch.setattr(bank_benefits, "achat", model)
    spec = bank_spec()
    result = asyncio.run(bank_benefits.assess_note(note, spec.bank_benefits, {"task_spec": spec, "_assessment_stage": "extract"}))
    assert result["relevant"] is None and "relevant" not in result["judgment_evidence"]
    assert result["tag_audience_mismatch"] is False and result["benefits"][0]["benefit_name"] == "私行开户"
    assert note["images"][0]["text"] == image_text
    note["assessment"] = result
    assert bank_benefits._exclusion(note, spec.bank_benefits) == "relevance_unknown"
