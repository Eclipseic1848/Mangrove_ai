import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

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

async function mockWorkspace(
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
function previewIdentityFixture(owner: string, revision: number) {
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

function responseBarrier() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

async function loginAsB(page: Page) {
  await page.getByPlaceholder("至少 2 位").fill("owner-b");
  await page.getByPlaceholder("至少 6 位").fill("synthetic-password");
  await page.locator('button[type="submit"]').click();
}

test.describe("结果缓存身份隔离", () => {
  test("修订切换加载与迟到正文不混用来源 QA 和下载", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    const v1 = previewIdentityFixture("A", 1);
    const v2 = previewIdentityFixture("A", 2);
    const delayed = responseBarrier();
    const requested = responseBarrier();
    const delivered = responseBarrier();
    await page.route("**/api/semantic-workspace/tasks?*", (route) => route.fulfill({ json: [v2.task] }));
    await page.route(/\/api\/semantic-workspace\/tasks\/identity-task(?:\?.*)?$/, (route) =>
      route.fulfill({ json: new URL(route.request().url()).searchParams.get("revision") === "1" ? v1.detail : v2.detail }));
    await page.route("**/identity-task/preview?*", async (route) => {
      const old = new URL(route.request().url()).searchParams.get("revision") === "1";
      if (old) { requested.release(); await delayed.promise; }
      await route.fulfill({ json: old ? v1.preview : v2.preview });
      if (old) delivered.release();
    });
    const downloads: string[] = [];
    await page.route("**/api/semantic-delivery/outputs/*", (route) => {
      downloads.push(route.request().url());
      return route.fulfill({ contentType: "application/octet-stream", body: "synthetic-output" });
    });
    await page.route("**/identity-task/bundle?*", (route) => {
      downloads.push(route.request().url());
      return route.fulfill({ contentType: "application/zip", body: "synthetic-zip" });
    });
    await page.goto("/data-prep");
    await page.getByRole("button", { name: /A的结果任务/ }).click();
    await expect(page.getByText("A-V2-正文", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "查看来源", exact: true }).click();
    await expect(page.getByText("A-V2-来源证据", { exact: true })).toBeVisible();
    await page.getByLabel("结果版本").selectOption("1");
    await page.getByRole("button", { name: "查看结果", exact: true }).click();
    await expect(page.getByRole("button", { name: "下载 A-V1.xlsx", exact: true })).toBeVisible();
    await expect(page.getByText("A-V2-正文", { exact: true })).toHaveCount(0);
    await expect(page.getByText("A-V2-来源证据", { exact: true })).toHaveCount(0);
    await expect(page.getByText("正在读取结果预览", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("revision-loading.png") });
    await requested.promise;
    await page.getByLabel("结果版本").selectOption("2");
    await expect(page.getByText("A-V2-正文", { exact: true })).toBeVisible();
    delayed.release();
    await delivered.promise;
    await expect(page.getByText("A-V1-正文", { exact: true })).toHaveCount(0);
    await expect(page.getByText("可交付", { exact: true })).toBeVisible();
    for (const revision of [2, 1]) {
      await page.getByLabel("结果版本").selectOption(String(revision));
      await page.getByRole("button", { name: "查看结果", exact: true }).click();
      await expect(page.getByText(`A-V${revision}-正文`, { exact: true })).toBeVisible();
      await expect(page.getByText(revision === 1 ? "有警告" : "可交付", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "查看来源", exact: true }).click();
      await expect(page.getByText(`A-V${revision}-来源证据`, { exact: true })).toBeVisible();
      if (revision === 1) await page.screenshot({ path: testInfo.outputPath("revision-source.png") });
      await page.getByRole("button", { name: "原文件预览", exact: true }).click();
      await page.getByRole("button", { name: "查看结果", exact: true }).click();
      const file = page.waitForEvent("download");
      await page.getByRole("button", { name: `下载 A-V${revision}.xlsx`, exact: true }).click();
      expect((await file).suggestedFilename()).toBe(`A-V${revision}.xlsx`);
      expect(downloads.at(-1)).toContain(`/A-V${revision}-output`);
      await page.getByRole("checkbox", { name: "ZIP 包含来源资料" }).setChecked(revision === 1);
      const zip = page.waitForEvent("download");
      await page.getByRole("button", { name: "下载全部", exact: true }).click();
      await zip;
      expect(new URL(downloads.at(-1)!).searchParams.get("revision")).toBe(String(revision));
      expect(new URL(downloads.at(-1)!).searchParams.get("include_sources")).toBe(String(revision === 1));
    }
  });

  test("修订切换重置筛选排序页码", async ({ page }) => {
    await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    const v2 = previewIdentityFixture("A", 2);
    const queries: URL[] = [];
    await page.route("**/api/semantic-workspace/tasks?*", (route) => route.fulfill({ json: [v2.task] }));
    await page.route(/\/api\/semantic-workspace\/tasks\/identity-task(?:\?.*)?$/, (route) =>
      route.fulfill({ json: previewIdentityFixture("A", Number(new URL(route.request().url()).searchParams.get("revision") || 2)).detail }));
    await page.route("**/identity-task/preview?*", (route) => {
      const url = new URL(route.request().url()); queries.push(url);
      return route.fulfill({ json: { ...v2.preview, offset: Number(url.searchParams.get("offset")) } });
    });
    await page.goto("/data-prep");
    await page.getByRole("button", { name: /A的结果任务/ }).click();
    await page.getByPlaceholder("在全部结果中搜索").fill("旧版条件");
    await page.getByRole("button", { name: "搜索", exact: true }).click();
    await page.getByRole("button", { name: "结果", exact: true }).click();
    await page.locator("button:has(svg.lucide-chevron-right)").click();
    await expect.poll(() => queries.at(-1)?.searchParams.get("offset")).toBe("100");
    await page.getByLabel("结果版本").selectOption("1");
    await page.getByRole("button", { name: "查看结果", exact: true }).click();
    await expect(page.getByRole("button", { name: "下载 A-V1.xlsx", exact: true })).toBeVisible();
    await expect(page.getByPlaceholder("在全部结果中搜索")).toHaveValue("");
    await expect.poll(() => queries.at(-1)?.searchParams.get("revision")).toBe("1");
    expect(queries.at(-1)?.searchParams.get("offset")).toBe("0");
    expect(queries.at(-1)?.searchParams.get("search") || "").toBe("");
    expect(queries.at(-1)?.searchParams.get("sort_by")).toBeNull();
  });

  test("同浏览器换账号等待列表详情正文时不显示前账号缓存", async ({ page }) => {
    await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    let owner = "A";
    const list = responseBarrier(), detail = responseBarrier(), body = responseBarrier(), source = responseBarrier();
    const listRequested = responseBarrier(), detailRequested = responseBarrier(), bodyRequested = responseBarrier(), sourceRequested = responseBarrier();
    await page.route("**/api/semantic-workspace/tasks/*/sources/*/preview?*", async (route) => {
      const requestedOwner = owner;
      if (requestedOwner === "B") { sourceRequested.release(); await source.promise; }
      await route.fulfill({ json: taskSourceFixture(route, { 原件: `${requestedOwner}-原件私有内容` }) });
    });
    await page.route("**/api/auth/login", (route) => {
      owner = "B";
      return route.fulfill({ json: { user_id: "owner-b", username: "owner-b", display_name: "账号乙", role: "admin" } });
    });
    await page.route("**/api/auth/logout", (route) => route.fulfill({ json: { ok: true } }));
    await page.route("**/api/semantic-workspace/tasks?*", async (route) => {
      const requestedOwner = owner;
      if (requestedOwner === "B") { listRequested.release(); await list.promise; }
      await route.fulfill({ json: [previewIdentityFixture(requestedOwner, 2).task] });
    });
    await page.route(/\/api\/semantic-workspace\/tasks\/identity-task(?:\?.*)?$/, async (route) => {
      const requestedOwner = owner;
      if (requestedOwner === "B") { detailRequested.release(); await detail.promise; }
      await route.fulfill({ json: previewIdentityFixture(requestedOwner, 2).detail });
    });
    await page.route("**/identity-task/preview?*", async (route) => {
      const requestedOwner = owner;
      if (requestedOwner === "B") { bodyRequested.release(); await body.promise; }
      await route.fulfill({ json: previewIdentityFixture(requestedOwner, 2).preview });
    });
    await page.goto("/data-prep");
    await page.getByRole("button", { name: /A的结果任务/ }).click();
    await expect(page.getByText("A-V2-正文", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "查看来源", exact: true }).click();
    await expect(page.getByText("A-V2-来源证据", { exact: true })).toBeVisible();
    await expect(page.getByText("A-原件私有内容", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "打开导航", exact: true }).click();
    await page.getByTitle("退出登录").click();
    await loginAsB(page);
    await page.getByRole("button", { name: "打开导航", exact: true }).click();
    await page.getByRole("link", { name: "任务工作台", exact: true }).click();
    await listRequested.promise;
    await expect.soft(page.getByRole("button", { name: /A的结果任务/ })).toHaveCount(0);
    list.release();
    await page.getByRole("button", { name: /B的结果任务/ }).click();
    await detailRequested.promise;
    await expect.soft(page.getByText("A-V2.xlsx", { exact: true })).toHaveCount(0);
    await expect.soft(page.getByText("A-V2-正文", { exact: true })).toHaveCount(0);
    detail.release();
    await bodyRequested.promise;
    await expect.soft(page.getByText("A-V2-正文", { exact: true })).toHaveCount(0);
    await expect.soft(page.getByText("A-V2-来源证据", { exact: true })).toHaveCount(0);
    body.release();
    await expect(page.getByText("B-V2-正文", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "查看来源", exact: true }).click();
    await sourceRequested.promise;
    await expect.soft(page.getByText("A-原件私有内容", { exact: true })).toHaveCount(0);
    source.release();
    await expect(page.getByText("B-原件私有内容", { exact: true })).toBeVisible();
  });

  test("缓存命中的修订切换首帧也重置共享多来源选择", async ({ page }) => {
    await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    const uploads = ["first", "second"].map((id) => ({
      upload_id: id, original_name: `${id}.csv`, media_type: "text/csv",
      size_bytes: 64, sha256: "0".repeat(64),
    }));
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [previewIdentityFixture("A", 2).task] }));
    await page.route(/\/api\/semantic-workspace\/tasks\/identity-task(?:\?.*)?$/, (route) => {
      const revision = Number(new URL(route.request().url()).searchParams.get("revision") || 2);
      return route.fulfill({ json: { ...previewIdentityFixture("A", revision).detail,
        uploads, upload_ids: ["first", "second"] } });
    });
    await page.route("**/identity-task/preview?*", (route) => {
      const revision = Number(new URL(route.request().url()).searchParams.get("revision") || 2);
      return route.fulfill({ json: previewIdentityFixture("A", revision).preview });
    });
    await page.route("**/api/semantic-workspace/tasks/*/sources/*/preview?*", (route) => route.fulfill({ json:
      taskSourceFixture(route, { 原件: `${new URL(route.request().url()).pathname.split("/").at(-2)}-共享原件正文` }),
    }));
    await page.goto("/data-prep");
    await page.getByRole("button", { name: /A的结果任务/ }).click();
    await page.getByRole("button", { name: "原文件预览", exact: true }).click();
    await expect(page.getByText("first-共享原件正文", { exact: true })).toBeVisible();
    await page.getByLabel("结果版本").selectOption("1");
    await page.getByRole("button", { name: "查看结果", exact: true }).click();
    await expect(page.getByRole("button", { name: "下载 A-V1.xlsx", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "原文件预览", exact: true }).click();
    await page.getByLabel("预览文件").selectOption("second");
    await expect(page.getByText("second-共享原件正文", { exact: true })).toBeVisible();
    await page.evaluate(async () => {
      const seen: string[] = [];
      const observer = new MutationObserver((records) => {
        for (const record of records) for (const node of record.addedNodes) {
          const inSource = record.target instanceof Element && record.target.closest('[data-testid="source"]');
          const containsSource = node instanceof Element && (node.matches('[data-testid="source"]') || node.querySelector('[data-testid="source"]'));
          if ((inSource || containsSource) && node.textContent?.includes("second-共享原件正文")) seen.push(node.textContent);
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
      // 校验同一轮挂载又删除的节点仍被记录，避免漏掉 effect 前的短暂内容。
      const probe = document.createElement("span");
      probe.textContent = "second-共享原件正文";
      document.querySelector('[data-testid="source"]')!.append(probe);
      probe.remove();
      await Promise.resolve();
      if (seen.length !== 1) throw new Error("来源瞬时 DOM 观察器未记录阳性对照");
      seen.length = 0;
      Object.assign(window, { sourceSwitchEvidence: { seen, observer } });
    });
    await page.getByLabel("结果版本").selectOption("2");
    await expect(page.getByText("first-共享原件正文", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "查看结果", exact: true }).click();
    await expect(page.getByRole("button", { name: "下载 A-V2.xlsx", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "原文件预览", exact: true }).click();
    await expect(page.getByText("first-共享原件正文", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => {
      const evidence = (window as unknown as { sourceSwitchEvidence: { seen: string[]; observer: MutationObserver } }).sourceSwitchEvidence;
      evidence.observer.disconnect();
      return evidence.seen;
    })).toEqual([]);
  });

  for (const status of [200, 401]) {
  test(`启动鉴权迟到 ${status} 不得覆盖已显式登录的新账号`, async ({ page }) => {
    await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    const started = responseBarrier(), release = responseBarrier();
    await page.route("**/api/auth/me", async (route) => {
      started.release(); await release.promise;
      await route.fulfill({ status, json: status === 401 ? { detail: "旧会话已过期" } : { user_id: "owner-a", username: "owner-a", display_name: "账号甲", role: "admin" } });
    });
    await page.route("**/api/auth/login", (route) => route.fulfill({ json: {
      user_id: "owner-b", username: "owner-b", display_name: "账号乙", role: "admin",
    } }));
    await page.goto("/login");
    await started.promise;
    await loginAsB(page);
    await expect(page.getByText("账号乙", { exact: true })).toBeVisible();
    const restored = page.waitForResponse("**/api/auth/me");
    release.release(); await restored;
    await expect(page.getByText("账号乙", { exact: true })).toBeVisible();
    await expect(page.getByText("账号甲", { exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem("mangrove_token"))).toBeNull();
  });
  }
});

test("会话过期重新登录后恢复原任务与结果修订", async ({ page }) => {
  let expired = false;
  const user = { user_id: "u1", username: "tester", display_name: "测试员", role: "admin" };
  await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
  await mockWorkspace(page);
  const sessionResponse = (route: Route) => expired
    ? route.fulfill({ status: 401, headers: { "X-Mangrove-Auth": "session-invalid" }, json: { detail: "登录已失效，请重新登录" } })
    : route.fulfill({ json: user });
  await page.route("**/api/auth/me", sessionResponse);
  await page.route("**/api/auth/login", (route) => {
    expired = false;
    return route.fulfill({ json: user });
  });
  await page.route("**/api/semantic-workspace/tasks?*", (route) => expired
    ? sessionResponse(route)
    : route.fulfill({ json: [previewIdentityFixture("A", 2).task] }));
  await page.route(/\/api\/semantic-workspace\/tasks\/identity-task(?:\?.*)?$/, (route) => {
    const revision = Number(new URL(route.request().url()).searchParams.get("revision") || 2);
    return route.fulfill({ json: previewIdentityFixture("A", revision).detail });
  });
  await page.route("**/identity-task/preview?*", (route) => {
    const revision = Number(new URL(route.request().url()).searchParams.get("revision") || 2);
    return route.fulfill({ json: previewIdentityFixture("A", revision).preview });
  });
  await page.goto("/data-prep");
  await page.getByRole("button", { name: /A的结果任务/ }).click();
  await expect(page.getByText("A-V2-正文", { exact: true })).toBeVisible();
  await page.getByLabel("结果版本").selectOption("1");
  await expect(page.getByText("A-V1-正文", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/task=identity-task.*revision=1/);
  expired = true;
  await expect(page.getByText("登录已失效，请重新登录", { exact: true })).toBeVisible();
  await page.getByLabel("用户名", { exact: true }).fill("tester");
  await page.getByLabel("密码", { exact: true }).fill("synthetic-password");
  await page.getByLabel("密码", { exact: true }).press("Enter");
  await expect(page).toHaveURL(/\/data-prep\?task=identity-task.*revision=1/);
  await expect(page.getByText("A-V1-正文", { exact: true })).toBeVisible();
  await expect(page.getByLabel("结果版本")).toHaveValue("1");
});

function searchAttempt(key: string, count = 9, hard = false) {
  const base = sourceAttempt(count ? "succeeded" : "failed");
  const scope = {
    kind: "public_search", normalized_url: "", site: "", query: "电池回收研究",
    time_range: "week", domains: ["example.com"], page_limit: 10,
    completeness: { mode: hard ? "hard_min_pages" : "exploratory", required_valid_pages: hard ? 10 : null },
  };
  const artifacts = Array.from({ length: count }, (_, index) => ({
    ...sourceAttempt("succeeded").snapshot!.artifacts[0], artifact_id: `search-artifact-${index}`,
    request_url: `https://example.com/article-${index}`, final_url: `https://example.com/article-${index}`,
    title: `研究原文 ${index + 1}`, text_preview: `第 ${index + 1} 页已保存摘要`,
  }));
  const report = {
    provider: "fixture-search", query: scope.query, time_range: scope.time_range, domains: scope.domains,
    candidates: count ? [
      ...artifacts.map(item => ({ url: item.final_url, title: item.title, status: "read" })),
      ...(count < 10 ? [{ url: "https://example.com/login", title: "登录后才可读取", status: "failed", error_code: "site_refused", message: "站点拒绝读取" },
        { url: "https://example.com/candidate", title: "仅搜索候选", status: "discovered" }] : []),
    ] : [],
    discovered_count: count ? (count < 10 ? count + 2 : count) : 0,
    read_count: count, failed_count: count && count < 10 ? 1 : 0, requested_count: 10,
    status: count === 10 ? "complete" : count ? "partial" : "no_results",
  };
  return {
    ...base, attempt_id: "search-attempt", idempotency_key: key, request_url: "", normalized_url: "", allowed_scope: scope,
    error_code: count ? null : "search_no_results", error_message: count ? null : "没有匹配的公开链接", search_report: report,
    snapshot: count ? { ...sourceAttempt("succeeded").snapshot!, attempt_id: "search-attempt", allowed_scope: scope,
      valid_page_count: count, failed_page_count: report.failed_count, artifacts,
      coverage: { status: hard ? "hard_insufficient" : "coverage_unknown", limit_reached: true,
        attempted_page_count: 10, required_valid_pages: hard ? 10 : null, search_report: report },
    } : null,
  };
}

test.describe("#134 公开搜索", () => {
  test("仅主题显式联网，9条正文与候选分开并沿默认模型启动", async ({ page }, testInfo) => {
    await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    const requests: Record<string, unknown>[] = [];
    let createdPayload: Record<string, unknown> | null = null;
    await page.route("**/api/semantic-workspace/source-acquisitions", route => {
      requests.push(route.request().postDataJSON());
      return route.fulfill({ json: searchAttempt(route.request().headers()["idempotency-key"]) });
    });
    const task = workspaceTask("query-task", "queued", "电池回收研究");
    await page.route("**/api/semantic-workspace/tasks", route => {
      createdPayload = route.request().postDataJSON();
      return route.fulfill({ json: task });
    });
    await page.route("**/api/semantic-workspace/tasks/query-task", route => route.fulfill({ json: workspaceDetail(task, {
      web_source: { runtime_binding: { model: "Qwen3.6-35B-A3B" }, snapshot: searchAttempt("frozen").snapshot },
      messages: [{ message_id: "query-answer", version: 1, task_id: "query-task", revision: 1, run_id: null,
        turn_id: null, role: "assistant", kind: "answer", content: "已根据九篇实际读取的研究整理结论，另有一页读取失败。", status: "completed", created_at: "2026-09-08T00:02:00Z" }],
    }) }));
    await page.route("**/api/semantic-workspace/tasks/query-task/sources/search-artifact-0/preview?*", route => route.fulfill({ json: {
      task_id: "query-task", revision: 1, artifact_id: "search-artifact-0", upload_id: null, sha256: "a".repeat(64),
      original_name: "研究原文 1", media_type: "text/html", content_url: null, kind: "web", text_preview: "第 1 页已保存摘要",
      representation: { kind: "source", parser_or_inspector_version: "fixture" }, is_complete: false, truncated: true,
    } }));
    await page.route("**/api/semantic-workspace/tasks/query-task/source-bundle?*", route => {
      expect(route.request().headers()["x-mangrove-owner"]).toBe("u1");
      expect(new URL(route.request().url()).searchParams.get("revision")).toBe("1");
      return route.fulfill({ contentType: "application/zip", body: "synthetic-frozen-original-package" });
    });
    await page.goto("/data-prep");
    await page.getByLabel("任务要求", { exact: true }).fill("电池回收研究");
    await page.getByRole("button", { name: "开始执行", exact: true }).click();
    await expect(page.getByLabel("搜索主题", { exact: true })).toHaveValue("电池回收研究");
    await expect(page.getByLabel("精确网址")).toHaveCount(0);
    expect(requests).toHaveLength(0);
    await page.getByLabel("时间范围", { exact: true }).selectOption("week");
    await page.getByLabel("限定域名（可选）").fill("example.com");
    await expect(page.getByText("以上搜索条件将发送给公开搜索服务", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "搜索并读取", exact: true }).click();
    await expect(page.getByLabel("公开搜索结果")).toContainText("已读取 9 页");
    expect(requests).toEqual([{ url: "", query: "电池回收研究", time_range: "week", domains: ["example.com"],
      purpose: "读取公开网页内容，供当前数据任务分析", allowed_scope: "public_search", page_limit: 10,
      completeness_mode: "exploratory", required_valid_pages: null }]);
    await expect(page.getByLabel("公开搜索结果")).toContainText("仅发现链接，尚未读取");
    await expect(page.getByLabel("公开搜索结果")).toContainText("读取失败");
    await expect(page.getByLabel("选择已读页面").locator("option")).toHaveCount(9);
    await page.getByLabel("选择已读页面").selectOption("search-artifact-8");
    await expect(page.getByRole("article", { name: "网页正文预览" })).toContainText("第 9 页已保存摘要");
    await expect(page.getByRole("article", { name: "网页正文预览" })).toContainText("未展示完整原文");
    await page.screenshot({ path: testInfo.outputPath("search-partial-1440.png"), fullPage: true });
    await page.getByRole("button", { name: "添加到当前任务", exact: true }).click();
    await page.getByRole("button", { name: "检查上下文草案" }).click();
    await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "启动任务", exact: true }).click();
    await expect.poll(() => createdPayload).not.toBeNull();
    expect(createdPayload).toMatchObject({ source_snapshot_ids: ["snapshot-1"], objective_text: "电池回收研究", model: "Qwen3.6-35B-A3B", upload_ids: [] });
    expect(createdPayload).not.toHaveProperty("runtime_version");
    expect(requests).toHaveLength(1);
    await expect(page.getByLabel("Mangrove 回答")).toContainText("九篇实际读取");
    await page.getByRole("button", { name: "原文件预览", exact: true }).click();
    await expect(page.getByLabel("网页摘要预览")).toContainText("第 1 页已保存摘要");
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载完整资料包", exact: true }).click();
    await download;
  });

  for (const scenario of ["zero", "blocked", "failed", "hard-nine", "complete-ten"] as const) {
    test(`${scenario} 不混同缺口、失败与完整读取`, async ({ page }) => {
      await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
      await mockWorkspace(page);
      const empty = ["zero", "blocked", "failed"].includes(scenario);
      await page.route("**/api/semantic-workspace/source-acquisitions", route => {
        const saved = searchAttempt(route.request().headers()["idempotency-key"], empty ? 0 : scenario === "hard-nine" ? 9 : 10, scenario === "hard-nine");
        if (scenario === "blocked" || scenario === "failed") {
          saved.search_report.status = scenario;
          saved.error_code = scenario === "blocked" ? "search_blocked" : "search_timeout";
        }
        return route.fulfill({ json: saved });
      });
      await page.goto("/data-prep");
      await page.getByLabel("任务要求", { exact: true }).fill("电池回收研究");
      await page.getByRole("button", { name: "开始执行", exact: true }).click();
      if (scenario === "hard-nine") {
        await page.getByLabel("结果要求", { exact: true }).selectOption("hard_min_pages");
        await page.getByLabel("至少有效页数", { exact: true }).fill("10");
      }
      await page.getByRole("button", { name: "搜索并读取", exact: true }).click();
      if (empty) {
        await expect(page.getByLabel("公开搜索结果")).toContainText(scenario === "zero" ? "没有找到符合搜索条件" : scenario === "blocked" ? "公开访问被阻止" : "本次搜索或读取失败");
        await expect(page.getByRole("article", { name: "网页正文预览" })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "启动任务", exact: true })).toHaveCount(0);
      } else {
        if (scenario === "hard-nine") {
          await expect(page.getByText("有效页面不足 10 个", { exact: false })).toBeVisible();
          await expect(page.getByLabel("选择已读页面").locator("option")).toHaveCount(9);
          await page.getByRole("button", { name: "添加到当前任务", exact: true }).click();
          await expect(page.getByRole("button", { name: "检查上下文草案" })).toBeDisabled();
          await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeDisabled();
          await expect(page.getByLabel("已选网页资料")).toContainText("其他资料不能抵消该组硬性缺口");
        } else {
          await expect(page.getByLabel("公开搜索结果")).toContainText("不表示覆盖了所有公开网页");
          await page.getByRole("button", { name: "添加到当前任务", exact: true }).click();
          await page.getByRole("button", { name: "检查上下文草案" }).click();
          await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeEnabled();
        }
      }
    });
  }

  test("查询未知响应只重放同一身份，刷新及取消拒绝迟到成功", async ({ page }) => {
    await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    const keys: string[] = [];
    const bodies: unknown[] = [];
    let delayed: Route | undefined;
    let canceled = false;
    const pending = () => ({ ...searchAttempt(keys[0], 0), status: canceled ? "canceled" : "acquiring", search_report: null, error_code: null, error_message: null });
    await page.route("**/api/semantic-workspace/source-acquisitions", route => {
      keys.push(route.request().headers()["idempotency-key"]);
      bodies.push(route.request().postDataJSON());
      if (keys.length === 1) return route.abort("connectionfailed");
      if (keys.length === 2) return route.fulfill({ json: pending() });
      delayed = route;
    });
    await page.route("**/api/semantic-workspace/source-acquisitions/search-attempt", route => route.fulfill({ json: pending() }));
    await page.route("**/api/semantic-workspace/source-acquisitions/search-attempt/cancel", route => { canceled = true; return route.fulfill({ json: pending() }); });
    await page.goto("/data-prep");
    await page.getByLabel("任务要求", { exact: true }).fill("电池回收研究");
    await page.getByRole("button", { name: "开始执行", exact: true }).click();
    await page.getByLabel("时间范围", { exact: true }).selectOption("week");
    await page.getByLabel("限定域名（可选）").fill("example.com");
    await page.getByRole("button", { name: "搜索并读取", exact: true }).click();
    await expect(page.getByRole("button", { name: "取消获取", exact: true })).toBeVisible();
    await expect.poll(() => Boolean(delayed)).toBe(true);
    await page.getByRole("button", { name: "取消获取", exact: true }).click();
    await expect(page.getByText("来源获取已停止", { exact: true })).toBeVisible();
    await delayed!.fulfill({ json: searchAttempt(keys[0], 10) });
    await expect(page.getByText("来源获取已停止", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "启动任务", exact: true })).toHaveCount(0);
    const requestsBeforeReload = keys.length;
    await page.reload();
    await page.getByRole("button", { name: "公开网页", exact: true }).click();
    await expect(page.getByText("来源获取已停止", { exact: true })).toBeVisible();
    expect(keys).toHaveLength(requestsBeforeReload);
    expect(new Set(keys).size).toBe(1);
    for (const body of bodies) expect(body).toEqual(bodies[0]);
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("mangrove_web_source_attempt_u1")!));
    expect(stored).toMatchObject({ query: "电池回收研究", time_range: "week", domains: ["example.com"], scope_kind: "public_search" });
  });

  test("手机暗色键盘与中文输入不自动联网，清空保持焦点", async ({ page }, testInfo) => {
    await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page, "dark");
    await page.setViewportSize({ width: 390, height: 700 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    let requests = 0;
    await page.route("**/api/semantic-workspace/source-acquisitions", route => { requests += 1; return route.fulfill({ json: searchAttempt("key", 0) }); });
    await page.goto("/data-prep");
    await page.getByLabel("任务要求", { exact: true }).fill("电池回收研究");
    await page.getByRole("button", { name: "开始执行", exact: true }).click();
    const query = page.getByLabel("搜索主题", { exact: true });
    await query.focus();
    await query.dispatchEvent("compositionstart");
    await query.press("Enter");
    await query.dispatchEvent("compositionend");
    expect(requests).toBe(0);
    await page.getByRole("button", { name: "清空搜索主题", exact: true }).click();
    await expect(query).toBeFocused();
    await expect(query).toHaveValue("");
    await expect(page.getByRole("button", { name: "搜索并读取", exact: true })).toBeDisabled();
    await query.fill("新的公开主题");
    await page.getByLabel("限定域名（可选）").fill(Array.from({ length: 11 }, (_, i) => `site${i}.com`).join(","));
    await expect(page.getByRole("button", { name: "搜索并读取", exact: true })).toBeDisabled();
    await page.getByLabel("限定域名（可选）").fill("");
    await page.getByLabel("时间范围", { exact: true }).focus();
    await page.getByLabel("时间范围", { exact: true }).selectOption("month");
    const scan = await new AxeBuilder({ page }).analyze();
    expect(scan.violations.filter(item => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.screenshot({ path: testInfo.outputPath("search-dark-390.png"), fullPage: true });
    await page.getByRole("button", { name: "搜索并读取", exact: true }).focus();
    await page.getByRole("button", { name: "搜索并读取", exact: true }).press("Space");
    await expect.poll(() => requests).toBe(1);
  });

  test("刷新成功搜索保留查询缺口语义，使用原快照不重新联网", async ({ page }) => {
    await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    await page.addInitScript(() => localStorage.setItem("mangrove_web_source_attempt_u1", JSON.stringify({
      attempt_id: "search-attempt", idempotency_key: "frozen-query-key", url: "", purpose: "公开研究分析",
      scope_kind: "public_search", query: "电池回收研究", time_range: "week", domains: ["example.com"],
      page_limit: 10, completeness_mode: "exploratory", required_valid_pages: null,
    })));
    let acquisitions = 0;
    let submitted: Record<string, unknown> | null = null;
    const task = workspaceTask("restored-search-task", "queued", "恢复公开研究");
    await page.route("**/api/semantic-workspace/source-acquisitions", route => { acquisitions += 1; return route.abort(); });
    await page.route("**/api/semantic-workspace/source-acquisitions/search-attempt", route => route.fulfill({ json: searchAttempt("frozen-query-key") }));
    await page.route("**/api/semantic-workspace/tasks", route => { submitted = route.request().postDataJSON(); return route.fulfill({ json: task }); });
    await page.route("**/api/semantic-workspace/tasks/restored-search-task", route => route.fulfill({ json: workspaceDetail(task) }));
    await page.goto("/data-prep");
    await page.getByRole("button", { name: "公开网页", exact: true }).click();
    await expect(page.getByLabel("公开搜索结果")).toContainText("本次搜索：电池回收研究");
    await page.getByRole("button", { name: "添加到当前任务", exact: true }).click();
    await expect(page.getByLabel("任务要求", { exact: true })).toHaveValue("电池回收研究");
    await page.getByRole("button", { name: "检查上下文草案" }).click();
    await page.getByRole("button", { name: "启动任务", exact: true }).click();
    await expect.poll(() => submitted).not.toBeNull();
    expect(submitted).toMatchObject({ source_snapshot_ids: ["snapshot-1"], quantity_requirement: "当前已成功读取页面中有证据的内容",
      completeness_requirement: "逐来源披露失败、范围和未覆盖内容，不承诺全网完整" });
    expect(acquisitions).toBe(0);
  });
});

function mixedAttempt(index: number) {
  const attempt = sourceAttempt("succeeded");
  const url = `https://example.com/source-${index}`;
  return { ...attempt, request_url: url, normalized_url: url, allowed_scope: { ...attempt.allowed_scope, normalized_url: url }, attempt_id: `mixed-attempt-${index}`, snapshot_id: `mixed-snapshot-${index}`,
    snapshot: { ...attempt.snapshot!, snapshot_id: `mixed-snapshot-${index}`, attempt_id: `mixed-attempt-${index}`,
      allowed_scope: { ...attempt.snapshot!.allowed_scope, normalized_url: url },
      artifacts: [{ ...attempt.snapshot!.artifacts[0], artifact_id: `mixed-artifact-${index}`, title: `独立说明 ${index}`, final_url: url, text_preview: `实体 E101 的来源 ${index}` }] } };
}
async function addMixedWeb(page: Page, index: number) {
  await page.getByRole("button", { name: "公开网页", exact: true }).click();
  await page.getByRole("combobox", { name: "来源方式", exact: true }).selectOption("url");
  await page.getByLabel("精确网址").fill(`https://example.com/source-${index}`);
  await page.getByRole("button", { name: "获取网页", exact: true }).click();
  await page.getByRole("button", { name: "添加到当前任务", exact: true }).click();
}

test.describe("#135 混合来源", () => {
  test("文件与两个独立网页连续添加并一次确认完整集合", async ({ page }) => {
    await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    let acquired = 0;
    let submitted: Record<string, unknown> | null = null;
    await page.route("**/api/semantic-workspace/source-acquisitions", route => route.fulfill({ json: mixedAttempt(++acquired) }));
    await page.route("**/api/semantic-workspace/tasks", route => { submitted = route.request().postDataJSON(); return route.fulfill({ json: workspaceTask("mixed-task", "queued", "混合任务") }); });
    await page.route("**/api/semantic-workspace/tasks/mixed-task", route => route.fulfill({ json: workspaceDetail(workspaceTask("mixed-task", "queued", "混合任务"), {
      upload_ids: ["upload-e2e"], uploads: [{ upload_id: "upload-e2e", original_name: "workload.csv", media_type: "text/csv", size_bytes: 64, sha256: "0".repeat(64) }],
      web_sources: [1, 2].map(index => ({ source_snapshot_id: mixedAttempt(index).snapshot_id, snapshot: mixedAttempt(index).snapshot })),
    }) }));
    await page.route("**/api/semantic-workspace/tasks/mixed-task/sources/mixed-artifact-2/preview?*", route => route.fulfill({ json: {
      task_id: "mixed-task", revision: 1, artifact_id: "mixed-artifact-2", upload_id: null, sha256: "a".repeat(64), original_name: "独立说明 2", media_type: "text/html", content_url: null, kind: "web", text_preview: "第二组独立冻结摘要", representation: { kind: "source", parser_or_inspector_version: "fixture" }, is_complete: false, truncated: true,
    } }));
    await page.goto("/data-prep");
    await page.getByLabel("任务要求", { exact: true }).fill("按实体ID关联表格和公开说明");
    await page.locator('input[type="file"]').setInputFiles({ name: "workload.csv", mimeType: "text/csv", buffer: Buffer.from("实体ID,规模\nE101,200", "utf-8") });
    await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "公开网页", exact: true })).toBeEnabled();
    await addMixedWeb(page, 1);
    await addMixedWeb(page, 2);
    await expect(page.getByLabel("已选网页资料")).toContainText("独立说明 1");
    await expect(page.getByLabel("已选网页资料")).toContainText("独立说明 2");
    await expect(page.getByText("workload.csv").first()).toBeVisible();
    expect(submitted).toBeNull();
    await page.getByRole("button", { name: "检查上下文草案" }).click();
    await page.getByRole("button", { name: "启动任务", exact: true }).click();
    await expect.poll(() => submitted).toMatchObject({ upload_ids: ["upload-e2e"], source_snapshot_ids: ["mixed-snapshot-1", "mixed-snapshot-2"], objective_text: "按实体ID关联表格和公开说明", output_formats: ["xlsx"] });
    expect(submitted).not.toHaveProperty("source_snapshot_id");
    expect(acquired).toBe(2);
    await page.getByRole("button", { name: "原文件预览", exact: true }).click();
    await expect(page.getByLabel("预览文件").locator("option")).toHaveCount(3);
    await page.getByLabel("预览文件").selectOption("mixed-artifact-2");
    await expect(page.getByText("第二组独立冻结摘要", { exact: true })).toBeVisible();
  });

  test("同名同大小不同文件不吞证据，精确重复引用仅保留一次", async ({ page }) => {
    await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    let calls = 0;
    let submitted: Record<string, unknown> | null = null;
    await page.route("**/api/data-sources/uploads", route => { calls++; return route.fulfill({ json: { upload_id: calls === 1 ? "same-a" : "same-b", original_name: "same.csv", media_type: "text/csv", size_bytes: 10, sha256: (calls === 1 ? "a" : "b").repeat(64) } }); });
    await page.route("**/api/semantic-workspace/tasks", route => { submitted = route.request().postDataJSON(); return route.fulfill({ status: 422, json: { detail: "保留当前草稿" } }); });
    await page.goto("/data-prep");
    await page.getByLabel("任务要求", { exact: true }).fill("对照三次选入的证据");
    for (const [index, body] of ["ID,n\nA,100", "ID,n\nA,200", "ID,n\nA,200"].entries()) {
      await page.locator('input[type="file"]').setInputFiles({ name: "same.csv", mimeType: "text/csv", buffer: Buffer.from(body, "utf-8") });
      await expect.poll(() => calls).toBe(index + 1);
      await expect(page.getByText("已上传，等待执行", { exact: true })).toHaveCount(Math.min(index + 1, 2));
      await expect(page.getByText(/上传中/)).toHaveCount(0);
    }
    await expect(page.getByText("已上传，等待执行", { exact: true })).toHaveCount(2);
    await page.getByRole("button", { name: "开始执行", exact: true }).click();
    await expect.poll(() => submitted).toMatchObject({ upload_ids: ["same-a", "same-b"], source_snapshot_ids: [] });
    expect(calls).toBe(3);
  });

  test("刷新恢复完整草稿，仅Owner读取元数据，未完成文件保留待重选", async ({ page }) => {
    await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    const upload = { upload_id: "saved-upload", original_name: "saved.csv", media_type: "text/csv", size_bytes: 10, sha256: "a".repeat(64) };
    await page.addInitScript(({ upload }) => {
      localStorage.setItem("mangrove_workspace_draft_u1_new", JSON.stringify({ draft: { prompt: "保留明确目标", formats: ["xlsx"], connectionId: null, connectionModel: null, localModel: "Qwen3.6-35B-A3B" }, sources: [{ snapshotId: "mixed-snapshot-1", attemptId: "mixed-attempt-1" }], quantity: "至少两条且披露不足", completeness: "只分析已选范围" }));
      localStorage.setItem("mangrove_workspace_draft_u1_new_files", JSON.stringify([{ id: "saved", name: "saved.csv", size: 10, status: "ready", progress: 100, upload }, { id: "pending", name: "pending.csv", size: 12, status: "uploading", progress: 25 }]));
    }, { upload });
    let reads = 0, posts = 0;
    await page.route("**/api/data-sources/uploads/saved-upload", route => { reads++; expect(route.request().headers()["x-mangrove-owner"]).toBe("u1"); return route.fulfill({ json: upload }); });
    await page.route("**/api/semantic-workspace/source-acquisitions/mixed-attempt-1", route => route.fulfill({ json: mixedAttempt(1) }));
    await page.route("**/api/data-sources/uploads", route => { posts++; return route.abort(); });
    await page.goto("/data-prep");
    await expect(page.getByLabel("任务要求", { exact: true })).toHaveValue("保留明确目标");
    await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
    await expect(page.getByLabel("重新选择 pending.csv")).toBeVisible();
    await expect(page.getByLabel("数量要求", { exact: true })).toHaveValue("至少两条且披露不足");
    await expect(page.getByLabel("完整性要求", { exact: true })).toHaveValue("只分析已选范围");
    await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeDisabled();
    expect(reads).toBeGreaterThan(0); expect(posts).toBe(0);
    await page.getByRole("button", { name: "移除网页组 独立说明 1" }).click();
    await expect(page.getByText("saved.csv").first()).toBeVisible();
    await expect(page.getByLabel("重新选择 pending.csv")).toBeVisible();
  });
});

