import { useState } from "react";
import { createRoot } from "react-dom/client";
import { WorkspaceSourceComposer } from "@/components/workspace/WorkspaceSourceComposer";
import "../../../src/index.css";
const mode = new URLSearchParams(location.search).get("mode") ?? "file";
const upload = { upload_id: "upload-a", original_name: "原始附件.csv", media_type: "text/csv", size_bytes: 24, sha256: "a".repeat(64) };
const snapshot: any = { snapshot_id: "snapshot-a", attempt_id: "attempt-a", created_at: "2026-09-09T00:00:00Z", valid_page_count: 1, failed_page_count: 0, allowed_scope: { kind: "exact_url", normalized_url: "https://example.invalid/source" }, coverage: { status: "scope_complete" }, failures: [], artifacts: [{ artifact_id: "artifact-a", content_sha256: "b".repeat(64), title: "网页证据", final_url: "https://example.invalid/source", text_preview: "工程合成证据" }] };
function Fixture() {
  const [result, setResult] = useState<any>(null);
  return <main className="mx-auto max-w-3xl p-4"><h1 className="mb-4 text-lg font-semibold">任务模板工程合成验证</h1><WorkspaceSourceComposer key="owner-a" ownerId="owner-a" preserveContext={new URLSearchParams(location.search).has("revision")} unified initialUploads={mode === "web" ? [] : [upload]} initialSources={mode === "file" ? [] : [snapshot]} initialPrompt="按证据汇总费用，不猜测缺失值" initialFormats={["json"]} draft={{ prompt: "按证据汇总费用，不猜测缺失值", formats: ["json"], connectionId: null, connectionModel: null, localModel: "fixture-model" }} allowPiRuntime allowLocalPiRuntime modelOptions={[{ provider: "local", model: "fixture-model", label: "工程模型" }]} defaultModel={{ provider: "local", model: "fixture-model", label: "工程模型" }} onSubmit={async payload => { (window as any).submitted = payload; setResult(payload); }} /><pre aria-label="工程提交结果">{result ? JSON.stringify(result, null, 2) : "尚未提交"}</pre></main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
