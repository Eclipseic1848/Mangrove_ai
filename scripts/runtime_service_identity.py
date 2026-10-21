"""历史 Run 封存窗口的本机 root 身份采集与只读核验。"""
from __future__ import annotations

import json
import os
import sys
import errno
from pathlib import Path, PurePosixPath
import stat
import argparse
import hashlib
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone


INSTALL_PATH = "/usr/local/lib/mangrove/runtime_service_identity.py"
MANIFEST_KEYS = {"version", "approval_reference", "service", "runtime_root", "candidate_root", "database",
                 "candidate_manifest_sha256", "targets", "configuration_preservation", "mask_path"}
TARGET_KEYS = {"owner_id", "task_id", "revision", "run_id", "runtime_identity_sha256", "snapshot_sha256", "request_id"}
RECEIPT_ROOT = "/run/mangrove-run-seal"
UNIT_PROPERTIES = ("Id", "Names", "MainPID", "ActiveState", "SubState", "LoadState", "UnitFileState", "User",
                   "WorkingDirectory", "FragmentPath", "DropInPaths", "NeedDaemonReload", "ExecMainStartTimestampMonotonic")


class IdentityUnconfirmed(ValueError):
    """缺少正面身份时停止，不返回路径或配置正文。"""


def require(condition, code):
    if not condition:
        raise IdentityUnconfirmed(code)


def absolute_path(value):
    require(isinstance(value, str) and value and "\0" not in value and PurePosixPath(value).is_absolute()
            and ".." not in PurePosixPath(value).parts, "absolute_path_required")
    return Path(value)


def trusted_path(path):
    for current in (path, *path.parents):
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                "root_path_untrusted")
        no_acl(current)
    return path.lstat()


def no_acl(path):
    try:
        os.getxattr(path, "system.posix_acl_access", follow_symlinks=False)
    except OSError as error:
        require(error.errno in {errno.ENODATA, errno.ENOTSUP}, "root_path_acl_unconfirmed")
    else:
        raise IdentityUnconfirmed("root_path_acl_unconfirmed")


def trusted_python():
    # 逐组件解析；只查最终 resolve 会隐藏可由维护账号替换的中间跳。
    current, pending, links = Path("/"), ["usr", "bin", "python3"], 0
    trusted_path(current)
    while pending:
        component, *pending = pending
        if component == "..":
            current = current.parent
            continue
        path = current / component
        info = path.lstat()
        symbolic = stat.S_ISLNK(info.st_mode)
        require(info.st_uid == 0 and (symbolic or not info.st_mode & 0o022), "root_path_untrusted")
        no_acl(path)
        if symbolic:
            links += 1
            require(links <= 40, "python_link_cycle")
            target = os.readlink(path)
            require(isinstance(target, str) and target and "\0" not in target, "python_link_invalid")
            target = PurePosixPath(target)
            if target.is_absolute():
                current, pending = Path("/"), list(target.parts[1:]) + pending
            else:
                pending = list(target.parts) + pending
        else:
            require(not pending or stat.S_ISDIR(info.st_mode), "root_path_untrusted")
            current = path
    require(stat.S_ISREG(current.lstat().st_mode), "system_python_required")


def secure_execution():
    require(sys.flags.isolated == 1 and sys.dont_write_bytecode, "isolated_python_required")
    require(str(__file__) == INSTALL_PATH, "installed_tool_required")
    trusted_path(Path(INSTALL_PATH))
    require(sys.executable == "/usr/bin/python3", "system_python_required")
    trusted_python()


def file_identity(info):
    return {"dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid,
            "mode": stat.S_IMODE(info.st_mode), "nlink": info.st_nlink}