function sourceAttempt(
  status: "succeeded" | "failed",
  extra: Record<string, unknown> = {},
) {
  const succeeded = status === "succeeded";
  return {
    attempt_id: `source-${status}`,
    idempotency_key: `key-${status}`,
    request_url: "https://example.com/article#intro",
    normalized_url: "https://example.com/article",
    allowed_scope: {
      kind: "current_page",
      normalized_url: "https://example.com/article",
      site: "example.com",
      page_limit: 1,
      completeness: {
        mode: "exploratory",
        required_valid_pages: null,
      },
    },
    purpose: "读取公开网页内容，供当前数据任务分析",
    status,
    started_at: "2026-08-27T10:00:00Z",
    finished_at: "2026-08-27T10:00:01Z",
    snapshot_id: succeeded ? "snapshot-1" : null,
    error_code: succeeded ? null : "non_html",
    error_message: succeeded ? null : "页面不是 HTML",
    snapshot: succeeded ? {
      snapshot_id: "snapshot-1",
      attempt_id: `source-${status}`,
      allowed_scope: {
        kind: "current_page",
        normalized_url: "https://example.com/article",
        site: "example.com",
        page_limit: 1,
        completeness: {
          mode: "exploratory",
          required_valid_pages: null,
        },
      },
      valid_page_count: 1,
      failed_page_count: 0,
      created_at: "2026-08-27T10:00:01Z",
      coverage: {
        status: "scope_complete",
        limit_reached: false,
        attempted_page_count: 1,
        required_valid_pages: null,
      },
      failures: [],
      artifacts: [{
        artifact_id: "artifact-1",
        request_url: "https://example.com/article",
        final_url: "https://example.com/article",
        read_at: "2026-08-27T10:00:01Z",
        content_sha256: "a".repeat(64),
        media_type: "text/html",
        size_bytes: 1024,
        title: "示例产品说明",
        text_preview: "这是页面中冻结的公开产品说明。",
      }],
    } : null,
    ...extra,
  };
}

