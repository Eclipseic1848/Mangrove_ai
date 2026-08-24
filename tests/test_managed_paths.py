# -*- coding: utf-8 -*-
"""受管语义制品路径的跨系统与越界测试。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.services.managed_paths import ManagedPathCodec, ManagedPathError


def _codec(root: Path) -> ManagedPathCodec:
    return ManagedPathCodec(
        root,
        legacy_anchor=("data", "semantic-executions"),
    )


def test_managed_path_survives_whole_root_move(tmp_path: Path) -> None:
    old_root = tmp_path / "old" / "semantic-executions"
    artifact = old_root / "runs" / "run-a" / "result.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("id\n1\n", encoding="utf-8")

    persisted = _codec(old_root).encode(artifact)
    new_root = tmp_path / "new" / "semantic-executions"
    new_root.parent.mkdir(parents=True)
    shutil.move(str(old_root), str(new_root))

    assert persisted == "managed:v1/runs/run-a/result.csv"
    assert _codec(new_root).decode(persisted).read_text(
        encoding="utf-8"
    ) == "id\n1\n"


@pytest.mark.parametrize(
    "legacy",
    [
        r"D:\old-host\data\semantic-executions\runs\run-a\result.csv",
        "/srv/old/data/semantic-executions/runs/run-a/result.csv",
    ],
)
def test_legacy_absolute_path_maps_from_anchor(
    tmp_path: Path,
    legacy: str,
) -> None:
    expected = tmp_path / "runs" / "run-a" / "result.csv"

    assert _codec(tmp_path).decode(legacy) == expected.resolve()


def test_controlled_relative_path_maps_under_current_root(tmp_path: Path) -> None:
    expected = tmp_path / "runs" / "run-a" / "result.csv"

    assert _codec(tmp_path).decode("runs/run-a/result.csv") == expected.resolve()


@pytest.mark.parametrize(
    "unsafe",
    [
        "managed:v1/../outside.txt",
        "managed:v1/runs/../../outside.txt",
        r"\\server\share\data\semantic-executions\secret.txt",
        r"\\?\C:\data\semantic-executions\secret.txt",
        r"\\.\C:\data\semantic-executions\secret.txt",
        r"D:\old-host\wrong-anchor\runs\result.csv",
        "/srv/old/wrong-anchor/runs/result.csv",
        "managed:v1/C:/outside.txt",
    ],
)
def test_decode_rejects_unsafe_or_wrong_anchor(
    tmp_path: Path,
    unsafe: str,
) -> None:
    with pytest.raises(ManagedPathError, match="受管路径无效") as exc_info:
        _codec(tmp_path).decode(unsafe)
    assert exc_info.value.code == "MANAGED_PATH_INVALID"
    assert str(tmp_path) not in str(exc_info.value)


def test_encode_rejects_parent_segments_and_outside_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(ManagedPathError, match="受管路径无效"):
        _codec(root).encode(root / "runs" / ".." / "result.csv")
    with pytest.raises(ManagedPathError, match="受管路径无效"):
        _codec(root).encode(tmp_path / "outside.csv")


def test_decode_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前环境不能创建目录软链接：{exc}")

    with pytest.raises(ManagedPathError, match="受管路径无效"):
        _codec(root).decode("managed:v1/linked/secret.txt")
