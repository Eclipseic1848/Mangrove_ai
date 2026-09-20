"""通过扩展事件接口验证等待预算，不运行 Shell 或模型。"""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_context_gate_blocks_waiting_and_caps_command_timeout():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if not node or not (root / "frontend/node_modules/typescript").is_dir():
        pytest.skip("扩展测试需先安装项目既有前端依赖；纯 Python 环境不运行")
    script = r"""
const fs = require('node:fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync('src/agentic_runtime/assets/mangrove-context-gate.ts', 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const exports = {};
new Function('require', 'exports', compiled)(require, exports);
const handlers = {};
exports.default({on: (name, handler) => handlers[name] = handler});
const assert = require('node:assert/strict');
(async () => {
  for (const command of ['sleep 300; echo retry', 'sleep 240; sleep 120', 'python -c "import time; time.sleep(180)"']) {
    const result = await handlers.tool_call({toolName: 'bash', input: {command}});
    assert.equal(result.block, true, command);
  }
  const input = {command: 'python process.py', timeout: 1800};
  assert.equal(await handlers.tool_call({toolName: 'bash', input}), undefined);
  assert.equal(input.timeout, 300);
  assert.equal(await handlers.tool_call({toolName: 'read', input: {path: 'sleep.txt'}}), undefined);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", script], cwd=root, capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