test.describe("统一数据工作台", () => {
  for (const externalConnection of [false, true]) {
  test(`已完成任务的长来源刷新仍可停止且不提前确认${externalConnection ? "（外部连接）" : ""}`, async ({ page }) => {
    // 页面异常应直接定位到渲染错误，不能退化为后续按钮的长超时。
    page.on("pageerror", error => { throw new Error(`任务详情渲染异常：${error.message}`); });
    // 拦住所有未声明的 API，隔离开发入口不会转发到真实服务。
    await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    let status = "completed";
    let refreshing: Route | undefined;
    const task = {
      ...workspaceTask("task-refresh-stop", status, "停止网页刷新"),
      model_connection_id: externalConnection ? "connection-refresh-stop" : null,
    };
    const snapshot = sourceAttempt("succeeded").snapshot!;
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [{ ...task, status }] }));
    await page.route("**/api/semantic-workspace/tasks/task-refresh-stop", (route) =>
      route.fulfill({ json: workspaceDetail({ ...task, status }, {
        web_source: {
          source_snapshot_id: snapshot.snapshot_id,
          snapshot,
          goal_contract: {
            objective: task.objective_text, must_include: [], explicit_exclusions: [],
            quantity_requirement: "", completeness_requirement: "探索范围",
          },
          delivery_spec: { formats: task.output_formats },
          runtime_binding: {
            adapter_id: "pi", adapter_version: "1", runtime_artifact: "fixture",
            protocol_version: "1", event_schema_version: "1", capability_digest: "a".repeat(64),
            external_run_id: "run-refresh-stop", model_connection_id: task.model_connection_id,
            model_connection_version: externalConnection ? "version-refresh-stop" : null,
            model: "synthetic-frozen-web-model",
          },
          created_at: snapshot.created_at,
        },
      }) }));
    await page.route("**/api/semantic-workspace/tasks/task-refresh-stop/source-refresh", (route) => {
      expect(route.request().postDataJSON().external_api_confirmed).toBe(externalConnection);
      refreshing = route;
    });
    await page.route("**/api/semantic-workspace/tasks/task-refresh-stop/cancel", (route) => {
      status = "cancelling";
      return route.fulfill({ json: { ...task, status } });
    });
    await page.goto("/data-prep");
    await page.getByRole("button", { name: /停止网页刷新/ }).click();
    await expect(page.getByText("本任务模型：synthetic-frozen-web-model", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "查看本次用量" }).click();
    await expect(page.getByRole("dialog", { name: "本次执行用量" })).toContainText("模型：synthetic-frozen-web-model");
    await page.getByRole("button", { name: "关闭用量" }).click();
    await expect(page.getByRole("button", { name: "取消任务", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "获取最新网页", exact: true }).click();
    if (externalConnection) {
      await expect.poll(() => Boolean(refreshing)).toBe(false);
      await page.getByRole("button", { name: "确认获取并创建新版本", exact: true }).click();
    }
    await expect.poll(() => Boolean(refreshing)).toBe(true);
    await page.getByRole("button", { name: "取消任务", exact: true }).click();
    await page.getByRole("button", { name: "确认取消", exact: true }).click();
    await expect(page.getByText("正在停止，等待读取和资源清理完成。", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "重试停止", exact: true })).toBeVisible();
    await expect(page.getByText("已停止", { exact: true })).toHaveCount(0);
    await refreshing!.fulfill({ status: 409, json: { detail: "来源刷新已请求停止" } });
    await expect(page.getByText("来源刷新已请求停止", { exact: true })).toBeVisible();
    await expect(page.getByText("正在停止，等待读取和资源清理完成。", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "获取最新网页", exact: true })).toBeEnabled();
  });
  }

  test("来源请求被拒绝后仍可修改网址", async ({ page }) => {
    await mockWorkspace(page);
    await page.route("**/api/semantic-workspace/source-acquisitions", (route) =>
      route.fulfill({ status: 422, json: { detail: "网址不在允许范围" } }));
    await page.goto("/data-prep");
    await page.getByRole("button", { name: "公开网页", exact: true }).focus();
    await page.getByRole("button", { name: "公开网页", exact: true }).press("Space");
    await page.getByLabel("精确网址").fill("https://example.com/article");
    await page.getByRole("button", { name: "获取网页", exact: true }).click();
    await expect(page.getByText("网址不在允许范围", { exact: true })).toBeVisible();
    await expect(page.getByLabel("精确网址")).toBeEnabled();
    await expect(page.getByRole("button", { name: "获取网页", exact: true })).toBeEnabled();
    expect(await page.evaluate(() => localStorage.getItem("mangrove_web_source_attempt_u1"))).toBeNull();
  });

  test("来源长首请求可取得停止身份并跨刷新等待已停止", async ({ page }, testInfo) => {
    await mockWorkspace(page);
    let status = "acquiring";
    let firstRequest: Route | undefined;
    const keys: string[] = [];
    const saved = () => sourceAttempt("failed", {
      attempt_id: "source-stopping", idempotency_key: keys[0], status,
      error_code: null, error_message: null,
      finished_at: status === "canceled" ? "2026-09-05T12:00:00Z" : null,
    });
    await page.route("**/api/semantic-workspace/source-acquisitions", async (route) => {
      keys.push(route.request().headers()["idempotency-key"] ?? "");
      if (keys.length === 1) {
        firstRequest = route;
        return;
      }
      await route.fulfill({ status: 202, json: saved() });
    });
    await page.route("**/api/semantic-workspace/source-acquisitions/source-stopping", (route) =>
      route.fulfill({ json: saved() }));
    await page.route("**/api/semantic-workspace/source-acquisitions/source-stopping/cancel", (route) => {
      status = "cancelling";
      return route.fulfill({ json: saved() });
    });
    await page.goto("/data-prep");
    await page.getByRole("button", { name: "公开网页", exact: true }).focus();
    await page.getByRole("button", { name: "公开网页", exact: true }).press("Space");
    await page.getByLabel("精确网址").fill("https://example.com/article");
    await page.getByRole("button", { name: "获取网页", exact: true }).click();
    await page.getByRole("button", { name: "取消获取" }).click();
    await expect(page.getByRole("status").filter({ hasText: "正在停止来源获取" })).toBeVisible();
    expect(keys.length).toBeGreaterThanOrEqual(2);
    expect(new Set(keys).size).toBe(1);
    const postCount = keys.length;
    // 迟到的首个响应不能覆盖已观察到的停止状态。
    await firstRequest!.fulfill({ status: 202, json: { ...saved(), status: "acquiring" } });
    const observed = page.waitForResponse((response) =>
      response.request().method() === "GET" && response.url().endsWith("/source-stopping"));
    await observed;
    await expect(page.getByText("正在停止来源获取", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "清除网页来源" })).toHaveCount(0);
    await page.reload();
    await page.getByRole("button", { name: "公开网页", exact: true }).focus();
    await page.getByRole("button", { name: "公开网页", exact: true }).press("Space");
    await expect(page.getByText("正在停止来源获取", { exact: true })).toBeVisible();
    status = "canceled";
    await expect(page.getByText("来源获取已停止", { exact: true })).toBeVisible();
    const stoppedScreenshot = testInfo.outputPath("source-stopped.png");
    await page.screenshot({ path: stoppedScreenshot });
    await testInfo.attach("source-stopped", { path: stoppedScreenshot, contentType: "image/png" });
    expect(keys).toHaveLength(postCount);
    await page.getByRole("button", { name: "清除网页来源" }).click();
    await expect(page.getByRole("button", { name: "获取网页", exact: true })).toBeVisible();
  });

  test("任务清理未完成可重试停止并显示已停止", async ({ page }, testInfo) => {
    await mockWorkspace(page);
    let status = "running";
    let stopCalls = 0;
    const task = workspaceTask("task-cleanup", status, "清理重试任务");
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [{ ...task, status }] }));
    await page.route("**/api/semantic-workspace/tasks/task-cleanup", (route) =>
      route.fulfill({ json: workspaceDetail({ ...task, status }, {
        events: status === "cancelling" ? [{
          event_id: "cleanup-pending", sequence: 1, stage: "cancelling",
          event_type: "runtime_cleanup_pending", summary: "正在停止，资源清理尚未完成，可重试停止",
          details: { recovery_status: "pending" }, created_at: task.updated_at,
        }] : [],
      }) }));
    await page.route("**/api/semantic-workspace/tasks/task-cleanup/cancel", (route) => {
      stopCalls += 1;
      status = stopCalls === 1 ? "cancelling" : "cancelled";
      return route.fulfill({ json: { ...task, status } });
    });
    await page.goto("/data-prep");
    await page.getByRole("button", { name: /清理重试任务/ }).click();
    await page.getByRole("button", { name: "取消任务", exact: true }).click();
    await page.getByRole("button", { name: "确认取消", exact: true }).click();
    await expect(page.getByText("清理未完成，任务仍在停止中。可重试停止。", { exact: true })).toBeVisible();
    const cleanupScreenshot = testInfo.outputPath("cleanup-pending.png");
    await page.screenshot({ path: cleanupScreenshot });
    await testInfo.attach("cleanup-pending", { path: cleanupScreenshot, contentType: "image/png" });
    await page.getByRole("button", { name: "重试停止", exact: true }).click();
    await expect(page.getByText("任务已停止，未发布新的正式交付。你可以从原要求创建新版本。", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "重试停止", exact: true })).toHaveCount(0);
    expect(stopCalls).toBe(2);
  });

  for (const item of [
    { theme: "light" as const, width: 1366, height: 768 },
    { theme: "dark" as const, width: 1920, height: 1080 },
  ]) {
    test(`${item.theme} 主题在 ${item.width}x${item.height} 下完整显示`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: item.width, height: item.height });
      await mockWorkspace(page, item.theme);
      await page.goto("/data-prep");

      await expect(page.getByRole("heading", { name: "想处理什么资料？" }))
        .toBeVisible();
      await expect(page.getByRole("button", { name: "添加模型", exact: true })).toBeVisible();
      await page.getByRole("button", { name: "帮助", exact: true }).click();
      await expect(page.getByText("筛选并交付")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("button", { name: "回收站" })).toBeVisible();
      await expect(page.locator("html")).toHaveClass(
        item.theme === "dark" ? /dark/ : /^(?!.*dark)/,
      );
      expect(await page.evaluate(() => document.documentElement.scrollWidth))
        .toBeLessThanOrEqual(item.width);
      await testInfo.attach(`workspace-${item.theme}`, {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      if (process.env.MANGROVE_VISUAL_CAPTURE === "1") {
        await page.screenshot({
          path: `../.pytest-tmp/workspace-${item.theme}.png`,
        });
      }
    });
  }

  test("公开网页来源会先披露范围并从持久事实恢复", async ({ page }) => {
    await mockWorkspace(page);
    let submitted: Record<string, unknown> | null = null;
    let idempotencyKey = "";
    let taskSubmitted: Record<string, unknown> | null = null;
    let taskIdempotencyKey = "";
    const saved = sourceAttempt("succeeded");
    await page.route("**/api/semantic-workspace/source-acquisitions", async (route) => {
      submitted = route.request().postDataJSON();
      idempotencyKey = route.request().headers()["idempotency-key"] ?? "";
      await route.fulfill({ status: 202, json: saved });
    });
    await page.route(
      "**/api/semantic-workspace/source-acquisitions/source-succeeded",
      (route) => route.fulfill({ json: saved }),
    );
    const createdTask = workspaceTask("web-task-1", "queued", "公开网页产品摘要");
    await page.route("**/api/semantic-workspace/tasks", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      taskSubmitted = route.request().postDataJSON();
      taskIdempotencyKey = route.request().headers()["idempotency-key"] ?? "";
      await route.fulfill({ status: 202, json: createdTask });
    });
    await page.route("**/api/semantic-workspace/tasks/web-task-1", (route) =>
      route.fulfill({ json: workspaceDetail(createdTask) }));
    await page.goto("/data-prep");

    const webMode = page.getByRole("button", { name: "公开网页", exact: true });
    await webMode.focus();
    await webMode.press("Space");
    await expect(page.getByLabel("精确网址")).toBeVisible();
    const url = page.getByLabel("精确网址");
    await url.focus();
    await url.fill("HTTPS://Example.com:443/article#intro");
    await expect(page.getByText("实际请求：")).toBeVisible();
    await expect(page.getByText("https://example.com/article", { exact: true }))
      .toBeVisible();
    await expect(page.getByText("允许范围：仅当前页面")).toBeVisible();
    await expect(page.getByText("可能外发：标题、正文、网址")).toBeVisible();
    await expect(page.getByText("本步骤不调用模型。", { exact: false }))
      .toBeVisible();

    const acquire = page.getByRole("button", { name: "获取网页" });
    await expect(acquire).toBeEnabled();
    await acquire.click();

    await expect(
      page.getByRole("region", { name: "获取一个公开网页" })
        .getByText("网页来源已冻结", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("article", { name: "网页正文预览" }))
      .toContainText("这是页面中冻结的公开产品说明");
    expect(submitted).toEqual({
      url: "https://example.com/article",
      purpose: "读取公开网页内容，供当前数据任务分析",
      allowed_scope: "current_page",
      page_limit: 1,
      completeness_mode: "exploratory",
      required_valid_pages: null,
    });
    expect(idempotencyKey.length).toBeGreaterThan(5);

    await page.getByRole("button", { name: "添加到当前任务", exact: true }).click();
    await page.getByLabel("任务要求", { exact: true }).fill("生成产品摘要并保留来源证据");
    await page.getByLabel("必须包含").fill("产品名称\n公开说明");
    await page.getByLabel("明确不要").fill("不要推测未公开价格");
    await page.getByLabel("任务模板（可选）").selectOption(
      JSON.stringify(["public-company-summary", 1]),
    );
    await page.getByText("个人记忆（可选）").click();
    await page.getByText("公司名使用官网全称").click();
    await page.getByRole("button", { name: "检查上下文草案" }).click();
    await expect(page.getByText("已检查，可以启动")).toBeVisible();
    await expect(page.getByText("建议目标：按公司提取名称和来源证据", { exact: true }))
      .toBeVisible();
    await page.getByRole("button", { name: "启动任务" }).click();
    await expect.poll(() => taskSubmitted).not.toBeNull();
    expect(taskSubmitted).toMatchObject({
      objective_text: "生成产品摘要并保留来源证据",
      upload_ids: [],
      source_snapshot_ids: ["snapshot-1"],
      must_include: ["产品名称", "公开说明"],
      explicit_exclusions: ["不要推测未公开价格"],
      quantity_requirement: "当前已成功读取页面中有证据的内容",
      completeness_requirement: "逐来源披露失败、范围和未覆盖内容，不承诺全网完整",
      output_formats: ["markdown"],
      model_connection_id: null,
      external_api_confirmed: false,
      context_selection: {
        template: { template_id: "public-company-summary", version: 1 },
        memories: [{ memory_id: 7 }],
      },
      context_preview_sha256: `sha256:${"3".repeat(64)}`,
    });
    expect(taskSubmitted).not.toHaveProperty("runtime_version");
    expect(taskIdempotencyKey.length).toBeGreaterThan(5);
    await expect(page.getByRole("heading", { name: "公开网页产品摘要" }))
      .toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { name: "公开网页产品摘要" })).toBeVisible();
    await page.getByRole("button", { name: "新建任务", exact: true }).click();
    await page.getByText("公开网页", { exact: true }).click();
    await expect(page.getByLabel("精确网址")).toHaveValue("");
    await expect(page.getByRole("article", { name: "网页正文预览" })).toHaveCount(0);
  });

  test("同站探索范围会披露未覆盖页面并允许继续", async ({ page }) => {
    await mockWorkspace(page);
    const base = sourceAttempt("succeeded");
    const scope = {
      kind: "same_site",
      normalized_url: "https://example.com/",
      site: "example.com",
      page_limit: 3,
      completeness: {
        mode: "exploratory",
        required_valid_pages: null,
      },
    };
    const saved = {
      ...base,
      normalized_url: "https://example.com/",
      allowed_scope: scope,
      snapshot: {
        ...base.snapshot!,
        allowed_scope: scope,
        valid_page_count: 2,
        failed_page_count: 1,
        coverage: {
          status: "coverage_unknown",
          limit_reached: false,
          attempted_page_count: 3,
          required_valid_pages: 2,
        },
        artifacts: [
          base.snapshot!.artifacts[0],
          {
            ...base.snapshot!.artifacts[0],
            artifact_id: "artifact-2",
            request_url: "https://example.com/details",
            final_url: "https://example.com/details",
            title: "详细说明",
          },
        ],
        failures: [{
          failure_id: "failure-1",
          request_url: "https://example.com/missing",
          final_url: "https://example.com/missing",
          error_code: "site_refused",
          error_message: "站点拒绝读取",
          failed_at: "2026-08-27T10:00:01Z",
        }],
      },
    };
    let submitted: Record<string, unknown> | null = null;
    let taskSubmitted: Record<string, unknown> | null = null;
    await page.route("**/api/semantic-workspace/source-acquisitions", async (route) => {
      submitted = route.request().postDataJSON();
      await route.fulfill({ status: 202, json: saved });
    });
    const createdTask = workspaceTask("web-task-exploratory", "queued", "探索式网页摘要");
    await page.route("**/api/semantic-workspace/tasks", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      taskSubmitted = route.request().postDataJSON();
      await route.fulfill({ status: 202, json: createdTask });
    });
    await page.route("**/api/semantic-workspace/tasks/web-task-exploratory", (route) =>
      route.fulfill({ json: workspaceDetail(createdTask) }));
    await page.goto("/data-prep");
    await page.getByText("公开网页", { exact: true }).click();
    await page.getByLabel("精确网址").fill("https://example.com/");
    await page.getByRole("button", { name: "同站有限扩展" }).click();
    await page.getByLabel("最多读取页数").fill("3");
    await expect(page.getByText("允许范围：example.com 内最多 3 页"))
      .toBeVisible();
    await page.getByRole("button", { name: "获取网页" }).click();

    await expect(page.getByText("已冻结 2 个有效页面，另有 1 个失败或越界记录"))
      .toBeVisible();
    await expect(page.getByText("不能声称覆盖了整个站点", { exact: false }))
      .toBeVisible();
    await page.getByText("查看页面清单与失败原因").click();
    await expect(page.getByText("https://example.com/missing", { exact: false }))
      .toBeVisible();
    expect(submitted).toEqual({
      url: "https://example.com/",
      purpose: "读取公开网页内容，供当前数据任务分析",
      allowed_scope: "same_site",
      page_limit: 3,
      completeness_mode: "exploratory",
      required_valid_pages: null,
    });

    await page.getByRole("button", { name: "添加到当前任务", exact: true }).click();
    await page.getByLabel("任务要求", { exact: true }).fill("汇总已成功读取的页面并披露缺口");
    await page.getByRole("button", { name: "检查上下文草案" }).click();
    await expect(page.getByText("已检查，可以启动")).toBeVisible();
    await page.getByRole("button", { name: "启动任务" }).click();
    await expect.poll(() => taskSubmitted).not.toBeNull();
    expect(taskSubmitted).toMatchObject({
      source_snapshot_ids: ["snapshot-1"],
      quantity_requirement: "当前已成功读取页面中有证据的内容",
      completeness_requirement: "逐来源披露失败、范围和未覆盖内容，不承诺全网完整",
    });
  });

  test("同站完整硬门槛不满足时只展示结果且不允许启动任务", async ({ page }) => {
    await mockWorkspace(page);
    const base = sourceAttempt("succeeded");
    const scope = {
      kind: "same_site",
      normalized_url: "https://example.com/",
      site: "example.com",
      page_limit: 3,
      completeness: {
        mode: "hard_scope_complete",
        required_valid_pages: null,
      },
    };
    await page.route("**/api/semantic-workspace/source-acquisitions", (route) =>
      route.fulfill({
        status: 202,
        json: {
          ...base,
          allowed_scope: scope,
          snapshot: {
            ...base.snapshot!,
            allowed_scope: scope,
            valid_page_count: 1,
            failed_page_count: 1,
            coverage: {
              status: "hard_insufficient",
              limit_reached: false,
              attempted_page_count: 2,
              required_valid_pages: null,
            },
            failures: [{
              failure_id: "failure-hard-scope",
              request_url: "https://example.com/missing",
              final_url: "https://example.com/missing",
              error_code: "site_refused",
              error_message: "站点拒绝读取",
              failed_at: "2026-08-27T10:00:01Z",
            }],
          },
        },
      }));

    await page.goto("/data-prep");
    await page.getByText("公开网页", { exact: true }).click();
    await page.getByLabel("精确网址").fill("https://example.com/");
    await page.getByRole("button", { name: "同站有限扩展" }).click();
    await page.getByLabel("最多读取页数").fill("3");
    await page.getByLabel("结果要求").selectOption("hard_scope_complete");
    await page.getByRole("button", { name: "获取网页" }).click();

    await expect(page.getByText("当前结果仅供查看，不能启动任务", { exact: false }))
      .toBeVisible();
    await expect(page.getByText("授权站内范围仍有未读取页面", { exact: false }))
      .toBeVisible();
    await expect(page.getByText("有效页面不足 0 个", { exact: false }))
      .toHaveCount(0);
    await expect(page.getByText("下一步：清除本次来源后降低硬性要求", { exact: false }))
      .toBeVisible();
    await page.getByRole("button", { name: "添加到当前任务", exact: true }).click();
    await expect(page.getByRole("button", { name: "启动任务" })).toBeDisabled();
  });

  test("来源刷新迟到成功不能把当前任务地址和正文切回旧任务", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    const task = { ...workspaceTask("task-late-refresh", "completed", "A的网页任务"), runtime_version: "pi", model_connection_id: null };
    const snapshot = sourceAttempt("succeeded").snapshot!;
    const detail = workspaceDetail(task, { web_source: {
      source_snapshot_id: snapshot.snapshot_id,
      goal_contract: { objective: task.objective_text, must_include: [], explicit_exclusions: [], quantity_requirement: "当前页面中有证据的内容", completeness_requirement: "仅对当前精确页面负责" },
      delivery_spec: { formats: ["markdown"] },
      runtime_binding: { adapter_id: "pi", adapter_version: "1", runtime_artifact: "fixture", protocol_version: "1", event_schema_version: "1", capability_digest: "a".repeat(64), external_run_id: "run-late-refresh", model_connection_id: null, model_connection_version: null, model: "Qwen3.6-35B-A3B" },
      created_at: snapshot.created_at, snapshot,
    } });
    const selected = previewIdentityFixture("B", 2);
    const requested = responseBarrier(), release = responseBarrier();
    await page.route("**/api/semantic-workspace/tasks?*", (route) => route.fulfill({ json: [task, selected.task] }));
    await page.route("**/api/semantic-workspace/tasks/task-late-refresh", (route) => route.fulfill({ json: detail }));
    await page.route("**/task-late-refresh/preview?*", (route) => route.fulfill({ json: { kind: "document", items: [], total: 0, offset: 0, limit: 100 } }));
    await page.route("**/api/semantic-workspace/tasks/identity-task", (route) => route.fulfill({ json: selected.detail }));
    await page.route("**/identity-task/preview?*", (route) => route.fulfill({ json: selected.preview }));
    await page.route("**/task-late-refresh/source-refresh", async (route) => {
      requested.release();
      await release.promise;
      await route.fulfill({ status: 202, json: { status: "revision_created", attempt: sourceAttempt("succeeded"), revision: { ...detail.revisions[0], revision: 2 } } });
    });
    try {
      await page.goto("/data-prep");
      await page.getByRole("button", { name: /A的网页任务/ }).click();
      await page.getByRole("button", { name: "获取最新网页", exact: true }).click();
      await requested.promise;
      await page.getByRole("button", { name: /B的结果任务/ }).click();
      await expect(page.getByText("B-V2-正文", { exact: true })).toBeVisible();
      await expect(page).toHaveURL(/task=identity-task/);
      release.release();
      await expect(page.getByText("最新网页已冻结，并创建了新版本", { exact: true })).toBeVisible();
      await expect(page).toHaveURL(/task=identity-task/);
      await expect(page.getByText("B-V2-正文", { exact: true })).toBeVisible();
    } finally {
      release.release();
    }
  });

  test("来源刷新结果未知时由用户恢复同一请求并创建一个新版本", async ({ page }) => {
    await mockWorkspace(page);
    const task = {
      ...workspaceTask("task-source-refresh", "completed", "公开网页摘要"),
      runtime_version: "pi",
      model_connection_id: null,
    };
    const snapshot = sourceAttempt("succeeded").snapshot!;
    const detail = workspaceDetail(task, {
      web_source: {
        source_snapshot_id: snapshot.snapshot_id,
        goal_contract: {
          objective: task.objective_text,
          must_include: [],
          explicit_exclusions: [],
          quantity_requirement: "当前页面中有证据的内容",
          completeness_requirement: "仅对当前精确页面负责",
        },
        delivery_spec: { formats: ["markdown"] },
        runtime_binding: {
          adapter_id: "pi",
          adapter_version: "1",
          runtime_artifact: "fixture",
          protocol_version: "1",
          event_schema_version: "1",
          capability_digest: "a".repeat(64),
          external_run_id: "run-source-refresh",
          model_connection_id: null,
          model_connection_version: null,
          model: "Qwen3.6-35B-A3B",
        },
        created_at: snapshot.created_at,
        snapshot,
      },
    });
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [task] }));
    await page.route("**/api/semantic-workspace/tasks/task-source-refresh", (route) =>
      route.fulfill({ json: detail }));
    await page.route("**/api/semantic-workspace/tasks/task-source-refresh/preview?*", (route) =>
      route.fulfill({
        json: {
          kind: "document",
          items: [],
          total: 0,
          offset: 0,
          limit: 100,
        },
      }));
    const receivedKeys: string[] = [];
    const receivedPayloads: Array<Record<string, unknown>> = [];
    let calls = 0;
    await page.route(
      "**/api/semantic-workspace/tasks/task-source-refresh/source-refresh",
      (route) => {
        calls += 1;
        receivedKeys.push(route.request().headers()["idempotency-key"] ?? "");
        receivedPayloads.push(route.request().postDataJSON());
        return route.fulfill({
          status: 202,
          json: calls === 1
            ? { status: "acquiring", attempt: sourceAttempt("succeeded"), revision: null }
            : {
              status: "revision_created",
              attempt: sourceAttempt("succeeded"),
              revision: { ...detail.revisions[0], revision: 2 },
            },
        });
      },
    );

    await page.goto("/data-prep");
    await page.getByText("公开网页摘要", { exact: true }).click();
    await page.getByRole("button", { name: "获取最新网页" }).click();
    await expect(page.getByText("刷新请求结果仍未知", { exact: false })).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { name: "公开网页摘要", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "获取最新网页" }).click();
    await expect(page.getByText("最新网页已冻结，并创建了新版本")).toBeVisible();
    expect(receivedKeys).toHaveLength(2);
    expect(new Set(receivedKeys).size).toBe(1);
    expect(receivedPayloads[0]).toMatchObject({ resume_unknown: false });
    expect(receivedPayloads[1]).toMatchObject({ resume_unknown: true });
    expect(await page.evaluate(() => (
      localStorage.getItem("mangrove_source_refresh_u1_task-source-refresh")
    ))).toBeNull();
  });

  test("网页来源在收到 Attempt ID 前刷新仍复用同一幂等请求", async ({ page }) => {
    await mockWorkspace(page);
    await page.addInitScript(() => {
      localStorage.setItem("mangrove_web_source_attempt_u1", JSON.stringify({
        attempt_id: null,
        idempotency_key: "pending-reconnect-key",
        url: "https://example.com/article",
        purpose: "读取公开网页内容，供当前数据任务分析",
      }));
    });
    const acquiring = sourceAttempt("succeeded", {
      attempt_id: "source-restored",
      idempotency_key: "pending-reconnect-key",
      status: "acquiring",
      finished_at: null,
      snapshot_id: null,
      snapshot: null,
    });
    const succeeded = sourceAttempt("succeeded", {
      attempt_id: "source-restored",
      idempotency_key: "pending-reconnect-key",
    });
    let createCalls = 0;
    const receivedKeys: string[] = [];
    await page.route("**/api/semantic-workspace/source-acquisitions", (route) => {
      createCalls += 1;
      receivedKeys.push(route.request().headers()["idempotency-key"] ?? "");
      return route.fulfill({
        status: 202,
        json: createCalls >= 3 ? succeeded : acquiring,
      });
    });
    await page.route(
      "**/api/semantic-workspace/source-acquisitions/source-restored",
      (route) => route.fulfill({ json: succeeded }),
    );

    await page.goto("/data-prep");
    await page.getByText("公开网页", { exact: true }).click();

    await expect(
      page.getByRole("region", { name: "获取一个公开网页" })
        .getByText("网页来源已冻结", { exact: true }),
    ).toBeVisible();
    expect(createCalls).toBeGreaterThanOrEqual(1);
    expect(new Set(receivedKeys)).toEqual(new Set(["pending-reconnect-key"]));
  });

  test("网页任务在收到 Task ID 前刷新仍复用同一幂等请求", async ({ page }) => {
    await mockWorkspace(page);
    const saved = sourceAttempt("succeeded");
    const pendingPayload = {
      objective_text: "生成产品摘要并保留来源证据",
      upload_ids: [],
      source_snapshot_ids: ["snapshot-1"],
      must_include: ["产品名称"],
      explicit_exclusions: ["不得推测价格"],
      quantity_requirement: "当前已成功读取页面中有证据的内容",
      completeness_requirement: "逐来源披露失败、范围和未覆盖内容，不承诺全网完整",
      output_formats: ["markdown"],
      runtime_version: "pi",
      permission_profile: "standard",
      provider: "local",
      model: "local-model",
      model_connection_id: null,
      model_connection_model: null,
      external_api_confirmed: false,
    };
    await page.addInitScript(({ source, payload }) => {
      localStorage.setItem("mangrove_web_source_attempt_u1", JSON.stringify({
        attempt_id: source.attempt_id,
        idempotency_key: source.idempotency_key,
        url: source.normalized_url,
        purpose: source.purpose,
      }));
      localStorage.setItem("mangrove_web_task_attempt_u1", JSON.stringify({
        fingerprint: JSON.stringify(payload),
        idempotency_key: "pending-task-reconnect-key",
        payload,
      }));
    }, { source: saved, payload: pendingPayload });
    await page.route(
      "**/api/semantic-workspace/source-acquisitions/source-succeeded",
      (route) => route.fulfill({ json: saved }),
    );
    const createdTask = workspaceTask("web-task-restored", "queued", "公开网页产品摘要");
    const receivedKeys: string[] = [];
    let receivedPayload: Record<string, unknown> | null = null;
    await page.route("**/api/semantic-workspace/tasks", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      receivedKeys.push(route.request().headers()["idempotency-key"] ?? "");
      receivedPayload = route.request().postDataJSON();
      await route.fulfill({ status: 202, json: createdTask });
    });
    await page.route("**/api/semantic-workspace/tasks/web-task-restored", (route) =>
      route.fulfill({ json: workspaceDetail(createdTask) }));

    await page.goto("/data-prep");
    await expect.poll(() => receivedKeys.length).toBe(1);
    expect(receivedKeys).toEqual(["pending-task-reconnect-key"]);
    expect(receivedPayload).toEqual(pendingPayload);
    await expect.poll(() => page.evaluate(() => (
      localStorage.getItem("mangrove_web_task_attempt_u1")
    ))).toBeNull();
  });

  test("网页来源失败在窄屏深色主题中明确说明零下游结果", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockWorkspace(page, "dark");
    await page.route("**/api/semantic-workspace/source-acquisitions", (route) =>
      route.fulfill({ status: 202, json: sourceAttempt("failed") }));
    await page.goto("/data-prep");

    await page.getByText("公开网页", { exact: true }).click();
    await page.getByLabel("精确网址").fill("https://example.com/file.pdf");
    await page.getByRole("button", { name: "获取网页" }).click();

    await expect(page.getByRole("alert")).toContainText("没有形成可用来源");
    await expect(page.getByRole("alert")).toContainText("不是 HTML 页面");
    await expect(page.locator("html")).toHaveClass(/dark/);
    await page.getByRole("button", { name: "打开导航" }).click();
    await expect(page.getByRole("link", { name: "旧版对话" })).toBeVisible();
    await expect(page.getByRole("button", { name: "浅色主题" })).toBeVisible();
    await page.getByRole("dialog", { name: "全局导航", exact: true }).getByRole("button", { name: "关闭导航", exact: true }).click();
    await expect(page.getByRole("link", { name: "旧版对话" })).toBeHidden();
    await page.getByRole("button", { name: "打开导航" }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("link", { name: "旧版对话" })).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(390);
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
  });

  test("文件、目标、模型和输出格式形成可提交任务", async ({ page }) => {
    await mockWorkspace(page);
    await page.goto("/data-prep");

    const submit = page.getByRole("button", { name: "开始执行" });
    await expect(submit).toBeDisabled();
    await page.locator('input[type="file"]').setInputFiles({
      name: "workload.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("姓名,工作量\n张三,5\n", "utf-8"),
    });
    await expect(page.getByText("已上传，等待执行")).toBeVisible();
    await expect(page.getByText("张三", { exact: true })).toBeVisible();
    await page.getByPlaceholder(/描述你想得到的结果/).fill(
      "只筛选张三并输出 XLSX",
    );
    await page.getByRole("button", { name: "更多", exact: true }).click();
    await expect(page.getByLabel("执行模型")).toHaveValue(
      "local::Qwen3.6-35B-A3B",
    );
    await expect(submit).toBeEnabled();
    await page.getByRole("button", { name: "更多", exact: true }).click();
    await page.getByRole("button", { name: "打开导航" }).click();
    await expect(page.getByAltText("howso@Mangrove")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "新建任务" })).toBeVisible();
    if (process.env.MANGROVE_VISUAL_CAPTURE === "1") {
      await page.screenshot({
        path: "../.pytest-tmp/workspace-upload-preview.png",
      });
    }
  });

  test("Word 上传完成后自动打开并显示原文件预览", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    await mockWorkspace(page);
    await page.unroute("**/api/data-sources/uploads");
    await page.route("**/api/data-sources/uploads", (route) => route.fulfill({
      json: {
        upload_id: "upload-docx",
        original_name: "商务条款.docx",
        media_type:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes: 1024,
        sha256: "1".repeat(64),
      },
    }));
    await page.route(
      "**/api/data-sources/uploads/upload-docx/document-preview",
      (route) => route.fulfill({
        json: {
          upload_id: "upload-docx",
          original_name: "商务条款.docx",
          status: "ready",
          elements: [{
            element_id: "clause-1",
            artifact_id: "upload-docx",
            page: 1,
            element_type: "paragraph",
            text: "投标方逾期交付时应承担违约责任。",
            reading_order: 1,
            extractor: "python-docx",
            extractor_version: "1.2.0",
            metadata: {
              location: { kind: "docx_paragraph", paragraph: 1 },
            },
          }],
          rejects: [],
        },
      }),
    );
    await page.goto("/data-prep");

    await page.locator('input[type="file"]').setInputFiles({
      name: "商务条款.docx",
      mimeType:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      buffer: Buffer.from("mock-docx", "utf-8"),
    });

    await expect(page.getByText("已上传，等待执行")).toBeVisible();
    await expect(page.getByText("核对文件并说明目标")).toBeVisible();
    await expect(
      page.getByLabel("商务条款.docx结构化预览"),
    ).toBeVisible();
    await expect(
      page.getByText("投标方逾期交付时应承担违约责任。"),
    ).toBeVisible();
    expect(pageErrors).toEqual([]);
  });

  test("不支持的文件格式会明确说明，不会静默忽略", async ({ page }) => {
    await mockWorkspace(page);
    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles({
      name: "program.exe",
      mimeType: "application/octet-stream",
      buffer: Buffer.from("not-an-executable", "utf-8"),
    });
    await expect(page.getByText("文件格式不受支持。", { exact: false }))
      .toBeVisible();
    await expect(page.getByRole("button", { name: "开始执行" })).toBeDisabled();
  });

  test("开始执行会提交推荐格式和默认模型并进入任务详情", async ({ page }) => {
    await mockWorkspace(page);
    const completed = workspaceTask("task-new", "completed", "张三工作量");
    let submitted: Record<string, unknown> | null = null;
    let submittedKey = "";
    await page.route("**/api/semantic-workspace/tasks", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      submitted = route.request().postDataJSON();
      submittedKey = route.request().headers()["idempotency-key"] ?? "";
      await route.fulfill({ status: 202, json: completed });
    });
    await page.route("**/api/semantic-workspace/tasks/task-new", (route) =>
      route.fulfill({
        json: workspaceDetail(completed, {
          events: [{
            event_id: "done",
            sequence: 1,
            stage: "deliver",
            event_type: "task_completed",
            summary: "正式文件已生成",
            details: {},
            created_at: completed.updated_at,
          }],
        }),
      }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-new/preview?*",
      (route) => route.fulfill({
        json: {
          kind: "table",
          columns: ["姓名", "工作量"],
          rows: [{ 姓名: "张三", 工作量: 5 }],
          total: 1,
          offset: 0,
          limit: 100,
        },
      }),
    );

    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles({
      name: "workload.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("姓名,工作量\n张三,5\n", "utf-8"),
    });
    await expect(page.getByText("张三", { exact: true })).toBeVisible();
    await page.getByPlaceholder(/描述你想得到的结果/).fill(
      "只筛选张三并输出 XLSX",
    );
    await page.getByRole("button", { name: "开始执行" }).click();

    await expect(page.getByRole("heading", { name: "张三工作量" })).toBeVisible();
    expect(submitted).toMatchObject({
      objective_text: "只筛选张三并输出 XLSX",
      upload_ids: ["upload-e2e"],
      output_formats: ["xlsx"],
      provider: "local",
      model: "Qwen3.6-35B-A3B",
    });
    expect(submitted).not.toHaveProperty("runtime_version");
    expect(submittedKey).toMatch(/^[A-Za-z0-9_-]{21}$/);
    await expect(page.getByText("实际执行：兼容模式（Legacy）"))
      .toBeVisible();
  });

  test("管理员沿用平台默认并冻结本地模型与能力", async ({ page }) => {
    await mockWorkspace(page);
    await page.unroute("**/api/semantic-workspace/capabilities");
    await page.route("**/api/semantic-workspace/capabilities", (route) =>
      route.fulfill({
        json: {
          enabled: true,
          items: [{
            pack_id: "gray-python-table",
            version: "1.0.0",
            digest: `sha256:${"a".repeat(64)}`,
            name: "Python 表格处理",
            kind: "tool",
            purpose: "按任务要求处理表格数据",
            scope: "platform",
          }],
        },
      }));
    const candidate = {
      ...workspaceTask("task-pi-new", "candidate_ready", "Mangrove 候选任务"),
      permission_profile: "standard",
    };
    let submitted: Record<string, unknown> | null = null;
    await page.route("**/api/semantic-workspace/tasks", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      submitted = route.request().postDataJSON();
      await route.fulfill({ status: 202, json: candidate });
    });
    await page.route("**/api/semantic-workspace/tasks/task-pi-new", (route) =>
      route.fulfill({
        json: workspaceDetail(candidate, {
          runtime_version: "pi",
          permission_profile: "standard",
          agentic_runtime: {
            runtime_version: "pi",
            permission_profile: "standard",
            status: "candidate_ready",
            candidates: [],
            events: [],
          },
        }),
      }));

    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles({
      name: "workload.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("姓名,工作量\n张三,5\n", "utf-8"),
    });
    await page.getByPlaceholder(/描述你想得到的结果/).fill(
      "只筛选张三并输出 CSV",
    );
    await page.getByRole("button", { name: "更多", exact: true }).click();
    await expect(page.getByRole("radio", { name: "平台默认（推荐）" })).toHaveCount(0);
    await expect(page.getByRole("radio", { name: "增强模式（Pi）" })).toHaveCount(0);
    await expect(page.getByLabel("执行模型")).toHaveValue(
      "local::Qwen3.6-35B-A3B",
    );
    await page.getByRole("checkbox", { name: /Python 表格处理/ }).check();
    await page.getByRole("button", { name: "开始执行" }).click();

    expect(submitted).not.toHaveProperty("runtime_version");
    expect(submitted).toMatchObject({
      permission_profile: "standard",
      provider: "local",
      model: "Qwen3.6-35B-A3B",
      capability_pack_refs: [{
        pack_id: "gray-python-table",
        version: "1.0.0",
        digest: `sha256:${"a".repeat(64)}`,
      }],
    });
    await expect(page.getByText("实际执行：增强模式（Pi）"))
      .toBeVisible();
  });

  test("无可用连接的普通用户得到配置入口且不能静默使用本地模型", async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    const completed = workspaceTask(
      "task-user-legacy",
      "completed",
      "Legacy 兼容任务",
    );
    let submitted: Record<string, unknown> | null = null;
    await page.route("**/api/semantic-workspace/tasks", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      submitted = route.request().postDataJSON();
      await route.fulfill({ status: 202, json: completed });
    });
    await page.route(
      "**/api/semantic-workspace/tasks/task-user-legacy",
      (route) => route.fulfill({ json: workspaceDetail(completed) }),
    );

    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles({
      name: "workload.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("姓名,工作量\n张三,5\n", "utf-8"),
    });
    await page.getByPlaceholder(/描述你想得到的结果/).fill(
      "只筛选张三并输出 XLSX",
    );
    await page.getByRole("button", { name: "更多", exact: true }).click();
    await expect(page.getByRole("radio", { name: "平台默认（推荐）" })).toHaveCount(0);
    await expect(page.getByRole("radio", { name: "增强模式（Pi）" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "添加模型", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "开始执行" })).toBeDisabled();
    expect(submitted).toBeNull();
  });

  test("普通用户走平台默认时仍须确认自己的连接外发", async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    await page.route("**/api/model-connections", (route) => route.fulfill({
      json: {
        items: [{
          connection_id: "conn-user-deepseek",
          owner_scope: "user_personal",
          preset_id: "deepseek",
          display_name: "DeepSeek",
          model: "deepseek-chat",
          api_format: "openai_chat_completions",
          locality: "public",
          status: "verified",
          default_model: "deepseek-chat",
          models: [
            {
              model_id: "deepseek-chat",
              display_name: "DeepSeek Chat",
              status: "available",
              enabled: true,
            },
            {
              model_id: "deepseek-reasoner",
              display_name: "DeepSeek Reasoner",
              status: "available",
              enabled: true,
            },
          ],
        }],
      },
    }));
    await page.route("**/api/model-connections/preferences/default", (route) =>
      route.fulfill({
        json: {
          preference: {
            connection_id: "conn-user-deepseek",
            model_id: "deepseek-reasoner",
            available: true,
          },
        },
      }));
    let submitted: Record<string, unknown> | null = null;
    await page.route("**/api/semantic-workspace/tasks", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      submitted = route.request().postDataJSON();
      await route.fulfill({
        status: 202,
        json: workspaceTask("task-user-pi", "queued", "外部模型任务"),
      });
    });

    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles({
      name: "workload.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("姓名,工作量\n张三,5\n", "utf-8"),
    });
    await page.getByPlaceholder(/描述你想得到的结果/).fill(
      "只筛选张三并输出 CSV",
    );
    await page.getByRole("button", { name: "更多", exact: true }).click();
    await expect(page.getByRole("radio", { name: "平台默认（推荐）" })).toHaveCount(0);

    await expect(page.getByLabel("模型连接")).toHaveValue(
      "conn-user-deepseek",
    );
    await expect(
      page.getByText("外发确认：DeepSeek · deepseek-reasoner"),
    ).toBeVisible();
    await page.getByLabel("本任务模型").selectOption("deepseek-chat");
    await expect(
      page.getByText("外发确认：DeepSeek · deepseek-chat"),
    ).toBeVisible();
    await expect(page.getByText(/当前任务中的表格内容与任务说明/)).toBeVisible();
    await expect(page.getByText(/仅用于当前任务版本/)).toBeVisible();
    await expect(page.getByRole("button", { name: "开始执行" })).toBeDisabled();
    const accessibility = await new AxeBuilder({ page })
      .include('[data-testid="external-model-disclosure"]')
      .analyze();
    expect(accessibility.violations).toEqual([]);

    await expect(page.getByRole("radio", { name: "兼容模式（Legacy）" })).toHaveCount(0);
    await page.getByLabel("本任务模型").selectOption("deepseek-reasoner");
    await expect(page.getByRole("checkbox", {
      name: /确认将上述内容发送到 DeepSeek/,
    })).not.toBeChecked();
    await expect(page.getByLabel("本任务模型")).toHaveValue(
      "deepseek-reasoner",
    );
    await page.getByLabel("本任务模型").selectOption("deepseek-chat");

    await page.getByRole("checkbox", {
      name: /确认将上述内容发送到 DeepSeek/,
    }).check();
    await page.getByRole("button", { name: "开始执行" }).click();

    expect(submitted).toMatchObject({
      provider: "deepseek",
      model: "deepseek-chat",
      model_connection_id: "conn-user-deepseek",
      model_connection_model: "deepseek-chat",
      external_api_confirmed: true,
    });
    expect(submitted).not.toHaveProperty("runtime_version");
  });

  test("Mangrove 候选明确区别于正式交付并可下载", async ({ page }) => {
    await mockWorkspace(page);
    const candidateTask = {
      ...workspaceTask(
        "task-pi-candidate",
        "candidate_ready",
        "服务费用候选",
      ),
      runtime_version: "pi",
      permission_profile: "standard",
    };
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidateTask] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-pi-candidate",
      (route) => route.fulfill({
        json: workspaceDetail(candidateTask, {
          runtime_version: "pi",
          permission_profile: "standard",
          agentic_runtime: {
            runtime_version: "pi",
            permission_profile: "standard",
            status: "candidate_ready",
            candidates: [{
              artifact_id: "candidate-1",
              filename: "服务费用标准及明细.csv",
              format: "csv",
              sha256: "a".repeat(64),
              size_bytes: 256,
              openable: true,
              qa_checks: ["non_empty", "reopened"],
              download_allowed: true,
              download_url: (
                "/api/semantic-workspace/tasks/task-pi-candidate/"
                + "candidates/candidate-1"
              ),
            }],
            verification: {
              status: "passed",
              summary: "候选已通过文件、来源证据和目标语义验证",
              checks: [
                {
                  code: "source_grounding",
                  passed: true,
                  summary: "已从原件重新确认 3 条证据",
                },
                {
                  code: "semantic_goal",
                  passed: true,
                  summary: "候选只包含用户要求的费用明细",
                },
              ],
              evidence_count: 3,
              formal_delivery_eligible: false,
            },
            events: [],
          },
        }),
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: "待确认" }).click();
    await expect(page.getByText("服务费用候选").first()).toBeVisible();
    await page.getByRole("button", { name: "已完成" }).click();
    await expect(page.getByText("服务费用候选")).toHaveCount(0);
    await page.getByRole("button", { name: "待确认" }).click();
    await page.getByText("服务费用候选").first().click();
    await expect(page.getByRole("heading", {
      name: "Mangrove 候选已通过独立验证",
    }))
      .toBeVisible();
    await expect(page.getByText("已从原件重新确认 3 条证据"))
      .toBeVisible();
    await expect(page.getByText("不是正式交付")).toBeVisible();
    await expect(page.getByRole("button", {
      name: "下载候选 服务费用标准及明细.csv",
    })).toBeVisible();
    await expect(page.getByRole("button", {
      name: "服务费用标准及明细.csv",
      exact: true,
    })).toBeVisible();
    await expect(page.getByRole("heading", { name: "结果与正式交付" }))
      .toHaveCount(0);
  });

  test("严格目标缺口展示部分结果并由用户键盘确认新版本", async ({ page }) => {
    await mockWorkspace(page);
    const candidateTask = {
      ...workspaceTask("task-partial", "candidate_ready", "10 家公司查找"),
      runtime_version: "pi",
      permission_profile: "standard",
    };
    const candidateHash = "b".repeat(64);
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidateTask] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-partial",
      (route) => route.fulfill({
        json: workspaceDetail(candidateTask, {
          runtime_version: "pi",
          permission_profile: "standard",
          agentic_runtime: {
            runtime_version: "pi",
            permission_profile: "standard",
            status: "candidate_ready",
            candidates: [{
              artifact_id: "candidate-partial",
              filename: "公司名单.json",
              format: "json",
              sha256: "a".repeat(64),
              size_bytes: 256,
              openable: true,
              qa_checks: ["non_empty", "reopened"],
              download_allowed: true,
              download_url: "/api/semantic-workspace/tasks/task-partial/candidates/candidate-partial",
            }],
            verification: {
              status: "failed",
              summary: "严格数量目标未满足",
              checks: [],
              evidence_count: 9,
              formal_delivery_eligible: false,
            },
            candidate_coverage: {
              result_items: Array.from({ length: 9 }, (_, index) => ({
                result_id: `company-${index + 1}`,
                label: `公司 ${index + 1}`,
                evidence_refs: [`evidence-${index + 1}`],
              })),
              actual_result_count: 9,
              target_result_count: 10,
              is_partial: true,
              formal_delivery_eligible: false,
              conclusion: {
                kind: "confirmed_scope_insufficient",
                reason: "本次获准有限范围已完整检查，确认只有 9 项有证据结果；这不代表范围之外不存在更多结果。",
                evidence_refs: Array.from({ length: 9 }, (_, index) => `evidence-${index + 1}`),
              },
              same_run_repair_allowed: false,
              repair_unit_ids: [],
              disclosure: {
                authorized_unit_count: 9,
                observed_unit_count: 9,
                failed_unit_count: 0,
                unknown_unit_count: 0,
                low_quality_unit_count: 0,
                actual_result_count: 9,
              },
            },
            gap_actions: [],
            events: [],
            awaiting_publication: true,
            reverification_offer: {
              eligible: false,
              candidate_set_hash: candidateHash,
              blockers: ["coverage_gap"],
              ruleset_changed: false,
              requires_provider: false,
              candidate_count: 1,
              candidate_formats: ["json"],
              egress_categories: [],
              egress_summary: "",
            },
          },
        }),
      }),
    );
    let gapPayload: Record<string, unknown> | null = null;
    await page.route(
      "**/api/semantic-workspace/tasks/task-partial/candidate-gap-actions",
      async (route) => {
        gapPayload = await route.request().postDataJSON();
        await route.fulfill({
          status: 202,
          json: {
            action: "accept_gap",
            status: "completed",
            source_revision: 1,
            target_revision: 2,
          },
        });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: "待确认" }).click();
    await page.getByText("10 家公司查找").first().click();
    const panel = page.getByTestId("partial-candidate-panel");
    await expect(panel).toContainText("已找到 9 项，目标是 10 项");
    await expect(panel).toContainText("公司 1");
    await expect(panel).toContainText("1 条证据");
    await expect(page.getByRole("button", { name: "发布正式结果" })).toHaveCount(0);
    await expect(panel).toContainText("确认本次范围不足");
    await expect(panel).toContainText("原版本不会发布 Delivery");
    await expect(panel.getByRole("button", { name: "不接受本次缺口" })).toBeVisible();
    await expect(panel.getByRole("button", { name: "补充来源" })).toBeVisible();
    await expect(panel.getByRole("button", { name: "刷新原来源" })).toBeVisible();

    const accept = panel.getByRole("button", { name: "接受 9 项并调整目标" });
    await accept.focus();
    await accept.press("Enter");
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("原版本、Candidate 和缺口结论不会被修改");
    const confirm = dialog.getByRole("button", { name: "确认创建新版本" });
    await confirm.focus();
    await confirm.press("Enter");
    await expect.poll(() => gapPayload).not.toBeNull();
    expect(gapPayload).toEqual({
      action: "accept_gap",
      expected_revision: 1,
      expected_candidate_set_hash: candidateHash,
      external_api_confirmed: false,
    });
    await expect(page.getByText("已创建结果版本 V2")).toBeVisible();

    const accessibility = await new AxeBuilder({ page })
      .include("[data-testid='partial-candidate-panel']")
      .analyze();
    expect(accessibility.violations).toEqual([]);
  });

  test("零条有证据结果时不提供接受零项动作", async ({ page }) => {
    await mockWorkspace(page);
    const candidateTask = {
      ...workspaceTask("task-zero-results", "candidate_ready", "10 家公司查找"),
      runtime_version: "pi",
      permission_profile: "standard",
    };
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidateTask] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-zero-results",
      (route) => route.fulfill({
        json: workspaceDetail(candidateTask, {
          runtime_version: "pi",
          permission_profile: "standard",
          agentic_runtime: {
            runtime_version: "pi",
            permission_profile: "standard",
            status: "candidate_ready",
            candidates: [],
            candidate_coverage: {
              result_items: [],
              actual_result_count: 0,
              target_result_count: 10,
              is_partial: true,
              formal_delivery_eligible: false,
              conclusion: {
                kind: "unknown",
                reason: "当前没有有证据结果，不能判断是否还有更多结果。",
                evidence_refs: [],
              },
              same_run_repair_allowed: false,
              repair_unit_ids: [],
              disclosure: {
                authorized_unit_count: 1,
                observed_unit_count: 1,
                failed_unit_count: 0,
                unknown_unit_count: 1,
                low_quality_unit_count: 0,
                actual_result_count: 0,
              },
            },
            gap_actions: [],
            events: [],
            awaiting_publication: true,
            reverification_offer: {
              eligible: false,
              candidate_set_hash: "c".repeat(64),
              blockers: ["coverage_gap"],
              ruleset_changed: false,
              requires_provider: false,
              candidate_count: 0,
              candidate_formats: [],
              egress_categories: [],
              egress_summary: "",
            },
          },
        }),
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: "待确认" }).click();
    await page.getByText("10 家公司查找").first().click();
    const panel = page.getByTestId("partial-candidate-panel");
    await expect(panel.getByRole("button", { name: /接受 0 项/ })).toHaveCount(0);
    await expect(panel.getByRole("status")).toContainText("补充或刷新来源");
    await expect(panel.getByRole("button", { name: "补充来源" })).toBeVisible();
    await expect(panel.getByRole("button", { name: "刷新原来源" })).toBeVisible();
  });

  test("语义验证无结论时可只重新验证现有候选", async ({ page }) => {
    await mockWorkspace(page);
    const candidateTask = {
      ...workspaceTask(
        "task-candidate-inconclusive",
        "candidate_ready",
        "待重新验证候选",
      ),
      runtime_version: "pi",
      permission_profile: "standard",
    };
    const detail = workspaceDetail(candidateTask, {
      runtime_version: "pi",
      permission_profile: "standard",
      agentic_runtime: {
        runtime_version: "pi",
        permission_profile: "standard",
        status: "candidate_ready",
        candidates: [{
          artifact_id: "candidate-retry",
          filename: "第2个报销审批单.json",
          format: "json",
          sha256: "b".repeat(64),
          size_bytes: 2868,
          openable: true,
          qa_checks: ["non_empty", "reopened"],
          download_allowed: true,
          download_url: (
            "/api/semantic-workspace/tasks/task-candidate-inconclusive/"
            + "candidates/candidate-retry"
          ),
        }],
        verification: {
          status: "inconclusive",
          summary: "文件与来源证据有效，但独立语义验证未形成可靠结论",
          checks: [
            {
              code: "source_grounding",
              passed: true,
              summary: "已从原件重新确认 37 条证据",
            },
            {
              code: "semantic_goal",
              passed: false,
              summary: "语义验证服务暂时不可用，请稍后重新验证候选。",
            },
          ],
          evidence_count: 37,
          formal_delivery_eligible: false,
        },
        latest_verification_attempt: {
          attempt_id: "legacy-inconclusive-attempt",
          status: "inconclusive",
          reason: "initial",
          ruleset_identity_status: "legacy_unversioned",
        },
        reverification_offer: {
          eligible: true,
          reason: "semantic_inconclusive",
          blockers: [],
          ruleset_changed: null,
          ruleset_change_summary: "当前验证规则身份暂时无法证明",
          requires_provider: true,
          connection_id: "connection-deepseek",
          model_id: "deepseek-v4-flash",
          egress_categories: ["任务目标", "候选预览", "来源证据"],
          egress_summary: "将外发任务目标、候选预览和 37 条来源证据",
        },
        awaiting_publication: false,
        events: [],
      },
    });
    let legacyRetryCalls = 0;
    let retryPayload: Record<string, unknown> | null = null;
    let revisionCalls = 0;
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidateTask] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-candidate-inconclusive/revisions",
      (route) => {
        revisionCalls += 1;
        return route.fulfill({ status: 500 });
      },
    );
    await page.route(
      "**/api/semantic-workspace/tasks/task-candidate-inconclusive/candidate-verification/retry",
      (route) => {
        legacyRetryCalls += 1;
        return route.fulfill({ json: detail });
      },
    );
    await page.route(
      "**/api/semantic-workspace/tasks/task-candidate-inconclusive/candidate-verifications",
      (route) => {
        retryPayload = route.request().postDataJSON();
        return route.fulfill({
          status: 202,
          json: {
            attempt_id: "semantic-attempt-new",
            task_id: candidateTask.task_id,
            revision: 1,
            run_id: "pi-run-inconclusive",
            previous_attempt_id: "legacy-inconclusive-attempt",
            status: "requested",
            task: detail,
          },
        });
      },
    );
    await page.route(
      "**/api/semantic-workspace/tasks/task-candidate-inconclusive",
      (route) => route.fulfill({ json: detail }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: "待确认" }).click();
    await page.getByText("待重新验证候选").first().click();
    await page.getByRole("button", {
      name: "只重跑语义验证",
      exact: true,
    }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("不会重新读取 37 条来源证据");
    await expect(dialog).toContainText("deepseek-v4-flash");
    await expect(dialog).toContainText("candidate-retry");
    await expect(dialog).toContainText("b".repeat(64));
    await dialog.getByRole("checkbox").check();
    await dialog.getByRole("button", { name: "开始语义验证" }).click();

    await expect.poll(() => retryPayload).toEqual({
      expected_revision: 1,
      expected_previous_attempt_id: "legacy-inconclusive-attempt",
      external_api_confirmed: true,
      accept_duplicate_provider_cost: false,
    });
    expect(legacyRetryCalls).toBe(0);
    expect(revisionCalls).toBe(0);
  });

  test("完成结果可预览，并能回到原文件定位来源", async ({ page }) => {
    await mockWorkspace(page);
    const completed = {
      ...workspaceTask("task-result", "completed", "结果来源检查"),
      upload_ids: ["upload-e2e"],
      plan_id: "plan-result",
      logical_revision: 1,
      binding_revision: 1,
      run_id: "run-result",
    };
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [completed] }));
    await page.route("**/api/semantic-workspace/tasks/task-result", (route) =>
      route.fulfill({
        json: workspaceDetail(completed, {
          uploads: [{
            upload_id: "upload-e2e",
            original_name: "workload.csv",
            media_type: "text/csv",
            size_bytes: 64,
            sha256: "0".repeat(64),
          }],
          events: [{
            event_id: "result-done",
            sequence: 1,
            stage: "deliver",
            event_type: "task_completed",
            summary: "正式文件已生成",
            details: {},
            created_at: completed.updated_at,
          }],
          run: { repair_rounds: 0 },
          delivery: {
            delivery_id: "delivery-result",
            run_id: "run-result",
            plan_id: "plan-result",
            status: "published",
            requested_formats: ["xlsx"],
            created_at: completed.updated_at,
            outputs: [{
              output_id: "output-xlsx",
              format: "xlsx",
              filename: "张三工作量.xlsx",
              media_type:
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              sha256: "1".repeat(64),
              size_bytes: 2048,
              qa: {
                openable: true,
                checks: ["格式重开通过", "记录数一致"],
                warnings: [],
                row_count: 1,
                sheet_count: 1,
              },
              download_url: "/api/semantic-delivery/outputs/output-xlsx",
            }],
          },
        }),
      }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-result/preview?*",
      (route) => route.fulfill({
        json: {
          kind: "table",
          columns: ["姓名", "工作量"],
          rows: [{
            姓名: "张三",
            工作量: 5,
            __lineage: [{
              artifact_id: "upload-e2e",
              row_number: 2,
              values: { 姓名: "张三", 工作量: 5 },
            }],
          }],
          total: 1,
          offset: 0,
          limit: 100,
        },
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /结果来源检查/ }).click();
    await expect(page.getByRole("heading", { name: "结果与正式交付" }))
      .toBeVisible();
    await expect(page.locator("p").getByText("张三工作量.xlsx", { exact: true })).toBeVisible();
    await expect(page.getByText("可交付", { exact: true })).toBeVisible();
    const filename = page.locator("p").getByText("张三工作量.xlsx", { exact: true });
    const bundle = page.getByRole("button", { name: "下载全部" });
    // 系统字体宽度不同；验证内容未裁切且按钮可操作，不固定汉字像素宽度。
    for (const element of [filename, bundle]) {
      expect(await element.evaluate((node) => (
        node.scrollWidth <= node.clientWidth && node.scrollHeight <= node.clientHeight
      ))).toBe(true);
    }
    await page.getByRole("button", { name: "展开预览", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "继续对话" })).toBeHidden();
    await page.getByRole("button", { name: "恢复分栏", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "继续对话" })).toBeVisible();
    await expect(bundle).toBeInViewport();
    await bundle.click({ trial: true });
    await page.getByRole("button", { name: "查看来源" }).click();
    await expect(page.getByText("已定位来源", { exact: false })).toBeVisible();
    await expect(page.getByText("原文件第 2 行", { exact: false })).toBeVisible();
    await expect(page.getByText("张三", { exact: true }).last()).toBeVisible();
    if (process.env.MANGROVE_VISUAL_CAPTURE === "1") {
      await page.screenshot({
        path: "../.pytest-tmp/workspace-result-source.png",
      });
    }
  });

  test("运行中追问先形成草案，确认后创建不可变新版本", async ({ page }) => {
    await mockWorkspace(page);
    let activeRevision = 1;
    let submitted: Record<string, unknown> | null = null;
    const base = workspaceTask("task-version", "completed", "版本检查");
    const revision = (number: number) => ({
      task_id: base.task_id,
      revision: number,
      objective_text:
        number === 1
          ? base.objective_text
          : `${base.objective_text}\n用户修改要求：增加地区汇总`,
      output_formats: ["xlsx"],
      plan_id: `plan-v${number}`,
      logical_revision: number,
      binding_revision: number,
      run_id: `run-v${number}`,
      status: "completed",
      summary: `V${number} 已完成`,
      change_summary: number === 1 ? "" : "增加地区汇总",
      created_at: base.created_at,
      updated_at: base.updated_at,
    });
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({
        json: [{ ...base, active_revision: activeRevision }],
      }));
    const versionDetail = (url: string) => {
      const selected = Number(
        new URL(url).searchParams.get("revision") || activeRevision,
      );
      const selectedRevision = revision(selected);
      return workspaceDetail(
        {
          ...base,
          ...selectedRevision,
          active_revision: activeRevision,
          current_revision: activeRevision,
          viewing_revision: selected,
        },
        {
          revisions: Array.from(
            { length: activeRevision },
            (_, index) => revision(index + 1),
          ),
          events: [{
            event_id: `done-v${selected}`,
            sequence: selected,
            stage: "deliver",
            event_type: "task_completed",
            summary: `V${selected} 已生成`,
            details: {},
            created_at: base.updated_at,
          }],
        },
      );
    };
    await page.route("**/api/semantic-workspace/tasks/task-version", (route) =>
      route.fulfill({ json: versionDetail(route.request().url()) }));
    await page.route("**/api/semantic-workspace/tasks/task-version?*", (route) =>
      route.fulfill({ json: versionDetail(route.request().url()) }));
    const steering = {
      result_id: "steering-1", task_id: "task-version", turn_id: "turn-1",
      delta_id: "delta-1", action: "revision_proposal",
      acknowledgement: "已形成修改草案，等待确认", answer: null,
      proposal_id: "proposal-1", run_id: "run-v1", revision: 1,
    };
    await page.route("**/api/semantic-workspace/tasks/task-version/turns", async route => {
      if (route.request().method() === "POST") {
        submitted = route.request().postDataJSON();
        await route.fulfill({ json: steering });
      } else {
        await route.fulfill({ json: {
          turns: submitted ? [{ turn_id: "turn-1", revision: 1, text: "增加地区汇总" }] : [],
          results: submitted ? [steering] : [],
          proposals: submitted ? [{ proposal_id: "proposal-1", base_revision: 1, status: activeRevision === 1 ? "pending" : "accepted" }] : [],
        } });
      }
    });
    await page.route(
      "**/api/semantic-workspace/tasks/task-version/revision-proposals/proposal-1/decision",
      async (route) => {
        expect(route.request().postDataJSON()).toEqual({
          mode: "cancel_now",
          external_api_confirmed: false,
        });
        activeRevision = 2;
        await route.fulfill({
          status: 202,
          json: {
            decision: { decision_id: "decision-1", status: "applied" },
            revision: revision(2),
          },
        });
      },
    );
    await page.route(
      "**/api/semantic-workspace/tasks/task-version/preview?*",
      (route) => route.fulfill({
        json: {
          kind: "table",
          columns: [],
          rows: [],
          total: 0,
          offset: 0,
          limit: 100,
        },
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /版本检查/ }).click();
    await page.getByPlaceholder(/可询问进度和原因/).fill("增加地区汇总");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText("已形成修改草案，等待确认")).toBeVisible();
    await page.reload();
    await expect(page.getByText("增加地区汇总", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "立即停止并切换" })).toBeVisible();
    await page.getByRole("button", { name: "模型设置", exact: true }).click();
    await page.getByRole("button", { name: "返回当前任务" }).click();
    await expect(page.getByRole("textbox", { name: "继续对话" })).toBeFocused();
    await page.getByRole("button", { name: "立即停止并切换" }).click();
    await expect(page.getByRole("button", { name: "立即停止并切换" })).toHaveCount(0);
    await expect(page.getByLabel("结果版本")).toHaveValue("2");
    await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
    expect(submitted).toEqual({ text: "增加地区汇总" });

    await page.getByLabel("结果版本").selectOption("1");
    await expect(
      page.getByRole("button", { name: /当前显示历史版本 V1/ }),
    ).toBeVisible();
    await expect(page.getByPlaceholder(/可询问进度和原因/)).toBeVisible();
    await page.getByRole("button", { name: /当前显示历史版本 V1/ }).click();
    await expect(page.getByLabel("结果版本")).toHaveValue("2");
    await page.goBack();
    await expect(page.getByLabel("结果版本")).toHaveValue("1");
    await page.goForward();
    await expect(page.getByLabel("结果版本")).toHaveValue("2");
  });

  test("回答操作使用真实浏览器能力并把历史消息编辑为新草稿", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await mockWorkspace(page);
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "share", {
        configurable: true,
        value: async ({ text }: ShareData) => localStorage.setItem("shared-answer", text || ""),
      });
    });
    const task = { ...workspaceTask("task-message-actions", "completed", "消息操作检查"), model_connection_id: "connection-external" };
    await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: [task] }));
    await page.route("**/api/semantic-workspace/tasks/task-message-actions", route => route.fulfill({ json: workspaceDetail(task) }));
    await page.route("**/api/semantic-workspace/tasks/task-message-actions/turns", route => route.fulfill({ json: {
      turns: [{ turn_id: "turn-actions", revision: 1, text: "把华东单独汇总" }],
      results: [{ result_id: "result-actions", task_id: task.task_id, turn_id: "turn-actions", delta_id: "delta-actions", action: "answer", acknowledgement: "已完成", answer: "华东合计 42", proposal_id: null, run_id: null, revision: 1 }],
      proposals: [],
    } }));
    const regenerated: Array<{ body: Record<string, unknown>; key: string | undefined }> = [];
    await page.route("**/api/semantic-workspace/tasks/task-message-actions/turns/result-actions/regenerate", route => {
      regenerated.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
      return regenerated.length === 1
        ? route.fulfill({ status: 503, json: { detail: "重新生成结果未知" } })
        : route.fulfill({ json: { result_id: "result-regenerated", task_id: task.task_id, turn_id: "turn-regenerated", delta_id: "delta-regenerated", action: "answer_only", acknowledgement: "已回答", answer: "华东合计 43", proposal_id: null, run_id: null, revision: 1 } });
    });

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /消息操作检查/ }).click();
    await expect(page.getByLabel("Mangrove 回答")).toContainText("华东合计 42");

    await page.getByRole("button", { name: "复制" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("华东合计 42");
    await page.getByRole("button", { name: "分享" }).click();
    await expect(page.getByText("不会创建公开链接", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "打开系统分享" }).click();
    await expect.poll(() => page.evaluate(() => localStorage.getItem("shared-answer"))).toBe("华东合计 42");

    const composer = page.getByRole("textbox", { name: "继续对话" });
    await composer.fill("这是尚未发送的草稿");
    await page.getByRole("button", { name: "编辑为新消息" }).click();
    await expect(composer).toHaveValue("这是尚未发送的草稿");
    await expect(page.getByText("输入框已有未发送内容；请先发送或清空，再编辑历史消息")).toBeVisible();
    await composer.fill("");
    await page.getByRole("button", { name: "编辑为新消息" }).click();
    await expect(page.getByRole("textbox", { name: "继续对话" })).toHaveValue("把华东单独汇总");
    await expect(page.getByLabel("对话记录").locator("p").getByText("把华东单独汇总", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "重新生成" }).click();
    await expect(page.getByText("会再次向已选外部模型发送本任务必要数据", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "确认重新生成" }).click();
    await expect(page.getByText("重新生成结果未知")).toBeVisible();
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("mangrove_regenerate_")).length)).toBe(1);
    await page.reload();
    await page.getByRole("button", { name: "重新生成" }).click();
    await page.getByRole("button", { name: "确认重新生成" }).click();
    await expect(page.getByText("已重新生成回答")).toBeVisible();
    expect(regenerated).toHaveLength(2);
    expect(regenerated[0]).toMatchObject({ body: { expected_revision: 1, external_api_confirmed: true } });
    expect(regenerated[0].key).toBeTruthy();
    expect(regenerated[1].key).toBe(regenerated[0].key);
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("mangrove_regenerate_")).length)).toBe(0);
  });

  test("待确认任务可收起后重新打开，并可随时取消", async ({ page }) => {
    await mockWorkspace(page);
    let status = "needs_input";
    const waiting = workspaceTask("task-waiting", status, "待确认任务");
    const question = {
      kind: "plan",
      question_id: "q1",
      round_id: `clarification_${"3".repeat(32)}`,
      revision: 1, purpose: "business", continuation: "resume", origin_turn_id: null, outbound_purpose: null,
      prompt: "“本月”指自然月还是最近 30 天？",
      reason: "时间范围会改变筛选结果",
      affected_scope: "结果行数",
      options: [
        { value: "calendar", label: "自然月" },
        { value: "rolling", label: "最近 30 天" },
      ],
      allow_free_text: false,
    };
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({
        json: [{ ...waiting, status }],
      }));
    await page.route("**/api/semantic-workspace/tasks/task-waiting", (route) =>
      route.fulfill({
        json: workspaceDetail(
          { ...waiting, status },
          {
            question: status === "needs_input" ? question : null,
            work_session: {
              task_id: "task-waiting",
              revision: 1,
              run_id: "run-waiting",
              status,
              started_at: waiting.created_at,
              ended_at: status === "cancelled" ? waiting.updated_at : null,
              work_duration_ms: 1000,
              waiting_duration_ms: 1000,
              action_count: status === "cancelled" ? 2 : 1,
              tool_call_count: 0,
              handled_retry_count: 0,
              usage: {
                input_tokens: 0,
                output_tokens: 0,
                cache_tokens: null,
                total_tokens: 0,
                call_count: 1,
                unknown_call_count: 1,
              },
              provider_usage: [],
              entries: [
                {
                  event_id: "q-event",
                  sequence: 1,
                  created_at: waiting.updated_at,
                  event_type: "question_required",
                  summary: question.prompt,
                  purpose: question.reason,
                  input_summary: null,
                  duration_ms: null,
                  result_summary: question.affected_scope,
                  evidence_refs: [],
                  recovery_status: "pending",
                  tool_name: null,
                  action_id: question.question_id,
                },
                ...(status === "cancelled" ? [{
                  event_id: "cancel-event",
                  sequence: 2,
                  created_at: waiting.updated_at,
                  event_type: "task_cancelled",
                  summary: "任务已取消",
                  purpose: null,
                  input_summary: null,
                  duration_ms: null,
                  result_summary: null,
                  evidence_refs: [],
                  recovery_status: "handled",
                  tool_name: null,
                  action_id: question.question_id,
                }] : []),
              ],
            },
            events: status === "needs_input"
              ? [{
                event_id: "q-event",
                sequence: 1,
                stage: "needs_input",
                event_type: "question_required",
                summary: question.prompt,
                details: {},
                created_at: waiting.updated_at,
              }]
              : [{
                event_id: "cancel-event",
                sequence: 2,
                stage: "cancelled",
                event_type: "task_cancelled",
                summary: "任务已取消",
                details: {},
                created_at: waiting.updated_at,
              }],
          },
        ),
      }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-waiting/cancel",
      async (route) => {
        status = "cancelled";
        await route.fulfill({ json: { ...waiting, status } });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /待确认任务/ }).click();
    const ownerAction = page.getByLabel("需要你处理后才能继续");
    await expect(ownerAction).toContainText(`原因：${question.reason}`);
    await expect(ownerAction).toContainText(`影响：${question.affected_scope}`);
    await expect(page.getByLabel("当前业务问题")).toContainText(question.prompt);
    await page.getByRole("button", { name: "收起问题" }).click();
    // 业务问题就地收起，保留既有待办、重开和取消旅程。
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await ownerAction.focus();
    await expect(ownerAction).toBeFocused();
    await expect(page.getByRole("button", { name: /继续回答/ })).toBeVisible();
    await page.getByRole("button", { name: /继续回答/ }).click();
    await expect(
      page.getByLabel("当前业务问题").getByText(question.prompt),
    ).toBeVisible();
    await page.getByRole("button", { name: "收起问题" }).click();
    await page.getByRole("button", { name: "取消任务" }).click();
    await page.getByRole("button", { name: "确认取消" }).click();
    await expect(page.getByText("任务已停止，未发布新的正式交付。你可以从原要求创建新版本。")).toBeVisible();
    await expect(page.getByLabel("需要你处理后才能继续")).toHaveCount(0);
    await expect(page.getByText("已停止", { exact: true }).first()).toBeVisible();
  });

  test("历史任务详情加载失败时说明原因并可重试恢复", async ({ page }) => {
    await mockWorkspace(page);
    const historical = workspaceTask(
      "task-history-retry",
      "completed",
      "历史工作量结果",
    );
    await page.unroute("**/api/semantic-workspace/tasks?*");
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [historical] }));
    let detailAttempts = 0;
    let detailAvailable = false;
    await page.route(
      "**/api/semantic-workspace/tasks/task-history-retry",
      (route) => {
        detailAttempts += 1;
        if (!detailAvailable) {
          return route.fulfill({
            status: 500,
            contentType: "application/json",
            json: { detail: "历史任务详情暂时无法读取" },
          });
        }
        return route.fulfill({ json: workspaceDetail(historical) });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /历史工作量结果/ }).click();

    const recovery = page.getByRole("alert", { name: "任务恢复失败" });
    await expect(recovery).toContainText("历史任务仍保留在任务列表中");
    await expect(recovery).toContainText("历史任务详情暂时无法读取");
    detailAvailable = true;
    await recovery.getByRole("button", { name: "重新加载" }).click();

    await expect(
      page.getByRole("heading", { name: "历史工作量结果" }),
    ).toBeVisible();
    expect(detailAttempts).toBeGreaterThan(1);
  });

  test("完成任务可以移入回收站并从回收站恢复", async ({ page }) => {
    await mockWorkspace(page);
    let deleted = false;
    const completed = workspaceTask("task-recycle", "completed", "可回收任务");
    const deletedTask = {
      ...completed,
      deleted_at: "2026-07-27T00:10:00Z",
      purge_after: "2026-08-26T00:10:00Z",
    };
    await page.route("**/api/semantic-workspace/tasks?*", (route) => {
      const wantsDeleted =
        new URL(route.request().url()).searchParams.get("deleted") === "true";
      return route.fulfill({
        json: wantsDeleted
          ? (deleted ? [deletedTask] : [])
          : (deleted ? [] : [completed]),
      });
    });
    await page.route("**/api/semantic-workspace/tasks/task-recycle", (route) =>
      route.fulfill({
        json: workspaceDetail(deleted ? deletedTask : completed),
      }));
    await page.route("**/api/semantic-workspace/tasks/task-recycle", async (route) => {
      if (route.request().method() !== "DELETE") {
        await route.fallback();
        return;
      }
      deleted = true;
      await route.fulfill({ json: deletedTask });
    });
    await page.route(
      "**/api/semantic-workspace/tasks/task-recycle/restore",
      async (route) => {
        deleted = false;
        await route.fulfill({ json: completed });
      },
    );
    await page.route(
      "**/api/semantic-workspace/tasks/task-recycle/preview?*",
      (route) => route.fulfill({
        json: {
          kind: "table",
          columns: [],
          rows: [],
          total: 0,
          offset: 0,
          limit: 100,
        },
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /可回收任务/ }).click();
    await page.getByRole("button", { name: "移入回收站" }).click();
    await page.getByRole("button", { name: "移入回收站" }).last().click();
    await expect(page.getByRole("textbox", { name: "任务要求" })).toBeVisible();
    await page.getByRole("button", { name: "回收站" }).click();
    await page.getByRole("button", { name: /可回收任务/ }).click();
    await expect(page.getByRole("button", { name: "恢复任务" })).toBeVisible();
    await page.getByRole("button", { name: "恢复任务" }).click();
    await expect(page.getByRole("heading", { name: "可回收任务" })).toBeVisible();
    await expect(page.getByRole("button", { name: /可回收任务/ })).toBeVisible();
  });

  test("完成任务按阶段归并进度且没有遗留转圈", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await mockWorkspace(page);
    const summary = {
      task_id: "task-completed",
      title: "工作量筛选",
      objective_text: "只筛选张三并输出 XLSX",
      upload_ids: [],
      output_formats: ["xlsx"],
      provider: "local",
      model: "Qwen3.6-35B-A3B",
      external_api_confirmed: false,
      status: "completed",
      active_revision: 1,
      current_revision: 1,
      viewing_revision: 1,
      plan_id: "plan-1",
      logical_revision: 1,
      binding_revision: 1,
      run_id: "run-1",
      summary: "已识别筛选条件和输出要求",
      error: null,
      question: null,
      cancel_requested: false,
      deleted_at: null,
      purge_after: null,
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:09Z",
    };
    const event = (
      event_id: string,
      sequence: number,
      stage: string,
      event_type: string,
      summaryText: string,
    ) => ({
      event_id,
      sequence,
      stage,
      event_type,
      summary: summaryText,
      details: {},
      created_at: `2026-07-27T00:00:0${sequence}Z`,
    });
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [summary] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-completed/preview?*",
      (route) => route.fulfill({
        json: {
          kind: "table",
          columns: [],
          rows: [],
          total: 0,
          page: 1,
          page_size: 50,
        },
      }),
    );
    await page.route("**/api/semantic-workspace/tasks/task-completed", (route) =>
      route.fulfill({
        json: {
          ...summary,
          revisions: [{
            task_id: "task-completed",
            revision: 1,
            objective_text: summary.objective_text,
            output_formats: ["xlsx"],
            plan_id: "plan-1",
            logical_revision: 1,
            binding_revision: 1,
            run_id: "run-1",
            status: "completed",
            summary: "",
            change_summary: "",
            created_at: summary.created_at,
            updated_at: summary.updated_at,
          }],
          events: [
            event("e1", 1, "queued", "task_created", "任务已进入队列"),
            event("e2", 2, "interpret", "stage_started", "正在理解要求"),
            event("e3", 3, "interpret", "stage_completed", "已形成任务理解"),
            event("e4", 4, "inspect", "stage_started", "正在读取来源"),
            event("e5", 5, "bind", "stage_completed", "来源和字段已绑定"),
            event("e9", 9, "deliver", "task_completed", "正式文件已生成"),
          ],
          harness_events: [
            event("h1", 3, "interpret", "node_completed", "语义计划已校验"),
            event("h2", 4, "inspect", "node_completed", "来源检查已通过"),
            event("h3", 5, "bind", "node_completed", "绑定已通过"),
            event("h4", 6, "plan", "node_completed", "执行计划已生成"),
            event("h5", 7, "execute", "node_completed", "数据处理已完成"),
            event("h6", 8, "verify", "verification_passed", "结果验证已通过"),
            event("h7", 9, "deliver", "delivery_published", "正式交付已发布"),
          ],
          uploads: [],
          plan: null,
          run: { repair_rounds: 0 },
          work_session: {
            task_id: "task-completed",
            revision: 1,
            run_id: "run-1",
            status: "completed",
            started_at: "2026-07-27T00:00:01Z",
            ended_at: "2026-07-27T00:00:09Z",
            work_duration_ms: 7000,
            waiting_duration_ms: 1000,
            action_count: 9,
            tool_call_count: 2,
            handled_retry_count: 1,
            usage: {
              input_tokens: 7000,
              output_tokens: 1420,
              cache_tokens: null,
              total_tokens: 8420,
              call_count: 4,
              unknown_call_count: 1,
            },
            entries: [
              {
                event_id: "owner-action-1",
                sequence: 1,
                created_at: "2026-07-27T00:00:03Z",
                event_type: "owner_action.requested",
                summary: "需要确认下一步",
                purpose: "确认范围",
                input_summary: "当前候选共 9 项",
                duration_ms: null,
                result_summary: null,
                evidence_refs: ["evidence-1"],
                recovery_status: "pending",
                tool_name: null,
                action_id: "accept-gap-1",
              },
              {
                event_id: "unrelated-action-resumed",
                sequence: 2,
                created_at: "2026-07-27T00:00:03.500Z",
                event_type: "resumed",
                summary: "另一个动作已处理",
                purpose: null,
                input_summary: null,
                duration_ms: null,
                result_summary: null,
                evidence_refs: [],
                recovery_status: "handled",
                tool_name: null,
                action_id: "another-action",
              },
              {
                event_id: "owner-action-resumed",
                sequence: 3,
                created_at: "2026-07-27T00:00:04Z",
                event_type: "resumed",
                summary: "已确认并继续",
                purpose: null,
                input_summary: null,
                duration_ms: 1000,
                result_summary: "继续执行",
                evidence_refs: [],
                recovery_status: "handled",
                tool_name: null,
                action_id: "accept-gap-1",
              },
            ],
          },
          attempts: [],
          delivery: null,
        },
      }));

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /工作量筛选/ }).click();
    await expect(page.getByRole("heading", { name: "工作量筛选" })).toBeVisible();

    const progressToggle = page.getByRole("button", { name: /工作记录/ });
    await expect(progressToggle).toContainText("9 个行动");
    await page.getByRole("button", { name: "查看本次用量" }).click();
    const usageDialog = page.getByRole("dialog", { name: "本次执行用量" });
    await expect(usageDialog).toContainText("工作耗时7.0 秒");
    await expect(usageDialog).toContainText("等待耗时1.0 秒");
    await expect(usageDialog).toContainText("开始时间");
    await expect(usageDialog).toContainText("结束时间");
    await expect(usageDialog).toContainText("2 次工具");
    await expect(usageDialog).toContainText("已知 8,420 Tokens · 4 次调用 · 另 1 次未知");
    await expect(usageDialog).toContainText("已处理 1 次重试");
    await page.getByRole("button", { name: "关闭用量" }).click();
    await expect(page.locator('[data-testid="progress-stage"]')).toHaveCount(0);
    if (process.env.MANGROVE_VISUAL_CAPTURE === "1") {
      await page.screenshot({
        path: "../.pytest-tmp/workspace-completed-collapsed.png",
      });
    }
    await expect(progressToggle).toHaveAttribute("aria-expanded", "false");
    await progressToggle.focus();
    await progressToggle.press("Enter");
    await expect(progressToggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator('[data-testid="progress-stage"]')).toHaveCount(8);
    await expect(page.getByText("输入：当前候选共 9 项")).toBeVisible();
    await expect(page.getByText("需要你处理后才能继续")).toHaveCount(0);
    await progressToggle.press("Space");
    await expect(progressToggle).toHaveAttribute("aria-expanded", "false");
    await progressToggle.press("Space");
    await expect(progressToggle).toHaveAttribute("aria-expanded", "true");
    const stages = page.locator('[data-testid="progress-stage"]');
    await expect(stages).toHaveCount(8);
    await expect(stages.filter({ hasText: "理解要求" })).toHaveCount(1);
    await expect(stages.filter({ hasText: "读取来源" })).toHaveCount(1);
    await expect(
      page.locator('[data-testid="progress-stage"][data-state="active"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-testid="progress-stage"][data-state="completed"]'),
    ).toHaveCount(8);
    const viewport = await page.evaluate(() => ({
      scrollY: window.scrollY,
      scrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
    }));
    expect(viewport.scrollY).toBe(0);
    expect(viewport.scrollHeight).toBeLessThanOrEqual(viewport.innerHeight);
    const shellBounds = await Promise.all([
      page.getByRole("button", { name: "打开导航", exact: true }).boundingBox(),
      page.getByRole("button", { name: "新建任务" }).boundingBox(),
      page.getByRole("heading", { name: "任务工作台", exact: true }).boundingBox(),
    ]);
    for (const bounds of shellBounds) {
      expect(bounds?.y).toBeGreaterThanOrEqual(0);
    }
    if (process.env.MANGROVE_VISUAL_CAPTURE === "1") {
      await page.screenshot({
        path: "../.pytest-tmp/workspace-completed-progress.png",
      });
    }
  });

  test("运行中最多一个阶段转圈，失败时停在实际失败阶段", async ({ page }) => {
    await mockWorkspace(page);
    let failed = false;
    const running = workspaceTask("task-running", "running", "运行逻辑检查");
    const progressEvents = () => [
      {
        event_id: "queued",
        sequence: 1,
        stage: "queued",
        event_type: "task_created",
        summary: "任务已进入队列",
        details: {},
        created_at: "2026-07-27T00:00:01Z",
      },
      {
        event_id: "interpret-done",
        sequence: 2,
        stage: "interpret",
        event_type: "stage_completed",
        summary: "任务要求已理解",
        details: {},
        created_at: "2026-07-27T00:00:02Z",
      },
      {
        event_id: "inspect-start",
        sequence: 3,
        stage: "inspect",
        event_type: "stage_started",
        summary: "正在读取来源",
        details: {},
        created_at: "2026-07-27T00:00:03Z",
      },
      {
        event_id: "bind-done",
        sequence: 4,
        stage: "bind",
        event_type: "stage_completed",
        summary: "来源和字段已绑定",
        details: {},
        created_at: "2026-07-27T00:00:04Z",
      },
      {
        event_id: "execute-start",
        sequence: 5,
        stage: "execute",
        event_type: "stage_started",
        summary: "正在处理数据",
        details: {},
        created_at: "2026-07-27T00:00:05Z",
      },
      ...(failed ? [{
        event_id: "failed",
        sequence: 7,
        stage: "failed",
        event_type: "task_failed",
        summary: "数据处理失败",
        details: {},
        created_at: "2026-07-27T00:00:07Z",
      }] : []),
    ];
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({
        json: [{ ...running, status: failed ? "failed" : "running" }],
      }));
    await page.route("**/api/semantic-workspace/tasks/task-running", (route) =>
      route.fulfill({
        json: workspaceDetail(
          {
            ...running,
            status: failed ? "failed" : "running",
            error: failed ? "数据处理失败" : null,
          },
          {
            events: progressEvents(),
            harness_events: [{
              event_id: "harness-recheck",
              sequence: 1,
              stage: "interpret",
              event_type: "node_completed",
              summary: "服务端语义计划已重新校验",
              details: {},
              created_at: "2026-07-27T00:00:06Z",
              source: "harness",
            }],
          },
        ),
      }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-running/stream",
      (route) => route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: "",
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /运行逻辑检查/ }).click();
    const workRecord = page.getByRole("button", { name: /工作记录/ });
    await expect(workRecord).toHaveAttribute("aria-expanded", "false");
    await workRecord.click();
    const active = page.locator(
      '[data-testid="progress-stage"][data-state="active"]',
    );
    await expect(active).toHaveCount(1);
    await expect(active).toContainText("处理数据");
    const states = await page.locator('[data-testid="progress-stage"]')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-state")));
    const activeIndex = states.indexOf("active");
    expect(states.slice(activeIndex + 1)).not.toContain("completed");

    failed = true;
    await page.reload();
    await page.getByRole("button", { name: /运行逻辑检查/ }).click();
    await page.getByRole("button", { name: /工作记录/ }).click();
    await expect(
      page.locator('[data-testid="progress-stage"][data-state="active"]'),
    ).toHaveCount(0);
    const failedStage = page.locator(
      '[data-testid="progress-stage"][data-state="failed"]',
    );
    await expect(failedStage).toHaveCount(1);
    await expect(failedStage).toContainText("处理数据");
  });

  test("普通用户能看懂编译失败原因和下一步", async ({ page }) => {
    await mockWorkspace(page);
    const failedTask = {
      ...workspaceTask("task-compile-failed", "failed", "失败说明检查"),
      plan_id: "plan-failed",
      error: "语义计划编译失败",
      failure: {
        error_code: "STP_COMPILE_FAILED",
        stage: "interpret",
        cause_summary: "本地模型两次输出被截断，最后生成的计划未通过校验",
        attempt_count: 3,
        elapsed_ms: 7342,
        source_read: false,
        intermediate_created: false,
        delivery_published: false,
        next_actions: ["修改要求后重试", "检查本地模型配置"],
        diagnostic_ref: "plan-failed",
      },
    };
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [failedTask] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-compile-failed",
      (route) => route.fulfill({
        json: workspaceDetail(failedTask, {
          events: [{
            event_id: "compile-failed",
            sequence: 2,
            stage: "interpret",
            event_type: "stage_failed",
            summary: "任务要求理解失败",
            details: { error_code: "STP_COMPILE_FAILED" },
            created_at: "2026-07-27T00:00:02Z",
          }],
        }),
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /失败说明检查/ }).click();

    const notice = page.getByTestId("task-failure-explanation");
    await expect(notice).toContainText("理解要求");
    await expect(notice).toContainText(
      "本地模型两次输出被截断，最后生成的计划未通过校验",
    );
    await expect(notice).toContainText("共尝试 3 次");
    await expect(notice).toContainText("耗时 7.3 秒");
    await expect(notice).toContainText("尚未读取原始资料");
    await expect(notice).toContainText("未生成中间结果");
    await expect(notice).toContainText("未发布正式交付");
    await expect(notice).toContainText("修改要求后重试");
    await expect(notice).toContainText("检查本地模型配置");
    await expect(notice).toContainText("STP_COMPILE_FAILED");
  });

  test("模型结果不确定时由用户确认后创建新版本", async ({ page }) => {
    await mockWorkspace(page);
    const failedTask = {
      ...workspaceTask("task-model-unknown", "needs_input", "模型结果待确认"),
      model_connection_id: "connection-a",
      external_api_confirmed: true,
      error: "模型请求结果不确定",
      failure: {
        error_code: "MODEL_OUTCOME_UNKNOWN",
        stage: "execute",
        cause_summary: "模型请求结果不确定，平台没有自动重试",
        attempt_count: 1,
        elapsed_ms: 21000,
        source_read: false,
        intermediate_created: false,
        delivery_published: false,
        next_actions: [
          "由你决定是否创建新版本重新执行",
          "取消并保留当前失败记录",
        ],
        diagnostic_ref: "pi-run-unknown",
      },
    };
    const otherFailedTask = { ...failedTask, task_id: "task-model-unknown-other", title: "另一模型结果待确认" };
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [failedTask, otherFailedTask] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-model-unknown-other",
      (route) => route.fulfill({ json: workspaceDetail(otherFailedTask) }),
    );
    await page.route(
      "**/api/semantic-workspace/tasks/task-model-unknown",
      (route) => route.fulfill({
        json: workspaceDetail(failedTask),
      }),
    );
    let revisionPayload: Record<string, unknown> | null = null;
    let revisionCalls = 0;
    const idempotencyKeys: string[] = [];
    await page.route(
      "**/api/semantic-workspace/tasks/task-model-unknown/revisions",
      async (route) => {
        revisionCalls += 1;
        idempotencyKeys.push(route.request().headers()["idempotency-key"] || "");
        revisionPayload = await route.request().postDataJSON();
        if (revisionCalls === 1) return route.fulfill({ status: 500, json: { detail: "unknown" } });
        await route.fulfill({
          status: 202,
          json: { revision: 2 },
        });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /^模型结果待确认/ }).click();
    const notice = page.getByTestId("task-failure-explanation");
    await expect(notice).toContainText("模型请求结果不确定");
    await notice.getByRole("button", { name: "重新执行" }).click();
    await expect(page.getByRole("alertdialog")).toContainText(
      "可能产生重复调用和费用",
    );
    await page.getByLabel("我确认把当前任务范围内的必要数据再次发送到已选外部模型连接。").check();
    await page.evaluate(() => {
      const url = new URL(window.location.href);
      url.searchParams.set("task", "task-model-unknown-other");
      window.history.pushState({}, "", url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    await page.getByTestId("task-failure-explanation").getByRole("button", { name: "重新执行" }).click();
    await expect(page.getByLabel("我确认把当前任务范围内的必要数据再次发送到已选外部模型连接。")).not.toBeChecked();
    await expect(page.getByRole("button", { name: "确认重新执行" })).toBeDisabled();
    await page.getByRole("button", { name: "取消" }).click();
    await page.getByRole("button", { name: /^模型结果待确认/ }).click();
    await page.getByTestId("task-failure-explanation").getByRole("button", { name: "重新执行" }).click();
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const confirmRetry = page.getByRole("button", { name: "确认重新执行" });
      await expect(confirmRetry).toBeDisabled();
      if (attempt === 0) expect((await new AxeBuilder({ page }).include('[role="alertdialog"]').analyze()).violations).toEqual([]);
      await page.getByLabel("我确认把当前任务范围内的必要数据再次发送到已选外部模型连接。").check();
      await confirmRetry.click();
      if (attempt === 0) {
        await page.reload();
        await page.getByRole("button", { name: /^模型结果待确认/ }).click();
        await page.getByTestId("task-failure-explanation").getByRole("button", { name: "重新执行" }).click();
      }
    }

    await expect.poll(() => revisionPayload).not.toBeNull();
    expect(revisionPayload).toEqual({
      instruction: "保持原要求，重新执行",
      external_api_confirmed: true,
      expected_active_revision: 1,
    });
    expect(revisionCalls).toBe(2);
    expect(idempotencyKeys[0]).toBeTruthy();
    expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);
  });

  test("后序完成事件会收口前序遗留开始态", async ({ page }) => {
    await mockWorkspace(page);
    const running = workspaceTask("task-stage-gap", "running", "阶段状态检查");
    const events = [
      {
        event_id: "task-created",
        sequence: 1,
        stage: "queued",
        event_type: "task_created",
        summary: "任务已进入队列",
        details: {},
        created_at: "2026-07-27T00:00:01Z",
      },
      {
        event_id: "inspect-start",
        sequence: 2,
        stage: "inspect",
        event_type: "stage_started",
        summary: "正在读取来源",
        details: {},
        created_at: "2026-07-27T00:00:02Z",
      },
      {
        event_id: "bind-done",
        sequence: 3,
        stage: "bind",
        event_type: "stage_completed",
        summary: "来源和字段已绑定",
        details: {},
        created_at: "2026-07-27T00:00:03Z",
      },
    ];
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [running] }));
    await page.route("**/api/semantic-workspace/tasks/task-stage-gap", (route) =>
      route.fulfill({
        json: workspaceDetail(running, { events, harness_events: [] }),
      }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-stage-gap/stream",
      (route) => route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: "",
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /阶段状态检查/ }).click();
    await page.getByRole("button", { name: /工作记录/ }).click();
    await expect(
      page.locator('[data-testid="progress-stage"][data-state="active"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-testid="progress-stage"]').filter({
        hasText: "读取来源",
      }),
    ).toHaveAttribute("data-state", "completed");
    await expect(
      page.locator('[data-testid="progress-stage"]').filter({
        hasText: "绑定字段",
      }),
    ).toHaveAttribute("data-state", "completed");
  });

  test("Mangrove 文档任务展示冻结理解和可恢复覆盖进度", async ({ page }) => {
    await mockWorkspace(page);
    const coverageEvent = (
      eventId: string,
      sequence: number,
      stage: string,
      eventType: string,
      summary: string,
    ) => ({
      event_id: eventId,
      sequence,
      stage,
      event_type: eventType,
      summary,
      details: {},
      created_at: `2026-07-31T00:00:0${sequence}Z`,
    });
    const running = workspaceTask(
      "task-coverage",
      "running",
      "全部报销记录",
    );
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [running] }));
    await page.route("**/api/semantic-workspace/tasks/task-coverage", (route) =>
      route.fulfill({
        json: workspaceDetail(running, {
          events: [
            coverageEvent("c1", 1, "queued", "task_created", "任务已进入队列"),
            coverageEvent("c2", 2, "goal_interpretation", "tool.completed", "已冻结目标理解"),
            coverageEvent("c3", 3, "source_probe", "tool.completed", "已识别 109 页"),
            coverageEvent("c4", 4, "source_discovery", "tool.started", "正在检查全部页面"),
          ],
          harness_events: [],
          agentic_runtime: {
            runtime_version: "pi",
            permission_profile: "standard",
            status: "running",
            candidates: [],
            coverage: {
              contract: {
                interpretation: "返回整份文件中的全部报销记录",
                result_cardinality: "all",
                completeness: "strict",
                stop_semantics: "全部 109 页完成可信发现且候选已精读",
              },
              progress: {
                authorized: 109,
                observed: 57,
                candidates: 2,
                authoritatively_read: 2,
                low_quality: 0,
                unknown: 52,
                evidence: 2,
                cache_hits: 41,
              },
              ledger: {},
            },
          },
        }),
      }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-coverage/stream",
      (route) => route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: "",
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /全部报销记录/ }).click();
    await page.getByRole("button", { name: /工作记录/ }).click();

    const coverage = page.getByRole("region", { name: "文档覆盖范围" });
    await expect(coverage).toContainText("返回整份文件中的全部报销记录");
    await expect(coverage).toContainText("严格完整");
    await expect(coverage).toContainText("已发现 57/109");
    await expect(coverage).toContainText("未覆盖 52");
    await expect(coverage).toContainText("缓存命中 41");
    await expect(coverage).toContainText("候选 2 · 已精读 2");
    await expect(
      page.locator('[data-testid="progress-stage"]').filter({
        hasText: "候选发现",
      }),
    ).toHaveAttribute("data-state", "active");

    await page.reload();
    await page.getByRole("button", { name: /全部报销记录/ }).click();
    await page.getByRole("button", { name: /工作记录/ }).click();
    await expect(
      page.getByRole("region", { name: "文档覆盖范围" }),
    ).toContainText("已发现 57/109");
    await expect(
      page.locator('[data-testid="progress-stage"]').filter({
        hasText: "候选发现",
      }),
    ).toHaveAttribute("data-state", "active");
  });

  test("普通用户可展开查看实际使用的专业能力", async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    const completed = workspaceTask(
      "task-capabilities",
      "completed",
      "解析合同并提取条款",
    );
    const capabilityEvent = {
      event_id: "capability-1",
      sequence: 3,
      task_id: completed.task_id,
      revision: 1,
      run_id: "run-capability",
      stage: "prepare_capabilities",
      event_type: "stage_completed",
      summary: "Pi 已准备 1 项能力：MinerU 文档解析（Tool）",
      progress: null,
      refs: {
        capabilities: [{
          name: "MinerU 文档解析",
          kind: "tool",
          version: "2.1.0",
          purpose: "解析 PDF 文档结构",
        }],
      },
      action: null,
      audience: "all",
      created_at: "2026-08-04T20:00:03Z",
    };
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [completed] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-capabilities",
      (route) => route.fulfill({
        json: workspaceDetail(completed, {
          events: [capabilityEvent],
          progress: {
            active_stage: null,
            stages: [
              { stage: "understand", status: "completed", summary: "已理解要求" },
              { stage: "inspect_sources", status: "completed", summary: "已检查来源" },
              {
                stage: "prepare_capabilities",
                status: "completed",
                summary: capabilityEvent.summary,
              },
              { stage: "execute", status: "completed", summary: "已处理数据" },
              { stage: "verify", status: "completed", summary: "验证通过" },
              { stage: "deliver", status: "completed", summary: "正式交付已发布" },
            ],
            events: [capabilityEvent],
          },
        }),
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /解析合同并提取条款/ }).click();
    await page.getByRole("button", { name: /工作记录/ }).click();
    await expect(page.getByText("Mangrove 已准备 1 项能力：MinerU 文档解析（Tool）").first())
      .toBeVisible();
    await expect(page.getByRole("button", { name: /行动记录/ }))
      .toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText("Mangrove 已准备 1 项能力：MinerU 文档解析（Tool）").last())
      .toBeVisible();
    await expect(page.getByText("Pi 已准备 1 项能力：MinerU 文档解析（Tool）"))
      .toHaveCount(0);
    await expect(page.getByText("MinerU 文档解析", { exact: true })).toBeVisible();
    await expect(page.getByText("Tool", { exact: true })).toBeVisible();
    await expect(page.getByText("v2.1.0", { exact: true })).toBeVisible();
    await expect(page.getByText("解析 PDF 文档结构", { exact: true })).toBeVisible();
  });

  test("旧候选可读取且明确说明暂不能重新验证", async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    const candidate = workspaceTask(
      "task-legacy-candidate",
      "candidate_ready",
      "旧版候选结果",
    );
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidate] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-legacy-candidate",
      (route) => route.fulfill({
        json: workspaceDetail(candidate, {
          agentic_runtime: {
            runtime_version: "pi",
            permission_profile: "standard",
            status: "candidate_ready",
            candidates: [{
              artifact_id: "candidate-legacy-json",
              filename: "历史结果.json",
              format: "json",
              sha256: "d".repeat(64),
              size_bytes: 256,
              openable: true,
              qa_checks: ["openable"],
              download_url: "/api/download/candidate-legacy-json",
              download_allowed: true,
            }],
            verification: {
              status: "failed",
              summary: "历史验证未通过",
              evidence_count: 1,
              formal_delivery_eligible: false,
              checks: [],
            },
            latest_verification_attempt: {
              attempt_id: "legacy-attempt-1",
              status: "failed",
              reason: "initial",
              ruleset_identity_status: "legacy_unversioned",
            },
            reverification_offer: null,
            reverification_unavailable_reason:
              "该历史任务缺少可证明的冻结运行信息，暂不能重新验证。",
            awaiting_publication: false,
          },
        }),
      }),
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /旧版候选结果/ }).click();

    await expect(page.getByText("历史结果.json")).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "该历史任务缺少可证明的冻结运行信息" })).toContainText(
      "该历史任务缺少可证明的冻结运行信息，暂不能重新验证。",
    );
    await expect(
      page.getByRole("button", { name: "使用最新规则重新验证" }),
    ).toHaveCount(0);
  });

  test("普通用户确认本次 Provider 外发后创建候选重验 Attempt", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await mockWorkspace(page, "light", "user");
    const candidate = workspaceTask(
      "task-reverification",
      "candidate_ready",
      "重新验证工作量结果",
    );
    const candidateDetail = workspaceDetail(candidate, {
      agentic_runtime: {
        runtime_version: "pi",
        permission_profile: "standard",
        status: "candidate_ready",
        candidates: [{
          artifact_id: "candidate-xlsx",
          filename: "工作量结果.xlsx",
          format: "xlsx",
          sha256: "a".repeat(64),
          size_bytes: 4096,
          openable: true,
          qa_checks: ["openable"],
          download_url: "/api/download/candidate-xlsx",
          download_allowed: true,
        }],
        verification: {
          status: "failed",
          summary: "旧规则错误地拒绝了文件数量。",
          evidence_count: 1,
          formal_delivery_eligible: false,
          checks: [{
            code: "artifact_count",
            passed: false,
            summary: "旧规则要求的文件数量不正确",
          }],
        },
        latest_verification_attempt: {
          attempt_id: "attempt-old",
          status: "failed",
          reason: "initial",
          ruleset_identity_status: "versioned",
        },
        reverification_offer: {
          eligible: true,
          reason: "ruleset_changed",
          blockers: [],
          ruleset_changed: true,
          ruleset_change_summary: "文件数量规则已修正",
          requires_provider: true,
          connection_id: "connection-deepseek",
          model_id: "deepseek-chat",
          egress_categories: ["目标摘要", "候选内容"],
          egress_summary: "目标摘要和候选内容将发送给模型进行语义核对",
        },
        awaiting_publication: false,
      },
    });
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidate] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-reverification",
      (route) => route.fulfill({ json: candidateDetail }),
    );
    let requestPayload: Record<string, unknown> | null = null;
    let requestIdempotencyKey: string | undefined;
    await page.route(
      "**/api/semantic-workspace/tasks/task-reverification/candidate-verifications",
      async (route) => {
        requestPayload = await route.request().postDataJSON();
        requestIdempotencyKey = route.request().headers()["idempotency-key"];
        if (candidateDetail.agentic_runtime) {
          candidateDetail.agentic_runtime.latest_verification_attempt = {
            attempt_id: "attempt-new",
            status: "requested",
            reason: "ruleset_changed",
            ruleset_identity_status: "versioned",
          };
          candidateDetail.agentic_runtime.reverification_offer = {
            ...candidateDetail.agentic_runtime.reverification_offer!,
            eligible: false,
            blockers: ["verification_attempt_active"],
          };
        }
        await route.fulfill({
          status: 202,
          json: {
            attempt_id: "attempt-new",
            task_id: candidate.task_id,
            revision: 1,
            run_id: "run-1",
            previous_attempt_id: "attempt-old",
            status: "requested",
            task: candidateDetail,
          },
        });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: "任务列表开关", exact: true }).click();
    await page.getByRole("button", { name: /重新验证工作量结果/ }).click();
    const reverifyTrigger = page.getByRole("button", { name: "使用最新规则重新验证" });
    await reverifyTrigger.click();

    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("不会重新执行整个任务或生成新文件");
    await expect(dialog).toContainText("工作量结果.xlsx");
    await expect(dialog).toContainText("文件数量规则已修正");
    await expect(dialog).toContainText("deepseek-chat");
    await expect(dialog).toContainText("目标摘要和候选内容");
    await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
    await expect(dialog.getByRole("button", { name: "开始重新验证" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(reverifyTrigger).toBeFocused();
    await reverifyTrigger.click();
    await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
    if (process.env.MANGROVE_CV08_PROVIDER_SCREENSHOT) {
      await page.screenshot({
        path: process.env.MANGROVE_CV08_PROVIDER_SCREENSHOT,
        fullPage: false,
      });
    }
    const dialogBounds = await dialog.boundingBox();
    const cancelBounds = await dialog.getByRole("button", { name: "取消" }).boundingBox();
    expect(dialogBounds?.height).toBeLessThanOrEqual(760);
    expect(cancelBounds?.y).toBeGreaterThanOrEqual(0);
    expect((cancelBounds?.y ?? 0) + (cancelBounds?.height ?? 0)).toBeLessThanOrEqual(844);

    // 1280×900 的浏览器在 200% 缩放下提供 640×450 CSS px 的布局视口。
    await page.setViewportSize({ width: 640, height: 450 });
    await expect(dialog.getByRole("button", { name: "取消" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "开始重新验证" })).toBeVisible();
    const dialogDoesNotOverflowHorizontally = await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    );
    expect(dialogDoesNotOverflowHorizontally).toBe(true);

    await dialog.getByRole("checkbox", {
      name: "我确认本次会向外部模型发送上述内容，并可能产生费用",
    }).check();
    await dialog.getByRole("button", { name: "开始重新验证" }).click();

    await expect.poll(() => requestPayload).toEqual({
      expected_revision: 1,
      expected_previous_attempt_id: "attempt-old",
      external_api_confirmed: true,
      accept_duplicate_provider_cost: false,
    });
    expect(requestIdempotencyKey).toMatch(/^reverify-/);
    await expect(page.getByRole("status").filter({ hasText: "重验请求已受理" })).toContainText("重验请求已受理");
  });

  test("旧规则身份未知的候选可双确认建立当前验证基线", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockWorkspace(page, "light", "user");
    const candidate = workspaceTask(
      "task-legacy-rebaseline",
      "candidate_ready",
      "旧候选建立验证基线",
    );
    const candidateDetail = workspaceDetail(candidate, {
      agentic_runtime: {
        runtime_version: "pi",
        permission_profile: "standard",
        status: "candidate_ready",
        run_id: "pi-run-legacy",
        candidates: [{
          artifact_id: "candidate-legacy-csv",
          filename: "技术指标.csv",
          format: "csv",
          sha256: "d".repeat(64),
          size_bytes: 4096,
          openable: true,
          qa_checks: ["openable"],
          download_url: "/api/download/candidate-legacy-csv",
          download_allowed: true,
        }],
        verification: {
          status: "failed",
          summary: "旧验证规则身份无法证明",
          evidence_count: 88,
          formal_delivery_eligible: false,
          checks: [],
        },
        latest_verification_attempt: {
          attempt_id: "attempt-legacy-old",
          status: "failed",
          reason: "initial",
          ruleset_identity_status: "legacy_unversioned",
        },
        reverification_offer: {
          eligible: true,
          reason: "legacy_rebaseline",
          blockers: [],
          ruleset_changed: null,
          ruleset_change_summary: "旧规则身份未知，将以当前规则建立可信基线",
          requires_provider: true,
          connection_id: "connection-deepseek",
          model_id: "deepseek-chat",
          candidate_set_hash: "a".repeat(64),
          target_ruleset_hash: "b".repeat(64),
          egress_categories: ["任务目标", "候选内容", "来源证据"],
          egress_summary: "任务目标、候选内容和来源证据将发送给模型",
        },
        awaiting_publication: false,
      },
    });
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidate] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-legacy-rebaseline",
      (route) => route.fulfill({ json: candidateDetail }),
    );
    let requestPayload: Record<string, unknown> | null = null;
    await page.route(
      "**/api/semantic-workspace/tasks/task-legacy-rebaseline/candidate-verifications",
      async (route) => {
        requestPayload = await route.request().postDataJSON();
        await route.fulfill({
          status: 202,
          json: {
            attempt_id: "attempt-legacy-new",
            task_id: candidate.task_id,
            revision: 1,
            run_id: "pi-run-legacy",
            previous_attempt_id: "attempt-legacy-old",
            status: "requested",
            task: candidateDetail,
          },
        });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: "任务列表开关", exact: true }).click();
    await page.getByRole("button", { name: /旧候选建立验证基线/ }).click();
    await page.getByRole("button", { name: "建立当前验证基线" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("旧验证规则身份无法证明");
    await expect(dialog).toContainText(`Target Ruleset SHA-256：${"b".repeat(64)}`);
    await expect(dialog).toContainText("不会重跑任务、不会修改候选");
    await expect(dialog).toContainText("通过后仍需单独发布");
    const submit = dialog.getByRole("button", { name: "开始建立验证基线" });
    await expect(submit).toBeDisabled();
    await dialog.getByRole("checkbox", {
      name: "我理解旧验证规则身份无法证明，本次将使用当前规则建立新的可信基线",
    }).check();
    await expect(submit).toBeDisabled();
    await dialog.getByRole("checkbox", {
      name: "我确认本次会向外部模型发送上述内容，并可能产生费用",
    }).check();
    await expect(submit).toBeEnabled();
    await submit.click();

    await expect.poll(() => requestPayload).toEqual({
      expected_revision: 1,
      expected_previous_attempt_id: "attempt-legacy-old",
      external_api_confirmed: true,
      accept_duplicate_provider_cost: false,
      expected_candidate_set_hash: "a".repeat(64),
      expected_target_ruleset_hash: "b".repeat(64),
      legacy_ruleset_unknown_acknowledged: true,
      authorization_text_version: "legacy-rebaseline-v1",
    });
  });

  test("历史候选由 Owner 双确认恢复窄重验权威", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockWorkspace(page, "dark", "user");
    const candidate = workspaceTask(
      "task-historical-reverification",
      "candidate_ready",
      "历史候选重验",
    );
    const candidateDetail = workspaceDetail(candidate, {
      agentic_runtime: {
        runtime_version: "pi",
        permission_profile: "standard",
        status: "candidate_ready",
        run_id: "pi-run-old",
        candidates: [{
          artifact_id: "candidate-historical-csv",
          filename: "历史结果.csv",
          format: "csv",
          sha256: "d".repeat(64),
          size_bytes: 4096,
          openable: true,
          qa_checks: ["openable"],
          download_url: "/api/download/candidate-historical-csv",
          download_allowed: true,
        }],
        verification: {
          status: "inconclusive",
          summary: "文件和来源证据有效，但语义判断未形成可靠结论",
          evidence_count: 88,
          formal_delivery_eligible: false,
          checks: [],
        },
        latest_verification_attempt: {
          attempt_id: "attempt-historical-old",
          status: "inconclusive",
          reason: "semantic_inconclusive",
          ruleset_identity_status: "legacy_unversioned",
        },
        reverification_offer: {
          eligible: false,
          reason: null,
          blockers: ["historical_authority_recovery_required"],
          ruleset_changed: null,
          ruleset_change_summary: "当前验证规则身份可冻结",
          requires_provider: true,
          connection_id: "connection-deepseek",
          model_id: "deepseek-v4-flash",
          candidate_count: 1,
          candidate_formats: ["csv"],
          egress_categories: ["任务目标", "候选预览", "来源证据"],
          egress_summary: "任务目标、候选预览和来源证据将发送给模型",
          historical_authority_recovery: {
            expected_evidence_hash: "e".repeat(64),
            purpose: "semantic_inconclusive_reverification",
            owner_id: "user-a",
            task_id: "task-historical-reverification",
            revision: 1,
            run_id: "pi-run-old",
            candidate_set_hash: "f".repeat(64),
            explanation: "系统不会补造旧 RuntimeAssignment；只记录当前重验确认。",
          },
        },
        awaiting_publication: false,
      },
    });
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidate] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-historical-reverification",
      (route) => route.fulfill({ json: candidateDetail }),
    );
    let requestPayload: Record<string, unknown> | null = null;
    await page.route(
      "**/api/semantic-workspace/tasks/task-historical-reverification/candidate-verifications",
      async (route) => {
        requestPayload = await route.request().postDataJSON();
        if (candidateDetail.agentic_runtime) {
          candidateDetail.agentic_runtime.latest_verification_attempt = {
            attempt_id: "attempt-historical-new",
            status: "requested",
            reason: "semantic_inconclusive",
            ruleset_identity_status: "versioned",
          };
        }
        await route.fulfill({
          status: 202,
          json: {
            attempt_id: "attempt-historical-new",
            task_id: candidate.task_id,
            revision: 1,
            run_id: "pi-run-old",
            previous_attempt_id: "attempt-historical-old",
            status: "requested",
            task: candidateDetail,
          },
        });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: "任务列表开关", exact: true }).click();
    await page.getByRole("button", { name: /历史候选重验/ }).click();
    const trigger = page.getByRole("button", { name: "恢复并重新验证候选" });
    await trigger.click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
    await expect(dialog).toContainText("不会补造旧 RuntimeAssignment");
    await expect(dialog).toContainText("Owner：user-a");
    await expect(dialog).toContainText("Run：pi-run-old");
    await expect(dialog).toContainText("1 个候选 · CSV");
    const accessibility = await new AxeBuilder({ page })
      .include('[role="alertdialog"]')
      .analyze();
    expect(accessibility.violations).toEqual([]);
    const submit = dialog.getByRole("button", { name: "恢复并开始语义验证" });
    await expect(submit).toBeDisabled();
    await dialog.getByRole("checkbox", {
      name: "我确认旧任务没有可证明的 RuntimeAssignment，系统不会补造这段历史",
    }).check();
    await dialog.getByRole("checkbox", {
      name: "我确认本授权只用于当前候选重验，不重跑 Pi、不创建版本、不发布",
    }).check();
    await expect(submit).toBeDisabled();
    await page.setViewportSize({ width: 640, height: 450 });
    await expect(dialog.getByRole("button", { name: "取消" })).toBeVisible();
    await expect(submit).toBeVisible();
    expect(await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    )).toBe(true);
    await dialog.getByRole("checkbox", {
      name: "我确认本次会向外部模型发送上述内容，并可能产生费用",
    }).check();
    await submit.click();

    await expect.poll(() => requestPayload).toEqual({
      expected_revision: 1,
      expected_previous_attempt_id: "attempt-historical-old",
      external_api_confirmed: true,
      accept_duplicate_provider_cost: false,
      historical_authority_recovery: {
        expected_evidence_hash: "e".repeat(64),
        acknowledge_no_historical_assignment: true,
        acknowledge_reverification_only: true,
      },
    });
    await expect(page.getByRole("status").filter({ hasText: "重验请求已受理" })).toContainText("重验请求已受理");
  });

  test("候选重验状态刷新后可恢复，并用独立幂等键显式发布", async ({ page }) => {
    await mockWorkspace(page, "dark", "user");
    const candidate = workspaceTask(
      "task-reverification-state",
      "candidate_ready",
      "候选重验状态",
    );
    let attemptStatus: "requested" | "outcome_unknown" | "passed" = "requested";
    const detailForStatus = () => workspaceDetail(candidate, {
      agentic_runtime: {
        runtime_version: "pi",
        permission_profile: "standard",
        status: "candidate_ready",
        candidates: [{
          artifact_id: "candidate-csv",
          filename: "结果.csv",
          format: "csv",
          sha256: "b".repeat(64),
          size_bytes: 128,
          openable: true,
          qa_checks: ["openable"],
          download_url: "/api/download/candidate-csv",
          download_allowed: true,
        }],
        verification: {
          status: attemptStatus === "passed" ? "passed" : "failed",
          summary: "候选文件保持不变",
          evidence_count: 1,
          formal_delivery_eligible: attemptStatus === "passed",
          checks: [],
        },
        latest_verification_attempt: {
          attempt_id: "attempt-state",
          status: attemptStatus,
          reason: "ruleset_changed",
          ruleset_identity_status: "versioned",
        },
        reverification_offer: {
          eligible: false,
          reason: null,
          blockers: attemptStatus === "passed" ? [] : ["verification_attempt_active"],
          ruleset_changed: false,
          ruleset_change_summary: "规则身份未变化",
          requires_provider: true,
          connection_id: "connection-deepseek",
          model_id: "deepseek-chat",
          egress_categories: ["候选内容"],
          egress_summary: "候选内容将发送给模型",
        },
        awaiting_publication: attemptStatus === "passed",
      },
    });
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidate] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-reverification-state",
      (route) => route.fulfill({ json: detailForStatus() }),
    );
    let publishPayload: Record<string, unknown> | null = null;
    let publishIdempotencyKey: string | undefined;
    await page.route(
      "**/api/semantic-workspace/tasks/task-reverification-state/candidate-verifications/attempt-state/publish",
      async (route) => {
        publishPayload = await route.request().postDataJSON();
        publishIdempotencyKey = route.request().headers()["idempotency-key"];
        await route.fulfill({
          json: {
            delivery_id: "delivery-state",
            run_id: "run-state",
            plan_id: "plan-state",
            status: "delivery_published",
            outputs: [],
            requested_formats: ["csv"],
            created_at: "2026-08-24T12:00:00Z",
          },
        });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /候选重验状态/ }).click();
    await expect(page.getByRole("status").filter({ hasText: "重验请求已受理" })).toContainText("重验请求已受理");

    attemptStatus = "outcome_unknown";
    await page.reload();
    await page.getByRole("button", { name: /候选重验状态/ }).click();
    await expect(page.getByRole("alert")).toContainText("已停止自动重试");
    await expect(page.getByRole("button", { name: "使用最新规则重新验证" })).toHaveCount(0);

    attemptStatus = "passed";
    await page.reload();
    await page.getByRole("button", { name: /候选重验状态/ }).click();
    await expect(page.getByRole("status").filter({ hasText: "还不是正式交付" })).toContainText("还不是正式交付");
    await page.getByRole("button", { name: "发布正式结果" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("不会重新运行任务或模型");
    await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
    if (process.env.MANGROVE_CV08_PUBLISH_SCREENSHOT) {
      await page.screenshot({
        path: process.env.MANGROVE_CV08_PUBLISH_SCREENSHOT,
        fullPage: false,
      });
    }
    const scan = await new AxeBuilder({ page }).include('[role="alertdialog"]').analyze();
    expect(
      scan.violations.filter(
        (item) => item.impact === "serious" || item.impact === "critical",
      ),
    ).toEqual([]);
    await dialog.getByRole("button", { name: "确认发布" }).click();

    await expect.poll(() => publishPayload).toEqual({ expected_revision: 1 });
    expect(publishIdempotencyKey).toMatch(/^publish-/);
    await expect(page.getByText("正式结果已发布")).toBeVisible();
  });

  test("本地重验不要求外发确认，服务端错误保留对话框并给出恢复建议", async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    const candidate = workspaceTask(
      "task-reverification-errors",
      "candidate_ready",
      "本地候选重验",
    );
    const detail = workspaceDetail(candidate, {
      agentic_runtime: {
        runtime_version: "pi",
        permission_profile: "standard",
        status: "candidate_ready",
        candidates: [{
          artifact_id: "candidate-json",
          filename: "结果.json",
          format: "json",
          sha256: "c".repeat(64),
          size_bytes: 256,
          openable: true,
          qa_checks: ["openable"],
          download_url: "/api/download/candidate-json",
          download_allowed: true,
        }],
        verification: {
          status: "failed",
          summary: "旧规则未通过",
          evidence_count: 1,
          formal_delivery_eligible: false,
          checks: [],
        },
        latest_verification_attempt: {
          attempt_id: "attempt-local-old",
          status: "failed",
          reason: "initial",
          ruleset_identity_status: "versioned",
        },
        reverification_offer: {
          eligible: true,
          reason: "ruleset_changed",
          blockers: [],
          ruleset_changed: true,
          ruleset_change_summary: "本地文件规则已修正",
          requires_provider: false,
          connection_id: null,
          model_id: null,
          egress_categories: [],
          egress_summary: "本次不外发",
        },
        awaiting_publication: false,
      },
    });
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [candidate] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-reverification-errors",
      (route) => route.fulfill({ json: detail }),
    );
    let responseStatus = 403;
    const retryKeys: string[] = [];
    await page.route(
      "**/api/semantic-workspace/tasks/task-reverification-errors/candidate-verifications",
      (route) => {
        retryKeys.push(route.request().headers()["idempotency-key"] ?? "");
        return route.fulfill({
          status: responseStatus,
          contentType: "application/json",
          json: { detail: "rejected" },
        });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /本地候选重验/ }).click();
    await page.getByRole("button", { name: "使用最新规则重新验证" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("本次不外发");
    await expect(dialog.getByRole("checkbox")).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "开始重新验证" })).toBeEnabled();

    const expected = [
      [403, "请使用任务所有者账号"],
      [409, "请刷新任务并核对最新状态"],
      [422, "请重新核对恢复证据和外发内容"],
      [503, "服务暂时不可用"],
    ] as const;
    for (const [status, message] of expected) {
      responseStatus = status;
      await dialog.getByRole("button", { name: "开始重新验证" }).click();
      await expect(dialog.getByRole("alert")).toContainText(message);
      await expect(dialog).toBeVisible();
    }
    expect(new Set(retryKeys)).toEqual(new Set([retryKeys[0]]));
    expect(retryKeys[0]).toMatch(/^reverify-/);
  });

  for (const theme of ["light", "dark"] as const) {
    test(`${theme} 主题没有严重或致命的可访问性问题`, async ({ page }) => {
      await mockWorkspace(page, theme);
      await page.goto("/data-prep");
      await expect(page.getByRole("heading", { name: "想处理什么资料？" }))
        .toBeVisible();

      const scan = await new AxeBuilder({ page }).analyze();
      const blocking = scan.violations.filter(
        (item) => item.impact === "serious" || item.impact === "critical",
      );
      expect(blocking).toEqual([]);
    });
  }
});


