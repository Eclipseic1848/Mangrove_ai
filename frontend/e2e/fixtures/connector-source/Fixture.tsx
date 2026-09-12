import { useState } from "react";
import { createRoot } from "react-dom/client";
import { WorkspaceSourceComposer } from "@/components/workspace/WorkspaceSourceComposer";
import { createWorkspaceTask } from "@/lib/semanticWorkspaceApi";
import "../../../src/index.css";
function Fixture() {
    const [owner, setOwner] = useState("owner-a");
    const [created, setCreated] = useState("");
    return <main className="mx-auto max-w-3xl p-4"><h1>连接资料任务合成验证</h1><button onClick={() => setOwner("owner-b")}>切换合成Owner</button><WorkspaceSourceComposer key={owner} ownerId={owner} unified initialUploads={[{ upload_id: "original-file", original_name: "原附件.csv", media_type: "text/csv", size_bytes: 20, sha256: "a".repeat(64) }]} initialPrompt="按证据整理连接记录" initialFormats={["json"]} draft={{ prompt: "按证据整理连接记录", formats: ["json"], connectionId: null, connectionModel: null, localModel: "fixture" }} allowPiRuntime allowLocalPiRuntime modelOptions={[{ provider: "local", model: "fixture", label: "合成模型" }]} defaultModel={{ provider: "local", model: "fixture", label: "合成模型" }} onSubmit={async (payload) => {
            const task = await createWorkspaceTask({ title: "合成连接任务", objective_text: payload.prompt, upload_ids: payload.uploads.map(item => item.upload_id), source_snapshot_ids: payload.sourceSnapshotIds, output_formats: payload.formats, ...(payload.taskContext ?? {}), ...(payload.sourceGoal ?? {}) }, "synthetic-task-key");
            setCreated(task.task_id);
        }}/><p role="status">{created ? `任务已创建：${created}` : "尚未启动"}</p></main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
