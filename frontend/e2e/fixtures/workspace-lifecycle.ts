import type { Page, Route } from "@playwright/test";

const guidance = {
  schema_version: "1",
  onboarding: [
    { title: "添加资料", description: "拖入表格或文档。" },
    { title: "说明目标", description: "描述筛选、汇总和交付要求。" },
    { title: "检查交付", description: "核对来源后下载正式文件。" },
  ],
  examples: [
    {
      id: "table-filter",
      category: "表格",
      title: "筛选并交付",
      description: "筛选目标人员并输出工作量明细。",
      required_inputs: "CSV 或 XLSX",
      prompt: "筛选张三的全部工作量，输出 XLSX",
      output_formats: ["xlsx"],
    },
    {
      id: "document-extract",
      category: "文档",
      title: "证据化抽取",
      description: "提取合同付款条款并保留原文证据。",
      required_inputs: "PDF 或 DOCX",
      prompt: "提取付款节点和比例，输出 DOCX",
      output_formats: ["docx"],
    },
  ],
};

function taskSourceFixture(route: Route, values: Record<string, unknown> = { 姓名: "张三", 工作量: 5 }) {
  const url = new URL(route.request().url());
  const parts = url.pathname.split("/");
  const artifactId = parts.at(-2)!;
  const tableRef = `artifact://${artifactId}/table/0`;
  return {
    task_id: parts.at(-4), revision: Number(url.searchParams.get("revision") || 1), artifact_id: artifactId,
    upload_id: artifactId, sha256: "0".repeat(64), original_name: `${artifactId}.csv`, media_type: "text/csv",
    content_url: `/api/data-sources/uploads/${artifactId}/content`, representation: { kind: "source", parser_or_inspector_version: "fixture" },
    kind: "table", tables: [{ table_ref: tableRef, table_index: 0, name: "数据", header_row: 1 }], selected_table_ref: tableRef,
    columns: Object.keys(values), rows: [{ row_number: Number(url.searchParams.get("row_number") || 2), values }],
    offset: 0, limit: 100, total: 1, is_complete: true, location_status: url.searchParams.has("row_number") ? "located" : "not_requested",
  };
}

