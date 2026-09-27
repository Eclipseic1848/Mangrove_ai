"""采集进度跨进程恢复，不用合成成功冒充真实平台采集。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def test_process_death_preserves_completed_unknown_and_credential_boundary(tmp_path):
    repo = Path(__file__).resolve().parents[1]

    def run(phase):
        return subprocess.run([sys.executable, '-X', 'utf8', '-m',
            'tests.fixtures.collection_resume_process', str(tmp_path), phase],
            cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=90)

    crashed = run('crash')
    assert crashed.returncode == 23, crashed.stderr
    progress = next((tmp_path / 'execution').rglob('progress.json'))
    saved = json.loads(progress.read_text(encoding='utf-8'))
    assert saved['candidates'][0]['status'] == 'selected'
    assert saved['candidates'][1]['_stage'] == 'extract_inflight'
    artifacts = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path / 'artifacts').rglob('*') if p.is_file()}
    resumed = run('resume')
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads((tmp_path / 'resume.json').read_text(encoding='utf-8'))
    assert [n['status'] for n in result['evidence_collection']['candidates']] == ['selected', 'review_required', 'selected']
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in artifacts.items())
    calls = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text(encoding='utf-8').splitlines()]
    for operation in [['discover'], ['0', 'extract'], ['1', 'extract'], ['2', 'extract']]:
        assert calls.count(operation) == 1
    before = progress.read_bytes()
    rotated = run('rotate')
    assert rotated.returncode == 0, rotated.stderr
    assert json.loads((tmp_path / 'rotate.json').read_text(encoding='utf-8')) == {'rotation_rejected': True}
    assert progress.read_bytes() == before
    assert len((tmp_path / 'calls.jsonl').read_text(encoding='utf-8').splitlines()) == len(calls)