test.describe("#127 统一工作台", () => {
  test("空任务直接选模，配置返回保留要求与附件", async ({ page }) => {
    await mockWorkspace(page);
    await page.route("**/api/model-connections/presets", route => route.fulfill({ json: { items: [] } }));
    await page.goto("/data-prep");
    await expect(page.getByRole("radio", { name: "公开网页" })).toHaveCount(0);
    await expect(page.getByLabel("模型连接", { exact: true })).toBeVisible();
    await page.getByRole("textbox").first().fill("核对这份工作量表");
    await page.locator('input[type="file"]').setInputFiles({ name: "workload.csv", mimeType: "text/csv", buffer: Buffer.from("姓名,工作量\n张三,5", "utf-8") });
    await page.getByRole("button", { name: "添加模型", exact: true }).click();
    await expect(page.getByRole("heading", { name: "模型设置", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "返回当前任务", exact: true }).click();
    await expect(page.getByRole("textbox").first()).toHaveValue("核对这份工作量表");
    await expect(page.getByText("workload.csv").first()).toBeVisible();
    await expect(page).toHaveURL(/data-prep/);
  });

  test("窄屏文件侧览可返回输入且不横向溢出", async ({ page }) => {
    await mockWorkspace(page);
    await page.setViewportSize({ width: 390, height: 640 });
    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles({ name: "workload.csv", mimeType: "text/csv", buffer: Buffer.from("姓名,工作量\n张三,5", "utf-8") });
    await expect(page.getByRole("button", { name: "关闭原文件预览" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.getByRole("button", { name: "关闭原文件预览" }).click();
    await expect(page.getByRole("textbox").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "开始执行", exact: true })).toBeInViewport();
  });
});


test.describe("#127 输入与导航边界", () => {
  const modelConnection = {
    connection_id: "conn-current", owner_scope: "user_personal", preset_id: "deepseek",
    display_name: "我的连接", model: "model-a", api_format: "openai_chat_completions",
    locality: "public_external", status: "verified", default_model: "model-a",
    models: ["model-a", "model-b"].map(id => ({ model_id: id, display_name: id, enabled: true, status: "available" })),
  };

  test("配置返回保留非默认模型；失效后明确重选", async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    let current = modelConnection;
    await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [current] } }));
    await page.route("**/api/model-connections/presets", route => route.fulfill({ json: { items: [] } }));
    await page.goto("/data-prep");
    await page.getByLabel("本任务模型").selectOption("model-b");
    await page.getByRole("button", { name: "添加模型", exact: true }).click();
    await page.getByRole("button", { name: "返回当前任务", exact: true }).click();
    await expect(page.getByLabel("本任务模型")).toHaveValue("model-b");
    current = { ...modelConnection, models: modelConnection.models.filter(model => model.model_id === "model-a") };
    await page.getByRole("button", { name: "添加模型", exact: true }).click();
    await page.getByRole("button", { name: "返回当前任务", exact: true }).click();
    await expect(page.getByLabel("本任务模型")).toHaveValue("");
    await expect(page.getByText("原先选择的模型已不可用，请重新选择；不会自动替换模型。")).toBeVisible();
    await page.getByLabel("本任务模型").selectOption("model-a");
    await expect(page.getByText("外发确认：我的连接 · model-a")).toBeVisible();
  });

  test("输入网址传递要求及已选模型，读取前不自动外发", async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [modelConnection] } }));
    let acquisitions = 0;
    await page.route("**/api/semantic-workspace/source-acquisitions", route => { acquisitions++; return route.fulfill({ json: sourceAttempt("succeeded") }); });
    await page.goto("/data-prep");
    await page.getByLabel("本任务模型").selectOption("model-b");
    const prompt = "总结 https://example.com/article 的公开说明";
    await page.getByRole("textbox", { name: "任务要求", exact: true }).fill(prompt);
    await page.getByRole("textbox", { name: "任务要求", exact: true }).press("Enter");
    await expect(page.getByLabel("精确网址")).toHaveValue("https://example.com/article");
    expect(acquisitions).toBe(0);
    await page.getByRole("button", { name: "获取网页", exact: true }).click();
    await page.getByRole("button", { name: "添加到当前任务", exact: true }).click();
    await expect(page.getByLabel("任务要求", { exact: true })).toHaveValue(prompt);
    await expect(page.getByLabel("模型连接", { exact: true })).toHaveValue("conn-current");
    await expect(page.getByRole("combobox", { name: "本任务模型", exact: true })).toHaveValue("model-b");
    await expect(page.getByRole("checkbox", { name: /我确认将上述内容/ })).not.toBeChecked();
    await page.getByLabel("任务要求", { exact: true }).fill(`${prompt}，提取三条结论`);
    await page.getByRole("combobox", { name: "本任务模型", exact: true }).selectOption("model-a");

    await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toHaveValue(`${prompt}，提取三条结论`);
    await expect(page.getByLabel("本任务模型")).toHaveValue("model-a");
    await page.getByRole("button", { name: "公开网页", exact: true }).click();
    await page.getByRole("button", { name: "返回当前资料", exact: true }).click();
    await expect(page.getByLabel("任务要求", { exact: true })).toHaveValue(`${prompt}，提取三条结论`);
    await expect(page.getByRole("combobox", { name: "本任务模型", exact: true })).toHaveValue("model-a");
    expect(acquisitions).toBe(1);
    await page.getByRole("button", { name: "检查上下文草案", exact: true }).click();
    const consent = page.getByRole("checkbox", { name: /我确认将上述内容/ });
    await consent.check();
    await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "添加模型", exact: true }).click();
    await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [{ ...modelConnection, models: modelConnection.models.filter(model => model.model_id === "model-b") }] } }));
    await page.getByRole("button", { name: "返回当前任务" }).click();
    await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toBeFocused();
    await expect(page.getByRole("combobox", { name: "本任务模型", exact: true })).toHaveValue("");
    await consent.check();
    await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeDisabled();
    await page.getByRole("combobox", { name: "本任务模型", exact: true }).selectOption("model-b");
    await consent.check();
    await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeEnabled();
  });

  test("上传迟到不切走手机输入；IME不发送，普通Enter只提交一次", async ({ page }) => {
    await mockWorkspace(page);
    await page.setViewportSize({ width: 390, height: 640 });
    const uploadGate = responseBarrier();
    const started = responseBarrier();
    await page.route("**/api/data-sources/uploads", async route => {
      started.release(); await uploadGate.promise;
      await route.fulfill({ json: { upload_id: "late-upload", original_name: "workload.csv", media_type: "text/csv", size_bytes: 64, sha256: "0".repeat(64) } });
    });
    let calls = 0;
    await page.route("**/api/semantic-workspace/tasks", route => {
      calls++;
      return route.fulfill({ status: 503, json: { detail: "合成失败" } });
    });
    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles({ name: "workload.csv", mimeType: "text/csv", buffer: Buffer.from("name,value\na,1", "utf-8") });
    await started.promise;
    const input = page.getByRole("textbox", { name: "任务要求", exact: true });
    await input.fill("筛选工作量");
    uploadGate.release();
    await expect(page.getByRole("button", { name: "开始执行", exact: true })).toBeEnabled();
    await expect(input).toBeFocused();
    await expect(page.getByRole("button", { name: "关闭原文件预览" })).toHaveCount(0);
    await input.dispatchEvent("keydown", { key: "Enter", isComposing: true, keyCode: 229 });
    expect(calls).toBe(0);
    await input.press("Shift+Enter");
    await expect(input).toHaveValue("筛选工作量\n");
    await input.press("Enter");
    await expect.poll(() => calls).toBe(1);
    await expect(input).toHaveValue("筛选工作量\n");
  });

  for (const explicit of [false, true]) test(`默认偏好迟到：用户已选模型=${explicit}`, async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [modelConnection] } }));
    const preference = responseBarrier();
    await page.route("**/api/model-connections/preferences/default", async route => {
      await preference.promise;
      await route.fulfill({ json: { preference: { available: true, connection_id: "conn-current", model_id: "model-b" } } });
    });
    await page.goto("/data-prep");
    const picker = page.getByRole("combobox", { name: "本任务模型", exact: true });
    await expect(picker).toHaveValue("model-a");
    if (explicit) {
      await picker.selectOption("model-b");
      await picker.selectOption("model-a");
    }
    const received = page.waitForResponse("**/api/model-connections/preferences/default");
    preference.release();
    await received;
    await expect(picker).toHaveValue(explicit ? "model-a" : "model-b");
  });

  test("目录前后只编辑要求，不阻挡迟到默认偏好", async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    const catalog = responseBarrier();
    const preference = responseBarrier();
    await page.route("**/api/model-connections", async route => {
      await catalog.promise;
      await route.fulfill({ json: { items: [modelConnection] } });
    });
    await page.route("**/api/model-connections/preferences/default", async route => {
      await preference.promise;
      await route.fulfill({ json: { preference: { available: true, connection_id: "conn-current", model_id: "model-b" } } });
    });
    await page.goto("/data-prep");
    const input = page.getByRole("textbox", { name: "任务要求", exact: true });
    await input.fill("初稿");
    catalog.release();
    const picker = page.getByRole("combobox", { name: "本任务模型", exact: true });
    await expect(picker).toHaveValue("model-a");
    await input.fill("继续补充要求");
    const received = page.waitForResponse("**/api/model-connections/preferences/default");
    preference.release();
    await received;
    await expect(picker).toHaveValue("model-b");
    await expect(input).toHaveValue("继续补充要求");
  });

  test("失败附件不能被Enter绕过；修正前不创建部分任务", async ({ page }) => {
    await mockWorkspace(page);
    let uploads = 0;
    let submissions = 0;
    await page.route("**/api/data-sources/uploads", route => {
      uploads++;
      return uploads === 1
        ? route.fulfill({ json: { upload_id: "good", original_name: "good.csv", media_type: "text/csv", size_bytes: 12, sha256: "0".repeat(64) } })
        : route.fulfill({ status: 422, json: { detail: "合成失败附件" } });
    });
    await page.route("**/api/semantic-workspace/tasks", route => { submissions++; return route.fulfill({ status: 503, json: { detail: "不应提交" } }); });
    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles([
      { name: "good.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2", "utf-8") },
      { name: "bad.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n3,4", "utf-8") },
    ]);
    await expect(page.getByText("合成失败附件", { exact: true })).toBeVisible();
    await page.getByRole("textbox", { name: "任务要求", exact: true }).fill("合并这两个文件");
    await page.getByRole("textbox", { name: "任务要求", exact: true }).press("Enter");
    await expect(page.getByRole("button", { name: "开始执行", exact: true })).toBeDisabled();
    expect(submissions).toBe(0);
  });

  for (const theme of ["light", "dark"] as const) for (const width of [390, 1440]) {
    test(`导航焦点和视觉 ${theme} ${width}`, async ({ page }, testInfo) => {
      await mockWorkspace(page, theme);
      await page.setViewportSize({ width, height: 640 });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto("/data-prep");
      await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toBeVisible();
      await page.getByRole("button", { name: "打开导航", exact: true }).click();
      await expect(page.getByRole("dialog", { name: "全局导航" })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("button", { name: "打开导航", exact: true })).toBeFocused();
      if (width === 390) {
        await page.getByRole("button", { name: "任务列表开关", exact: true }).click();
        await expect(page.getByRole("dialog", { name: "任务列表", exact: true })).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(page.getByRole("button", { name: "任务列表开关", exact: true })).toBeFocused();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const accessibility = await new AxeBuilder({ page }).analyze();
      expect(accessibility.violations).toEqual([]);
      await page.screenshot({ path: `../.artifacts/issue-127/screenshots/${theme}-${width}.png` });
      await testInfo.attach("workbench", { body: await page.screenshot(), contentType: "image/png" });
    });
  }
});