export async function mockWorkspace(
  page: Page,
  theme: "light" | "dark" = "light",
  role = "admin",
) {
  await page.addInitScript(({ selectedTheme }) => {
    localStorage.setItem("mangrove_token", "e2e-token");
    localStorage.setItem("mangrove_theme", selectedTheme);
  }, { selectedTheme: theme });
  await page.route("**/api/auth/me", (route) => route.fulfill({
    json: {
      user_id: "u1",
      username: "tester",
      display_name: "测试员",
      role,
    },
  }));
  await page.route("**/api/semantic-workspace/tasks?*", (route) =>
    route.fulfill({ json: [] }));
  await page.route("**/api/semantic-workspace/guidance", (route) =>
    route.fulfill({ json: guidance }));
  await page.route("**/api/semantic-workspace/storage", (route) =>
    route.fulfill({
      json: {
        task_count: 0,
        recycle_bin_count: 0,
        upload_bytes: 0,
        delivery_bytes: 0,
        total_bytes: 0,
        retention: "回收站保留 30 天",
        calculated_at: "2026-07-27T00:00:00",
      },
    }));
  await page.route("**/api/model-connections/presets", route => route.fulfill({ json: { presets: [] } }));
  await page.route("**/api/semantic-workspace/tasks/*/stream*", route => route.fulfill({ contentType: "text/event-stream", body: "" }));
  await page.route("**/api/settings/onboarding/model-connections", route => route.fulfill({ json: { completed: true } }));
  await page.route("**/api/semantic-workspace/tasks/*/turns", route => route.fulfill({ json: { turns: [], results: [], proposals: [] } }));
  await page.route("**/api/models", (route) => route.fulfill({
    json: {
      options: [
        {
          provider: "local",
          model: "Qwen3.6-35B-A3B",
          label: "本地模型 · Qwen3.6-35B-A3B",
        },
        {
          provider: "deepseek",
          model: "deepseek-chat",
          label: "DeepSeek · deepseek-chat",
        },
      ],
      default: {
        provider: "local",
        model: "Qwen3.6-35B-A3B",
        label: "本地模型 · Qwen3.6-35B-A3B",
      },
      pi_runtime_enabled: true,
      pi_capability_host_enabled: true,
    },
  }));
  await page.route("**/api/semantic-workspace/capabilities", (route) =>
    route.fulfill({ json: { enabled: true, items: [] } }));
  await page.route("**/api/semantic-workspace/context-options?*", (route) =>
    route.fulfill({
      json: {
        templates: [{
          template_id: "public-company-summary",
          version: 1,
          title: "公开公司摘要",
          source: "owner_created",
          purpose: "web_research",
          goal_contract_draft: "按公司提取名称和来源证据",
          delivery_spec_draft: { formats: ["markdown"] },
          method_draft: "逐页读取并按公司去重",
          summary_sha256: `sha256:${"1".repeat(64)}`,
        }],
        memories: [{
          memory_id: 7,
          purpose: "web_research",
          source: "user_entered",
          summary: "公司名使用官网全称",
          summary_sha256: `sha256:${"2".repeat(64)}`,
        }],
      },
    }));
  await page.route("**/api/semantic-workspace/context-preview", async (route) => {
    const payload = route.request().postDataJSON() as {
      selection: {
        template?: { template_id: string; version: number } | null;
        memories: Array<{ memory_id: number }>;
      };
    };
    const hasTemplate = Boolean(payload.selection.template);
    const hasMemory = payload.selection.memories.some((item) => item.memory_id === 7);
    await route.fulfill({
      json: {
        purpose: "web_research",
        template: hasTemplate ? {
          template_id: "public-company-summary",
          version: 1,
          title: "公开公司摘要",
          source: "owner_created",
          purpose: "web_research",
          goal_contract_draft: "按公司提取名称和来源证据",
          delivery_spec_draft: { formats: ["markdown"] },
          method_draft: "逐页读取并按公司去重",
          summary_sha256: `sha256:${"1".repeat(64)}`,
        } : null,
        memories: hasMemory ? [{
          memory_id: 7,
          purpose: "web_research",
          source: "user_entered",
          summary: "公司名使用官网全称",
          summary_sha256: `sha256:${"2".repeat(64)}`,
        }] : [],
        proposed_changes: {
          goal_contract: hasTemplate ? "按公司提取名称和来源证据" : null,
          delivery_spec: hasTemplate ? { formats: ["markdown"] } : {},
          method: hasTemplate ? "逐页读取并按公司去重" : null,
        },
        preview_sha256: `sha256:${"3".repeat(64)}`,
      },
    });
  });
  await page.route("**/api/model-connections", (route) =>
    route.fulfill({ json: { items: [] } }));
  await page.route("**/api/model-connections/preferences/default", (route) =>
    route.fulfill({ json: { preference: null } }));
  await page.route("**/api/data-sources/uploads", (route) => route.fulfill({
    json: {
      upload_id: "upload-e2e",
      original_name: "workload.csv",
      media_type: "text/csv",
      size_bytes: 64,
      sha256: "0".repeat(64),
    },
  }));
  await page.route("**/api/data-tasks/preview", (route) => route.fulfill({
    json: {
      schema: {
        fields: [
          { name: "姓名", dtype: "string", nullable: false },
          { name: "工作量", dtype: "integer", nullable: false },
        ],
      },
      sample: [{ 姓名: "张三", 工作量: 5 }],
      estimated_records: 1,
    },
  }));
  await page.route("**/api/semantic-workspace/tasks/*/sources/*/preview?*", route => route.fulfill({ json: taskSourceFixture(route) }));
}

