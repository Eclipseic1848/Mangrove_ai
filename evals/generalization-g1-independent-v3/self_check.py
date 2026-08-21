# -*- coding: utf-8 -*-
"""G1-03 跨时间确定性、资格与对抗自检。"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent; PROJECT_ROOT = ROOT.parents[1]
for path in (ROOT, PROJECT_ROOT):
    if str(path) not in sys.path: sys.path.insert(0, str(path))

import assertions
from artifact_io import read_source, write_table
from build_independent_set import IMMUTABLE, canonical, payloads, sha, source_hashes
from definitions import functional_cases, safety_cases
from oracle_engine import derive
from src.evaluation.g1_manifest import qualification_gaps

HASH = re.compile(r"^[0-9a-f]{64}$")
def load(path: Path) -> dict: return json.loads(path.read_text(encoding="utf-8"))
def pretty_hash(value: object) -> str: return hashlib.sha256((json.dumps(value, ensure_ascii=False, indent=2)+"\n").encode()).hexdigest()


def write_candidate(path: Path, case: dict, columns: list[str], rows: list[dict[str, str]], wrong_shape: bool = False) -> None:
    shape = case["goal_contract"]["delivery_spec"].get("json_shape")
    if wrong_shape: shape = "records" if shape == "columns_rows" else "columns_rows"
    write_table(path, case["output_format"], columns, rows, shape)


def write_bad_json_row(path: Path, case: dict, columns: list[str], rows: list[dict[str, str]], mutation: str) -> None:
    if mutation == "positional":
        payload = {"columns": columns, "rows": [[row[column] for column in columns] for row in rows]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"); return
    records = [{column: row[column] for column in columns} for row in rows]
    if mutation == "width": records[0].pop(columns[-1])
    elif mutation == "keys": records[0]["额外键"] = "forbidden"
    elif mutation == "order":
        first = records[0]; records[0] = {columns[1]: first[columns[1]], columns[0]: first[columns[0]], **{column: first[column] for column in columns[2:]}}
    else: raise ValueError(mutation)
    shape = case["goal_contract"]["delivery_spec"]["json_shape"]
    payload = records if shape == "records" else {"columns": columns, "rows": records}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def write_columns_rows_envelope(path: Path, columns: list[str], rows: list[dict[str, str]], mutation: str) -> None:
    records = [{column: row[column] for column in columns} for row in rows]
    if mutation == "reverse": payload = {"rows": records, "columns": columns}
    elif mutation == "missing": payload = {"columns": columns}
    elif mutation == "extra": payload = {"columns": columns, "rows": records, "metadata": "not-allowed"}
    else: raise ValueError(mutation)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def reject(callback, label: str) -> None:
    try: callback()
    except assertions.AssertionRejected: return
    raise AssertionError(f"反例未拒绝：{label}")


def verify_freeze(manifest: dict, freeze: dict, expected: str) -> None:
    rebuilt = payloads(ROOT, expected, write_sources=False)
    if manifest != rebuilt[0]: raise AssertionError("manifest 不能由定义与 expected freeze 等价重建")
    for name, value in zip(("oracles.json", "derivation_proof.json", "source_catalog.json"), rebuilt[1:]):
        if load(ROOT / name) != value: raise AssertionError(f"{name} 不能等价重建")
    files = {name: sha(ROOT / name) for name in IMMUTABLE}; sources = source_hashes(ROOT); all_files = {**files, **sources}
    if freeze.get("code_freeze_sha256") != expected or freeze.get("files") != all_files: raise AssertionError("冻结身份或逐文件哈希不一致")
    if freeze.get("heldout_manifest_sha256") != files["heldout_manifest.json"] or freeze.get("source_bundle_sha256") != canonical(sources) or freeze.get("evaluation_bundle_sha256") != canonical(all_files): raise AssertionError("bundle 冻结不一致")


def run(expected: str) -> dict:
    manifest, freeze = load(ROOT / "heldout_manifest.json"), load(ROOT / "freeze.json"); verify_freeze(manifest, freeze, expected)
    gaps = list(qualification_gaps(manifest, expected_code_freeze_sha256=expected)); functional = [c for c in manifest["cases"] if not c["safety_tags"]]; safety = [c for c in manifest["cases"] if c["safety_tags"]]
    if (len(functional), len(safety)) != (31, 5): gaps.append("题量不是 31+5")
    formats = Counter(c["output_format"] for c in manifest["cases"]); categories = Counter(c["category"] for c in manifest["cases"])
    shapes = Counter(c["goal_contract"]["delivery_spec"].get("json_shape") for c in functional if c["output_format"] == "json")
    if formats != Counter({"csv": 12, "json": 12, "xlsx": 12}): gaps.append("输出格式不是 12/12/12")
    if shapes != Counter({"records": 5, "columns_rows": 5}): gaps.append("JSON 形态不是 5/5")
    for case in functional:
        spec = case["goal_contract"].get("delivery_spec") or {}
        if spec.get("format") != case["output_format"] or not spec.get("exact_columns") or not spec.get("row_order") or not spec.get("value_formats"): gaps.append(case["id"]+" 交付规格不完整")
        if case["output_format"] == "json" and spec.get("json_shape") not in {"records", "columns_rows"}: gaps.append(case["id"]+" JSON 形态缺失")
    with tempfile.TemporaryDirectory(prefix="g103-time-a-") as a, tempfile.TemporaryDirectory(prefix="g103-time-b-") as b:
        pa, pb = Path(a), Path(b); payload_a = payloads(pa, expected, write_sources=True); time.sleep(1.1); payload_b = payloads(pb, expected, write_sources=True)
        hashes_a, hashes_b = source_hashes(pa), source_hashes(pb)
        if hashes_a != hashes_b: gaps.append("跨时间来源生成字节不稳定")
        if payload_a != payload_b: gaps.append("跨时间 manifest/oracle/证明/目录重建不等价")
        stable_formats = {suffix: all(hashes_a[name] == hashes_b[name] for name in hashes_a if name.endswith(suffix)) for suffix in (".pdf", ".docx", ".xlsx", ".csv")}
    defs = {c["id"]: c for c in functional_cases()}; oracle = load(ROOT / "oracles.json")["cases"]; reopened = 0
    for case_id, definition in defs.items():
        for source in definition["sources"]:
            if read_source(ROOT/"sources"/source["filename"], source["format"]) != source["sections"]: raise AssertionError(case_id+" 来源重开不一致")
            reopened += 1
        if derive(definition, ROOT) != oracle[case_id]["rows"]: raise AssertionError(case_id+" oracle 重算不一致")
    for definition in safety_cases():
        source = definition["source"]
        if read_source(ROOT/"sources"/source["filename"], source["format"]) != source["sections"]: raise AssertionError(definition["id"]+" 安全来源重开不一致")
        reopened += 1
    with tempfile.TemporaryDirectory(prefix="g103-negative-") as temp:
        tmp = Path(temp)
        for case in functional:
            expected_oracle = oracle[case["id"]]; suffix="."+case["output_format"]
            good=tmp/(case["id"]+"-good"+suffix); write_candidate(good, case, list(expected_oracle["columns"]), deepcopy(expected_oracle["rows"])); assertions.assert_candidate(case, good)
            rows=deepcopy(expected_oracle["rows"]); rows[0][expected_oracle["columns"][-1]]="-777.77"; bad=tmp/(case["id"]+"-value"+suffix); write_candidate(bad, case, list(expected_oracle["columns"]), rows); reject(lambda c=case,p=bad: assertions.assert_candidate(c,p), case["id"]+" 错值")
            cols=list(expected_oracle["columns"]); cols[0],cols[1]=cols[1],cols[0]; badc=tmp/(case["id"]+"-cols"+suffix); write_candidate(badc, case, cols, deepcopy(expected_oracle["rows"])); reject(lambda c=case,p=badc: assertions.assert_candidate(c,p), case["id"]+" 错列")
            if case["output_format"]=="json":
                bads=tmp/(case["id"]+"-shape.json"); write_candidate(bads,case,list(expected_oracle["columns"]),deepcopy(expected_oracle["rows"]),True); reject(lambda c=case,p=bads: assertions.assert_candidate(c,p),case["id"]+" 错形态")
                for mutation in ("width", "keys", "order"):
                    bad_row=tmp/(case["id"]+f"-{mutation}.json"); write_bad_json_row(bad_row,case,list(expected_oracle["columns"]),deepcopy(expected_oracle["rows"]),mutation)
                    if mutation == "order" and case["goal_contract"]["delivery_spec"]["json_shape"] == "columns_rows":
                        assertions.assert_candidate(case, bad_row)
                    else:
                        reject(lambda c=case,p=bad_row: assertions.assert_candidate(c,p),case["id"]+" "+mutation)
                if case["goal_contract"]["delivery_spec"]["json_shape"] == "columns_rows":
                    positional=tmp/(case["id"]+"-positional.json"); write_bad_json_row(positional,case,list(expected_oracle["columns"]),deepcopy(expected_oracle["rows"]),"positional"); reject(lambda c=case,p=positional: assertions.assert_candidate(c,p),case["id"]+" 位置数组")
                    reversed_top=tmp/(case["id"]+"-top-reverse.json"); write_columns_rows_envelope(reversed_top,list(expected_oracle["columns"]),deepcopy(expected_oracle["rows"]),"reverse"); assertions.assert_candidate(case,reversed_top)
                    for envelope_mutation in ("missing", "extra"):
                        bad_top=tmp/(case["id"]+f"-top-{envelope_mutation}.json"); write_columns_rows_envelope(bad_top,list(expected_oracle["columns"]),deepcopy(expected_oracle["rows"]),envelope_mutation); reject(lambda c=case,p=bad_top: assertions.assert_candidate(c,p),case["id"]+" 顶层 "+envelope_mutation)
        forbidden=next(c for c in safety if c["safety_tags"]==["forbidden_content"]); fp=tmp/"forbidden.json"; fp.write_text(json.dumps({"x":forbidden["probe"]["forbidden_literals"][0]}),encoding="utf-8"); reject(lambda: assertions.assert_forbidden_candidate(forbidden,fp),"禁止内容")
        lineage_case=functional[0]; expected_oracle=oracle[lineage_case["id"]]; delivery_path=tmp/"lineage.csv"; write_candidate(delivery_path,lineage_case,list(expected_oracle["columns"]),deepcopy(expected_oracle["rows"])); digest=sha(delivery_path)
        formal={"id":lineage_case["id"],"outcome":"formal_delivery","formal_delivery":{"status":"delivery_published","qa_passed":True,"owner_id":lineage_case["owner_id"],"path":str(delivery_path),"sha256":digest,"size_bytes":delivery_path.stat().st_size,"delivery_id":"g103-delivery-selfcheck","output_id":"g103-output-selfcheck","source_snapshot_refs":[{"source_id":x["source_id"],"sha256":x["sha256"]} for x in lineage_case["source_bindings"]],"candidate_sha256":digest,"verification_report_hash":"b"*64}}
        assertions.assert_functional(lineage_case,formal)
        for field,value in (("delivery_id",""),("owner_id","wrong-owner"),("source_snapshot_refs",[]),("verification_report_hash","bad-hash")):
            bad=deepcopy(formal); bad["formal_delivery"][field]=value; reject(lambda b=bad: assertions.assert_functional(lineage_case,b),"正式交付 "+field)
    envelope={"schema_version":"g1-independent-results.v1","code_freeze_sha256":freeze["code_freeze_sha256"],"heldout_manifest_sha256":freeze["heldout_manifest_sha256"],"cases":[{"id":case["id"]} for case in manifest["cases"]]}
    assertions.validate_results_envelope(envelope,manifest,freeze)
    envelope_mutations=[]
    bad=deepcopy(envelope); bad["schema_version"]="wrong"; envelope_mutations.append(("schema",bad))
    bad=deepcopy(envelope); bad["code_freeze_sha256"]="0"*64; envelope_mutations.append(("code-freeze",bad))
    bad=deepcopy(envelope); bad["heldout_manifest_sha256"]="0"*64; envelope_mutations.append(("manifest",bad))
    bad=deepcopy(envelope); bad["cases"].append(deepcopy(bad["cases"][0])); envelope_mutations.append(("duplicate",bad))
    bad=deepcopy(envelope); bad["cases"].pop(); envelope_mutations.append(("missing",bad))
    bad=deepcopy(envelope); bad["cases"].append({"id":"G103-EXTRA"}); envelope_mutations.append(("extra",bad))
    for label,bad in envelope_mutations: reject(lambda p=bad: assertions.validate_results_envelope(p,manifest,freeze),"结果信封 "+label)
    altered_m, altered_f = deepcopy(manifest), deepcopy(freeze); fake="0"*64 if expected!="0"*64 else "1"*64; altered_m["blind_set_attestation"]["code_freeze_sha256"]=fake; altered_f["code_freeze_sha256"]=fake; mh=pretty_hash(altered_m); altered_f["heldout_manifest_sha256"]=mh; altered_f["files"]["heldout_manifest.json"]=mh; altered_f["evaluation_bundle_sha256"]=canonical(altered_f["files"])
    tamper=False
    try: verify_freeze(altered_m, altered_f, expected)
    except AssertionError: tamper=True
    if not tamper: gaps.append("协调篡改未拒绝")
    report={"status":"PASS" if not gaps else "FAIL","qualification_gaps":gaps,"case_count":len(manifest["cases"]),"functional_count":len(functional),"safety_count":len(safety),"category_distribution":dict(sorted(categories.items())),"format_distribution":dict(sorted(formats.items())),"json_shape_distribution":dict(sorted(shapes.items())),"transformation_trap_count":sum(bool(set(c["traps"])&{"paraphrase","colloquial","ellipsis","reordered"}) for c in manifest["cases"]),"ambiguity_trap_count":sum(bool(set(c["traps"])&{"similar","conflict"}) for c in manifest["cases"]),"source_reopen_count":reopened,"cross_time_source_hashes_equal":hashes_a==hashes_b,"cross_time_payloads_equal":payload_a==payload_b,"stable_format_checks":stable_formats,"counterexamples":{"wrong_value":31,"wrong_column_order":31,"columns_rows_top_columns_order":5,"columns_rows_top_key_order_accepted":5,"columns_rows_top_missing_key":5,"columns_rows_top_extra_key":5,"wrong_json_shape":10,"legacy_positional_rows":5,"wrong_json_row_width":10,"wrong_json_key_set":10,"records_wrong_key_order":5,"columns_rows_key_order_normalized":5,"forbidden_content":1,"formal_delivery_contract":4,"results_envelope":6},"coordinated_tamper_rejected":tamper,"manifest_sha256":sha(ROOT/"heldout_manifest.json"),"source_bundle_sha256":freeze["source_bundle_sha256"],"evaluation_bundle_sha256":freeze["evaluation_bundle_sha256"]}
    return report


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--expected-code-freeze-sha256"); args=parser.parse_args(); expected=args.expected_code_freeze_sha256 or load(ROOT/"freeze.json")["code_freeze_sha256"]
    if not HASH.fullmatch(expected): raise SystemExit("expected code-freeze 格式错误")
    report=run(expected); (ROOT/"self-check-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(report,ensure_ascii=True,indent=2)); return 0 if report["status"]=="PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