test("统一图片草稿保留混合附件，移除上传会中止且迟到不复活", async ({ page }) => {
  await mockWorkspace(page);
  await page.addInitScript(() => {
    const original = XMLHttpRequest.prototype.abort;
    (window as unknown as { uploadAborts: number }).uploadAborts = 0;
    XMLHttpRequest.prototype.abort = function () {
      (window as unknown as { uploadAborts: number }).uploadAborts++;
      return original.call(this);
    };
  });
  const originalImage = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAABQAAAAeCAIAAACjcKk8AAAANElEQVR4nO3LoQEAMAgDwTT7z4nuCJXFPpacv1O3NOXxVDJmXr5kyDR0yZBp6JIh06Dl+QFlNgL4Ku76ZQAAAABJRU5ErkJggg==", "base64");
  let releaseUpload!: () => void;
  const gate = new Promise<void>(resolve => { releaseUpload = resolve; });
  let waiting = false;
  let documentReads = 0;
  await page.route("**/api/data-sources/uploads", async route => {
    const slow = route.request().postDataBuffer()?.includes(Buffer.from("slow.png"));
    if (slow) { waiting = true; await gate; }
    await route.fulfill({ json: { upload_id: slow ? "slow-image" : "good-image", original_name: slow ? "slow.png" : "good.png", media_type: "image/png", size_bytes: originalImage.length, sha256: "0".repeat(64) } });
  });
  await page.route("**/api/data-sources/uploads/good-image/content", route => route.fulfill({ contentType: "image/png", body: originalImage }));
  await page.route("**/api/data-sources/uploads/good-image/document-preview", route => { documentReads++; return route.abort(); });
  await page.goto("/data-prep");
  await page.locator('input[type="file"]').setInputFiles({ name: "good.png", mimeType: "image/png", buffer: originalImage });
  await expect(page.getByRole("img", { name: "good.png原件" })).toBeVisible();
  expect(documentReads).toBe(0);
  await page.getByRole("textbox", { name: "任务要求", exact: true }).fill("核对原件内容");
  await page.locator('input[type="file"]').setInputFiles({ name: "slow.png", mimeType: "image/png", buffer: originalImage });
  await expect.poll(() => waiting).toBe(true);
  await page.getByRole("button", { name: "移除 slow.png", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { uploadAborts: number }).uploadAborts)).toBe(1);
  releaseUpload();
  await expect(page.getByRole("button", { name: "移除 slow.png", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "移除 good.png", exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toHaveValue("核对原件内容");
  await page.route("**/api/data-sources/uploads", route => route.fulfill({ json: { upload_id: "good-table", original_name: "good.csv", media_type: "text/csv", size_bytes: 8, sha256: "0".repeat(64) } }));
  await page.locator('input[type="file"]').setInputFiles({ name: "good.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2") });
  await expect(page.getByRole("button", { name: "移除 good.csv", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "移除 good.png", exact: true })).toBeVisible();
  await page.route("**/api/data-sources/uploads", route => route.fulfill({ status: 422, json: { detail: "合成图片解码失败" } }));
  await page.locator('input[type="file"]').setInputFiles({ name: "bad.jpg", mimeType: "image/jpeg", buffer: Buffer.from("损坏") });
  await expect(page.getByText("合成图片解码失败", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始执行", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "移除 good.png", exact: true })).toBeVisible();
  let releaseLeaving!: () => void;
  const leavingGate = new Promise<void>(resolve => { releaseLeaving = resolve; });
  let leavingStarted = false;
  await page.route("**/api/data-sources/uploads", async route => { leavingStarted = true; await leavingGate; await route.abort(); });
  await page.locator('input[type="file"]').setInputFiles({ name: "leaving.png", mimeType: "image/png", buffer: originalImage });
  await expect.poll(() => leavingStarted).toBe(true);
  await page.getByRole("button", { name: "回收站", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { uploadAborts: number }).uploadAborts)).toBe(2);
  releaseLeaving();
});


for (const sample of [{"extension": "jpg", "mime": "image/jpeg", "base64": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAeABQDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD2yiiioMwooooAKKKKACiiigD/2Q=="}, {"extension": "jpeg", "mime": "image/jpeg", "base64": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAeABQDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD2yiiioMwooooAKKKKACiiigD/2Q=="}, {"extension": "webp", "mime": "image/webp", "base64": "UklGRjYAAABXRUJQVlA4ICoAAADQAgCdASoUAB4APm00lUekIyIhKAgAgA2JaQAAPaOgAP75iK71fpZgAAA="}]) test(`统一入口可解码 ${sample.extension} 原件`, async ({ page }) => {
  await mockWorkspace(page);
  const body = Buffer.from(sample.base64, "base64");
  const name = `实际图片.${sample.extension}`;
  await page.route("**/api/data-sources/uploads", route => route.fulfill({ json: {
    upload_id: "image-format", original_name: name, media_type: sample.mime, size_bytes: body.length, sha256: "0".repeat(64),
  } }));
  await page.route("**/api/data-sources/uploads/image-format/content", route => route.fulfill({ contentType: sample.mime, body }));
  await page.goto("/data-prep");
  await page.locator('input[type="file"]').setInputFiles({ name, mimeType: sample.mime, buffer: body });
  const image = page.getByRole("img", { name: `${name}原件` });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(node => (node as HTMLImageElement).naturalWidth)).toBe(20);
  await expect(page.getByText("此预览仅显示原件，不执行文字识别。", { exact: true })).toBeVisible();
});


test.describe("#133 获准工具复用", () => {
  const need = { purpose: "筛选表格记录", operations: ["filter"], input_formats: ["csv", "xlsx"], output_formats: ["csv", "xlsx"] };
  const capability = { pack_id: "table-tool", version: "1.0.0", digest: `sha256:${"a".repeat(64)}`, name: "表格筛选工具", kind: "tool", purpose: need.purpose, scope: "platform", reuse_need: need };
  const match = { ref: { pack_id: "approved-table-tool", version: "2.0.0", digest: `sha256:${"b".repeat(64)}` }, compatibility: { operations: ["filter"], input_formats: ["csv"], output_formats: ["xlsx"] }, authorization: "freeze_gate_passed", health: "not_checked", license: "MIT", source_provenance: ["https://github.com/example/table-tool"] };
  async function prepare(page: Page) {
    await mockWorkspace(page);
    await page.route("**/api/semantic-workspace/capabilities", route => route.fulfill({ json: { enabled: true, items: [capability] } }));
    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles({ name: "workload.csv", mimeType: "text/csv", buffer: Buffer.from("姓名,工作量\n张三,5\n", "utf-8") });
    await expect(page.getByText("已上传，等待执行")).toBeVisible();
    await page.getByRole("textbox", { name: "任务要求" }).fill("保留符合条件的记录");
    await page.getByRole("button", { name: "更多", exact: true }).click();
  }

  test("连续任务复用精确版本，当前格式覆盖模板且不发现外部工具", async ({ page }, testInfo) => {
    await prepare(page);
    const resolutions: Record<string, unknown>[] = [];
    const submissions: Record<string, unknown>[] = [];
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/api/semantic-workspace/capabilities/resolve", route => {
      resolutions.push(route.request().postDataJSON());
      return route.fulfill({ json: { matches: [match], gaps: [] } });
    });
    const created = workspaceTask("reused-task", "candidate_ready", "复用工具任务");
    await page.route("**/api/semantic-workspace/tasks", async route => {
      if (route.request().method() !== "POST") return route.fallback();
      submissions.push(route.request().postDataJSON());
      await route.fulfill({ status: 202, json: created });
    });
    await page.route("**/api/semantic-workspace/tasks/reused-task", route => route.fulfill({ json: workspaceDetail(created) }));
    for (let attempt = 0; attempt < 2; attempt++) {
      if (attempt) {
        await page.goto("/data-prep");
        await page.locator('input[type="file"]').setInputFiles({ name: "workload.csv", mimeType: "text/csv", buffer: Buffer.from("姓名,工作量\n李四,8\n", "utf-8") });
        await expect(page.getByText("已上传，等待执行")).toBeVisible();
        await page.getByRole("textbox", { name: "任务要求" }).fill("保留符合条件的记录");
        await page.getByRole("button", { name: "更多", exact: true }).click();
      }
      await page.getByRole("checkbox", { name: /表格筛选工具/ }).check();
      await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
      await expect(page.getByTestId("capability-reuse-result")).toContainText("可复用：approved-table-tool · v2.0.0");
      await expect(page.getByTestId("capability-reuse-result")).toContainText("运行时检查健康状态");
      await expect(page.getByRole("checkbox", { name: /表格筛选工具/ })).not.toBeChecked();
      if (!attempt) {
        for (const width of [1440, 390]) {
          await page.setViewportSize({ width, height: 900 });
          if (width === 390) await page.getByRole("button", { name: "关闭原文件预览" }).click();
          await page.getByTestId("capability-reuse-result").scrollIntoViewIfNeeded();
          await expect(page.getByRole("button", { name: "取消复用" })).toBeVisible();
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
          await page.screenshot({ path: testInfo.outputPath(`reuse-${width}.png`) });
        }
        await page.setViewportSize({ width: 1440, height: 900 });
      }
      await page.getByRole("button", { name: "开始执行" }).click();
      await expect(page.getByRole("heading", { name: "复用工具任务" })).toBeVisible();
    }
    expect(resolutions).toEqual(Array.from({ length: 2 }, () => ({ need: { ...need, input_formats: ["csv"], output_formats: ["xlsx"] }, allow_discovery: false })));
    expect(submissions).toHaveLength(2);
    for (const submitted of submissions) {
      expect(submitted).toMatchObject({ capability_need: { ...need, input_formats: ["csv"], output_formats: ["xlsx"] }, capability_pack_refs: [match.ref] });
      expect(submitted).not.toHaveProperty("runtime_version");
    }
    expect(errors).toEqual([]);
  });

  test("修改要求或来源使旧匹配失效，迟到响应不能恢复许可", async ({ page }) => {
    await prepare(page);
    const arrived = responseBarrier();
    const release = responseBarrier();
    await page.route("**/api/semantic-workspace/capabilities/resolve", async route => {
      arrived.release();
      await release.promise;
      await route.fulfill({ json: { matches: [match], gaps: [] } });
    });
    await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
    await arrived.promise;
    await page.getByRole("textbox", { name: "任务要求" }).fill("改为另一组筛选条件");
    const response = page.waitForResponse("**/api/semantic-workspace/capabilities/resolve");
    release.release();
    await response;
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(page.getByTestId("capability-reuse-result")).toContainText("请重新匹配工具");
    await expect(page.getByTestId("capability-reuse-result")).not.toContainText("可复用：");
    await expect(page.getByRole("button", { name: "开始执行" })).toBeDisabled();
    await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
    await expect(page.getByTestId("capability-reuse-result")).toContainText("可复用：");
    await page.getByRole("button", { name: "CSV", exact: true }).click();
    await expect(page.getByTestId("capability-reuse-result")).not.toContainText("可复用：");
    await expect(page.getByRole("button", { name: "开始执行" })).toBeDisabled();
    await page.getByRole("button", { name: "CSV", exact: true }).click();
    await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
    await expect(page.getByTestId("capability-reuse-result")).toContainText("可复用：");
    await page.getByRole("button", { name: "移除 workload.csv" }).click();
    await expect(page.getByTestId("capability-reuse-result")).not.toContainText("可复用：");
    await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
    await expect(page.getByTestId("capability-reuse-result")).toContainText("请先完成文件上传并选择输出格式");
  });

  test("#135 网页组变化使在途工具匹配失效，迟到不得恢复旧许可", async ({ page }) => {
    await prepare(page);
    const arrived = responseBarrier(), release = responseBarrier();
    await page.route("**/api/semantic-workspace/source-acquisitions", route => route.fulfill({ json: mixedAttempt(1) }));
    await page.route("**/api/semantic-workspace/capabilities/resolve", async route => { arrived.release(); await release.promise; return route.fulfill({ json: { matches: [match], gaps: [] } }); });
    await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
    await arrived.promise;
    await addMixedWeb(page, 1);
    const response = page.waitForResponse("**/api/semantic-workspace/capabilities/resolve");
    release.release(); await response;
    await expect(page.getByTestId("capability-reuse-result")).not.toContainText("可复用：");
    await expect(page.getByTestId("capability-reuse-result")).toContainText("请重新匹配");
    await page.getByRole("button", { name: "检查上下文草案" }).click();
    await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeDisabled();
  });

  test("不兼容与创建时撤销均阻止启用，用户可明确取消后手选", async ({ page }) => {
    await prepare(page);
    await page.route("**/api/semantic-workspace/capabilities/resolve", route => route.fulfill({ json: { matches: [], gaps: [{ code: "governance_rejected", remediation: "工具已撤销，请核对治理状态后重试" }] } }));
    await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
    await expect(page.getByTestId("capability-reuse-result")).toContainText("工具已撤销");
    await expect(page.getByRole("button", { name: "开始执行" })).toBeDisabled();
    await page.route("**/api/semantic-workspace/capabilities/resolve", route => route.fulfill({ json: { matches: [match], gaps: [] } }));
    await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
    await expect(page.getByTestId("capability-reuse-result")).toContainText("可复用：");
    const taskKeys: string[] = [];
    await page.route("**/api/semantic-workspace/tasks", route => { taskKeys.push(route.request().headers()["idempotency-key"]); return route.fulfill({ status: 409, headers: { "X-Mangrove-Task-Outcome": "rejected" }, json: { detail: "所选工具与当前需求或治理许可不兼容" } }); });
    await page.getByRole("button", { name: "开始执行" }).click();
    await expect(page.getByTestId("capability-reuse-result")).toContainText("任务未创建，工具尚未启用");
    await expect(page.getByRole("button", { name: "开始执行" })).toBeDisabled();
    await page.getByRole("button", { name: "取消复用" }).click();
    await page.getByRole("checkbox", { name: /表格筛选工具/ }).check();
    await expect(page.getByRole("button", { name: "开始执行" })).toBeEnabled();
    await page.getByRole("button", { name: "开始执行" }).click();
    await expect.poll(() => taskKeys.length).toBe(2);
    expect(taskKeys[1]).not.toBe(taskKeys[0]);
  });

  test("匹配后目录清空仍可取消复用，不锁死任务输入", async ({ page }) => {
    await prepare(page);
    await page.route("**/api/semantic-workspace/capabilities/resolve", route => route.fulfill({ json: { matches: [match], gaps: [] } }));
    await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
    await expect(page.getByTestId("capability-reuse-result")).toContainText("可复用：");
    await page.route("**/api/semantic-workspace/capabilities", route => route.fulfill({ json: { enabled: true, items: [] } }));
    // 用浏览器的恢复联网事件触发目录复核，模拟权限或治理目录被收回。
    await page.evaluate(() => {
      window.dispatchEvent(new Event("offline"));
      window.dispatchEvent(new Event("online"));
    });
    await expect(page.getByRole("checkbox", { name: /表格筛选工具/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "开始执行" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "取消复用" })).toBeVisible();
    await page.getByRole("button", { name: "取消复用" }).click();
    await expect(page.getByRole("button", { name: "开始执行" })).toBeEnabled();
  });

  test("Markdown 文件需求使用规范格式，原文件名保持不变", async ({ page }) => {
    await mockWorkspace(page);
    await page.route("**/api/semantic-workspace/capabilities", route => route.fulfill({ json: { enabled: true, items: [capability] } }));
    await page.route("**/api/data-sources/uploads", route => route.fulfill({ json: { upload_id: "upload-markdown", original_name: "来源.md", media_type: "text/markdown", size_bytes: 10, sha256: "0".repeat(64) } }));
    let request: Record<string, unknown> | null = null;
    await page.route("**/api/semantic-workspace/capabilities/resolve", route => {
      request = route.request().postDataJSON();
      return route.fulfill({ json: { matches: [], gaps: [{ code: "incompatible_contract", remediation: "此工具未声明 Markdown 输入，请选择兼容工具" }] } });
    });
    await page.goto("/data-prep");
    await page.getByRole("textbox", { name: "任务要求" }).fill("从文档筛选记录");
    await page.locator('input[type="file"]').setInputFiles({ name: "来源.md", mimeType: "text/markdown", buffer: Buffer.from("# 合成资料\n", "utf-8") });
    await expect(page.getByText("已上传，等待执行")).toBeVisible();
    await page.getByRole("button", { name: "更多", exact: true }).click();
    await page.getByRole("button", { name: "复用同类工具：表格筛选工具" }).click();
    await expect(page.getByTestId("capability-reuse-result")).toContainText("此工具未声明 Markdown 输入");
    expect(request).toEqual({ need: { ...need, input_formats: ["markdown"], output_formats: ["docx", "pdf"] }, allow_discovery: false });
    await expect(page.getByText("来源.md", { exact: true })).toBeVisible();
  });

  test("普通用户无复用入口且不会请求灰度目录", async ({ page }) => {
    await mockWorkspace(page, "light", "user");
    const grayRequests: string[] = [];
    page.on("request", request => { if (request.url().includes("/semantic-workspace/capabilities")) grayRequests.push(request.url()); });
    await page.goto("/data-prep");
    await page.locator('input[type="file"]').setInputFiles({ name: "workload.csv", mimeType: "text/csv", buffer: Buffer.from("姓名,工作量\n张三,5\n", "utf-8") });
    await expect(page.getByText("已上传，等待执行")).toBeVisible();
    await page.getByRole("button", { name: "更多", exact: true }).click();
    await expect(page.getByRole("button", { name: /复用同类工具/ })).toHaveCount(0);
    await expect(page.getByText("本地任务能力（管理员灰度）")).toHaveCount(0);
    expect(grayRequests).toEqual([]);
  });
});