type WorkspaceFixture = {
  task_id: string;
  objective_text: string;
  output_formats: string[];
  plan_id: string | null;
  logical_revision: number | null;
  binding_revision: number | null;
  run_id: string | null;
  status: string;
  summary: string;
  created_at: string;
  updated_at: string;
  [key: string]: unknown;
};

function workspaceTask(
  taskId: string,
  status: string,
  title: string,
): WorkspaceFixture {
  return {
    task_id: taskId,
    title,
    objective_text: "只筛选张三并输出 XLSX",
    upload_ids: [],
    output_formats: ["xlsx"],
    provider: "local",
    model: "Qwen3.6-35B-A3B",
    runtime_version: "legacy",
    external_api_confirmed: false,
    status,
    active_revision: 1,
    current_revision: 1,
    viewing_revision: 1,
    plan_id: null,
    logical_revision: null,
    binding_revision: null,
    run_id: null,
    summary: "已理解任务要求",
    error: null,
    question: null,
    cancel_requested: false,
    deleted_at: null,
    purge_after: null,
    created_at: "2026-07-27T00:00:00Z",
    updated_at: "2026-07-27T00:00:01Z",
  };
}

function workspaceDetail(
  task: WorkspaceFixture,
  extra: Record<string, unknown> = {},
) {
  return {
    ...task,
    revisions: [{
      task_id: task.task_id,
      revision: 1,
      objective_text: task.objective_text,
      output_formats: task.output_formats,
      plan_id: task.plan_id,
      logical_revision: task.logical_revision,
      binding_revision: task.binding_revision,
      run_id: task.run_id,
      status: task.status,
      summary: task.summary,
      change_summary: "",
      created_at: task.created_at,
      updated_at: task.updated_at,
    }],
    events: [],
    uploads: [],
    plan: null,
    run: null,
    attempts: [],
    harness_events: [],
    delivery: null,
    ...extra,
  };
}

// 不同版本、账号使用可辨认的正文与交付，避免只检查版本标签。
export function previewIdentityFixture(owner: string, revision: number) {
  const identity = `${owner}-V${revision}`;
  const task = {
    ...workspaceTask("identity-task", "completed", `${owner}的结果任务`),
    current_revision: 2, active_revision: 2, viewing_revision: revision,
    upload_ids: [`V${revision}-upload`],
  };
  const detail = workspaceDetail(task, {
    revisions: [1, 2].map((value) => ({ ...task, revision: value })),
    uploads: [{ upload_id: `V${revision}-upload`, original_name: `${identity}-原件.csv`,
      media_type: "text/csv", size_bytes: 64, sha256: "0".repeat(64) }],
    delivery: {
      delivery_id: `${identity}-delivery`, run_id: `${identity}-run`, plan_id: `${identity}-plan`,
      status: "published", requested_formats: ["xlsx"], created_at: task.created_at,
      outputs: [{ output_id: `${identity}-output`, format: "xlsx", filename: `${identity}.xlsx`,
        media_type: "application/octet-stream", sha256: "1".repeat(64), size_bytes: 64,
        qa: { openable: true, checks: [`${identity}-QA`], warnings: revision === 1 ? ["旧版警告"] : [] },
        download_url: `/api/semantic-delivery/outputs/${identity}-output` }],
    },
  });
  const preview = {
    kind: "table", columns: ["结果"], total: 201, offset: 0, limit: 100,
    rows: [{ 结果: `${identity}-正文`, __lineage: [{ artifact_id: `V${revision}-upload`,
      row_number: revision + 1, values: { 结果: `${identity}-来源证据` } }] }],
  };
  return { task, detail, preview };
}

export function responseBarrier() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

export async function loginAsB(page: Page) {
  await page.getByPlaceholder("至少 2 位").fill("owner-b");
  await page.getByPlaceholder("至少 6 位").fill("synthetic-password");
  await page.locator('button[type="submit"]').click();
}
