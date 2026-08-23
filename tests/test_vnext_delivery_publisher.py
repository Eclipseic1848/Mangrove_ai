# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from src.delivery_publishing.models import (
    CandidateRef,
    DeliverySpec,
    PublicationGate,
    PublishCommand,
    TableOutputContract,
)
from src.delivery_publishing.repository import DeliveryPublishingRepository
from src.delivery_publishing.service import DeliveryPublisher
from src.runtime_routing import (
    GateCheck,
    GateSnapshot,
    RolloutActor,
    RuntimeRouting,
    SqliteRuntimeRoutingRepository,
    migrate_runtime_routing,
    runtime_routing_is_p0_blocked,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(
    candidate: Path,
    *,
    verification_status: str = "passed",
    table_output_contracts: tuple[TableOutputContract, ...] = (),
) -> PublishCommand:
    candidate_ref = CandidateRef(
        artifact_id="candidate_json",
        filename="result.json",
        format="json",
        sha256=_sha256(candidate),
        size_bytes=candidate.stat().st_size,
    )
    return PublishCommand.build(
        owner_id="owner-a",
        task_id="task-a",
        task_revision=1,
        task_revision_hash="1" * 64,
        goal_contract_hash="2" * 64,
        run_id="pi-run-a",
        candidates=(candidate_ref,),
        verification_report_id="verification-a",
        verification_report_hash="3" * 64,
        verification_status=verification_status,
        delivery_spec=DeliverySpec(
            requested_formats=("json",),
            output_name="报销结果",
            requested_file_count=1,
            table_output_contracts=table_output_contracts,
        ),
        source_snapshot_refs=("upload-a:" + "4" * 64,),
    )


def test_table_output_contract_changes_new_lineage_without_rewriting_legacy(
    candidate: Path,
) -> None:
    legacy = _command(candidate)
    structured = _command(
        candidate,
        table_output_contracts=(TableOutputContract(
            format="json",
            exact_columns=("name", "amount"),
            json_shape="records",
        ),),
    )

    assert "table_output_contracts" not in legacy.delivery_spec.model_dump(
        mode="json"
    )
    assert structured.delivery_spec.model_dump(mode="json")[
        "table_output_contracts"
    ][0]["json_shape"] == "records"
    assert structured.delivery_spec_hash != legacy.delivery_spec_hash
    assert structured.publication_key != legacy.publication_key


@pytest.fixture
def candidate(tmp_path: Path) -> Path:
    path = tmp_path / "workspace" / "result.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"items": [{"name": "张三", "amount": 10}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def repository(tmp_path: Path) -> DeliveryPublishingRepository:
    return DeliveryPublishingRepository(tmp_path / "webui.db")


def _publisher(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
    *,
    gate: PublicationGate | None = None,
) -> DeliveryPublisher:
    return DeliveryPublisher(
        repository=repository,
        output_root=tmp_path / "deliveries",
        candidate_resolver=lambda _command: {"candidate_json": candidate},
        gate_reader=lambda _command: gate or PublicationGate(),
    )


def test_rejects_candidate_without_passed_verification(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
) -> None:
    publisher = _publisher(tmp_path, repository, candidate)

    with pytest.raises(ValueError, match="独立验证未通过"):
        publisher.publish(
            _command(candidate, verification_status="failed"),
            actor_id="owner-a",
        )

    assert repository.latest_delivery("owner-a", "pi-run-a") is None


def test_rejects_tampered_candidate_before_publication(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
) -> None:
    command = _command(candidate)
    candidate.write_text("{}", encoding="utf-8")
    publisher = _publisher(tmp_path, repository, candidate)

    with pytest.raises(ValueError, match="候选文件哈希已变化"):
        publisher.publish(command, actor_id="owner-a")

    assert repository.latest_delivery("owner-a", "pi-run-a") is None


def test_rejects_candidate_set_that_does_not_match_delivery_spec(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
) -> None:
    base = _command(candidate)
    command = base.model_copy(
        update={
            "delivery_spec": DeliverySpec(
                requested_formats=("csv",),
                output_name="报销结果",
                requested_file_count=1,
            )
        }
    )
    publisher = _publisher(tmp_path, repository, candidate)

    with pytest.raises(ValueError, match="候选格式集合"):
        publisher.publish(command, actor_id="owner-a")


def test_cancel_before_commit_publishes_no_formal_output(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
) -> None:
    publisher = _publisher(
        tmp_path,
        repository,
        candidate,
        gate=PublicationGate(cancel_requested=True),
    )

    with pytest.raises(ValueError, match="已取消"):
        publisher.publish(_command(candidate), actor_id="owner-a")

    assert repository.latest_delivery("owner-a", "pi-run-a") is None
    intent = repository.get_intent(_command(candidate).publication_key)
    assert intent is not None
    assert intent["status"] == "aborted"


def test_central_p0_rollback_blocks_new_vnext_publication(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
) -> None:
    database = tmp_path / "webui.db"
    existing_publisher = _publisher(tmp_path, repository, candidate)
    existing_delivery = existing_publisher.publish(
        _command(candidate),
        actor_id="owner-a",
    )
    with sqlite3.connect(database) as connection:
        delivery_before = tuple(connection.execute(
            "SELECT * FROM formal_delivery_runs WHERE delivery_id=?",
            (existing_delivery.delivery_id,),
        ).fetchone())
        outputs_before = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM formal_delivery_outputs WHERE delivery_id=? "
                "ORDER BY output_id",
                (existing_delivery.delivery_id,),
            ).fetchall()
        ]
        output_dir = Path(connection.execute(
            "SELECT output_dir FROM formal_delivery_runs WHERE delivery_id=?",
            (existing_delivery.delivery_id,),
        ).fetchone()[0])
    files_before = {
        path.name: path.read_bytes() for path in output_dir.iterdir() if path.is_file()
    }
    migrate_runtime_routing(database, tmp_path / "webui-before-g3.db")
    routing = RuntimeRouting(SqliteRuntimeRoutingRepository(database))
    routing.record_gate(
        GateSnapshot.build(
            gate_version="phase4-g3-v1",
            code_commit="a" * 40,
            environment_digest="b" * 64,
            checks=(
                GateCheck(
                    gate_id="delivery-integrity",
                    passed=False,
                    evidence_hash="c" * 64,
                ),
            ),
        ),
        RolloutActor(actor_id="admin-a", role="admin"),
    )
    def gate_reader(_command):
        return PublicationGate(
            p0_blocked=runtime_routing_is_p0_blocked(database)
        )
    blocked_candidate = tmp_path / "blocked" / "result.json"
    blocked_candidate.parent.mkdir()
    blocked_candidate.write_text('{"blocked":true}', encoding="utf-8")

    with pytest.raises(ValueError, match="P0"):
        DeliveryPublisher(
            repository=repository,
            output_root=tmp_path / "deliveries",
            candidate_resolver=lambda _command: {
                "candidate_json": blocked_candidate
            },
            gate_reader=gate_reader,
        ).publish(_command(blocked_candidate), actor_id="owner-a")

    with sqlite3.connect(database) as connection:
        assert tuple(connection.execute(
            "SELECT * FROM formal_delivery_runs WHERE delivery_id=?",
            (existing_delivery.delivery_id,),
        ).fetchone()) == delivery_before
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM formal_delivery_outputs WHERE delivery_id=? "
                "ORDER BY output_id",
                (existing_delivery.delivery_id,),
            ).fetchall()
        ] == outputs_before
    assert {
        path.name: path.read_bytes() for path in output_dir.iterdir() if path.is_file()
    } == files_before