test("JPEG 原件按 EXIF 方向显示而不改写上传", async ({ page }) => {
  await mockWorkspace(page);
  const original = Buffer.from("/9j/4AAQSkZJRgABAQAAAQABAAD/4QAiRXhpZgAATU0AKgAAAAgAAQESAAMAAAABAAYAAAAAAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAeABQDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDaooor5A/KwooooAKKKKACiiigD//Z", "base64");
  let uploadedOriginal = false;
  await page.route("**/api/data-sources/uploads", route => {
    uploadedOriginal = Boolean(route.request().postDataBuffer()?.includes(original));
    return route.fulfill({ json: { upload_id: "rotated-image", original_name: "旋转.jpg", media_type: "image/jpeg", size_bytes: original.length, sha256: "0".repeat(64) } });
  });
  await page.route("**/api/data-sources/uploads/rotated-image/content", route => route.fulfill({ contentType: "image/jpeg", body: original }));
  await page.goto("/data-prep");
  await page.locator('input[type="file"]').setInputFiles({ name: "旋转.jpg", mimeType: "image/jpeg", buffer: original });
  const image = page.getByRole("img", { name: "旋转.jpg原件" });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(node => [(node as HTMLImageElement).naturalWidth, (node as HTMLImageElement).naturalHeight])).toEqual([30, 20]);
  expect(uploadedOriginal).toBe(true);
});


