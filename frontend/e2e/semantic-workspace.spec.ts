import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

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
      await expect(page.getByText("三步完成第一个任务")).toBeVisible();
      await expect(page.getByText("筛选并交付")).toBeVisible();
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

    const webMode = page.getByRole("radio", { name: "公开网页" });
    await webMode.focus();
    await webMode.press("Space");
    await expect(webMode).toBeChecked();
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

    await page.getByLabel("想得到什么结果").fill("生成产品摘要并保留来源证据");
    await page.getByLabel("必须包含").fill("产品名称\n公开说明");
    await page.getByLabel("明确不要").fill("不要推测未公开价格");
    await page.getByLabel("任务模板（可选）").selectOption(
      "public-company-summary",
    );
    await page.getByText("个人记忆（可选）").click();
    await page.getByText("公司名使用官网全称").click();
    await page.getByRole("button", { name: "检查上下文草案" }).click();
    await expect(page.getByText("已检查，可以启动")).toBeVisible();
    await expect(page.getByText("按公司提取名称和来源证据", { exact: true }))
      .toBeVisible();
    await page.getByRole("button", { name: "启动任务" }).click();
    await expect.poll(() => taskSubmitted).not.toBeNull();
    expect(taskSubmitted).toMatchObject({
      objective_text: "生成产品摘要并保留来源证据",
      upload_ids: [],
      source_snapshot_id: "snapshot-1",
      must_include: ["产品名称", "公开说明"],
      explicit_exclusions: ["不要推测未公开价格"],
      quantity_requirement: "当前页面中有证据的全部内容",
      completeness_requirement: "仅对当前精确页面负责",
      output_formats: ["markdown"],
      runtime_version: "pi",
      model_connection_id: null,
      external_api_confirmed: false,
      context_selection: {
        template: { template_id: "public-company-summary", version: 1 },
        memories: [{ memory_id: 7 }],
      },
      context_preview_sha256: `sha256:${"3".repeat(64)}`,
    });
    expect(taskIdempotencyKey.length).toBeGreaterThan(5);
    await expect(page.getByRole("heading", { name: "公开网页产品摘要" }))
      .toBeVisible();

    await page.reload();
    await page.getByText("公开网页", { exact: true }).click();
    await expect(
      page.getByRole("region", { name: "获取一个公开网页" })
        .getByText("网页来源已冻结", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("这是页面中冻结的公开产品说明。"))
      .toBeVisible();
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

    await page.getByLabel("想得到什么结果").fill("汇总已成功读取的页面并披露缺口");
    await page.getByRole("button", { name: "检查上下文草案" }).click();
    await expect(page.getByText("已检查，可以启动")).toBeVisible();
    await page.getByRole("button", { name: "启动任务" }).click();
    await expect.poll(() => taskSubmitted).not.toBeNull();
    expect(taskSubmitted).toMatchObject({
      source_snapshot_id: "snapshot-1",
      quantity_requirement: "当前已成功读取页面中有证据的内容",
      completeness_requirement: "披露失败、截断和未覆盖页面，不承诺完整覆盖",
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
    await expect(page.getByRole("button", { name: "启动任务" })).toBeDisabled();
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
    await page.getByText("公开网页摘要", { exact: true }).click();
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
      source_snapshot_id: "snapshot-1",
      must_include: ["产品名称"],
      explicit_exclusions: ["不得推测价格"],
      quantity_requirement: "当前页面中有证据的全部内容",
      completeness_requirement: "仅对当前精确页面负责",
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
    await page.getByText("公开网页", { exact: true }).click();

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
    await expect(page.getByRole("link", { name: "对话工作区" })).toBeVisible();
    await expect(page.getByRole("button", { name: "浅色主题" })).toBeVisible();
    await page.locator("aside").getByRole("button", { name: "关闭导航" }).click();
    await expect(page.getByRole("link", { name: "对话工作区" })).toBeHidden();
    await page.getByRole("button", { name: "打开导航" }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("link", { name: "对话工作区" })).toBeHidden();
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
    await expect(page.getByAltText("howso@Mangrove")).toBeVisible();
    await expect(page.getByRole("button", { name: "新建任务" })).toBeVisible();
    if (process.env.MANGROVE_VISUAL_CAPTURE === "1") {
      await page.screenshot({
        path: "../.pytest-tmp/workspace-upload-preview.png",
      });
    }
  });

  test("Word 上传完成后自动打开并显示原文件预览", async ({ page }) => {
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

  test("管理员可显式选择 Mangrove 增强模式且仍使用本地模型", async ({ page }) => {
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
      runtime_version: "pi",
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
    await expect(page.getByRole("radio", { name: "平台默认（推荐）" }))
      .toBeChecked();
    await page.getByRole("radio", { name: "增强模式（Pi）" }).check();
    await expect(page.getByText("输入文件只读", { exact: false }))
      .toBeVisible();
    await expect(page.getByLabel("执行模型")).toHaveValue(
      "local::Qwen3.6-35B-A3B",
    );
    await page.getByRole("checkbox", { name: /Python 表格处理/ }).check();
    await page.getByRole("button", { name: "开始执行" }).click();

    expect(submitted).toMatchObject({
      runtime_version: "pi",
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

  test("无可用连接的普通用户仍可显式选择 Legacy", async ({ page }) => {
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
    await expect(page.getByRole("radio", { name: "平台默认（推荐）" }))
      .toBeChecked();
    await expect(page.getByRole("radio", { name: "增强模式（Pi）" }))
      .toBeDisabled();
    await page.getByRole("radio", { name: "兼容模式（Legacy）" }).check();
    await page.getByRole("button", { name: "开始执行" }).click();

    expect(submitted).toMatchObject({ runtime_version: "legacy" });
    await expect(page.getByText("实际执行：兼容模式（Legacy）"))
      .toBeVisible();
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
    await expect(page.getByRole("radio", { name: "平台默认（推荐）" }))
      .toBeChecked();

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

    await page.getByRole("radio", { name: "兼容模式（Legacy）" }).check();
    await expect(page.getByTestId("external-model-disclosure"))
      .not.toBeVisible();
    await page.getByRole("radio", { name: "平台默认（推荐）" }).check();
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
    await expect(page.getByText("张三工作量.xlsx")).toBeVisible();
    await expect(page.getByText("可交付", { exact: true })).toBeVisible();
    const filenameBounds = await page.getByText("张三工作量.xlsx").boundingBox();
    const bundleBounds = await page.getByRole("button", { name: "下载全部" })
      .boundingBox();
    expect(filenameBounds?.width).toBeGreaterThan(100);
    expect(bundleBounds?.width).toBeGreaterThan(80);
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
    await page.route(
      "**/api/semantic-workspace/tasks/task-version/turns",
      async (route) => {
        submitted = route.request().postDataJSON();
        await route.fulfill({
          json: {
            result_id: "steering-1",
            task_id: "task-version",
            turn_id: "turn-1",
            delta_id: "delta-1",
            action: "revision_proposal",
            acknowledgement: "已形成修改草案，等待确认",
            answer: null,
            proposal_id: "proposal-1",
            run_id: "run-v1",
            revision: 1,
          },
        });
      },
    );
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
    await page.getByRole("button", { name: "立即停止并切换" }).click();
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
  });

  test("待确认任务可收起后重新打开，并可随时取消", async ({ page }) => {
    await mockWorkspace(page);
    let status = "needs_input";
    const waiting = workspaceTask("task-waiting", status, "待确认任务");
    const question = {
      kind: "plan",
      question_id: "q1",
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
    await expect(
      page.getByRole("heading", { name: "需要确认一项信息" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "稍后回答" }).click();
    await expect(page.getByRole("button", { name: /继续回答/ })).toBeVisible();
    await page.getByRole("button", { name: /继续回答/ }).click();
    await expect(
      page.getByRole("dialog").getByText(question.prompt),
    ).toBeVisible();
    await page.getByRole("button", { name: "稍后回答" }).click();
    await page.getByRole("button", { name: "取消任务" }).click();
    await page.getByRole("button", { name: "确认取消" }).click();
    await expect(page.getByText("任务已取消，未发布新的正式交付。")).toBeVisible();
    await expect(page.getByText("已取消", { exact: true }).first()).toBeVisible();
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
    await expect(page.getByText("选择任务或新建任务")).toBeVisible();
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
              },
              {
                event_id: "owner-action-resumed",
                sequence: 2,
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
    await expect(progressToggle).toContainText("工作 7.0 秒");
    await expect(progressToggle).toContainText("等待 1.0 秒");
    await expect(progressToggle).toContainText("开始");
    await expect(progressToggle).toContainText("完成");
    await expect(progressToggle).toContainText("9 个行动");
    await expect(progressToggle).toContainText("2 次工具");
    await expect(progressToggle).toContainText("至少 8,420 Tokens · 4 次调用 · 1 次未知");
    await expect(progressToggle).toContainText("已处理 1 次重试");
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
      page.getByAltText("howso@Mangrove").boundingBox(),
      page.getByRole("button", { name: "新建任务" }).boundingBox(),
      page.getByRole("heading", { name: "数据工作台" }).boundingBox(),
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
      ...workspaceTask("task-model-unknown", "failed", "模型结果待确认"),
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
    await page.route("**/api/semantic-workspace/tasks?*", (route) =>
      route.fulfill({ json: [failedTask] }));
    await page.route(
      "**/api/semantic-workspace/tasks/task-model-unknown",
      (route) => route.fulfill({
        json: workspaceDetail(failedTask),
      }),
    );
    let revisionPayload: Record<string, unknown> | null = null;
    await page.route(
      "**/api/semantic-workspace/tasks/task-model-unknown/revisions",
      async (route) => {
        revisionPayload = await route.request().postDataJSON();
        await route.fulfill({
          status: 202,
          json: { revision: 2 },
        });
      },
    );

    await page.goto("/data-prep");
    await page.getByRole("button", { name: /模型结果待确认/ }).click();
    const notice = page.getByTestId("task-failure-explanation");
    await expect(notice).toContainText("模型请求结果不确定");
    await notice.getByRole("button", { name: "重新执行" }).click();
    await expect(page.getByRole("alertdialog")).toContainText(
      "可能产生重复调用和费用",
    );
    await page.getByRole("button", { name: "确认重新执行" }).click();

    await expect.poll(() => revisionPayload).not.toBeNull();
    expect(revisionPayload).toEqual({
      instruction: "保持原要求，重新执行",
      external_api_confirmed: true,
      expected_active_revision: 1,
    });
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
    await expect(page.getByRole("status")).toContainText(
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
    await expect(page.getByRole("status")).toContainText("重验请求已受理");
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
    await expect(page.getByRole("status")).toContainText("重验请求已受理");
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
    await expect(page.getByRole("status")).toContainText("重验请求已受理");

    attemptStatus = "outcome_unknown";
    await page.reload();
    await page.getByRole("button", { name: /候选重验状态/ }).click();
    await expect(page.getByRole("alert")).toContainText("已停止自动重试");
    await expect(page.getByRole("button", { name: "使用最新规则重新验证" })).toHaveCount(0);

    attemptStatus = "passed";
    await page.reload();
    await page.getByRole("button", { name: /候选重验状态/ }).click();
    await expect(page.getByRole("status")).toContainText("还不是正式交付");
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
