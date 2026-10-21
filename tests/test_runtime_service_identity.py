"""root 身份工具的离线契约；系统命令必须使用替身。"""
import json
import os
import subprocess
import hashlib
import io
import errno
from pathlib import PurePosixPath
import stat
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from scripts import runtime_service_identity as identity


def test_capture_refuses_non_root_before_any_system_operation(monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    command = Mock(side_effect=AssertionError("禁止真实系统命令"))
    monkeypatch.setattr(subprocess, "run", command)
    code = identity.main(["capture", "--manifest", "/run/approved.json", "--manifest-sha256", "a" * 64])
    assert code == 1
    assert json.loads(capsys.readouterr().out) == {"version": 1, "ok": False, "error": "root_required"}
    command.assert_not_called()


def test_capture_refuses_non_linux_root(monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(identity, "sys", SimpleNamespace(platform="win32"), raising=False)
    assert identity.main(["capture", "--manifest", "/run/approved.json", "--manifest-sha256", "a" * 64]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "linux_required"


@pytest.fixture
def machine(monkeypatch):
    """仅替换操作系统边界，生产工具仍经过真实的解析与验证流程。"""
    nodes, descriptors = {}, {}
    sequence = 100

    def add(name, *, raw=None, mode=0o755, uid=0, link=None, dev=1):
        nonlocal sequence
        name = str(name)
        parent = str(PurePosixPath(name).parent)
        if parent != name and parent not in nodes:
            add(parent)
        sequence += 1
        kind = stat.S_IFLNK if link is not None else stat.S_IFREG if raw is not None else stat.S_IFDIR
        nodes[name] = {"raw": raw, "link": link, "stat": SimpleNamespace(st_mode=kind | mode,
            st_uid=uid, st_gid=0, st_dev=dev, st_ino=sequence, st_nlink=1,
            st_size=len(raw or b""), st_mtime_ns=123, st_ctime_ns=123)}
        return nodes[name]

    class FakePath(PurePosixPath):
        def lstat(self):
            if str(self) not in nodes:
                raise FileNotFoundError(str(self))
            if nodes[str(self)].get("inaccessible"):
                raise PermissionError(str(self))
            return nodes[str(self)]["stat"]

        def resolve(self, strict=False):
            self.lstat()
            target = nodes[str(self)]["link"]
            return FakePath(target).resolve(strict=strict) if target is not None else self

        def stat(self):
            if "magic_stat" in nodes.get(str(self), {}):
                return nodes[str(self)]["magic_stat"]
            return self.resolve(strict=True).lstat()

        def is_dir(self):
            return stat.S_ISDIR(self.stat().st_mode)

        def iterdir(self):
            self.lstat()
            return iter([FakePath(name) for name in nodes if name != str(self) and FakePath(name).parent == self])

        def read_text(self, encoding):
            return nodes[str(self.resolve(strict=True))]["raw"].decode(encoding)

        def mkdir(self, mode=0o777):
            if str(self) in nodes:
                raise FileExistsError(str(self))
            add(self, mode=mode)

    class Stream(io.BytesIO):
        def __init__(self, fd):
            self.fd = fd
            self.name, self.writable_file = descriptors[fd]
            super().__init__(nodes[self.name]["raw"] or b"")

        def fileno(self):
            return self.fd

        def close(self):
            if self.writable_file and not self.closed:
                nodes[self.name]["raw"] = self.getvalue()
                nodes[self.name]["stat"].st_size = len(self.getvalue())
            super().close()

    def open_file(path, flags, mode=0o777):
        nonlocal sequence
        name = str(path)
        if flags & os.O_CREAT:
            if name in nodes and flags & os.O_EXCL:
                raise FileExistsError(name)
            add(name, raw=b"", mode=mode)
        if name not in nodes:
            raise FileNotFoundError(name)
        if nodes[name]["link"] is not None:
            raise OSError(errno.ELOOP, "拒绝链接")
        sequence += 1
        descriptors[sequence] = (name, bool(flags & os.O_WRONLY))
        return sequence

    fake_os = SimpleNamespace(geteuid=lambda: 0, getpid=lambda: 999,
        getcwd=lambda: "/opt/maintenance/candidate", O_RDONLY=os.O_RDONLY, O_WRONLY=os.O_WRONLY,
        O_CREAT=os.O_CREAT, O_EXCL=os.O_EXCL, O_NOFOLLOW=0x20000, O_CLOEXEC=0x80000,
        open=open_file, fdopen=lambda fd, mode: Stream(fd), fstat=lambda fd: nodes[descriptors[fd][0]]["stat"],
        fsync=lambda fd: None, umask=lambda value: 0o077,
        readlink=lambda path: nodes[str(path)]["link"],
        getxattr=Mock(side_effect=OSError(errno.ENODATA, "无ACL")))
    monkeypatch.setattr(identity, "os", fake_os)
    monkeypatch.setattr(identity, "sys", SimpleNamespace(platform="linux", executable="/usr/bin/python3",
        flags=SimpleNamespace(isolated=1), dont_write_bytecode=True))
    monkeypatch.setattr(identity, "Path", FakePath, raising=False)
    monkeypatch.setattr(identity, "__file__", "/usr/local/lib/mangrove/runtime_service_identity.py")
    add("/usr/local/lib/mangrove/runtime_service_identity.py", raw=b"approved tool", mode=0o644)
    add("/usr/bin/python3", link="/usr/bin/python3.10")
    add("/usr/bin/python3.10", raw=b"system python", mode=0o755)
    add("/run/approved", mode=0o700)
    add("/run/approved/manifest.json", raw=b"{}", mode=0o600)
    command = Mock(side_effect=AssertionError("禁止真实系统命令"))
    monkeypatch.setattr(subprocess, "run", command)
    return SimpleNamespace(nodes=nodes, add=add, command=command, os=fake_os, Path=FakePath)


def approved_manifest(machine):
    target = {"owner_id": "owner", "task_id": "task", "revision": 1, "run_id": "run",
              "runtime_identity_sha256": "a" * 64, "snapshot_sha256": "b" * 64, "request_id": "c" * 32}
    manifest = {"version": 1, "approval_reference": "approved-244", "service": "mangrove.service",
        "runtime_root": "/opt/mangrove/app", "candidate_root": "/opt/maintenance/candidate",
        "database": "/var/lib/mangrove/webui.db", "candidate_manifest_sha256": "d" * 64,
        "targets": [target], "configuration_preservation": [], "mask_path": "/run/systemd/system/mangrove.service"}
    return manifest


def put_manifest(machine, manifest):
    raw = json.dumps(manifest).encode("utf-8")
    machine.add("/run/approved/manifest.json", raw=raw, mode=0o600)
    return hashlib.sha256(raw).hexdigest()


def capture_args(digest):
    return ["capture", "--manifest", "/run/approved/manifest.json", "--manifest-sha256", digest,
            "--approval-reference", "approved-244"]


def test_capture_refuses_writable_installed_tool(machine, capsys):
    machine.nodes["/usr/local/lib/mangrove/runtime_service_identity.py"]["stat"].st_mode |= 0o020
    assert identity.main(["capture", "--manifest", "/run/approved/manifest.json",
                          "--manifest-sha256", hashlib.sha256(b"{}").hexdigest()]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "root_path_untrusted"
    machine.command.assert_not_called()


@pytest.mark.parametrize("change,error", [("owner", "root_path_untrusted"), ("write", "root_path_untrusted"),
    ("hardlink", "root_file_untrusted"), ("digest", "manifest_digest_mismatch"),
    ("self-identity", "manifest_schema_invalid")])
def test_capture_refuses_unprotected_or_self_attested_manifest(machine, capsys, change, error):
    manifest = approved_manifest(machine)
    if change == "self-identity":
        manifest["User"] = "ubuntu"
    digest = put_manifest(machine, manifest)
    info = machine.nodes["/run/approved/manifest.json"]["stat"]
    if change == "owner":
        info.st_uid = 1000
    elif change == "write":
        info.st_mode |= 0o020
    elif change == "hardlink":
        info.st_nlink = 2
    elif change == "digest":
        digest = "0" * 64
    assert identity.main(capture_args(digest)) == 1
    assert json.loads(capsys.readouterr().out)["error"] == error
    machine.command.assert_not_called()


def live_service(machine, monkeypatch):
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda user: SimpleNamespace(pw_uid=1000)))
    monkeypatch.setattr(identity, "secrets", SimpleNamespace(token_hex=lambda length: "e" * 32), raising=False)
    machine.add("/opt/mangrove/app", uid=1000)
    machine.add("/opt/maintenance/candidate", uid=1000, mode=0o700)
    machine.add("/var/lib/mangrove/webui.db", raw=b"sqlite fixture", uid=1000, mode=0o600)
    machine.add("/proc/42", uid=1000)
    machine.add("/proc/42/stat", raw=("42 (python) S " + "0 " * 18 + "777 0").encode("utf-8"), uid=1000)
    machine.add("/proc/42/cwd", link="/opt/mangrove/app", uid=1000)
    machine.add("/proc/42/exe", link="/usr/bin/python3.10", uid=1000)
    machine.add("/proc/sys/kernel/random/boot_id", raw=b"boot-original\n")
    machine.add("/etc/systemd/system/mangrove.service", raw=b"[Service]\nUser=ubuntu\n", mode=0o644)
    machine.add("/etc/systemd/system/mangrove.service.d/first.conf", raw=b"[Service]\nEnvironment=PRIVATE=value\n", mode=0o644)
    unit = {"Id": "mangrove.service", "Names": "mangrove.service", "LoadState": "loaded", "UnitFileState": "enabled",
        "MainPID": "42", "ActiveState": "active", "SubState": "running", "NeedDaemonReload": "no", "User": "ubuntu",
        "WorkingDirectory": "/opt/mangrove/app", "FragmentPath": "/etc/systemd/system/mangrove.service",
        "DropInPaths": "/etc/systemd/system/mangrove.service.d/first.conf", "ExecMainStartTimestampMonotonic": "7770000"}
    machine.command.side_effect = lambda *args, **kwargs: SimpleNamespace(stdout="".join(f"{key}={value}\n" for key, value in unit.items()))
    return unit


def test_capture_records_live_identity_in_a_new_root_receipt_without_stopping(machine, monkeypatch, capsys):
    live_service(machine, monkeypatch)
    digest = put_manifest(machine, approved_manifest(machine))
    assert identity.main(capture_args(digest)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["current_state"] == "identity_captured"
    assert result["window_ref"]["nonce"] == "e" * 32
    assert set(result["window_ref"]) == {"version", "nonce", "receipt_sha256", "manifest_sha256", "approval_reference", "captured_at"}
    receipt_node = machine.nodes["/run/mangrove-run-seal/" + "e" * 32 + "/identity.json"]
    assert stat.S_IMODE(receipt_node["stat"].st_mode) == 0o600
    receipt = json.loads(receipt_node["raw"])
    assert receipt["process"]["pid"] == 42 and receipt["process"]["start_ticks"] == "777"
    assert len(receipt["configurations"]) == 2
    assert "PRIVATE=value" not in json.dumps(result) and "PRIVATE=value" not in receipt_node["raw"].decode("utf-8")
    for call in machine.command.call_args_list:
        assert call.args[0][:2] == ["/usr/bin/systemctl", "show"]
        assert call.args[0][-2:] == ["--", "mangrove.service"]


def stopped_window(machine, monkeypatch, capsys, *, manifest=None):
    state = live_service(machine, monkeypatch)
    manifest = manifest or approved_manifest(machine)
    digest = put_manifest(machine, manifest)
    assert identity.main(capture_args(digest)) == 0
    captured = json.loads(capsys.readouterr().out)
    for path in list(machine.nodes):
        if path == "/proc/42" or path.startswith("/proc/42/"):
            del machine.nodes[path]
    for mapping in manifest["configuration_preservation"]:
        machine.nodes[mapping["preserved"]] = machine.nodes.pop(mapping["source"])
    machine.add("/dev/null", raw=b"", mode=0o666)
    machine.add(manifest["mask_path"], link="/dev/null")
    state.update({"LoadState": "masked", "UnitFileState": "masked-runtime", "MainPID": "0",
                  "ActiveState": "inactive", "SubState": "dead", "User": "", "WorkingDirectory": "",
                  "FragmentPath": manifest["mask_path"], "DropInPaths": "", "ExecMainStartTimestampMonotonic": "0"})
    target = manifest["targets"][0]
    args = ["verify", *capture_args(digest)[1:], "--nonce", captured["window_ref"]["nonce"]]
    for key, value in target.items():
        args.extend(["--" + key.replace("_", "-"), str(value)])
    return args, captured, state


def test_verify_uses_protected_identity_and_live_mask_without_running_the_service(machine, monkeypatch, capsys):
    args, captured, _ = stopped_window(machine, monkeypatch, capsys)
    assert identity.main(args) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["current_state"] == "stopped_masked"
    assert verified["window_ref"] == captured["window_ref"]
    assert verified["candidate_root"] == "/opt/maintenance/candidate"
    assert verified["service_uid"] == 1000 and verified["boot_id"] == "boot-original"
    assert type(verified["expires_monotonic_ns"]) is int and verified["expires_monotonic_ns"] > 0
    for call in machine.command.call_args_list:
        assert call.args[0][:2] == ["/usr/bin/systemctl", "show"]


def test_capture_requires_the_exact_system_python_entrypoint(machine, monkeypatch, capsys):
    live_service(machine, monkeypatch)
    digest = put_manifest(machine, approved_manifest(machine))
    machine.add("/tmp/user/python", link="/usr/bin/python3.10", uid=1000)
    identity.sys.executable = "/tmp/user/python"
    assert identity.main(capture_args(digest)) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "system_python_required"


def test_public_identity_contract_preserves_full_database_mode_and_verification_time(machine, monkeypatch, capsys):
    args, captured, _ = stopped_window(machine, monkeypatch, capsys)
    assert captured["database_identity"]["mode"] == stat.S_IFREG | 0o600
    assert identity.main(args) == 0
    verified = json.loads(capsys.readouterr().out)
    for result in (captured, verified):
        assert datetime.fromisoformat(result["verified_at"]).utcoffset().total_seconds() == 0
        assert result["expires_monotonic_ns"] > 0


def test_verify_does_not_return_success_if_window_expires_during_observation(machine, monkeypatch, capsys):
    clock = Mock(side_effect=[100, 101, 601_000_000_100])
    monkeypatch.setattr(identity, "time", SimpleNamespace(monotonic_ns=clock))
    args, _, _ = stopped_window(machine, monkeypatch, capsys)
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "window_expired_or_future"


@pytest.mark.parametrize("replace", [False, True])
def test_database_legal_inplace_writes_are_allowed_but_inode_replacement_is_rejected(machine, monkeypatch, capsys, replace):
    args, captured, _ = stopped_window(machine, monkeypatch, capsys)
    node = machine.nodes["/var/lib/mangrove/webui.db"]
    node["raw"] += b"migration 0024 and prior Run seal"
    node["stat"].st_size += 33
    node["stat"].st_mtime_ns += 10
    if replace:
        node["stat"].st_ino += 1
    assert identity.main(args) == (1 if replace else 0)
    result = json.loads(capsys.readouterr().out)
    if replace:
        assert result["error"] == "database_changed"
    else:
        assert result["database_identity"] == captured["database_identity"]
        assert result["window_ref"] == captured["window_ref"]


@pytest.mark.parametrize("field,value", [("owner-id", "other"), ("task-id", "other"), ("revision", "2"),
    ("run-id", "other"), ("runtime-identity-sha256", "f" * 64), ("snapshot-sha256", "f" * 64), ("request-id", "f" * 32)])
def test_verify_requires_the_exact_approved_target_and_request_nonce(machine, monkeypatch, capsys, field, value):
    args, _, _ = stopped_window(machine, monkeypatch, capsys)
    args[args.index("--" + field) + 1] = value
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "target_not_approved"


@pytest.mark.parametrize("clock,offset", [("monotonic", 601), ("monotonic", -1), ("utc", 601), ("utc", -1)])
def test_verify_rejects_expired_or_future_window_on_each_clock(machine, monkeypatch, capsys, clock, offset):
    args, _, _ = stopped_window(machine, monkeypatch, capsys)
    receipt = json.loads(machine.nodes["/run/mangrove-run-seal/" + "e" * 32 + "/identity.json"]["raw"])
    if clock == "monotonic":
        monkeypatch.setattr(identity, "time", SimpleNamespace(monotonic_ns=lambda: receipt["captured_monotonic_ns"] + offset * 10**9))
    else:
        observed = datetime.fromisoformat(receipt["captured_at"]) + timedelta(seconds=offset)
        monkeypatch.setattr(identity, "datetime", SimpleNamespace(fromisoformat=datetime.fromisoformat, now=lambda tz: observed))
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "window_expired_or_future"


@pytest.mark.parametrize("change,error", [("boot", "boot_changed"), ("pid-reused", "original_pid_still_exists"),
    ("same-root", "same_root_python_exists"), ("self-pid", "same_root_python_exists"), ("uninspectable", "identity_unconfirmed")])
def test_verify_requires_original_pid_gone_and_no_same_root_python_without_any_exemption(machine, monkeypatch, capsys, change, error):
    args, _, _ = stopped_window(machine, monkeypatch, capsys)
    if change == "boot":
        machine.nodes["/proc/sys/kernel/random/boot_id"]["raw"] = b"new-boot"
    elif change == "pid-reused":
        machine.add("/proc/42", uid=1000)
    else:
        pid = 999 if change == "self-pid" else 55
        machine.add(f"/proc/{pid}", uid=1000)
        machine.add(f"/proc/{pid}/exe", link="/usr/bin/python3.10", uid=1000)
        machine.add(f"/proc/{pid}/cwd", link="/opt/mangrove/app", uid=1000)
        if change == "uninspectable":
            machine.nodes[f"/proc/{pid}/exe"]["inaccessible"] = True
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == error


@pytest.mark.parametrize("change", ["original-root", "unapproved-root"])
def test_verify_refuses_cli_cwd_inside_original_root_or_outside_approved_candidate(machine, monkeypatch, capsys, change):
    args, _, _ = stopped_window(machine, monkeypatch, capsys)
    if change == "original-root":
        machine.os.getcwd = lambda: "/opt/mangrove/app"
    else:
        machine.add("/opt/other", uid=1000)
        machine.os.getcwd = lambda: "/opt/other"
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "candidate_cwd_required"


@pytest.mark.parametrize("key,value,error", [("LoadState", "loaded", "effective_mask_unconfirmed"),
    ("UnitFileState", "enabled", "effective_mask_unconfirmed"), ("MainPID", "7", "effective_mask_unconfirmed"),
    ("ActiveState", "active", "effective_mask_unconfirmed"), ("SubState", "running", "effective_mask_unconfirmed"),
    ("NeedDaemonReload", "yes", "effective_mask_unconfirmed"), ("User", "ubuntu", "effective_mask_unconfirmed"),
    ("WorkingDirectory", "/opt/mangrove/app", "effective_mask_unconfirmed"),
    ("MainPID", None, "unit_identity_unconfirmed"), ("User", None, "unit_identity_unconfirmed"),
    ("FragmentPath", "/etc/systemd/system/mangrove.service", "effective_mask_path_unconfirmed")])
def test_verify_requires_effective_mask_and_explicit_empty_identity_keys(machine, monkeypatch, capsys, key, value, error):
    args, _, state = stopped_window(machine, monkeypatch, capsys)
    if value is None:
        del state[key]
    else:
        state[key] = value
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == error


def test_masked_optional_service_fields_may_disappear_without_inventing_values(machine, monkeypatch, capsys):
    args, _, state = stopped_window(machine, monkeypatch, capsys)
    del state["ExecMainStartTimestampMonotonic"]
    del state["DropInPaths"]
    before = set(machine.nodes)
    assert identity.main(args) == 0
    assert json.loads(capsys.readouterr().out)["current_state"] == "stopped_masked"
    assert set(machine.nodes) == before


@pytest.mark.parametrize("change,error", [("bytes", "configuration_changed"), ("owner", "root_path_untrusted"),
    ("hardlink", "root_file_untrusted")])
def test_verify_checks_every_original_dropin_after_mask(machine, monkeypatch, capsys, change, error):
    args, _, _ = stopped_window(machine, monkeypatch, capsys)
    node = machine.nodes["/etc/systemd/system/mangrove.service.d/first.conf"]
    if change == "bytes":
        node["raw"] = b"[Service]\nUser=other\n"
    elif change == "owner":
        node["stat"].st_uid = 1000
    else:
        node["stat"].st_nlink = 2
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == error


@pytest.mark.parametrize("copied", [False, True])
def test_only_approved_same_filesystem_original_inode_preservation_is_accepted(machine, monkeypatch, capsys, copied):
    manifest = approved_manifest(machine)
    parent = "/etc/systemd/system/.approved-244-preserved"
    machine.add(parent, mode=0o700)
    source = "/etc/systemd/system/mangrove.service"
    preserved = parent + "/mangrove.service"
    manifest["configuration_preservation"] = [{"source": source, "preserved": preserved}]
    manifest["mask_path"] = source
    args, _, _ = stopped_window(machine, monkeypatch, capsys, manifest=manifest)
    if copied:
        machine.nodes[preserved]["stat"].st_ino += 1
    assert identity.main(args) == (1 if copied else 0)
    result = json.loads(capsys.readouterr().out)
    assert result.get("error") == ("configuration_changed" if copied else None)


def test_capture_refuses_cross_filesystem_preservation_plan(machine, monkeypatch, capsys):
    live_service(machine, monkeypatch)
    manifest = approved_manifest(machine)
    machine.add("/run/preserved", mode=0o700, dev=2)
    manifest["configuration_preservation"] = [{"source": "/etc/systemd/system/mangrove.service",
                                               "preserved": "/run/preserved/mangrove.service"}]
    assert identity.main(capture_args(put_manifest(machine, manifest))) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "configuration_preservation_untrusted"


def test_capture_nonce_collision_never_overwrites_prior_receipt(machine, monkeypatch, capsys):
    live_service(machine, monkeypatch)
    digest = put_manifest(machine, approved_manifest(machine))
    assert identity.main(capture_args(digest)) == 0
    capsys.readouterr()
    path = "/run/mangrove-run-seal/" + "e" * 32 + "/identity.json"
    prior = machine.nodes[path]["raw"]
    assert identity.main(capture_args(digest)) == 1
    assert machine.nodes[path]["raw"] == prior


@pytest.mark.parametrize("fragment", ["/dev/null", "/run/systemd/system/other-mask.service"])
def test_verify_requires_fragment_to_be_the_approved_mask_link_not_just_the_same_target(machine, monkeypatch, capsys, fragment):
    args, _, state = stopped_window(machine, monkeypatch, capsys)
    if fragment != "/dev/null":
        machine.add(fragment, link="/dev/null")
    state["FragmentPath"] = fragment
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "effective_mask_path_unconfirmed"


@pytest.mark.parametrize("missing", ["exe", "deleted-exe", "cwd"])
def test_existing_pid_with_missing_exe_or_cwd_is_not_treated_as_a_gone_process(machine, monkeypatch, capsys, missing):
    args, _, _ = stopped_window(machine, monkeypatch, capsys)
    machine.add("/proc/55", uid=1000)
    if missing != "exe":
        machine.add("/proc/55/exe", link="/usr/bin/python3.10" + (" (deleted)" if missing == "deleted-exe" else ""), uid=1000)
    if missing != "cwd":
        machine.add("/proc/55/cwd", link="/opt/mangrove/app", uid=1000)
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "process_identity_unconfirmed"


@pytest.mark.parametrize("change", ["link-owner", "parent-owner", "parent-write"])
def test_system_python_root_target_does_not_hide_untrusted_intermediate_links(machine, monkeypatch, capsys, change):
    live_service(machine, monkeypatch)
    digest = put_manifest(machine, approved_manifest(machine))
    machine.add("/opt/python-hop", uid=1000 if change == "parent-owner" else 0,
                mode=0o775 if change == "parent-write" else 0o755)
    machine.add("/opt/python-hop/python3", link="/usr/bin/python3.10", uid=1000 if change == "link-owner" else 0)
    machine.nodes["/usr/bin/python3"]["link"] = "/opt/python-hop/python3"
    assert identity.main(capture_args(digest)) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "root_path_untrusted"


def test_root_controlled_relative_and_absolute_python_link_chain_is_supported(machine, monkeypatch, capsys):
    live_service(machine, monkeypatch)
    digest = put_manifest(machine, approved_manifest(machine))
    machine.add("/usr/bin/python-stage", link="/etc/alternatives/python-real")
    machine.add("/etc/alternatives/python-real", link="/usr/bin/python3.10")
    machine.nodes["/usr/bin/python3"]["link"] = "python-stage"
    assert identity.main(capture_args(digest)) == 0
    assert json.loads(capsys.readouterr().out)["current_state"] == "identity_captured"


def test_root_controlled_python_chain_still_rejects_intermediate_acl(machine, monkeypatch, capsys):
    live_service(machine, monkeypatch)
    digest = put_manifest(machine, approved_manifest(machine))
    machine.add("/opt/python-hop/python3", link="/usr/bin/python3.10")
    machine.nodes["/usr/bin/python3"]["link"] = "/opt/python-hop/python3"

    def acl(path, *args, **kwargs):
        if str(path) == "/opt/python-hop":
            return b"extended ACL"
        raise OSError(errno.ENODATA, "无ACL")

    machine.os.getxattr = acl
    assert identity.main(capture_args(digest)) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "root_path_acl_unconfirmed"


def test_process_that_really_disappears_during_scan_does_not_block_a_window(machine, monkeypatch, capsys):
    args, _, _ = stopped_window(machine, monkeypatch, capsys)
    machine.add("/proc/55", uid=1000)
    original = machine.Path.lstat

    def disappearing(path):
        if str(path) == "/proc/55":
            machine.nodes.pop(str(path), None)
            raise FileNotFoundError(str(path))
        return original(path)

    monkeypatch.setattr(machine.Path, "lstat", disappearing)
    assert identity.main(args) == 0
    assert json.loads(capsys.readouterr().out)["current_state"] == "stopped_masked"


@pytest.mark.parametrize("change", ["inode", "target"])
def test_mask_link_is_rechecked_after_the_live_process_scan(machine, monkeypatch, capsys, change):
    args, _, state = stopped_window(machine, monkeypatch, capsys)
    observations = 0

    def observed(*args, **kwargs):
        nonlocal observations
        observations += 1
        if observations == 2:
            node = machine.nodes["/run/systemd/system/mangrove.service"]
            if change == "inode":
                node["stat"].st_ino += 1
            else:
                node["link"] = "/tmp/other"
        return SimpleNamespace(stdout="".join(f"{key}={value}\n" for key, value in state.items()))

    machine.command.side_effect = observed
    assert identity.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "mask_link_changed"


@pytest.mark.parametrize("kind", ["cwd", "exe"])
def test_capture_rejects_same_named_process_cwd_or_exe_with_a_different_actual_inode(machine, monkeypatch, capsys, kind):
    live_service(machine, monkeypatch)
    digest = put_manifest(machine, approved_manifest(machine))
    resolved = "/opt/mangrove/app" if kind == "cwd" else "/usr/bin/python3.10"
    different = SimpleNamespace(**vars(machine.nodes[resolved]["stat"]))
    different.st_ino += 999
    machine.nodes[f"/proc/42/{kind}"]["magic_stat"] = different
    assert identity.main(capture_args(digest)) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "process_object_identity_unconfirmed"


def test_service_owned_conda_executable_is_only_observed_and_its_actual_identity_is_recorded(machine, monkeypatch, capsys):
    live_service(machine, monkeypatch)
    digest = put_manifest(machine, approved_manifest(machine))
    executable = "/home/ubuntu/conda/envs/mangrove/bin/python3.13"
    machine.add(executable, raw=b"service python", uid=1000, mode=0o755)
    machine.nodes["/proc/42/exe"]["link"] = executable
    assert identity.main(capture_args(digest)) == 0
    assert json.loads(capsys.readouterr().out)["current_state"] == "identity_captured"
    receipt = json.loads(machine.nodes["/run/mangrove-run-seal/" + "e" * 32 + "/identity.json"]["raw"])
    assert receipt["process"]["exe"] == executable
    assert receipt["process"]["exe_identity"]["uid"] == 1000
    assert receipt["process"]["exe_identity"]["ino"] == machine.nodes[executable]["stat"].st_ino
    assert receipt["process"]["cwd_identity"]["dev"] == machine.nodes["/opt/mangrove/app"]["stat"].st_dev
    assert receipt["process"]["cwd_identity"]["ino"] == machine.nodes["/opt/mangrove/app"]["stat"].st_ino


@pytest.mark.parametrize("kind", ["cwd", "exe"])
def test_capture_rejects_process_object_identity_change_during_second_sampling(machine, monkeypatch, capsys, kind):
    state = live_service(machine, monkeypatch)
    digest = put_manifest(machine, approved_manifest(machine))
    observations = 0

    def observe(*args, **kwargs):
        nonlocal observations
        observations += 1
        if observations == 2:
            resolved = "/opt/mangrove/app" if kind == "cwd" else "/usr/bin/python3.10"
            machine.nodes[resolved]["stat"].st_ino += 1
        return SimpleNamespace(stdout="".join(f"{key}={value}\n" for key, value in state.items()))

    machine.command.side_effect = observe
    assert identity.main(capture_args(digest)) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "capture_identity_changed"