def test_same_publication_is_idempotent_and_owner_scoped(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
) -> None:
    publisher = _publisher(tmp_path, repository, candidate)
    command = _command(candidate)

    first = publisher.publish(command, actor_id="owner-a")
    second = publisher.publish(command, actor_id="owner-a")

    assert second.delivery_id == first.delivery_id
    assert len(first.outputs) == 1
    assert repository.get_delivery("owner-b", first.delivery_id) is None
    assert repository.get_output("owner-b", first.outputs[0].output_id) is None


def test_same_publication_key_with_different_frozen_input_conflicts(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
) -> None:
    publisher = _publisher(tmp_path, repository, candidate)
    command = _command(candidate)
    publisher.publish(command, actor_id="owner-a")
    conflicting = command.model_copy(update={"goal_contract_hash": "9" * 64})

    with pytest.raises(ValueError, match="发布幂等键已用于不同冻结输入"):
        publisher.publish(conflicting, actor_id="owner-a")


def test_recovers_commit_after_filesystem_rename(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _publisher(tmp_path, repository, candidate)
    command = _command(candidate)
    original_commit = repository.commit_delivery
    calls = 0

    def interrupt_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("模拟改名后进程中断")
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(repository, "commit_delivery", interrupt_once)
    with pytest.raises(RuntimeError, match="模拟改名后进程中断"):
        publisher.publish(command, actor_id="owner-a")

    assert repository.get_intent(command.publication_key)["status"] == "committing"
    recovered = publisher.publish(command, actor_id="owner-a")
    assert recovered.status.value == "succeeded"
    assert repository.get_intent(command.publication_key)["status"] == "published"


def test_published_delivery_tamper_fails_closed(
    tmp_path: Path,
    repository: DeliveryPublishingRepository,
    candidate: Path,
) -> None:
    publisher = _publisher(tmp_path, repository, candidate)
    command = _command(candidate)
    manifest = publisher.publish(command, actor_id="owner-a")
    output = repository.get_output("owner-a", manifest.outputs[0].output_id)
    assert output is not None
    Path(output["file_path"]).write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="正式交付文件"):
        publisher.publish(command, actor_id="owner-a")