def read_root_file(path, *, private=False):
    trusted_path(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC), "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and before.st_size <= 131072 and (not private or stat.S_IMODE(before.st_mode) == 0o600),
                "root_file_untrusted")
        raw = stream.read(131073)
        after = os.fstat(stream.fileno())
    stable = lambda value: (file_identity(value), value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    require(len(raw) <= 131072 and stable(before) == stable(after) == stable(path.lstat()), "root_file_changed")
    return raw, file_identity(after)


def json_document(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda value: require(False, "invalid_json_constant"))


def hex_value(value, length):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None


def load_manifest(args):
    require(hex_value(args.manifest_sha256, 64), "manifest_digest_invalid")
    path = absolute_path(args.manifest)
    require(stat.S_IMODE(trusted_path(path.parent).st_mode) == 0o700, "manifest_directory_untrusted")
    raw, _ = read_root_file(path, private=True)
    require(hashlib.sha256(raw).hexdigest() == args.manifest_sha256, "manifest_digest_mismatch")
    manifest = json_document(raw)
    require(isinstance(manifest, dict) and set(manifest) == MANIFEST_KEYS
            and type(manifest["version"]) is int and manifest["version"] == 1, "manifest_schema_invalid")
    require(manifest["service"] == "mangrove.service" and isinstance(manifest["approval_reference"], str)
            and 0 < len(manifest["approval_reference"]) <= 200
            and manifest["approval_reference"] == args.approval_reference
            and hex_value(manifest["candidate_manifest_sha256"], 64), "manifest_approval_invalid")
    for key in ("runtime_root", "candidate_root", "database", "mask_path"):
        absolute_path(manifest[key])
    require(manifest["mask_path"] in {"/etc/systemd/system/mangrove.service", "/run/systemd/system/mangrove.service"},
            "mask_path_invalid")
    require(isinstance(manifest["targets"], list) and manifest["targets"], "manifest_targets_invalid")
    for target in manifest["targets"]:
        require(isinstance(target, dict) and set(target) == TARGET_KEYS
                and all(isinstance(target[key], str) and 0 < len(target[key]) <= 200 for key in ("owner_id", "task_id", "run_id"))
                and type(target["revision"]) is int and target["revision"] >= 1
                and all(hex_value(target[key], 64) for key in ("runtime_identity_sha256", "snapshot_sha256"))
                and hex_value(target["request_id"], 32), "manifest_targets_invalid")
    mappings = manifest["configuration_preservation"]
    require(isinstance(mappings, list), "configuration_mapping_invalid")
    sources, destinations = [], []
    for mapping in mappings:
        require(isinstance(mapping, dict) and set(mapping) == {"source", "preserved"}, "configuration_mapping_invalid")
        sources.append(str(absolute_path(mapping["source"])))
        destinations.append(str(absolute_path(mapping["preserved"])))
    require(len(sources) == len(set(sources)) and len(destinations) == len(set(destinations))
            and not set(sources) & set(destinations), "configuration_mapping_invalid")
    return manifest


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("capture", "verify"):
        child = commands.add_parser(command)
        for name in ("manifest", "manifest-sha256", "approval-reference"):
            child.add_argument("--" + name, required=True)
        if command == "verify":
            for name in ("nonce", "owner-id", "task-id", "run-id", "runtime-identity-sha256", "snapshot-sha256", "request-id"):
                child.add_argument("--" + name, required=True)
            child.add_argument("--revision", type=int, required=True)
    return parser.parse_args(argv)


def unit_state(service, *, masked=False):
    result = subprocess.run(["/usr/bin/systemctl", "show", "--all", "--property=" + ",".join(UNIT_PROPERTIES), "--", service],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=10,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    pairs = [line.split("=", 1) for line in result.stdout.splitlines() if line]
    require(all(len(pair) == 2 for pair in pairs) and len({pair[0] for pair in pairs}) == len(pairs), "unit_output_unconfirmed")
    state = dict(pairs)
    required = set(UNIT_PROPERTIES) - ({"DropInPaths", "ExecMainStartTimestampMonotonic"} if masked else set())
    require(required <= set(state) <= set(UNIT_PROPERTIES) and state["Id"] == service and state["Names"] == service,
            "unit_identity_unconfirmed")
    return state


def canonical_directory(value):
    path = absolute_path(value)
    require(path.is_dir() and path.resolve(strict=True) == path, "canonical_directory_required")
    return path


def workspace_identity(manifest):
    runtime = canonical_directory(manifest["runtime_root"])
    candidate = canonical_directory(manifest["candidate_root"])
    cwd = Path(os.getcwd()).resolve(strict=True)
    require(not candidate.is_relative_to(runtime) and cwd.is_relative_to(candidate) and not cwd.is_relative_to(runtime),
            "candidate_cwd_required")
    return runtime, candidate


def database_identity(value, uid):
    path = absolute_path(value)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and path.resolve(strict=True) == path and info.st_uid == uid
            and not info.st_mode & 0o022, "database_identity_unconfirmed")
    # 合法迁移和封存会改变库内容；窗口只绑定稳定的文件身份与权限。
    return {"canonical_path": str(path), "dev": info.st_dev, "ino": info.st_ino,
            "uid": info.st_uid, "mode": info.st_mode}


def process_identity(pid):
    root = Path("/proc") / str(pid)
    status = (root / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    cwd_link, exe_link = root / "cwd", root / "exe"
    # magic link 的实际对象可能来自另一挂载命名空间，路径同名不能替代 dev/ino。
    cwd_stat, exe_stat = cwd_link.stat(), exe_link.stat()
    cwd, exe = cwd_link.resolve(strict=True), exe_link.resolve(strict=True)
    cwd_identity, exe_identity = file_identity(cwd_stat), file_identity(exe_stat)
    require(stat.S_ISDIR(cwd_stat.st_mode) and stat.S_ISREG(exe_stat.st_mode)
            and cwd_identity == file_identity(cwd.stat()) and exe_identity == file_identity(exe.stat()),
            "process_object_identity_unconfirmed")
    return {"pid": pid, "uid": root.stat().st_uid, "start_ticks": status[19],
            "cwd": str(cwd), "cwd_identity": cwd_identity, "exe": str(exe), "exe_identity": exe_identity}


def boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    require(bool(value) and len(value) <= 64, "boot_identity_unconfirmed")
    return value


def captured_configurations(state, manifest):
    sources = [state["FragmentPath"], *state["DropInPaths"].split()]
    require(len(sources) == len(set(sources)), "configuration_list_unconfirmed")
    mapping = {item["source"]: item["preserved"] for item in manifest["configuration_preservation"]}
    require(set(mapping) <= set(sources), "configuration_mapping_unbound")
    result = []
    for source in sources:
        path = absolute_path(source)
        raw, anchor = read_root_file(path)
        preserved = mapping.get(source, source)
        if preserved != source:
            destination = absolute_path(preserved)
            parent = trusted_path(destination.parent)
            require(stat.S_ISDIR(parent.st_mode) and stat.S_IMODE(parent.st_mode) == 0o700
                    and parent.st_dev == anchor["dev"], "configuration_preservation_untrusted")
            try:
                destination.lstat()
            except FileNotFoundError:
                pass
            else:
                raise IdentityUnconfirmed("configuration_preservation_already_exists")
        result.append({"source": source, "preserved": preserved, "identity": anchor,
                       "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return result


def private_directory(path):
    info = trusted_path(path)
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700, "receipt_directory_untrusted")


def new_receipt_directory(nonce):
    base = Path(RECEIPT_ROOT)
    try:
        base.mkdir(mode=0o700)
    except FileExistsError:
        pass
    private_directory(base)
    directory = base / nonce
    directory.mkdir(mode=0o700)
    private_directory(directory)
    return directory


def window_ref(receipt, raw):
    return {"version": 1, "nonce": receipt["nonce"], "receipt_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest_sha256": receipt["manifest_sha256"], "approval_reference": receipt["approval_reference"],
            "captured_at": receipt["captured_at"]}


def public_result(receipt, raw, current_state):
    return {"version": 1, "ok": True, "window_ref": window_ref(receipt, raw), "service": receipt["service"],
            "runtime_root": receipt["runtime_root"], "candidate_root": receipt["candidate_root"],
            "service_uid": receipt["process"]["uid"], "database_identity": receipt["database_identity"],
            "boot_id": receipt["boot_id"], "current_state": current_state,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "expires_monotonic_ns": receipt["captured_monotonic_ns"] + 600_000_000_000}


def capture(args, manifest):
    runtime, candidate = workspace_identity(manifest)
    state = unit_state(manifest["service"])
    require(state["LoadState"] == "loaded" and state["ActiveState"] == "active" and state["SubState"] == "running"
            and state["NeedDaemonReload"] == "no" and state["MainPID"].isdecimal() and int(state["MainPID"]) > 0
            and state["User"] and state["WorkingDirectory"] == str(runtime)
            and state["ExecMainStartTimestampMonotonic"].isdecimal() and int(state["ExecMainStartTimestampMonotonic"]) > 0,
            "loaded_service_identity_unconfirmed")
    import pwd

    uid = int(state["User"]) if state["User"].isdecimal() else pwd.getpwnam(state["User"]).pw_uid
    process = process_identity(int(state["MainPID"]))
    require(uid != 0 and process["uid"] == uid and process["cwd"] == str(runtime)
            and process["cwd_identity"] == file_identity(runtime.stat())
            and process["start_ticks"].isdecimal(), "original_process_identity_unconfirmed")
    configurations = captured_configurations(state, manifest)
    database = database_identity(manifest["database"], uid)
    require(unit_state(manifest["service"]) == state and process_identity(process["pid"]) == process,
            "capture_identity_changed")
    tool_raw, _ = read_root_file(Path(INSTALL_PATH))
    nonce = secrets.token_hex(16)
    require(hex_value(nonce, 32), "generated_nonce_invalid")
    receipt = {"version": 1, "nonce": nonce, "manifest_sha256": args.manifest_sha256,
        "approval_reference": args.approval_reference, "captured_at": datetime.now(timezone.utc).isoformat(),
        "captured_monotonic_ns": time.monotonic_ns(), "tool_sha256": hashlib.sha256(tool_raw).hexdigest(),
        "boot_id": boot_id(), "service": manifest["service"], "runtime_root": str(runtime), "candidate_root": str(candidate),
        "runtime_root_identity": file_identity(runtime.stat()), "database_identity": database,
        "unit": state, "process": process, "configurations": configurations}
    raw = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    os.umask(0o077)
    path = new_receipt_directory(nonce) / "identity.json"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    observed, _ = read_root_file(path, private=True)
    require(observed == raw, "created_receipt_changed")
    return public_result(receipt, raw, "identity_captured")


def fresh_window(receipt):
    captured = datetime.fromisoformat(receipt["captured_at"])
    observed = datetime.now(timezone.utc)
    monotonic = receipt["captured_monotonic_ns"]
    require(captured.utcoffset() is not None and captured.utcoffset().total_seconds() == 0
            and type(monotonic) is int and 0 <= time.monotonic_ns() - monotonic <= 600_000_000_000
            and 0 <= (observed - captured).total_seconds() <= 600, "window_expired_or_future")
    return observed.isoformat()


def verify(args, manifest):
    require(hex_value(args.nonce, 32), "window_nonce_invalid")
    target = {key: getattr(args, key) for key in TARGET_KEYS}
    require(target in manifest["targets"], "target_not_approved")
    runtime, candidate = workspace_identity(manifest)
    directory = Path(RECEIPT_ROOT) / args.nonce
    private_directory(Path(RECEIPT_ROOT))
    private_directory(directory)
    raw, _ = read_root_file(directory / "identity.json", private=True)
    receipt = json_document(raw)
    require(isinstance(receipt, dict) and type(receipt.get("version")) is int and receipt["version"] == 1
            and receipt.get("nonce") == args.nonce and receipt.get("manifest_sha256") == args.manifest_sha256
            and receipt.get("approval_reference") == args.approval_reference
            and all(receipt.get(key) == manifest[key] for key in ("service", "runtime_root", "candidate_root")),
            "window_binding_mismatch")
    fresh_window(receipt)
    require(boot_id() == receipt["boot_id"], "boot_changed")
    tool_raw, _ = read_root_file(Path(INSTALL_PATH))
    require(hashlib.sha256(tool_raw).hexdigest() == receipt["tool_sha256"], "capture_tool_changed")
    require(file_identity(runtime.stat()) == receipt["runtime_root_identity"], "runtime_root_changed")
    require(database_identity(manifest["database"], receipt["process"]["uid"]) == receipt["database_identity"], "database_changed")
    mappings = {item["source"]: item["preserved"] for item in manifest["configuration_preservation"]}
    sources = [receipt["unit"]["FragmentPath"], *receipt["unit"]["DropInPaths"].split()]
    require([item["source"] for item in receipt["configurations"]] == sources, "configuration_list_unconfirmed")
    for item in receipt["configurations"]:
        require(item["preserved"] == mappings.get(item["source"], item["source"]), "configuration_mapping_unbound")
        current, anchor = read_root_file(absolute_path(item["preserved"]))
        require(anchor == item["identity"] and len(current) == item["size"]
                and hashlib.sha256(current).hexdigest() == item["sha256"], "configuration_changed")
    state = unit_state(manifest["service"], masked=True)
    require(state["LoadState"] == "masked" and state["UnitFileState"] in {"masked", "masked-runtime"}
            and state["MainPID"] == "0" and state["ActiveState"] == "inactive" and state["SubState"] == "dead"
            and state["NeedDaemonReload"] == "no" and state["User"] == "" and state["WorkingDirectory"] == "",
            "effective_mask_unconfirmed")
    mask = absolute_path(manifest["mask_path"])
    trusted_path(mask.parent)
    mask_info = mask.lstat()
    mask_anchor = file_identity(mask_info)
    fragment = absolute_path(state["FragmentPath"])
    require(stat.S_ISLNK(mask_info.st_mode) and mask_info.st_uid == 0 and mask_info.st_nlink == 1
            and os.readlink(mask) == "/dev/null" and fragment == mask and file_identity(fragment.lstat()) == mask_anchor,
            "effective_mask_path_unconfirmed")
    try:
        (Path("/proc") / str(receipt["process"]["pid"])).lstat()
    except FileNotFoundError:
        pass
    else:
        raise IdentityUnconfirmed("original_pid_still_exists")
    # CLI 必须在维护候选根；对原根内 Python 不设自身或 PID 白名单。
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid == receipt["process"]["uid"] and (entry / "exe").resolve(strict=True).name.startswith("python"):
                require(not (entry / "cwd").resolve(strict=True).is_relative_to(runtime), "same_root_python_exists")
        except FileNotFoundError:
            try:
                entry.lstat()
            except FileNotFoundError:
                continue
            raise IdentityUnconfirmed("process_identity_unconfirmed")
    require(unit_state(manifest["service"], masked=True) == state, "mask_state_changed")
    require(file_identity(mask.lstat()) == mask_anchor and os.readlink(mask) == "/dev/null", "mask_link_changed")
    result = public_result(receipt, raw, "stopped_masked")
    result["verified_at"] = fresh_window(receipt)
    return result


def main(argv=None):
    if getattr(os, "geteuid", lambda: -1)() != 0:
        print(json.dumps({"version": 1, "ok": False, "error": "root_required"}))
        return 1
    if sys.platform != "linux":
        print(json.dumps({"version": 1, "ok": False, "error": "linux_required"}))
        return 1
    try:
        secure_execution()
        args = arguments(argv)
        manifest = load_manifest(args)
        result = capture(args, manifest) if args.command == "capture" else verify(args, manifest)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except IdentityUnconfirmed as error:
        print(json.dumps({"version": 1, "ok": False, "error": str(error)}))
    except Exception:
        print(json.dumps({"version": 1, "ok": False, "error": "identity_unconfirmed"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