test.describe("#135 修订与迟到边界", () => {
  async function existingMixed(page: Page) {
    await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    const upload = { upload_id: "original-file", original_name: "original.csv", media_type: "text/csv", size_bytes: 10, sha256: "a".repeat(64) };
    const task = { ...workspaceTask("edit-mixed", "completed", "待修订混合任务"), runtime_version: "pi", provider: "local", model: "Qwen3.6-35B-A3B", model_connection_id: null };
    const webSources = [1, 2].map(index => ({ source_snapshot_id: mixedAttempt(index).snapshot_id, snapshot: mixedAttempt(index).snapshot }));
    const detail = workspaceDetail(task, { uploads: [upload], upload_ids: [upload.upload_id], web_sources: webSources,
      web_source: { ...webSources[0], goal_contract: { objective: task.objective_text }, runtime_binding: { model: task.model }, delivery_spec: { formats: ["xlsx"] } } });
    await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: [task] }));
    await page.route("**/api/semantic-workspace/tasks/edit-mixed", route => route.fulfill({ json: detail }));
    await page.route("**/api/data-sources/uploads/original-file", route => route.fulfill({ json: upload }));
    await page.route("**/api/semantic-workspace/source-acquisitions/mixed-attempt-*", route => route.fulfill({ json: mixedAttempt(Number(route.request().url().split("-").at(-1))) }));
    await page.goto("/data-prep?task=edit-mixed");
    await page.getByRole("button", { name: "编辑本次资料", exact: true }).click();
    await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
    return { task, detail };
  }

  test("移除最后网页提交全量文件与空网页集合，未知响应刷新重试保持原身份", async ({ page }) => {
    await existingMixed(page);
    const requests: { body: Record<string, unknown>; key: string }[] = [];
    await page.route("**/api/semantic-workspace/tasks/edit-mixed/revisions", route => {
      requests.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
      return route.fulfill({ status: 503, json: { detail: "修订结果未知" } });
    });
    await page.getByRole("button", { name: "移除网页组 独立说明 1" }).click();
    await page.getByRole("button", { name: "移除网页组 独立说明 2" }).click();
    await page.getByRole("button", { name: "创建新版本", exact: true }).click();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0].body).toMatchObject({ upload_ids: ["original-file"], source_snapshot_ids: [], expected_active_revision: 1 });
    expect(requests[0].body).not.toHaveProperty("context_selection");
    await page.getByLabel("任务要求", { exact: true }).fill("不能将未知请求改成第二次修订");
    await page.getByRole("button", { name: "创建新版本", exact: true }).click();
    expect(requests).toHaveLength(1);
    await page.reload();
    await page.getByRole("button", { name: "编辑本次资料", exact: true }).click();
    await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "恢复上次资料修订", exact: true }).click();
    await expect.poll(() => requests.length).toBe(2);
    expect(requests[1]).toEqual(requests[0]);
    await expect(page.getByText("original.csv").first()).toBeVisible();
  });

  test("目标组刷新未知不能改组重发，原组只恢复同一请求", async ({ page }) => {
    await existingMixed(page);
    await page.getByRole("button", { name: "编辑本次资料", exact: true }).click();
    const requests: { body: Record<string, unknown>; key: string }[] = [];
    await page.route("**/api/semantic-workspace/tasks/edit-mixed/source-refresh", route => {
      requests.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
      return route.fulfill({ status: 202, json: { status: "acquiring", attempt: mixedAttempt(2), revision: null } });
    });
    await page.getByLabel("获取最新的网页组").selectOption("mixed-snapshot-2");
    await page.getByRole("button", { name: "获取最新网页", exact: true }).click();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0].body).toMatchObject({ target_source_snapshot_id: "mixed-snapshot-2", expected_active_revision: 1, resume_unknown: false });
    await page.getByLabel("获取最新的网页组").selectOption("mixed-snapshot-1");
    await page.getByRole("button", { name: "获取最新网页", exact: true }).click();
    expect(requests).toHaveLength(1);
    await page.getByLabel("获取最新的网页组").selectOption("mixed-snapshot-2");
    await page.getByRole("button", { name: "获取最新网页", exact: true }).click();
    await expect.poll(() => requests.length).toBe(2);
    expect(requests[1].key).toBe(requests[0].key);
    expect(requests[1].body).toMatchObject({ target_source_snapshot_id: "mixed-snapshot-2", resume_unknown: true });
  });

  for (const rejected of [true, false]) test(`仅明确未启动拒绝可以修改资料；普通422保留unknown：${rejected}`, async ({ page }) => {
    await existingMixed(page);
    const requests: { body: Record<string, unknown>; key: string }[] = [];
    await page.route("**/api/semantic-workspace/tasks/edit-mixed/revisions", route => {
      requests.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
      return route.fulfill({ status: 422, headers: rejected ? { "X-Mangrove-Revision-Outcome": "rejected" } : {}, json: { detail: rejected ? "来源无效，尚未开始" : "执行状态未确认" } });
    });
    await page.getByRole("button", { name: "创建新版本", exact: true }).click();
    await expect.poll(() => requests.length).toBe(1);
    await expect(page.getByText(rejected ? "来源无效，尚未开始" : "执行状态未确认", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "移除网页组 独立说明 2" }).click();
    await page.getByRole("button", { name: "创建新版本", exact: true }).click();
    if (rejected) {
      await expect.poll(() => requests.length).toBe(2);
      expect(requests[1].key).not.toBe(requests[0].key);
      expect(requests[1].body.source_snapshot_ids).toEqual(["mixed-snapshot-1"]);
    } else {
      await expect(page.getByText("上次资料修订结果未知，请恢复原资料和要求后重试同一请求", { exact: true })).toBeVisible();
      expect(requests).toHaveLength(1);
      await expect(page.getByRole("button", { name: "恢复上次资料修订", exact: true })).toBeVisible();
    }
  });

  test("另一标签页更新后，旧创建迟到成功不能清除新草稿", async ({ page, context }) => {
    await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
    await mockWorkspace(page);
    const requested = responseBarrier(), release = responseBarrier();
    await page.route("**/api/semantic-workspace/tasks", async route => { requested.release(); await release.promise; return route.fulfill({ json: workspaceTask("late-mixed", "queued", "旧请求已完成") }); });
    await page.goto("/data-prep");
    await page.getByLabel("任务要求", { exact: true }).fill("旧草稿请求");
    await page.locator('input[type="file"]').setInputFiles({ name: "old.csv", mimeType: "text/csv", buffer: Buffer.from("x\n1", "utf-8") });
    await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "开始执行", exact: true }).click();
    await requested.promise;
    const peer = await context.newPage();
    await peer.route("**/*", route => route.fulfill({ contentType: "text/html", body: "<html lang=zh><title>合成草稿窗口</title></html>" }));
    await peer.goto(new URL("/draft-peer", page.url()).href);
    expect(new URL(peer.url()).origin).toBe(new URL(page.url()).origin);
    await peer.evaluate(() => {
      localStorage.setItem("mangrove_workspace_draft_u1_new", JSON.stringify({ draft: { prompt: "另一个标签页的新资料" }, sources: [] }));
      localStorage.setItem("mangrove_workspace_draft_u1_new_files", JSON.stringify([{ id: "peer", name: "peer.csv", size: 12, status: "reselect" }]));
    });
    await expect(page.getByText("当前草稿已在其他页面更新", { exact: false })).toBeVisible();
    release.release();
    await expect.poll(() => page.evaluate(() => localStorage.getItem("mangrove_web_task_attempt_u1"))).toBeNull();
    expect(await peer.evaluate(() => localStorage.getItem("mangrove_workspace_draft_u1_new"))).toContain("另一个标签页的新资料");
    expect(await peer.evaluate(() => localStorage.getItem("mangrove_workspace_draft_u1_new_files"))).toContain("peer.csv");
    await peer.close();
  });
});


test("#135 混合资料明暗390与1440、键盘IME及取消保持焦点", async ({ page }, testInfo) => {
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
  await mockWorkspace(page);
  let acquisitions = 0, tasks = 0;
  await page.route("**/api/semantic-workspace/source-acquisitions", route => { acquisitions++; return route.fulfill({ json: mixedAttempt(acquisitions) }); });
  await page.route("**/api/semantic-workspace/tasks", route => { tasks++; return route.fulfill({ status: 422, json: { detail: "此用例不应创建任务" } }); });
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("关联实体 ID，逐份解释冲突与缺口");
  await page.locator('input[type="file"]').setInputFiles({ name: "proof.csv", mimeType: "text/csv", buffer: Buffer.from("ID,n\nE101,200", "utf-8") });
  await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
  await addMixedWeb(page, 1);
  await expect(page.getByLabel("任务要求", { exact: true })).toBeFocused();
  const prompt = page.getByLabel("任务要求", { exact: true });
  await prompt.dispatchEvent("compositionstart");
  await prompt.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
  await prompt.dispatchEvent("compositionend", { data: "资料" });
  expect(tasks).toBe(0);
  await page.getByRole("button", { name: "公开网页", exact: true }).click();
  await page.getByRole("button", { name: "返回当前资料", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(prompt).toBeFocused();
  expect(acquisitions).toBe(1);
  for (const theme of ["light", "dark"]) for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.evaluate(async theme => {
      document.documentElement.classList.toggle("dark", theme === "dark");
      void getComputedStyle(document.querySelector('[aria-label="当前任务资料"] [role="presentation"]')!).backgroundColor;
      await Promise.all(document.getAnimations().filter(animation => animation instanceof CSSTransition).map(animation => animation.finished.catch(() => undefined)));
    }, theme);
    await page.getByLabel("已选网页资料").scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const scan = await new AxeBuilder({ page }).include('[aria-label="当前任务资料"]').analyze();
    expect(scan.violations.filter(item => item.impact === "serious" || item.impact === "critical")).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`mixed-${theme}-${width}.png`), fullPage: true });
  }
});


test("#135 取消新网页获取的迟到成功保留既有文件和网页组", async ({ page }) => {
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
  await mockWorkspace(page);
  let posts = 0, canceled = false;
  const requested = responseBarrier(), release = responseBarrier();
  await page.route("**/api/semantic-workspace/source-acquisitions", async route => {
    posts++;
    if (posts > 2) { requested.release(); await release.promise; return route.fulfill({ json: mixedAttempt(2) }); }
    return route.fulfill({ json: posts === 1 ? mixedAttempt(1) : { ...mixedAttempt(2), idempotency_key: route.request().headers()["idempotency-key"], status: "acquiring", snapshot: null, snapshot_id: null } });
  });
  await page.route("**/api/semantic-workspace/source-acquisitions/mixed-attempt-2", async route => {
    if (canceled) return route.fulfill({ json: { ...mixedAttempt(2), status: "canceled", snapshot: null, snapshot_id: null } });
    requested.release(); await release.promise;
    return route.fulfill({ json: mixedAttempt(2) });
  });
  await page.route("**/api/semantic-workspace/source-acquisitions/mixed-attempt-2/cancel", route => { canceled = true; return route.fulfill({ json: { ...mixedAttempt(2), status: "canceled", snapshot: null, snapshot_id: null } }); });
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("保留当前证据");
  await page.locator('input[type="file"]').setInputFiles({ name: "keep.csv", mimeType: "text/csv", buffer: Buffer.from("ID\nE101", "utf-8") });
  await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
  await addMixedWeb(page, 1);
  await page.getByRole("button", { name: "公开网页", exact: true }).click();
  await page.getByRole("combobox", { name: "来源方式", exact: true }).selectOption("url");
  await page.getByLabel("精确网址").fill("https://example.com/source-2");
  await page.getByRole("button", { name: "获取网页", exact: true }).click();
  await requested.promise;
  await page.getByRole("button", { name: "取消获取", exact: true }).click();
  release.release();
  await page.getByRole("button", { name: "返回当前资料", exact: true }).click();
  await expect(page.getByLabel("任务要求", { exact: true })).toBeFocused();
  await expect(page.getByRole("button", { name: "移除网页组 独立说明 1" })).toBeVisible();
  await expect(page.getByRole("button", { name: "移除网页组 独立说明 2" })).toHaveCount(0);
  await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
  expect(posts).toBe(3);
});


test("#135 首创建未知后刷新遇到在途409，继续保留原请求和键", async ({ page }) => {
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
  await mockWorkspace(page);
  const requests: { body: unknown; key: string }[] = [];
  await page.route("**/api/data-sources/uploads/upload-e2e", route => route.fulfill({ json: { upload_id: "upload-e2e", original_name: "workload.csv", media_type: "text/csv", size_bytes: 64, sha256: "0".repeat(64) } }));
  await page.route("**/api/semantic-workspace/tasks", route => {
    requests.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
    return route.fulfill({ status: requests.length === 1 ? 503 : 409, json: { detail: requests.length === 1 ? "创建结果未知" : "同一幂等请求正在创建" } });
  });
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("只创建一次完整任务");
  await page.locator('input[type="file"]').setInputFiles({ name: "workload.csv", mimeType: "text/csv", buffer: Buffer.from("ID\nE101", "utf-8") });
  await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始执行", exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  await page.reload();
  await expect.poll(() => requests.length).toBe(2);
  await expect(page.getByText("上次任务尚未恢复，资料仍保留", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("mangrove_web_task_attempt_u1"))).toContain(requests[0].key);
  await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始执行", exact: true }).click();
  await expect.poll(() => requests.length).toBe(3);
  expect(requests[1]).toEqual(requests[0]); expect(requests[2]).toEqual(requests[0]);
});

function reusableFixtures() {
  const snapshot = mixedAttempt(1).snapshot;
  const common = { acquired_at: "2026-09-08T08:00:00Z", time_kind: "acquired", availability: "available", reason_code: null, limitations: [], origin: { task_id: "origin-task", revision: 2, run_id: null, delivery_id: null }, media_type: "text/csv", size_bytes: 12, sha256: "a".repeat(64) };
  return [
    { ...common, source_key: "upload:history-file", kind: "upload", identity: "original", label: "历史明细.csv", upload_id: "history-file" },
    { ...common, source_key: "snapshot:mixed-snapshot-1", kind: "snapshot", identity: "original", label: "历史网页组", source_snapshot_id: snapshot.snapshot_id, attempt_id: snapshot.attempt_id, allowed_scope: snapshot.allowed_scope, coverage: snapshot.coverage, sha256: null },
    { ...common, source_key: "delivery_output:history-output", kind: "delivery_output", identity: "derived", label: "正式分析.md", output_id: "history-output", time_kind: "generated", media_type: "text/markdown", origin: { task_id: "origin-task", revision: 2, run_id: "origin-run", delivery_id: "origin-delivery" } },
  ];
}
async function mockReusable(page: Page) {
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
  await mockWorkspace(page);
  const items = reusableFixtures();
  await page.route("**/api/semantic-workspace/reusable-sources?*", route => route.fulfill({ json: { items, total: 3, page_complete: true, next_cursor: null, snapshot_token: "catalog-1" } }));
  await page.route("**/api/semantic-workspace/reusable-sources/resolve", route => {
    const body = route.request().postDataJSON();
    const ids = [...(body.upload_ids ?? []), ...(body.source_snapshot_ids ?? []), ...(body.delivery_output_ids ?? [])];
    return route.fulfill({ json: { items: items.filter(item => ids.includes(item.upload_id ?? item.source_snapshot_id ?? item.output_id)) } });
  });
  await page.route("**/api/semantic-workspace/source-acquisitions/mixed-attempt-1", route => route.fulfill({ json: mixedAttempt(1) }));
  await page.route("**/api/data-sources/uploads/history-file", route => route.fulfill({ json: { upload_id: "history-file", original_name: "历史明细.csv", media_type: "text/csv", size_bytes: 12, sha256: "a".repeat(64) } }));
  await page.route("**/api/semantic-workspace/reusable-sources/outputs/history-output/preview?*", route => route.fulfill({ json: {
    kind: "document", action: "preview", items: [{ id: "line-1", type: "passage", label: "正式输出正文", content: "销售金额已汇总为 200 元。", evidence_refs: [] }], total: 1, offset: 0, limit: 30, warnings: [],
    source_key: "delivery_output:history-output", identity: "derived", sha256: "a".repeat(64), output_id: "history-output", delivery_id: "origin-delivery", run_id: "origin-run", origin: items[2].origin,
    representation: { kind: "output", sha256: "a".repeat(64), associated_output_id: "history-output", media_type: "text/markdown", lineage_available: true },
  } }));
  return items;
}

function deletionPlan(policy = "keep_shared", token = "delete-plan-1") {
  return { plan_token: token, task_id: "delete-target", shared_policy: policy, can_execute: true, blockers: [],
    objects: [{ source_key: "upload:exclusive", kind: "upload", sha256: "a".repeat(64), label: "独占原件.csv", disposition: "delete", references: [] },
      { source_key: "delivery_output:shared", kind: "delivery_output", sha256: "b".repeat(64), label: "共享分析.md", disposition: policy === "keep_shared" ? "keep_shared" : "delete", references: [{ task_id: "other-task", revision: 2, reference_kind: "delivery", delivery_id: "other-delivery", run_id: "other-run", task_exists: true, state: "published", use_id: null, in_recycle_bin: false }] }],
    affected_tasks: [{ task_id: "other-task", revision: 2, task_exists: true, in_recycle_bin: false }] };
}
function deletionOperation(state: string) {
  return { operation_id: "delete-op", task_id: "delete-target", state, completed_source_keys: state === "completed" ? ["upload:exclusive"] : [], retained_source_keys: ["delivery_output:shared"], affected_tasks: deletionPlan().affected_tasks, error_code: null, message: null };
}
async function mockDeletion(page: Page) {
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
  await mockWorkspace(page);
  const task = { ...workspaceTask("delete-target", "completed", "待清理项目"), deleted_at: "2026-09-09T08:00:00Z", purge_after: null };
  await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: new URL(route.request().url()).searchParams.get("deleted") === "true" ? [task] : [] }));
  await page.route("**/api/semantic-workspace/tasks/delete-target", route => route.fulfill({ json: workspaceDetail(task) }));
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-plan?*", route => route.fulfill({ json: deletionPlan(new URL(route.request().url()).searchParams.get("shared_policy")!) }));
}
async function openDeletion(page: Page) {
  await page.goto("/data-prep");
  await page.getByRole("button", { name: "回收站", exact: true }).click();
  await page.getByRole("button", { name: /待清理项目/ }).click();
  await page.getByRole("button", { name: "永久删除", exact: true }).click();
}

test("#137 永久清理先核对资料，默认保留共享且取消零写", async ({ page }) => {
  await mockDeletion(page);
  let writes = 0;
  await page.route("**/api/semantic-workspace/tasks/delete-target/permanent", route => { writes++; return route.fulfill({ json: { ok: true } }); });
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", route => { writes++; return route.fulfill({ json: deletionOperation("completed") }); });
  await openDeletion(page);
  await expect(page.getByRole("heading", { name: "清理任务和资料", exact: true })).toBeVisible();
  await expect(page.getByLabel("资料清理清单")).toContainText("独占原件.csv");
  await expect(page.getByRole("radio", { name: "保留其他任务使用的资料（默认）", exact: true })).toBeChecked();
  await expect(page.getByRole("radio", { name: "同时删除共享资料，先停止依赖任务", exact: true })).not.toBeChecked();
  await expect(page.getByRole("button", { name: "取消", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByRole("button", { name: "永久删除", exact: true })).toBeFocused();
  expect(writes).toBe(0);
});

test("#137 默认清理保留共享，服务端完成后才显示真实回执", async ({ page }) => {
  await mockDeletion(page);
  const posts: { body: unknown; key: string }[] = [];
  const requested = responseBarrier(), release = responseBarrier();
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", async route => { posts.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] }); requested.release(); await release.promise; return route.fulfill({ json: deletionOperation("completed") }); });
  await openDeletion(page);
  await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).click();
  await requested.promise;
  await expect(page.getByText("删除完成", { exact: true })).toHaveCount(0);
  release.release();
  await expect(page.getByLabel("清理操作状态")).toContainText("删除完成");
  await expect(page.getByLabel("清理操作状态")).toContainText("已清理 1 份 · 保留共享 1 份");
  expect(posts).toHaveLength(1);
  expect(posts[0].body).toEqual({ plan_token: "delete-plan-1", shared_policy: "keep_shared" });
  expect(posts[0].key).toBeTruthy();
  expect(await page.evaluate(() => localStorage.getItem("mangrove_task_deletion_u1"))).toBeNull();
});

test("#137 明确删共享后失败保留原操作，继续不创建第二次清理", async ({ page }) => {
  await mockDeletion(page);
  const bodies: unknown[] = [], resumes: unknown[] = [];
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", route => { bodies.push(route.request().postDataJSON()); return route.fulfill({ json: { ...deletionOperation("incomplete"), message: "依赖任务尚未确认停止，未清理正文" } }); });
  await page.route("**/api/semantic-workspace/deletion-operations/delete-op/resume", route => { resumes.push(route.request().postDataJSON()); return route.fulfill({ json: deletionOperation("completed") }); });
  await openDeletion(page);
  await page.getByRole("radio", { name: "同时删除共享资料，先停止依赖任务", exact: true }).check();
  await expect(page.getByLabel("资料清理清单")).not.toContainText("保留共享资料");
  await page.getByRole("button", { name: "确认清理任务和共享资料", exact: true }).click();
  await expect(page.getByLabel("清理操作状态")).toContainText("删除未完成");
  await expect(page.getByText("依赖任务尚未确认停止，未清理正文", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "继续原清理操作", exact: true }).click();
  await expect(page.getByLabel("清理操作状态")).toContainText("删除完成");
  expect(bodies).toEqual([{ plan_token: "delete-plan-1", shared_policy: "delete_shared" }]); expect(resumes).toEqual([{}]);
});

test("#137 删除未知刷新按原键查询，目标不存在也不冒充成功", async ({ page }) => {
  await mockDeletion(page);
  let posts = 0; const queries: URL[] = [];
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", route => { posts++; return route.fulfill({ status: 503, json: { detail: "结果未知" } }); });
  await page.route("**/api/semantic-workspace/deletion-operations/by-key?*", route => { queries.push(new URL(route.request().url())); return route.fulfill({ status: 404, json: {} }); });
  await openDeletion(page);
  await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("结果未知");
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("mangrove_task_deletion_u1")!));
  await page.route("**/api/semantic-workspace/tasks/delete-target", route => route.fulfill({ status: 404, json: {} }));
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("尚未查到原操作");
  expect(posts).toBe(1); expect(queries[0].searchParams.get("idempotency_key")).toBe(saved.key);
  expect(await page.evaluate(() => localStorage.getItem("mangrove_task_deletion_u1"))).toContain(saved.key);
  await page.route("**/api/semantic-workspace/deletion-operations/by-key?*", route => route.fulfill({ json: deletionOperation("completed") }));
  await page.getByRole("button", { name: "查询原操作状态", exact: true }).click();
  await expect(page.getByLabel("清理操作状态")).toContainText("删除完成"); expect(posts).toBe(1);
});

test("#137 新引用使清理确认失效，安全默认重确认同一个操作", async ({ page }) => {
  await mockDeletion(page);
  let changed = false; const resumes: unknown[] = [];
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-plan?*", route => route.fulfill({ json: deletionPlan(new URL(route.request().url()).searchParams.get("shared_policy")!, changed ? "new-plan" : "old-plan") }));
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", route => { changed = true; return route.fulfill({ json: deletionOperation("needs_confirmation") }); });
  await page.route("**/api/semantic-workspace/deletion-operations/delete-op/resume", route => { resumes.push(route.request().postDataJSON()); return route.fulfill({ json: deletionOperation("completed") }); });
  await openDeletion(page);
  await page.getByRole("radio", { name: "同时删除共享资料，先停止依赖任务", exact: true }).check();
  await page.getByRole("button", { name: "确认清理任务和共享资料", exact: true }).click();
  await expect(page.getByLabel("清理操作状态")).toContainText("关联已变化，需要重新确认");
  await expect(page.getByRole("radio", { name: "保留其他任务使用的资料（默认）", exact: true })).toBeChecked();
  await expect(page.getByRole("button", { name: "按新清单确认继续", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "重新核对清单", exact: true }).click();
  await page.getByRole("button", { name: "按新清单确认继续", exact: true }).click();
  await expect(page.getByLabel("清理操作状态")).toContainText("删除完成");
  expect(resumes).toEqual([{ plan_token: "new-plan", shared_policy: "keep_shared" }]);
});

test("#137 清单阻断或确定拒绝后取消危险选择，不用旧令牌执行", async ({ page }) => {
  await mockDeletion(page);
  let blocked = true, posts = 0;
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-plan?*", route => route.fulfill({ json: { ...deletionPlan(new URL(route.request().url()).searchParams.get("shared_policy")!), can_execute: !blocked, blockers: blocked ? [{ code: "source_in_use", message: "有结果未知的导出，不能清理" }] : [] } }));
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", route => { posts++; return route.fulfill({ status: 409, headers: { "X-Mangrove-Deletion-Outcome": "rejected" }, json: { detail: "关联已变化，请重新核对" } }); });
  await openDeletion(page);
  await expect(page.getByRole("alert")).toContainText("有结果未知的导出");
  await expect(page.getByRole("button", { name: "确认清理并保留共享资料", exact: true })).toBeDisabled();
  blocked = false;
  await page.getByRole("radio", { name: "同时删除共享资料，先停止依赖任务", exact: true }).check();
  await page.getByRole("button", { name: "确认清理任务和共享资料", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("关联已变化");
  await expect(page.getByRole("radio", { name: "保留其他任务使用的资料（默认）", exact: true })).toBeChecked();
  await expect(page.getByRole("button", { name: "确认清理并保留共享资料", exact: true })).toBeDisabled();
  expect(posts).toBe(1);
  expect(await page.evaluate(() => localStorage.getItem("mangrove_task_deletion_u1"))).toBeNull();
});

test("#137 清单迟到不能重开已取消弹窗，重新打开按新清单读取", async ({ page }) => {
  await mockDeletion(page);
  const requested = responseBarrier(), release = responseBarrier(); let reads = 0;
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-plan?*", async route => { if (++reads === 1) { requested.release(); await release.promise; } return route.fulfill({ json: deletionPlan() }); });
  await openDeletion(page); await requested.promise;
  await page.getByRole("button", { name: "取消", exact: true }).click(); release.release();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await page.getByRole("button", { name: "永久删除", exact: true }).click();
  await expect(page.getByLabel("资料清理清单")).toContainText("独占原件.csv");
  expect(reads).toBe(2);
});

test("#137 关闭未知清理只关闭查看，迟到后仍按原键查询", async ({ page }) => {
  await mockDeletion(page);
  const requested = responseBarrier(), release = responseBarrier(); let posts = 0, reads = 0;
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", async route => { posts++; requested.release(); await release.promise; return route.fulfill({ json: deletionOperation("completed") }); });
  await page.route("**/api/semantic-workspace/deletion-operations/by-key?*", route => { reads++; return route.fulfill({ json: deletionOperation("completed") }); });
  await openDeletion(page);
  await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).click(); await requested.promise;
  await page.getByRole("button", { name: "关闭查看", exact: true }).click(); release.release();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看未完成的资料清理", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看未完成的资料清理", exact: true }).click();
  await expect(page.getByLabel("清理操作状态")).toContainText("删除完成");
  expect(posts).toBe(1); expect(reads).toBe(1);
});

test("#137 Owner切换不读取前Owner的删除身份或关联正文", async ({ page }) => {
  await mockDeletion(page);
  let queries = 0;
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", route => route.fulfill({ status: 503, json: { detail: "结果未知" } }));
  await page.route("**/api/semantic-workspace/deletion-operations/by-key?*", route => { queries++; return route.fulfill({ json: deletionOperation("incomplete") }); });
  await openDeletion(page);
  await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("结果未知");
  await page.route("**/api/auth/me", route => route.fulfill({ json: { user_id: "u2", username: "other", display_name: "另一人", role: "admin" } }));
  await page.route("**/api/semantic-workspace/tasks/delete-target", route => route.fulfill({ status: 404, json: {} }));
  await page.reload();
  await expect(page.getByRole("heading", { name: "任务工作台", exact: true })).toBeVisible();
  await expect(page.getByRole("alertdialog")).toHaveCount(0); expect(queries).toBe(0);
  await expect(page.getByText("独占原件.csv", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem("mangrove_task_deletion_u1"))).toBeTruthy();
});

test("#137 同源标签更新清理身份使旧确认失效", async ({ page, context }) => {
  await mockDeletion(page); await openDeletion(page);
  await expect(page.getByLabel("资料清理清单")).toBeVisible();
  const peer = await context.newPage();
  await peer.route("**/peer-deletion.html", route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>同源草稿测试</title>" }));
  await peer.goto(new URL("/peer-deletion.html", page.url()).toString());
  await peer.evaluate(() => localStorage.setItem("mangrove_task_deletion_u1", JSON.stringify({ task_id: "other-task", key: "peer-key", payload: { plan_token: "peer-plan", shared_policy: "keep_shared" } })));
  await expect(page.getByRole("alert")).toContainText("其他页面更新");
  await expect(page.getByRole("button", { name: "确认清理并保留共享资料", exact: true })).toBeDisabled();
  expect(await page.evaluate(() => localStorage.getItem("mangrove_task_deletion_u1"))).toContain("peer-key");
  await peer.close();
});

test("#137 明暗390和1440清理确认安全焦点、IME与可访问性", async ({ page }, testInfo) => {
  await mockDeletion(page); let posts = 0;
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", route => { posts++; return route.fulfill({ json: deletionOperation("completed") }); });
  await openDeletion(page);
  await expect(page.getByRole("button", { name: "取消", exact: true })).toBeFocused();
  await page.keyboard.press("Enter"); await expect(page.getByRole("alertdialog")).toHaveCount(0); expect(posts).toBe(0);
  await page.getByRole("button", { name: "永久删除", exact: true }).click();
  for (const theme of ["light", "dark"]) for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.evaluate(theme => document.documentElement.classList.toggle("dark", theme === "dark"), theme);
    await page.getByText("查看 1 条关联记录", { exact: true }).click();
    await expect(page.getByLabel("资料清理清单")).toContainText("other-task · V2");
    await expect(page.getByLabel("资料清理清单")).toContainText("正式交付记录");
    await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).focus();
    await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).dispatchEvent("keydown", { key: "Enter", isComposing: true, bubbles: true });
    expect(posts).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const result = await new AxeBuilder({ page }).include('[role="alertdialog"]').analyze();
    expect(result.violations.filter(item => item.impact === "serious" || item.impact === "critical")).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`deletion-${theme}-${width}.png`), fullPage: true });
    await page.getByText("查看 1 条关联记录", { exact: true }).click();
  }
  await page.keyboard.press("Escape"); await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "永久删除", exact: true })).toBeFocused(); expect(posts).toBe(0);
});

