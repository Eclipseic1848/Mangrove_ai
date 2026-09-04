# -*- coding: utf-8 -*-
"""AC-04：通过 CapabilityCatalog Interface 验证作用域和不可变版本。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import stat
import sqlite3
import subprocess

import pytest

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityMountResolver,
    DefaultCapabilityMounts,
    CapabilityComponent,
    CapabilityValidation,
    CapabilityPackRef,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
    SqliteCapabilityCatalogRepository,
    AutomationProcedureRef,
    LegacyCapabilityManifestAdapter,
    LegacyDraftImporter,
    OciArtifactDescriptor,
    OrasOciLayoutStore,
)
from src.conversation_steering import (
    CapabilityMaturity,
    CapabilityPack,
    AutomationProcedure,
    ProcedureScope,
)
from src.semantic_harness.capabilities import TABLE_DUCKDB_MANIFEST
from tests.database_migration_helpers import migrated_webui_database


def _pack(
    pack_id: str,
    *,
    owner_id: str | None,
    scope: ProcedureScope,
    digest_char: str,
) -> CapabilityPack:
    return CapabilityPack(
        pack_id=pack_id,
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
        scope=scope,
        maturity=(
            CapabilityMaturity.DRAFT
            if scope is ProcedureScope.PERSONAL
            else CapabilityMaturity.VERIFIED
        ),
        owner_id=owner_id,
    )


def test_owner_only_sees_personal_packs_and_published_platform_packs() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    user_a = CatalogActor(owner_id="user-a", role="user")
    user_b = CatalogActor(owner_id="user-b", role="user")

    catalog.register_pack(
        user_a,
        _pack(
            "private-a",
            owner_id="user-a",
            scope=ProcedureScope.PERSONAL,
            digest_char="a",
        ),
    )
    catalog.register_pack(
        user_b,
        _pack(
            "private-b",
            owner_id="user-b",
            scope=ProcedureScope.PERSONAL,
            digest_char="b",
        ),
    )
    repository.save_pack(
        _pack(
            "platform-pdf",
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            digest_char="c",
        )
    )

    assert [item.pack_id for item in catalog.list_visible_packs(user_a)] == [
        "platform-pdf",
        "private-a",
    ]
    assert catalog.resolve_pack(user_a, "private-b", "1.0.0") is None
    assert catalog.resolve_pack(user_a, "platform-pdf", "1.0.0") is not None


def test_task_revision_freezes_exact_digest_and_pack_version_is_immutable() -> None:
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    user = CatalogActor(owner_id="user-a", role="user")
    original = _pack(
        "private-a",
        owner_id="user-a",
        scope=ProcedureScope.PERSONAL,
        digest_char="a",
    )
    catalog.register_pack(user, original)

    selection = catalog.freeze_selection(
        user,
        task_id="workspace-1",
        revision=2,
        pack_refs=(
            CapabilityPackRef(
                pack_id="private-a",
                version="1.0.0",
                digest=original.digest,
            ),
        ),
    )

    assert selection.task_id == "workspace-1"
    assert selection.revision == 2
    assert selection.pack_refs[0].digest == original.digest

    changed = original.model_copy(
        update={"digest": "sha256:" + "f" * 64}
    )
    try:
        catalog.register_pack(user, changed)
    except ValueError as exc:
        assert "不可覆盖" in str(exc)
    else:
        raise AssertionError("同一能力包版本不应允许覆盖")

    wrong_digest = CapabilityPackRef(
        pack_id="private-a",
        version="1.0.0",
        digest="sha256:" + "e" * 64,
    )
    try:
        catalog.freeze_selection(
            user,
            task_id="workspace-2",
            revision=1,
            pack_refs=(wrong_digest,),
        )
    except ValueError as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("digest 不一致必须失败关闭")


def test_mount_resolver_uses_owner_selection_and_frozen_digest(tmp_path) -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    user = CatalogActor(owner_id="user-a", role="user")
    pack = _pack(
        "private-a",
        owner_id="user-a",
        scope=ProcedureScope.PERSONAL,
        digest_char="a",
    )
    catalog.register_pack(user, pack)
    catalog.freeze_selection(
        user,
        task_id="workspace-mount",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
        ),
    )

    class FakeArtifactStore:
        def materialize(self, *, artifact_name, version, digest, destination):
            assert artifact_name == pack.pack_id
            assert version == pack.version
            assert digest == pack.digest
            destination.mkdir(parents=True)
            (destination / "payload").write_bytes(b"frozen")
            return destination

    resolver = CapabilityMountResolver(
        catalog,
        FakeArtifactStore(),
        tmp_path / "mounts",
    )

    mounts = resolver.resolve_for_owner("user-a", "workspace-mount", 1)

    assert len(mounts) == 1
    assert (mounts[0] / "payload").read_bytes() == b"frozen"
    assert (mounts[0] / ".mangrove-capability-digest").read_text(
        encoding="utf-8"
    ) == pack.digest
    assert resolver.resolve_for_owner("user-b", "workspace-mount", 1) == ()


@pytest.mark.parametrize("mutation", ["content", "mode"])
def test_mount_resolver_rejects_changed_materialized_content_or_mode(
    tmp_path,
    mutation,
) -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    actor = CatalogActor(owner_id="user-a", role="user")
    pack = _pack(
        "private-a",
        owner_id=actor.owner_id,
        scope=ProcedureScope.PERSONAL,
        digest_char="a",
    )
    catalog.register_pack(actor, pack)
    catalog.freeze_selection(
        actor,
        task_id="workspace-integrity",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
        ),
    )

    class FakeArtifactStore:
        def materialize(self, *, artifact_name, version, digest, destination):
            destination.mkdir(parents=True)
            (destination / "payload").write_bytes(b"frozen")
            return destination

    resolver = CapabilityMountResolver(
        catalog,
        FakeArtifactStore(),
        tmp_path / "mounts",
    )
    mount = resolver.resolve_for_owner(
        actor.owner_id,
        "workspace-integrity",
        1,
    )[0]
    payload = mount / "payload"
    if mutation == "content":
        payload.write_bytes(b"changed")
    else:
        payload.chmod(stat.S_IREAD)

    try:
        with pytest.raises(RuntimeError, match="完整性"):
            resolver.resolve_for_owner(actor.owner_id, "workspace-integrity", 1)
    finally:
        payload.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_mount_resolver_rematerializes_legacy_cache_without_integrity_record(
    tmp_path,
) -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    actor = CatalogActor(owner_id="user-a", role="user")
    pack = _pack(
        "private-a",
        owner_id=actor.owner_id,
        scope=ProcedureScope.PERSONAL,
        digest_char="b",
    )
    catalog.register_pack(actor, pack)
    catalog.freeze_selection(
        actor,
        task_id="workspace-legacy-cache",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
        ),
    )
    mount_root = tmp_path / "mounts"
    legacy_mount = mount_root / pack.digest.removeprefix("sha256:")
    legacy_mount.mkdir(parents=True)
    (legacy_mount / ".mangrove-capability-digest").write_text(
        pack.digest,
        encoding="utf-8",
    )
    (legacy_mount / "payload").write_bytes(b"legacy-untrusted")

    class FrozenArtifactStore:
        def materialize(self, *, artifact_name, version, digest, destination):
            assert (artifact_name, version, digest) == (
                pack.pack_id,
                pack.version,
                pack.digest,
            )
            destination.mkdir(parents=True)
            (destination / "payload").write_bytes(b"frozen-from-oci")
            return destination

    resolver = CapabilityMountResolver(
        catalog,
        FrozenArtifactStore(),
        mount_root,
    )

    mounted = resolver.resolve_for_owner(
        actor.owner_id,
        "workspace-legacy-cache",
        1,
    )[0]

    assert mounted == legacy_mount.resolve()
    assert (mounted / "payload").read_bytes() == b"frozen-from-oci"
    assert (mounted.parent / f"{mounted.name}.integrity.json").is_file()


def test_mount_resolver_describes_selected_components_without_sensitive_fields(
    tmp_path,
) -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    actor = CatalogActor(owner_id="user-a", role="user")
    digest = "sha256:" + "a" * 64
    component = CapabilityComponent(
        component_id="MinerU 文档解析",
        version="2.1.0",
        digest=digest,
        scope=ProcedureScope.PERSONAL,
        owner_id="user-a",
        kind="tool",
        oci_reference="oci-layout://local/mineru@" + digest,
        source_provenance=("https://example.invalid/private",),
        entrypoint="python secret.py --token hidden",
    )
    pack = CapabilityPack(
        pack_id="document-parser",
        version="2.1.0",
        digest=digest,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.DRAFT,
        owner_id="user-a",
        component_refs=(f"{component.component_id}@{component.version}@{digest}",),
        manifest=(("purpose", "解析 PDF 文档结构"),),
    )
    repository.save_component(component)
    repository.save_pack(pack)
    catalog.freeze_selection(
        actor,
        task_id="workspace-describe",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
        ),
    )
    resolver = CapabilityMountResolver(
        catalog,
        OrasOciLayoutStore(tmp_path / "oci", layout_id="test"),
        tmp_path / "mounts",
    )

    descriptions = resolver.describe_for_owner(
        "user-a",
        "workspace-describe",
        1,
    )

    assert [item.model_dump() for item in descriptions] == [
        {
            "name": "MinerU 文档解析",
            "kind": "tool",
            "version": "2.1.0",
            "purpose": "解析 PDF 文档结构",
        }
    ]


def test_default_mounts_does_not_migrate_missing_production_database(tmp_path) -> None:
    db_path = tmp_path / "missing" / "webui.db"
    resolver = DefaultCapabilityMounts(
        db_path=db_path,
        oci_layout_path=tmp_path / "oci",
        mount_root=tmp_path / "mounts",
    )

    assert resolver("user-a", "task-a", 1) == ()
    assert not db_path.exists()


def test_workspace_default_runtime_wires_capability_mounts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config.settings import settings

    database = migrated_webui_database(tmp_path / "webui.db")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(
        settings,
        "semantic_execution_root",
        str(tmp_path / "executions"),
    )
    monkeypatch.setattr(
        settings,
        "capability_oci_layout_path",
        str(tmp_path / "oci"),
    )
    monkeypatch.setattr(
        settings,
        "capability_mount_cache_path",
        str(tmp_path / "mounts"),
    )
    from src.api.semantic_workspace_runtime import SemanticWorkspaceManager

    manager = SemanticWorkspaceManager()

    assert isinstance(
        manager._kernel("pi-runtime")._adapter._runtime._capability_mount_resolver,
        DefaultCapabilityMounts,
    )


def test_task_revision_rejects_changed_procedure_selection() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    user = CatalogActor(owner_id="user-a", role="user")
    first = AutomationProcedure(
        procedure_id="invoice-sop",
        version="1.0.0",
        digest="sha256:" + "1" * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.DRAFT,
        owner_id="user-a",
    )
    second = first.model_copy(
        update={
            "procedure_id": "invoice-sop-next",
            "digest": "sha256:" + "2" * 64,
        }
    )
    catalog.register_procedure(user, first)
    catalog.register_procedure(user, second)
    catalog.freeze_selection(
        user,
        task_id="workspace-immutable",
        revision=1,
        pack_refs=(),
        procedure_refs=(
            AutomationProcedureRef(
                procedure_id=first.procedure_id,
                version=first.version,
                digest=first.digest,
            ),
        ),
    )

    try:
        catalog.freeze_selection(
            user,
            task_id="workspace-immutable",
            revision=1,
            pack_refs=(),
            procedure_refs=(
                AutomationProcedureRef(
                    procedure_id=second.procedure_id,
                    version=second.version,
                    digest=second.digest,
                ),
            ),
        )
    except ValueError as exc:
        assert "不可覆盖" in str(exc)
    else:
        raise AssertionError("同一 TaskRevision 的 Procedure 选择不可改变")


def test_catalog_registration_cannot_bypass_maturity_or_platform_publish_gate() -> None:
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    user = CatalogActor(owner_id="user-a", role="user")
    admin = CatalogActor(owner_id="admin-a", role="admin")
    superadmin = CatalogActor(owner_id="root-a", role="superadmin")
    verified_personal = _pack(
        "verified-personal",
        owner_id="user-a",
        scope=ProcedureScope.PERSONAL,
        digest_char="4",
    ).model_copy(update={"maturity": CapabilityMaturity.VERIFIED})
    platform = _pack(
        "platform-direct",
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        digest_char="5",
    )

    for actor, pack in (
        (user, verified_personal),
        (admin, platform),
        (superadmin, platform),
    ):
        try:
            catalog.register_pack(actor, pack)
        except ValueError as exc:
            assert "草稿" in str(exc) or "发布流程" in str(exc)
        else:
            raise AssertionError("目录登记不得绕过成熟度或平台发布门")


def test_user_admin_and_superadmin_share_the_same_owner_visibility_boundary() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    catalog.register_pack(
        CatalogActor(owner_id="user-a", role="user"),
        _pack(
            "private-a",
            owner_id="user-a",
            scope=ProcedureScope.PERSONAL,
            digest_char="a",
        ),
    )
    repository.save_pack(
        _pack(
            "platform-shared",
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            digest_char="b",
        )
    )

    for actor in (
        CatalogActor(owner_id="user-b", role="user"),
        CatalogActor(owner_id="admin-b", role="admin"),
        CatalogActor(owner_id="root-b", role="superadmin"),
    ):
        visible = catalog.list_visible_packs(actor)
        assert [item.pack_id for item in visible] == ["platform-shared"]
        assert catalog.resolve_pack(actor, "private-a", "1.0.0") is None


def test_platform_procedure_and_component_are_hidden_until_published() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    user = CatalogActor(owner_id="user-a", role="user")
    admin = CatalogActor(owner_id="admin-a", role="admin")
    draft = AutomationProcedure(
        procedure_id="platform-draft",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PLATFORM,
        maturity=CapabilityMaturity.DRAFT,
    )
    published = draft.model_copy(
        update={
            "procedure_id": "platform-published",
            "digest": "sha256:" + "b" * 64,
            "maturity": CapabilityMaturity.VERIFIED,
        }
    )
    hidden_component = CapabilityComponent(
        component_id="platform-hidden",
        version="1.0.0",
        digest="sha256:" + "c" * 64,
        scope=ProcedureScope.PLATFORM,
        kind="tool",
        oci_reference="oci-layout://local/hidden@sha256:" + "c" * 64,
    )
    visible_component = hidden_component.model_copy(
        update={
            "component_id": "platform-visible",
            "digest": "sha256:" + "d" * 64,
            "oci_reference": (
                "oci-layout://local/visible@sha256:" + "d" * 64
            ),
            "published": True,
        }
    )
    repository.save_procedure(draft)
    repository.save_procedure(published)
    repository.save_component(hidden_component)
    repository.save_component(visible_component)

    assert catalog.list_visible_procedures(user) == (published,)
    assert catalog.list_visible_components(user) == (visible_component,)
    try:
        catalog.register_component(admin, visible_component)
    except ValueError as exc:
        assert "发布流程" in str(exc)
    else:
        raise AssertionError("管理员不能绕过发布流程直写平台组件")


def test_published_platform_snapshot_is_read_only_and_history_stays_frozen() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    owner = CatalogActor(owner_id="user-a", role="user")
    personal = CapabilityPack(
        pack_id="personal-pdf",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.VERIFIED,
        owner_id="user-a",
        task_refs=("private-task-1",),
        component_refs=("component-1",),
        validation_refs=("validation-1",),
    )
    # AC-07 的发布流程会产出独立脱敏快照；AC-04 目录只读取该结果。
    repository.save_pack(personal)
    published = CapabilityPack(
        pack_id="platform-pdf",
        version="1.0.0",
        digest="sha256:" + "b" * 64,
        scope=ProcedureScope.PLATFORM,
        maturity=CapabilityMaturity.VERIFIED,
        component_refs=("component-1",),
    )
    repository.save_pack(published)

    assert published.scope is ProcedureScope.PLATFORM
    assert published.owner_id is None
    assert published.task_refs == ()
    assert published.digest != personal.digest
    assert catalog.resolve_pack(owner, "personal-pdf", "1.0.0") == personal

    frozen = catalog.freeze_selection(
        owner,
        task_id="workspace-history",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id="platform-pdf",
                version="1.0.0",
                digest=published.digest,
            ),
        ),
    )
    assert catalog.resolve_pack(owner, "platform-pdf", "1.0.0") == published
    assert frozen.pack_refs[0].digest == published.digest


def test_sqlite_catalog_is_concurrent_idempotent_and_does_not_rewrite_legacy_data(
    tmp_path,
) -> None:
    db_path = tmp_path / "catalog.db"
    migrated_webui_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE legacy_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_state VALUES ('keep-me')")

    pack = _pack(
        "private-concurrent",
        owner_id="user-a",
        scope=ProcedureScope.PERSONAL,
        digest_char="d",
    )

    def register_once() -> str:
        catalog = CapabilityCatalog(
            SqliteCapabilityCatalogRepository(str(migrated_webui_database(db_path)))
        )
        return catalog.register_pack(
            CatalogActor(owner_id="user-a", role="user"),
            pack,
        ).digest

    with ThreadPoolExecutor(max_workers=4) as executor:
        digests = list(executor.map(lambda _index: register_once(), range(8)))

    assert digests == [pack.digest] * 8
    catalog = CapabilityCatalog(SqliteCapabilityCatalogRepository(str(migrated_webui_database(db_path))))
    assert len(
        catalog.list_visible_packs(CatalogActor(owner_id="user-a", role="user"))
    ) == 1
    procedure = AutomationProcedure(
        procedure_id="sqlite-sop",
        version="1.0.0",
        digest="sha256:" + "8" * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.DRAFT,
        owner_id="user-a",
    )
    catalog.register_procedure(
        CatalogActor(owner_id="user-a", role="user"),
        procedure,
    )
    reopened = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(str(migrated_webui_database(db_path)))
    )
    assert reopened.resolve_procedure(
        CatalogActor(owner_id="user-a", role="user"),
        "sqlite-sop",
        "1.0.0",
    ) == procedure
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT value FROM legacy_state").fetchone() == (
            "keep-me",
        )


def test_procedure_validation_and_selection_follow_same_owner_and_digest_rules() -> None:
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    user_a = CatalogActor(owner_id="user-a", role="user")
    user_b = CatalogActor(owner_id="user-b", role="user")
    procedure = AutomationProcedure(
        procedure_id="invoice-sop",
        version="1.0.0",
        digest="sha256:" + "9" * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.DRAFT,
        owner_id="user-a",
        capability_refs=("private-a@1.0.0",),
        task_refs=("private-task",),
    )
    catalog.register_procedure(user_a, procedure)
    catalog.register_validation(
        user_a,
        CapabilityValidation(
            validation_id="validation-a",
            owner_id="user-a",
            target_kind="procedure",
            target_id="invoice-sop",
            target_version="1.0.0",
            target_digest=procedure.digest,
            status="passed",
            evidence_refs=("private-evidence",),
        ),
    )

    assert catalog.resolve_procedure(user_b, "invoice-sop", "1.0.0") is None
    assert catalog.list_visible_validations(user_b) == ()
    selection = catalog.freeze_selection(
        user_a,
        task_id="workspace-procedure",
        revision=1,
        pack_refs=(),
        procedure_refs=(
            AutomationProcedureRef(
                procedure_id="invoice-sop",
                version="1.0.0",
                digest=procedure.digest,
            ),
        ),
    )
    assert selection.procedure_refs[0].digest == procedure.digest


def test_component_repository_keeps_oci_digest_and_owner_scope_separate() -> None:
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    user_a = CatalogActor(owner_id="user-a", role="user")
    user_b = CatalogActor(owner_id="user-b", role="user")
    component = CapabilityComponent(
        component_id="pdf-tool",
        version="1.0.0",
        digest="sha256:" + "7" * 64,
        scope=ProcedureScope.PERSONAL,
        owner_id="user-a",
        kind="tool",
        oci_reference="oci-layout://capabilities@sha256:" + "7" * 64,
        permission_requirements=("read_task_sources",),
    )

    catalog.register_component(user_a, component)

    assert catalog.list_visible_components(user_b) == ()
    assert catalog.list_visible_components(user_a) == (component,)


def test_legacy_manifest_adapter_is_read_only_platform_input() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    adapter = LegacyCapabilityManifestAdapter((TABLE_DUCKDB_MANIFEST,))
    catalog = CapabilityCatalog(repository, builtin_adapter=adapter)
    user = CatalogActor(owner_id="user-a", role="user")

    visible = catalog.list_visible_packs(user)

    assert len(visible) == 1
    assert visible[0].pack_id == TABLE_DUCKDB_MANIFEST.capability_id
    assert visible[0].version == TABLE_DUCKDB_MANIFEST.version
    assert visible[0].scope is ProcedureScope.PLATFORM
    assert visible[0].digest.startswith("sha256:")
    assert repository.list_packs() == ()


def test_oras_layout_store_uses_relative_layer_and_returns_digest(tmp_path) -> None:
    source = tmp_path / "legacy-skill.md"
    source.write_text("# 示例能力\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], str]] = []

    def fake_runner(command, *, cwd, **kwargs):
        calls.append((tuple(command), cwd))
        if command[1:3] == ("manifest", "fetch"):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"reference":"ignored","mediaType":'
                '"application/vnd.oci.image.manifest.v1+json",'
                '"digest":"sha256:' + "6" * 64 + '",'
                '"artifactType":"application/vnd.mangrove.capability.v1"}'
            ),
            stderr="",
        )

    store = OrasOciLayoutStore(
        tmp_path / "layout",
        oras_executable="oras-test",
        runner=fake_runner,
    )
    descriptor = store.push_file(
        source,
        artifact_name="legacy-skill",
        version="1.0.0",
        artifact_type="application/vnd.mangrove.capability.v1",
        layer_media_type="text/markdown",
    )

    assert descriptor.digest == "sha256:" + "6" * 64
    assert descriptor.reference.endswith(descriptor.digest)
    assert calls[0][1] == str(source.parent)
    assert "legacy-skill.md:text/markdown" in calls[-1][0]
    assert str(source) not in calls[-1][0]
    assert str(tmp_path) not in descriptor.reference


def test_oras_layout_store_reuses_same_blob_without_second_push(tmp_path) -> None:
    source = tmp_path / "same.txt"
    source.write_text("same", encoding="utf-8")
    layer_digest = "sha256:" + hashlib.sha256(b"same").hexdigest()
    push_digest = "sha256:" + "8" * 64
    pushed = False
    push_count = 0

    def fake_runner(command, **kwargs):
        nonlocal pushed, push_count
        if command[1:3] == ("manifest", "fetch"):
            if not pushed:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing")
            if "--descriptor" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=(
                        '{"mediaType":"application/vnd.oci.image.manifest.v1+json",'
                        f'"digest":"{push_digest}","artifactType":'
                        '"application/vnd.mangrove.capability.v1"}'
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"mediaType":"application/vnd.oci.image.manifest.v1+json",'
                    '"artifactType":"application/vnd.mangrove.capability.v1",'
                    f'"layers":[{{"digest":"{layer_digest}"}}]}}'
                ),
                stderr="",
            )
        pushed = True
        push_count += 1
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"mediaType":"application/vnd.oci.image.manifest.v1+json",'
                f'"digest":"{push_digest}","artifactType":'
                '"application/vnd.mangrove.capability.v1"}'
            ),
            stderr="",
        )

    store = OrasOciLayoutStore(
        tmp_path / "layout",
        oras_executable="oras-test",
        runner=fake_runner,
    )
    def push_once(_index: int):
        return store.push_file(
            source,
            artifact_name="same",
            version="1.0.0",
            artifact_type="application/vnd.mangrove.capability.v1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(push_once, range(2))

    assert first == second
    assert push_count == 1


def test_legacy_import_is_explicit_personal_draft_without_host_path(tmp_path) -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(repository)
    actor = CatalogActor(owner_id="user-a", role="user")
    source = tmp_path / "old-skill.md"
    source.write_text("# 旧 Skill\n", encoding="utf-8")

    class FakeStore:
        def push_file(self, source_path, **kwargs):
            return OciArtifactDescriptor(
                reference="oci-layout://catalog@sha256:" + "3" * 64,
                digest="sha256:" + "3" * 64,
                media_type="application/vnd.oci.image.manifest.v1+json",
                artifact_type=kwargs["artifact_type"],
            )

    importer = LegacyDraftImporter(catalog, FakeStore())

    assert repository.list_packs() == ()
    imported = importer.import_skill(
        actor,
        source,
        pack_id="legacy-pdf",
        version="1.0.0",
    )

    assert imported.maturity is CapabilityMaturity.DRAFT
    assert imported.scope is ProcedureScope.PERSONAL
    assert imported.owner_id == "user-a"
    assert str(tmp_path) not in imported.model_dump_json()
    assert repository.list_packs() == (imported,)


def test_oci_materialize_expands_frozen_capability_archive(tmp_path) -> None:
    """任务装载得到的必须是可执行目录，不能把归档文件误当能力根目录。"""

    import io
    import json
    from pathlib import Path
    import shutil
    import tarfile

    archive = tmp_path / "mangrove-capability.tar"
    with tarfile.open(archive, "w") as bundle:
        manifest = b'{"schema_version":1,"name":"gray-python"}'
        info = tarfile.TarInfo("mangrove-capability.json")
        info.size = len(manifest)
        bundle.addfile(info, io.BytesIO(manifest))
    digest = "sha256:" + "9" * 64

    def fake_runner(command, **kwargs):
        if command[1:3] == ("manifest", "fetch"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"digest": digest}),
                stderr="",
            )
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, output / archive.name)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    store = OrasOciLayoutStore(
        tmp_path / "layout",
        oras_executable="oras-test",
        runner=fake_runner,
    )

    result = store.materialize(
        artifact_name="gray-python",
        version="1.0.0",
        digest=digest,
        destination=tmp_path / "mounted",
    )

    assert (result / "mangrove-capability.json").is_file()
    assert not (result / archive.name).exists()


def test_oci_materialize_rejects_archive_path_traversal(tmp_path) -> None:
    """能力归档不能借成员路径写出任务级挂载目录。"""

    import io
    import json
    from pathlib import Path
    import shutil
    import tarfile

    archive = tmp_path / "mangrove-capability.tar"
    with tarfile.open(archive, "w") as bundle:
        payload = b"escape"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    digest = "sha256:" + "8" * 64

    def fake_runner(command, **kwargs):
        if command[1:3] == ("manifest", "fetch"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"digest": digest}),
                stderr="",
            )
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, output / archive.name)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    store = OrasOciLayoutStore(
        tmp_path / "layout",
        oras_executable="oras-test",
        runner=fake_runner,
    )

    with pytest.raises(RuntimeError, match="路径越界"):
        store.materialize(
            artifact_name="gray-malicious",
            version="1.0.0",
            digest=digest,
            destination=tmp_path / "mounted",
        )

    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "mounted").exists()
