"""正式 Office 输出预览复用隔离转换和既有交付权限，不改变原件。"""
import hashlib
import io
import subprocess

import pytest
from pypdf import PdfReader

from src.api.auth import get_current_user, get_store
from src.account_execution import ExecutionAuthorization, execution_context
from src.semantic_harness.delivery import create_delivery
from src.semantic_harness.models import DeliveryFormat, DeliverySpec
from tests.test_pi_runtime_workspace_api import _client
from tests.test_semantic_delivery import _result
from tests.test_semantic_path_portability import _run
from tests.test_semantic_table_execution import _gate_plan


@pytest.fixture(autouse=True)
def _execution():
    with execution_context(ExecutionAuthorization("user-a", 0)):
        yield


def _published(tmp_path, monkeypatch, fmt=DeliveryFormat.DOCX):
    client = _client(tmp_path, monkeypatch, role="user")
    plan = _gate_plan(("artifact-a",)).model_copy(update={
        "delivery": DeliverySpec(formats=(fmt,), output_name="合成预览报告"),
    })
    get_store().create_semantic_harness_run(_run("preview-run"))
    delivery = create_delivery(store=get_store(), output_root=tmp_path / "executions",
        user_id="user-a", run_id="preview-run", plan=plan,
        artifact_paths={"result": _result(tmp_path)})
    return client, delivery.outputs[0]


def test_formal_word_preview_preserves_file_and_read_scope(tmp_path, monkeypatch):
    client, output = _published(tmp_path, monkeypatch)
    if subprocess.run(["docker", "image", "inspect", "mangrove/office-preview:local"], capture_output=True).returncode:
        pytest.skip("需要已有的 Office 隔离预览镜像")
    url = f"/api/semantic-deliveries/outputs/{output.output_id}"
    before = client.get(url).content
    response = client.get(url + "/office-preview")
    assert response.status_code == 200, response.text[:200]
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert PdfReader(io.BytesIO(response.content)).pages
    assert client.get(url).content == before
    assert hashlib.sha256(before).hexdigest() == output.sha256

    client.app.dependency_overrides[get_current_user] = lambda: {"user_id": "user-b", "execution_generation": 0, "role": "user"}
    assert client.get(url + "/office-preview").status_code == 404


@pytest.mark.parametrize("failure", ["tampered", "converter", "unsupported"])
def test_formal_office_preview_fails_closed(tmp_path, monkeypatch, failure):
    client, output = _published(tmp_path, monkeypatch,
        DeliveryFormat.CSV if failure == "unsupported" else DeliveryFormat.DOCX)
    calls = []
    def convert(*args):
        calls.append(args)
        raise RuntimeError("Office 预览转换失败")
    monkeypatch.setattr("src.services.office_preview.office_preview", convert)
    if failure == "tampered":
        from pathlib import Path
        row = get_store().get_semantic_delivery_output("user-a", output.output_id)
        Path(row["file_path"]).write_bytes(b"tampered")
    response = client.get(f"/api/semantic-deliveries/outputs/{output.output_id}/office-preview")
    assert response.status_code == {"tampered": 409, "converter": 422, "unsupported": 415}[failure]
    assert len(calls) == (1 if failure == "converter" else 0)