test("#137 来源墓碑限制重跑但保留独立正式结果和历史QA", async ({ page }) => {
  await mockWorkspace(page);
  const fixture = previewIdentityFixture("A", 2);
  await page.route("**/api/semantic-workspace/tasks/identity-task", route => route.fulfill({ json: { ...fixture.detail, source_integrity: { state: "source_deleted", deleted_source_keys: ["upload:V2-upload"], can_rerun: false, can_reverify: false } } }));
  await page.route("**/api/semantic-workspace/tasks/identity-task/preview?*", route => route.fulfill({ json: fixture.preview }));
  await page.goto("/data-prep?task=identity-task");
  await expect(page.getByText("部分来源已删除，不能按原来源完整重跑或复验。", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  await expect(page.getByText("A-V2-正文", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 A-V2.xlsx", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("来源已删除，原文不可再读取");
  await expect(page.getByText("张三", { exact: true })).toHaveCount(0);
});

test("#137 已删原来源仍可下载独立结果，并显式换新资料创建修订", async ({ page }) => {
  await mockWorkspace(page);
  const fixture = previewIdentityFixture("A", 2);
  await page.route("**/api/semantic-workspace/tasks/identity-task", route => route.fulfill({ json: { ...fixture.detail, source_integrity: { state: "source_deleted", deleted_source_keys: ["upload:V2-upload"], can_rerun: false, can_reverify: false } } }));
  await page.route("**/api/semantic-workspace/tasks/identity-task/preview?*", route => route.fulfill({ json: fixture.preview }));
  await page.route("**/api/semantic-delivery/outputs/A-V2-output", route => route.fulfill({ contentType: "application/octet-stream", body: "independent-formal-output" }));
  const posts: Record<string, unknown>[] = [];
  await page.route("**/api/semantic-workspace/tasks/identity-task/revisions", route => { posts.push(route.request().postDataJSON()); return route.fulfill({ status: 503, json: { detail: "合成测试保留修订未知" } }); });
  await page.goto("/data-prep?task=identity-task");
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 A-V2.xlsx", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("A-V2.xlsx");
  await page.getByRole("button", { name: "编辑本次资料", exact: true }).click();
  await page.getByRole("button", { name: "移除 A-V2-原件.csv", exact: true }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "新证据.csv", mimeType: "text/csv", buffer: Buffer.from("ID\nN01", "utf-8") });
  await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "创建新版本", exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ expected_active_revision: 2, upload_ids: ["upload-e2e"], source_snapshot_ids: [], delivery_output_ids: [] });
});

test("#137 切换任务使旧清理响应失效，保留原操作供查询", async ({ page }) => {
  await mockDeletion(page);
  const requested = responseBarrier(), release = responseBarrier();
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", async route => { requested.release(); await release.promise; return route.fulfill({ json: deletionOperation("completed") }); });
  await page.route("**/api/semantic-workspace/tasks/other-view", route => route.fulfill({ json: workspaceDetail(workspaceTask("other-view", "completed", "另一任务")) }));
  await openDeletion(page);
  await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).click(); await requested.promise;
  await page.evaluate(() => { history.pushState({}, "", "/data-prep?task=other-view"); window.dispatchEvent(new PopStateEvent("popstate")); });
  await expect(page.getByRole("alertdialog")).toHaveCount(0); release.release();
  await expect(page.getByRole("heading", { name: "另一任务", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看未完成的资料清理", exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("task")).toBe("other-view");
  expect(await page.evaluate(() => localStorage.getItem("mangrove_task_deletion_u1"))).toBeTruthy();
});

test("#137 实际网页原件和生产归属显示精确组与原件身份", async ({ page }) => {
  await mockDeletion(page);
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-plan?*", route => route.fulfill({ json: { ...deletionPlan(), objects: [{ source_key: "web_artifact:web-1", kind: "web_artifact", identity: "original", artifact_id: "web-1", snapshot_id: "web-group", label: "历史网页原文", sha256: "c".repeat(64), time_kind: "acquired", acquired_at: "2026-09-09T01:00:00Z", disposition: "keep_shared", references: [{ task_id: "producer-task", revision: 1, delivery_id: "producer-delivery", run_id: "producer-run", reference_kind: "producer", state: "retained", use_id: null, task_exists: true, in_recycle_bin: false }] }] } }));
  await openDeletion(page);
  const list = page.getByLabel("资料清理清单");
  await expect(list).toContainText("网页原文");
  await expect(list).toContainText("来源组：web-group");
  await expect(list).toContainText("原件：web-1");
  await page.getByText("查看 1 条关联记录", { exact: true }).click();
  await expect(list).toContainText("生产归属");
  await expect(list).toContainText("producer-task · V1");
});

for (const state of ["incomplete", "needs_confirmation"]) test(`#137 继续操作 ${state} 结果未知只能查询原操作`, async ({ page }) => {
  await mockDeletion(page); let resumes = 0, reads = 0;
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", route => route.fulfill({ json: deletionOperation(state) }));
  await page.route("**/api/semantic-workspace/deletion-operations/delete-op/resume", route => { resumes++; return route.fulfill({ status: 503, json: { detail: "继续结果未知" } }); });
  await page.route("**/api/semantic-workspace/deletion-operations/delete-op", route => { reads++; return route.fulfill({ json: deletionOperation("stopping") }); });
  await openDeletion(page);
  await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).click();
  if (state === "needs_confirmation") await page.getByRole("button", { name: "重新核对清单", exact: true }).click();
  await page.getByRole("button", { name: state === "needs_confirmation" ? "按新清单确认继续" : "继续原清理操作", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("继续结果未知");
  await expect(page.getByRole("button", { name: "继续原清理操作", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "按新清单确认继续", exact: true })).toHaveCount(0);
  await expect(page.getByRole("radio")).toHaveCount(0);
  await page.getByRole("button", { name: "查询原操作状态", exact: true }).click();
  await expect(page.getByLabel("清理操作状态")).toContainText("正在停止依赖任务");
  expect(resumes).toBe(1); expect(reads).toBe(1);
});

for (const afterSubmit of [false, true]) test(`#137 同源存储事件未送达时也不覆盖新清理身份 afterSubmit=${afterSubmit}`, async ({ page, context }) => {
  await page.addInitScript(() => window.addEventListener("storage", event => event.stopImmediatePropagation(), true));
  await mockDeletion(page); let posts = 0;
  const requested = responseBarrier(), release = responseBarrier();
  await page.route("**/api/semantic-workspace/tasks/delete-target/deletion-operations", async route => { posts++; requested.release(); if (afterSubmit) await release.promise; return route.fulfill({ json: deletionOperation("completed") }); });
  await openDeletion(page); await expect(page.getByLabel("资料清理清单")).toBeVisible();
  // 屏障刻意扣住事件通知，检查提交/清理自身也核对共享存储，不能依赖通知时序。
  if (afterSubmit) { await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).click(); await requested.promise; }
  const peer = await context.newPage();
  await peer.route("**/peer-deletion.html", route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>同源屏障</title>" }));
  await peer.goto(new URL("/peer-deletion.html", page.url()).toString());
  await peer.evaluate(() => localStorage.setItem("mangrove_task_deletion_u1", JSON.stringify({ task_id: "peer-task", key: "new-peer-key", payload: { plan_token: "new-peer-plan", shared_policy: "keep_shared" } })));
  if (afterSubmit) release.release(); else await page.getByRole("button", { name: "确认清理并保留共享资料", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("其他页面更新");
  expect(await page.evaluate(() => localStorage.getItem("mangrove_task_deletion_u1"))).toContain("new-peer-key");
  await expect(page.getByText("删除完成", { exact: true })).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("task")).toBe("delete-target");
  expect(posts).toBe(afterSubmit ? 1 : 0); await peer.close();
});

test("#136 当前文件和历史三类资料一次确认，预览返回且零重复上传采集", async ({ page }) => {
  await mockReusable(page);
  let uploads = 0, acquisitions = 0;
  const submitted: Record<string, unknown>[] = [];
  await page.route("**/api/data-sources/uploads", route => { uploads++; return route.fulfill({ json: { upload_id: "today-file", original_name: "今日.csv", media_type: "text/csv", size_bytes: 3, sha256: "b".repeat(64) } }); });
  await page.route("**/api/semantic-workspace/source-acquisitions", route => { acquisitions++; return route.fulfill({ status: 422, json: {} }); });
  await page.route("**/api/semantic-workspace/tasks", route => { submitted.push(route.request().postDataJSON()); return route.fulfill({ status: 503, json: { detail: "本例保留创建未知事实" } }); });
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("综合昨天原件、历史说明和今天文件，核对正式分析");
  await page.locator('input[type="file"]').setInputFiles({ name: "今日.csv", mimeType: "text/csv", buffer: Buffer.from("x\n1", "utf-8") });
  await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "从历史资料添加" });
  await dialog.getByRole("button", { name: "预览 正式分析.md", exact: true }).click();
  await expect(dialog.getByText("销售金额已汇总为 200 元。", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog.getByRole("button", { name: "预览 正式分析.md", exact: true })).toBeFocused();
  for (const name of ["历史明细.csv", "历史网页组", "正式分析.md"]) await dialog.getByRole("checkbox", { name: `选择 ${name}`, exact: true }).check();
  await dialog.getByRole("button", { name: "添加 3 份资料", exact: true }).click();
  await expect(page.getByLabel("任务要求", { exact: true })).toBeFocused();
  await expect(page.getByText("正式处理结果 · 非原件", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "检查上下文草案", exact: true }).click();
  await page.getByRole("button", { name: "启动任务", exact: true }).click();
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0]).toMatchObject({ upload_ids: ["today-file", "history-file"], source_snapshot_ids: ["mixed-snapshot-1"], delivery_output_ids: ["history-output"] });
  expect(uploads).toBe(1);
  expect(acquisitions).toBe(0);
});

async function selectHistoricalOutput(page: Page) {
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await page.getByRole("dialog").getByRole("checkbox", { name: "选择 正式分析.md", exact: true }).check();
  await page.getByRole("button", { name: "添加 1 份资料", exact: true }).click();
  await expect(page.getByLabel("已选历史资料")).toContainText("正式分析.md");
}

test("#136 纯正式结果未知后刷新，完整第三类与原幂等键恢复", async ({ page }) => {
  await mockReusable(page);
  const posts: { body: Record<string, unknown>; key: string }[] = [];
  let acquisitions = 0;
  await page.route("**/api/semantic-workspace/source-acquisitions", route => { acquisitions++; return route.fulfill({ status: 422, json: {} }); });
  await page.route("**/api/semantic-workspace/tasks", route => { posts.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] }); return route.fulfill({ status: 503, json: { detail: "结果未知" } }); });
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("用昨天的正式分析继续归纳");
  await selectHistoricalOutput(page);
  await page.getByRole("button", { name: "开始执行", exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  await page.reload();
  await expect.poll(() => posts.length).toBe(2);
  await expect(page.getByLabel("已选历史资料")).toContainText("正式分析.md");
  expect(posts[0]).toEqual(posts[1]);
  expect(posts[0].body).toMatchObject({ upload_ids: [], source_snapshot_ids: [], delivery_output_ids: ["history-output"] });
  await page.getByRole("button", { name: "移除历史资料 正式分析.md" }).click();
  await page.getByLabel("任务要求", { exact: true }).fill("修改为另外一个任务");
  expect(posts).toHaveLength(2);
  expect(acquisitions).toBe(0);
});

test("#136 取消在途历史核验后迟到不能加入，保留文字并归还焦点", async ({ page }) => {
  await mockReusable(page);
  const requested = responseBarrier(), release = responseBarrier();
  await page.route("**/api/semantic-workspace/reusable-sources/resolve", async route => { requested.release(); await release.promise; return route.fulfill({ json: { items: [reusableFixtures()[2]] } }); });
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("保留原来的文字");
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await page.getByRole("checkbox", { name: "选择 正式分析.md" }).check();
  await page.getByRole("button", { name: "添加 1 份资料" }).click();
  await requested.promise;
  await page.getByRole("button", { name: "取消添加" }).click();
  release.release();
  await expect(page.getByRole("button", { name: "历史资料", exact: true })).toBeFocused();
  await expect(page.getByLabel("任务要求", { exact: true })).toHaveValue("保留原来的文字");
  await expect(page.getByLabel("已选历史资料")).toHaveCount(0);
});

test("#136 历史列表可见后失权不添加，不使用缓存正文", async ({ page }) => {
  await mockReusable(page);
  let previews = 0;
  await page.route("**/api/semantic-workspace/reusable-sources/resolve", route => route.fulfill({ json: { items: [{ ...reusableFixtures()[2], availability: "unavailable", reason_code: "unavailable" }] } }));
  await page.route("**/api/semantic-workspace/reusable-sources/outputs/history-output/preview?*", route => { previews++; return route.fulfill({ json: {} }); });
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("既有任务保持");
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await page.getByRole("button", { name: "预览 正式分析.md" }).click();
  await expect(page.getByRole("alert")).toContainText("所选资料不可用");
  expect(previews).toBe(0);
  await page.getByRole("button", { name: "返回资料列表" }).click();
  await page.getByRole("checkbox", { name: "选择 正式分析.md" }).check();
  await page.getByRole("button", { name: "添加 1 份资料" }).click();
  await expect(page.getByRole("alert")).toContainText("所选资料已不可用");
  await page.getByRole("button", { name: "取消添加" }).click();
  await expect(page.getByLabel("已选历史资料")).toHaveCount(0);
  await expect(page.getByLabel("任务要求", { exact: true })).toHaveValue("既有任务保持");
});

test("#136 Owner切换不恢复其他人历史引用和预览", async ({ page }) => {
  await mockReusable(page);
  await page.goto("/data-prep");
  await selectHistoricalOutput(page);
  const restored: unknown[] = [];
  await page.route("**/api/auth/me", route => route.fulfill({ json: { user_id: "u2", username: "second", role: "admin" } }));
  await page.route("**/api/semantic-workspace/reusable-sources/resolve", route => { restored.push(route.request().postDataJSON()); return route.fulfill({ json: { items: [] } }); });
  await page.route("**/api/semantic-workspace/reusable-sources?*", route => route.fulfill({ json: { items: [], total: 0, next_cursor: null, snapshot_token: "u2", page_complete: true } }));
  await page.reload();
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await expect(page.getByText("暂无已保存的历史资料；可以先添加文件或公开网页。")).toBeVisible();
  await expect(page.getByText("正式分析.md", { exact: true })).toHaveCount(0);
  expect(restored).toEqual([]);
});

test("#136 引用清单显示非活动版本与未知导出，分页变化不拼旧清单", async ({ page }) => {
  await mockReusable(page);
  let changed = false;
  await page.route("**/api/semantic-workspace/source-references?*", route => {
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    if (cursor) { changed = true; return route.fulfill({ status: 409, json: { detail: "references_changed，请重新读取引用清单" } }); }
    return route.fulfill({ json: { source_key: "delivery_output:history-output", items: changed ? [] : [{ task_id: "older-task", revision: 1, reference_kind: "revision", use_id: null, state: "retained", in_recycle_bin: true }, { task_id: null, revision: null, reference_kind: "export", use_id: "unknown-export", state: "unknown", in_recycle_bin: null }], total: changed ? 0 : 3, unknown_uses: changed ? 0 : 1, next_cursor: changed ? null : "next", snapshot_token: changed ? "r2" : "r1", page_complete: changed } });
  });
  await page.goto("/data-prep");
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await page.getByRole("button", { name: "预览 正式分析.md" }).click();
  await expect(page.getByText("销售金额已汇总为 200 元。")).toBeVisible();
  await page.getByRole("button", { name: "查看出处与引用" }).click();
  await expect(page.getByLabel("资料引用清单")).toContainText("older-task · V1");
  await expect(page.getByLabel("资料引用清单")).toContainText("未知使用 1");
  await page.getByRole("button", { name: "加载更多引用" }).click();
  await expect(page.getByRole("alert")).toContainText("references_changed");
  await expect(page.getByLabel("资料引用清单")).toHaveCount(0);
  await page.getByRole("button", { name: "查看出处与引用" }).click();
  await expect(page.getByLabel("资料引用清单")).toHaveText("0 个引用");
});

test("#136 正式交付引用跨页保留已清理原任务记录，不伪造导航", async ({ page }) => {
  await mockReusable(page);
  const requestedPages: URL[] = [];
  await page.route("**/api/semantic-workspace/source-references?*", route => {
    const url = new URL(route.request().url()); requestedPages.push(url);
    const second = url.searchParams.has("cursor");
    return route.fulfill({ json: { source_key: "delivery_output:history-output", items: second
      ? [{ task_id: "cleaned-consumer", revision: 3, reference_kind: "delivery", delivery_id: "consumer-delivery", run_id: "consumer-run", task_exists: false, use_id: null, state: "published", in_recycle_bin: null }]
      : [{ task_id: "active-consumer", revision: 2, reference_kind: "revision", use_id: null, state: "retained", in_recycle_bin: false }], total: 2, unknown_uses: 0, next_cursor: second ? null : "delivery-page", snapshot_token: "refs-with-formal-consumer", page_complete: second } });
  });
  await page.goto("/data-prep");
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await page.getByRole("button", { name: "预览 正式分析.md", exact: true }).click();
  await expect(page.getByText("销售金额已汇总为 200 元。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看出处与引用", exact: true }).click();
  const references = page.getByLabel("资料引用清单", { exact: true });
  await expect(references).toContainText("已显示 1 / 2 条引用");
  await page.getByRole("button", { name: "加载更多引用", exact: true }).click();
  await expect(references).toContainText("已显示 2 / 2 条引用");
  await expect(references).toContainText("active-consumer · V2");
  await expect(references).toContainText("cleaned-consumer · V3");
  await expect(references).toContainText("正式交付记录");
  await expect(references).toContainText("consumer-delivery");
  await expect(references).toContainText("已发布");
  await expect(references).toContainText("原任务记录已清理");
  await expect(references.getByRole("link")).toHaveCount(0);
  await expect(references.getByRole("button")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "预览 正式分析.md", exact: true })).toHaveCount(0);
  expect(new URL(page.url()).searchParams.has("task")).toBe(false);
  expect(requestedPages).toHaveLength(2);
  expect(requestedPages[1].searchParams.get("cursor")).toBe("delivery-page");
  expect(requestedPages[1].searchParams.get("snapshot_token")).toBe("refs-with-formal-consumer");
});

test("#136 已有任务修订第三类全量，未知恢复不丢输出ID", async ({ page }) => {
  await mockReusable(page);
  const task = workspaceTask("rev-history", "completed", "基于历史继续处理");
  const detail = workspaceDetail(task, { upload_ids: [], uploads: [], delivery_output_ids: ["history-output"], reusable_sources: [reusableFixtures()[2]] });
  await page.route("**/api/semantic-workspace/tasks/rev-history", route => route.fulfill({ json: detail }));
  const requests: { body: Record<string, unknown>; key: string }[] = [];
  await page.route("**/api/semantic-workspace/tasks/rev-history/revisions", route => { requests.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] }); return route.fulfill({ status: 503, json: { detail: "修订未知" } }); });
  await page.goto("/data-prep?task=rev-history");
  await page.getByRole("button", { name: "编辑本次资料", exact: true }).click();
  await expect(page.getByLabel("已选历史资料")).toContainText("正式分析.md");
  await page.getByRole("button", { name: "创建新版本", exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  await page.reload();
  await page.getByRole("button", { name: "恢复上次资料修订", exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toEqual(requests[0]);
  expect(requests[0].body).toMatchObject({ upload_ids: [], source_snapshot_ids: [], delivery_output_ids: ["history-output"] });
});

test("#136 历史选择明暗390与1440、键盘IME和取消保留完整草稿", async ({ page }, testInfo) => {
  await mockReusable(page);
  await page.goto("/data-prep");
  const prompt = page.getByLabel("任务要求", { exact: true });
  await prompt.fill("中文组合态保留需求");
  await prompt.dispatchEvent("compositionstart");
  await prompt.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
  await prompt.dispatchEvent("compositionend", { data: "资料" });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "历史资料", exact: true }).focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "从历史资料添加" });
  await dialog.getByRole("checkbox", { name: "选择 正式分析.md" }).focus();
  await page.keyboard.press("Space");
  for (const theme of ["light", "dark"]) for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.evaluate(async theme => { document.documentElement.classList.toggle("dark", theme === "dark"); void getComputedStyle(document.body).backgroundColor; await Promise.all(document.getAnimations().filter(animation => animation instanceof CSSTransition).map(animation => animation.finished.catch(() => undefined))); }, theme);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const scan = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
    expect(scan.violations.filter(item => item.impact === "serious" || item.impact === "critical")).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`history-${theme}-${width}.png`), fullPage: true });
  }
  await dialog.getByRole("button", { name: "预览 正式分析.md" }).click();
  await expect(dialog.getByText("销售金额已汇总为 200 元。")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog.getByRole("button", { name: "预览 正式分析.md" })).toBeFocused();
  await expect(dialog.getByRole("checkbox", { name: "选择 正式分析.md" })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "历史资料", exact: true })).toBeFocused();
  await expect(prompt).toHaveValue("中文组合态保留需求");
  await expect(page.getByLabel("已选历史资料")).toHaveCount(0);
});

test("#136 精确引用只添加一次，同摘要不同出处仍可选", async ({ page }) => {
  const items = await mockReusable(page);
  const second = { ...items[2], source_key: "delivery_output:second-output", output_id: "second-output", label: "另一出处.md", origin: { ...items[2].origin, revision: 3 } };
  await page.route("**/api/semantic-workspace/reusable-sources?*", route => route.fulfill({ json: { items: [...items, second], total: 4, page_complete: true, next_cursor: null, snapshot_token: "catalog" } }));
  await page.route("**/api/semantic-workspace/reusable-sources/resolve", route => { const ids = route.request().postDataJSON().delivery_output_ids; return route.fulfill({ json: { items: [items[2], second].filter(item => ids.includes(item.output_id)) } }); });
  await page.goto("/data-prep");
  await selectHistoricalOutput(page);
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "选择 正式分析.md" })).toBeDisabled();
  await page.getByRole("checkbox", { name: "选择 另一出处.md" }).check();
  await page.getByRole("button", { name: "添加 1 份资料" }).click();
  const selected = page.getByLabel("已选历史资料");
  await expect(selected.getByRole("button", { name: "移除历史资料 正式分析.md" })).toHaveCount(1);
  await expect(selected.getByRole("button", { name: "移除历史资料 另一出处.md" })).toHaveCount(1);
});

test("#136 历史网页硬缺口不会被正式结果抵消", async ({ page }) => {
  const items = await mockReusable(page);
  const attempt = mixedAttempt(1);
  attempt.snapshot.coverage.status = "hard_insufficient";
  items[1].coverage = attempt.snapshot.coverage;
  await page.route("**/api/semantic-workspace/reusable-sources?*", route => route.fulfill({ json: { items, total: 3, page_complete: true, next_cursor: null, snapshot_token: "hard" } }));
  await page.route("**/api/semantic-workspace/reusable-sources/resolve", route => route.fulfill({ json: { items: [items[1], items[2]] } }));
  await page.route("**/api/semantic-workspace/source-acquisitions/mixed-attempt-1", route => route.fulfill({ json: attempt }));
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("必须完整覆盖原范围");
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await page.getByRole("checkbox", { name: "选择 历史网页组" }).check();
  await page.getByRole("checkbox", { name: "选择 正式分析.md" }).check();
  await page.getByRole("button", { name: "添加 2 份资料" }).click();
  await expect(page.getByText("此组有效页面不足，当前仅供查看；其他资料不能抵消该组硬性缺口。")).toBeVisible();
  await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeDisabled();
});

test("#136 同源另一标签页更新三类草稿，旧核验迟到不覆盖", async ({ page, context }) => {
  await mockReusable(page);
  const requested = responseBarrier(), release = responseBarrier();
  await page.route("**/api/semantic-workspace/reusable-sources/resolve", async route => { requested.release(); await release.promise; return route.fulfill({ json: { items: [reusableFixtures()[2]] } }); });
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("旧选择尚未确认");
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await page.getByRole("checkbox", { name: "选择 正式分析.md" }).check();
  await page.getByRole("button", { name: "添加 1 份资料" }).click();
  await requested.promise;
  const peer = await context.newPage();
  await peer.route("**/*", route => route.fulfill({ contentType: "text/html", body: "<html lang=zh><title>草稿窗口</title></html>" }));
  await peer.goto(new URL("/draft-peer", page.url()).href);
  expect(new URL(peer.url()).origin).toBe(new URL(page.url()).origin);
  await peer.evaluate(() => localStorage.setItem("mangrove_workspace_draft_u1_new", JSON.stringify({ draft: { prompt: "新标签页已更新" }, sources: [], history: [{ source_key: "delivery_output:peer-output", kind: "delivery_output", output_id: "peer-output" }] })));
  await expect(page.getByText("当前草稿已在其他页面更新", { exact: false })).toBeVisible();
  release.release();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByLabel("已选历史资料")).toHaveCount(0);
  expect(await peer.evaluate(() => localStorage.getItem("mangrove_workspace_draft_u1_new"))).toContain("peer-output");
  await peer.close();
});

test("#136 正式结果模式确定拒绝保留集合与模型，不自动换执行配置", async ({ page }) => {
  await mockReusable(page);
  const posts: { body: Record<string, unknown>; key: string }[] = [];
  await page.route("**/api/semantic-workspace/tasks", route => {
    posts.push({ body: route.request().postDataJSON(), key: route.request().headers()["idempotency-key"] });
    return route.fulfill({ status: 422, headers: { "X-Mangrove-Task-Outcome": "rejected" }, json: { detail: "当前兼容执行模式不支持正式结果作为输入，请选择已启用的模型配置后重试。" } });
  });
  await page.goto("/data-prep");
  await page.getByLabel("任务要求", { exact: true }).fill("继续昨天的正式分析");
  await selectHistoricalOutput(page);
  await page.getByRole("button", { name: "开始执行", exact: true }).click();
  await expect(page.getByText("当前兼容执行模式不支持正式结果作为输入，请选择已启用的模型配置后重试。", { exact: true }).first()).toBeVisible();
  await expect(page.getByLabel("已选历史资料")).toContainText("正式分析.md");
  await page.getByLabel("任务要求", { exact: true }).fill("调整要求后继续昨天的正式分析");
  await page.getByRole("button", { name: "开始执行", exact: true }).click();
  await expect.poll(() => posts.length).toBe(2);
  for (const post of posts) {
    expect(post.body).toMatchObject({ upload_ids: [], source_snapshot_ids: [], delivery_output_ids: ["history-output"], provider: "local", model: "Qwen3.6-35B-A3B" });
    expect(post.body).not.toHaveProperty("runtime_version");
  }
  expect(posts[1].key).not.toBe(posts[0].key);
});

test("#136 添加核验在途不能切换预览操作，取消后迟到不添加", async ({ page }) => {
  const items = await mockReusable(page);
  await page.goto("/data-prep");
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await page.getByLabel("选择 正式分析.md", { exact: true }).check();
  await page.getByRole("button", { name: "预览 正式分析.md", exact: true }).click();
  await expect(page.getByText("销售金额已汇总为 200 元。", { exact: true })).toBeVisible();
  const requested = responseBarrier(), release = responseBarrier();
  await page.route("**/api/semantic-workspace/reusable-sources/resolve", async route => {
    requested.release(); await release.promise;
    return route.fulfill({ json: { items: [items[2]] } });
  });
  await page.getByRole("button", { name: "添加 1 份资料", exact: true }).click();
  await requested.promise;
  await expect(page.getByRole("button", { name: "返回资料列表", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "查看出处与引用", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("历史资料预览", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "取消添加", exact: true }).click();
  release.release();
  await expect(page.getByRole("button", { name: "历史资料", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "历史资料", exact: true }).click();
  await expect(page.getByLabel("选择 正式分析.md", { exact: true })).toBeEnabled();
  await expect(page.getByLabel("选择 正式分析.md", { exact: true })).not.toBeChecked();
  await page.getByRole("button", { name: "取消添加", exact: true }).click();
  await expect(page.getByRole("button", { name: "移除历史资料 正式分析.md" })).toHaveCount(0);
});

test("#136 派生画布重新读取期间不展示失权前缓存正文", async ({ page }) => {
  await mockReusable(page);
  const first = reusableFixtures()[2];
  const second = { ...first, source_key: "delivery_output:another-output", output_id: "another-output", label: "另一个正式结果.md" };
  const task = workspaceTask("derived-cache", "completed", "核对两个正式来源");
  await page.route("**/api/semantic-workspace/tasks/derived-cache", route => route.fulfill({ json: workspaceDetail(task, { upload_ids: [], uploads: [], delivery_output_ids: [first.output_id, second.output_id], reusable_sources: [first, second] }) }));
  let reads = 0;
  const requested = responseBarrier(), release = responseBarrier();
  await page.route("**/api/semantic-workspace/tasks/derived-cache/sources/*/preview?*", async route => {
    const outputId = route.request().url().includes("/history-output/") ? first.output_id : second.output_id;
    if (outputId === first.output_id && ++reads > 1) {
      requested.release(); await release.promise;
      return route.fulfill({ status: 403, json: { detail: "来源权限已失效" } });
    }
    const item = outputId === first.output_id ? first : second;
    return route.fulfill({ json: { task_id: "derived-cache", revision: 1, artifact_id: outputId, kind: "document", action: "preview", items: [{ id: "passage", type: "passage", label: "正式来源", content: `正文 ${outputId}`, evidence_refs: [] }], total: 1, offset: 0, limit: 30, warnings: [], source_key: item.source_key, identity: "derived", sha256: item.sha256, output_id: outputId, delivery_id: "origin-delivery", run_id: "origin-run", origin: item.origin, representation: { kind: "output", sha256: item.sha256, associated_output_id: outputId, media_type: "text/markdown", lineage_available: true } } });
  });
  await page.goto("/data-prep?task=derived-cache");
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect(page.getByText("正文 history-output", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "预览文件", exact: true }).selectOption("another-output");
  await expect(page.getByText("正文 another-output", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "预览文件", exact: true }).selectOption("history-output");
  await requested.promise;
  await expect(page.getByText("正文 history-output", { exact: true })).toHaveCount(0);
  await expect(page.getByText("正在读取正式来源…", { exact: true })).toBeVisible();
  release.release();
  await expect(page.getByText("来源权限已失效，请重新核对来源。", { exact: true })).toBeVisible();
  await expect(page.getByText("正文 history-output", { exact: true })).toHaveCount(0);
});

test("#136 派生输入任务画布读取冻结输出，移除后修订发送空第三类", async ({ page }) => {
  await mockReusable(page);
  const task = workspaceTask("derived-canvas", "completed", "使用正式历史结果");
  const upload = { upload_id: "file-kept", original_name: "保留.csv", media_type: "text/csv", size_bytes: 3, sha256: "b".repeat(64) };
  await page.route("**/api/data-sources/uploads/file-kept", route => route.fulfill({ json: upload }));
  await page.route("**/api/semantic-workspace/tasks/derived-canvas", route => route.fulfill({ json: workspaceDetail(task, { upload_ids: [upload.upload_id], uploads: [upload], delivery_output_ids: ["history-output"], reusable_sources: [reusableFixtures()[2]] }) }));
  const queries: string[] = [];
  await page.route("**/api/semantic-workspace/tasks/derived-canvas/sources/history-output/preview?*", route => {
    queries.push(route.request().url());
    return route.fulfill({ json: { task_id: "derived-canvas", revision: 1, artifact_id: "history-output", kind: "document", action: "preview", items: [{ id: "source-1", type: "passage", label: "正式来源", content: "冻结的正式输入本体", evidence_refs: [] }], total: 1, offset: 0, limit: 30, warnings: [], source_key: "delivery_output:history-output", identity: "derived", sha256: "a".repeat(64), output_id: "history-output", delivery_id: "origin-delivery", run_id: "origin-run", origin: reusableFixtures()[2].origin, representation: { kind: "output", sha256: "a".repeat(64), associated_output_id: "history-output", media_type: "text/markdown", lineage_available: true } } });
  });
  const submitted: Record<string, unknown>[] = [];
  await page.route("**/api/semantic-workspace/tasks/derived-canvas/revisions", route => { submitted.push(route.request().postDataJSON()); return route.fulfill({ status: 503, json: { detail: "测试保留未知" } }); });
  await page.goto("/data-prep?task=derived-canvas");
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await page.getByRole("combobox", { name: "预览文件", exact: true }).selectOption("history-output");
  await expect(page.getByText("冻结的正式输入本体", { exact: true })).toBeVisible();
  expect(new URL(queries[0]).searchParams.get("revision")).toBe("1");
  await expect(page.getByRole("button", { name: "下载正式来源文件" })).toBeVisible();
  await page.getByRole("button", { name: "关闭原文件预览", exact: true }).click();
  await page.getByRole("button", { name: "编辑本次资料", exact: true }).click();
  await page.getByRole("button", { name: "移除历史资料 正式分析.md" }).click();
  await page.getByRole("button", { name: "创建新版本", exact: true }).click();
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0]).toMatchObject({ upload_ids: ["file-kept"], source_snapshot_ids: [], delivery_output_ids: [] });
});


test("#137 已删网页空快照保留整组，明确移除后才可换新资料", async ({ page }) => {
  await mockWorkspace(page); let acquisitions = 0;
  const fixture = previewIdentityFixture("A", 2);
  await page.route("**/api/semantic-workspace/tasks/identity-task", route => route.fulfill({ json: { ...fixture.detail, web_sources: [{ source_snapshot_id: "deleted-web-group", snapshot: null, availability: "unavailable", reason_code: "source_deleted" }], source_integrity: { state: "source_deleted", deleted_source_keys: ["web_artifact:deleted-page"], can_rerun: false, can_reverify: false } } }));
  await page.route("**/api/semantic-workspace/tasks/identity-task/preview?*", route => route.fulfill({ json: fixture.preview }));
  await page.route("**/api/semantic-workspace/source-acquisitions**", route => { acquisitions++; return route.fulfill({ status: 404, json: {} }); });
  const posts: Record<string, unknown>[] = [];
  await page.route("**/api/semantic-workspace/tasks/identity-task/revisions", route => { posts.push(route.request().postDataJSON()); return route.fulfill({ status: 503, json: { detail: "合成测试保留修订未知" } }); });
  await page.goto("/data-prep?task=identity-task");
  await expect(page.getByText("来源组已清理：deleted-web-group", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  await expect(page.getByText("A-V2-正文", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "编辑本次资料", exact: true }).click();
  await expect(page.getByLabel("已选网页资料")).toContainText("来源已删除");
  await expect(page.getByRole("button", { name: "创建新版本", exact: true })).toBeDisabled();
  await page.reload();
  await page.getByRole("button", { name: "编辑本次资料", exact: true }).click();
  await expect(page.getByLabel("已选网页资料")).toContainText("deleted-web-group");
  await expect(page.getByRole("button", { name: "创建新版本", exact: true })).toBeDisabled();
  expect(acquisitions).toBe(0); expect(posts).toHaveLength(0);
  await page.getByRole("button", { name: "移除网页组 deleted-web-group", exact: true }).click();
  await page.getByRole("button", { name: "移除 A-V2-原件.csv", exact: true }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "新证据.csv", mimeType: "text/csv", buffer: Buffer.from("ID\nN01", "utf-8") });
  await expect(page.getByText("已上传，等待执行", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "创建新版本", exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toMatchObject({ expected_active_revision: 2, upload_ids: ["upload-e2e"], source_snapshot_ids: [], delivery_output_ids: [] });
  expect(acquisitions).toBe(0);
});


test("#137 历史网页删除不能借当前完整版本或旧缓存恢复原文", async ({ page }) => {
  await mockWorkspace(page);
  const old = previewIdentityFixture("A", 1), current = previewIdentityFixture("A", 2);
  const snapshot = mixedAttempt(1).snapshot!;
  let deleted = false, marked = false;
  const requested = responseBarrier(), release = responseBarrier();
  await page.route(/\/api\/semantic-workspace\/tasks\/identity-task(?:\?.*)?$/, route => {
    const isOld = new URL(route.request().url()).searchParams.get("revision") === "1";
    return route.fulfill({ json: { ...(isOld ? old.detail : current.detail), ...(isOld ? { upload_ids: [], uploads: [], web_sources: [{ source_snapshot_id: snapshot.snapshot_id, snapshot }] } : {}), source_integrity: { state: isOld && marked ? "source_deleted" : "intact", deleted_source_keys: isOld && marked ? ["web_artifact:mixed-artifact-1"] : [], can_rerun: !(isOld && marked), can_reverify: !(isOld && marked) } } });
  });
  await page.route("**/identity-task/preview?*", route => route.fulfill({ json: new URL(route.request().url()).searchParams.get("revision") === "1" ? old.preview : current.preview }));
  await page.route("**/identity-task/sources/mixed-artifact-1/preview?*", async route => {
    if (deleted) { requested.release(); await release.promise; return route.fulfill({ status: 404, json: { detail: "来源已删除" } }); }
    return route.fulfill({ json: { task_id: "identity-task", revision: 1, artifact_id: "mixed-artifact-1", snapshot_id: snapshot.snapshot_id, upload_id: null, sha256: "a".repeat(64), original_name: "历史网页.html", media_type: "text/html", content_url: null, kind: "web", text_preview: "历史网页真实旧正文", representation: { kind: "source", parser_or_inspector_version: "fixture" }, is_complete: false, truncated: true } });
  });
  await page.goto("/data-prep?task=identity-task");
  await page.getByLabel("结果版本").selectOption("1");
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect(page.getByText("历史网页真实旧正文", { exact: true })).toBeVisible();
  await page.getByLabel("结果版本").selectOption("2");
  await expect(page.getByText("历史网页真实旧正文", { exact: true })).toHaveCount(0);
  await expect(page.getByText("部分来源已删除，不能按原来源完整重跑或复验。", { exact: false })).toHaveCount(0);
  deleted = true;
  await page.getByLabel("结果版本").selectOption("1");
  await expect(page.getByRole("button", { name: "关闭原文件预览", exact: true })).toBeVisible();
  await requested.promise;
  await expect(page.getByText("历史网页真实旧正文", { exact: true })).toHaveCount(0);
  release.release();
  await expect(page.getByRole("alert")).toContainText("来源已删除");
  await expect(page.getByText("历史网页真实旧正文", { exact: true })).toHaveCount(0);
  marked = true;
  await page.getByLabel("结果版本").selectOption("2");
  await expect(page.getByText("部分来源已删除，不能按原来源完整重跑或复验。", { exact: false })).toHaveCount(0);
  await page.getByLabel("结果版本").selectOption("1");
  await expect(page.getByText("部分来源已删除，不能按原来源完整重跑或复验。", { exact: false })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("来源已删除，原文不可再读取");
  await expect(page.getByText("历史网页真实旧正文", { exact: true })).toHaveCount(0);
});
