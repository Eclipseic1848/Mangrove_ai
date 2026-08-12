import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const PRESETS = [
  {
    preset_id: "deepseek",
    version: "2026-07-30.1",
    display_name: "DeepSeek",
    description: "适合中文、推理和通用 Agent 任务",
    recommended_model: "deepseek-v4-flash",
    models: ["deepseek-v4-flash", "deepseek-v4-pro"],
    model_catalog: [
      {
        model_id: "deepseek-v4-flash",
        display_name: "DeepSeek V4 Flash",
        role: "balanced",
      },
      {
        model_id: "deepseek-v4-pro",
        display_name: "DeepSeek V4 Pro",
        role: "quality",
      },
    ],
    help_url: "https://api-docs.deepseek.com/",
  },
  {
    preset_id: "openai",
    version: "2026-07-30.1",
    display_name: "OpenAI",
    description: "原生 Responses API",
    recommended_model: "gpt-5.6-terra",
    models: ["gpt-5.6-terra"],
    model_catalog: [{
      model_id: "gpt-5.6-terra",
      display_name: "GPT-5.6 Terra",
      role: "balanced",
    }],
    help_url: "https://developers.openai.com/api/docs/models",
  },
];

async function mockSettings(
  page: Page,
  role: "user" | "admin" | "super_admin",
  theme: "light" | "dark" = "light",
) {
  await page.addInitScript(() => {
    localStorage.setItem("mangrove_token", "e2e-token");
  });
  await page.addInitScript((selectedTheme) => {
    localStorage.setItem("mangrove_theme", selectedTheme);
  }, theme);
  await page.route("**/api/auth/me", (route) => route.fulfill({
    json: {
      access_token: "e2e-token",
      user_id: `${role}-a`,
      username: role,
      display_name: role === "user"
        ? "普通用户甲"
        : role === "admin"
          ? "管理员甲"
          : "超级管理员甲",
      role,
    },
  }));
  await page.route("**/api/models", (route) => route.fulfill({
    json: {
      options: [{
        provider: "local",
        model: "Qwen3.6-35B-A3B",
        label: "本地模型 · Qwen3.6-35B-A3B",
      }],
      available: ["local"],
      default: {
        provider: "local",
        model: "Qwen3.6-35B-A3B",
        label: "本地模型 · Qwen3.6-35B-A3B",
      },
      document_default: {
        provider: "local",
        model: "Qwen3.6-35B-A3B",
        label: "本地模型 · Qwen3.6-35B-A3B",
      },
      document_default_source: "global",
    },
  }));
  await page.route("**/api/overview", (route) => route.fulfill({
    json: {
      collectors: [],
      scheduler: { enabled: false, active_count: 0 },
      connectors: {
        email: false,
        slack: false,
        embedding: false,
        checkpoint: true,
      },
      connectors_enabled: {
        email: false,
        slack: false,
        embedding: false,
        checkpoint: true,
      },
    },
  }));
  await page.route("**/api/config/self", (route) => route.fulfill({
    json: {
      items: [{
        key: "mc_cookie_dy",
        label: "抖音 Cookie",
        secret: true,
        group: "cookies",
        set: false,
        value: "",
      }],
    },
  }));
  await page.route("**/api/model-connections/presets", (route) => route.fulfill({
    json: { items: PRESETS },
  }));
  await page.route("**/api/model-connections", (route) => route.fulfill({
    json: { items: [] },
  }));
  await page.route("**/api/capability-governance/packs", (route) => route.fulfill({
    json: { items: [] },
  }));
  await page.route("**/api/capability-governance/validations", (route) => route.fulfill({
    json: { items: [] },
  }));
  await page.route(
    "**/api/settings/onboarding/model-connections",
    (route) => route.fulfill({ json: { state: "completed" } }),
  );
  await page.route("**/api/config/models*", (route) => route.fulfill({
    json: {
      models: {
        local: ["Qwen3.6-35B-A3B"],
        document: ["local::Qwen3.6-35B-A3B"],
      },
      default_provider: "local",
      available_providers: ["local"],
      ...(role !== "user"
        ? { local_urls: { "Qwen3.6-35B-A3B": "http://192.168.1.20:6012/v1" } }
        : {}),
    },
  }));
  await page.route("**/api/config?*", (route) => route.fulfill({
    json: {
      groups: [
        {
          key: "llm_deepseek",
          label: "模型 · DeepSeek",
          items: [{
            key: "deepseek_api_key",
            label: "DeepSeek API Key",
            value: "•••• 1234",
            source: "override",
            secret: true,
          }],
        },
        {
          key: "search",
          label: "搜索与采集服务",
          items: [{
            key: "tavily_api_key",
            label: "Tavily API Key",
            value: "",
            source: "env",
            secret: true,
          }],
        },
      ],
    },
  }));
}

test("普通用户只看到个人范围并可配置自己的 Provider 连接", async ({ page }) => {
  await mockSettings(page, "user");
  let configured: Record<string, unknown> | null = null;
  await page.route("**/api/model-connections/presets/deepseek", (route) => {
    configured = route.request().postDataJSON();
    return route.fulfill({
      json: {
        connection_id: "conn-user-a",
        owner_scope: "user_personal",
        preset_id: "deepseek",
        display_name: "DeepSeek",
        model: "deepseek-v4-flash",
        status: "verified",
        key_hint: "1234",
      },
    });
  });

  await page.goto("/settings");

  await expect(page.getByRole("tab", { name: "我的设置" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "模型与连接" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "采集账号" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "平台配置" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "运行与诊断" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "能力治理" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "我的能力验证" })).toBeVisible();

  await page.getByRole("tab", { name: "模型与连接" }).click();
  await expect(page.getByRole("heading", { name: "连接一个模型服务" })).toBeVisible();
  await expect(page.getByText("自定义兼容接口")).toHaveCount(0);
  await page.getByLabel("模型 Provider").selectOption("deepseek");
  await expect(page.getByLabel("首选默认模型")).toHaveValue("deepseek-v4-flash");
  await expect(page.getByLabel("Base URL")).toHaveCount(0);
  await expect(page.getByLabel("API 格式")).toHaveCount(0);
  await page.getByLabel("连接名称").fill("我的 DeepSeek");
  await page.getByLabel("API Key").fill("sk-user-secret-1234");
  await page.getByRole("button", { name: "保存并验证" }).click();

  expect(configured).toEqual({
    display_name: "我的 DeepSeek",
    api_key: "sk-user-secret-1234",
    model: "deepseek-v4-flash",
  });
  await expect(page.getByText("连接已验证并保存")).toBeVisible();
});

test("管理员从能力卡片创建验证并渐进查看步骤缺口", async ({ page }) => {
  await mockSettings(page, "admin");
  let validationRun: Record<string, any> | null = null;
  await page.route("**/api/capability-governance/packs", (route) => route.fulfill({
    json: {
      items: [
        {
          pack_id: "gray-python-table",
          version: "1.0.0",
          scope: "platform",
          maturity: "verified",
          lifecycle: "active",
          eligibility: "eligible",
          source: "legacy_compat",
          owner_id: null,
          digest: `sha256:${"a".repeat(64)}`,
          can_validate: true,
        },
        {
          pack_id: "everything-mcp",
          version: "2026.7.4",
          scope: "platform",
          maturity: "verified",
          lifecycle: "deprecated",
          eligibility: "eligible",
          source: "legacy_compat",
          owner_id: null,
          digest: `sha256:${"b".repeat(64)}`,
          can_validate: false,
        },
      ],
    },
  }));
  await page.route("**/api/capability-governance/validations", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      validationRun = {
        run_id: "capval-e2e",
        owner_id: "admin-a",
        target: {
          pack_id: body.pack_id,
          version: body.version,
          digest: body.digest,
        },
        task_ref: { task_id: body.task_id, revision: body.revision },
        status: "queued",
        evidence: [],
        created_at: "2026-08-07T00:00:00Z",
      };
      return route.fulfill({ status: 202, json: validationRun });
    }
    return route.fulfill({ json: { items: validationRun ? [validationRun] : [] } });
  });
  await page.route("**/api/capability-governance/packs/*/*/supply-chain-evidence?*", (route) => route.fulfill({
    json: {
      evidence: {
        status: "passed",
        blockers: [],
        secret_count: 0,
        critical_count: 0,
        fixable_high_count: 0,
        misconfiguration_failure_count: 0,
        trivy_version: "0.70.0",
        trivy_database: { version: 2, updated_at: "2026-08-07T00:00:00Z" },
        syft_version: "1.50.0",
        cyclonedx_spec_version: "1.6",
        occurred_at: "2026-08-07T00:00:00Z",
      },
    },
  }));
  await page.route("**/api/capability-governance/packs/*/*/validation-tasks?*", (route) => route.fulfill({
    json: {
      items: [{
        task_id: "workspace-table-1",
        revision: 2,
        title: "季度表格汇总",
        updated_at: "2026-08-07T00:00:00Z",
      }],
    },
  }));

  await page.goto("/settings?section=governance");

  await expect(page.getByRole("tab", { name: "能力治理" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("heading", { name: "能力治理状态" })).toBeVisible();
  await expect(page.getByText("gray-python-table")).toBeVisible();
  const grayCard = page.locator("article").filter({ hasText: "gray-python-table" }).first();
  await expect(grayCard).toContainText("平台");
  await expect(grayCard).toContainText("已验证");
  await expect(grayCard).toContainText("正常");
  await expect(page.getByText("可运行")).toHaveCount(2);
  await expect(page.getByText("兼容读取").first()).toBeVisible();
  await expect(grayCard).toContainText("供应链证据");
  await expect(grayCard).toContainText("扫描通过");
  await expect(grayCard).toContainText("Trivy 0.70.0 · DB v2 · Syft 1.50.0 · CycloneDX 1.6");
  await expect(page.getByText("sha256:bbbbbbbbbbbb…bbbbbbbbbbbb").first()).toBeVisible();
  await grayCard.getByRole("button", { name: "发起验证" }).click();
  await expect(page.getByRole("dialog", { name: "发起能力验证" })).toBeVisible();
  await expect(page.getByLabel("真实任务证据")).toContainText("季度表格汇总 · V2");
  await expect(page.getByText("自动定位到当前能力卡片下方的“验证进度与结果”")).toBeVisible();
  await page.getByRole("button", { name: "创建验证运行" }).click();
  const progress = page.getByRole("group", { name: "验证进度与结果：gray-python-table" }).first();
  await expect(progress).toBeVisible();
  await expect(progress).toHaveAttribute("open", "");
  await expect(progress).toContainText("验证进度与结果 · 等待验证 · 任务 workspace-table-1 V2");
  await expect(progress).toContainText("本页每 1.5 秒自动更新");
  await expect(progress.getByText("合成 Smoke")).toBeVisible();
  await expect(progress.getByText("已完成 0/5 个步骤。一次业务成功不会自动改变能力成熟度。")).toBeVisible();
});

test("普通用户可创建并区分同一 Provider 的多套命名连接", async ({ page }) => {
  await mockSettings(page, "user");
  const connections: Record<string, unknown>[] = [];
  const createdBodies: Record<string, unknown>[] = [];
  await page.route("**/api/model-connections", (route) => route.fulfill({
    json: { items: connections },
  }));
  await page.route("**/api/model-connections/presets/deepseek", (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    createdBodies.push(body);
    const connection = {
      connection_id: `conn-${createdBodies.length}`,
      owner_scope: "user_personal",
      preset_id: "deepseek",
      display_name: body.display_name,
      model: body.model,
      status: "verified",
      key_hint: createdBodies.length === 1 ? "1111" : "2222",
    };
    connections.push(connection);
    return route.fulfill({ status: 201, json: connection });
  });

  await page.goto("/settings?section=models");
  await page.getByLabel("连接名称").fill("DeepSeek 日常");
  await page.getByLabel("API Key").fill("sk-personal-primary-1111");
  await page.getByRole("button", { name: "保存并验证" }).click();
  await expect(page.getByText("DeepSeek 日常")).toBeVisible();

  await page.getByRole("button", { name: "添加个人连接" }).click();
  await page.getByLabel("连接名称").fill("DeepSeek 备用");
  await page.getByLabel("API Key").fill("sk-personal-backup-2222");
  await page.getByRole("button", { name: "保存并验证" }).click();

  await expect(page.getByText("DeepSeek 日常")).toBeVisible();
  await expect(page.getByText("DeepSeek 备用")).toBeVisible();
  await expect(page.getByText("Key •••• 1111")).toBeVisible();
  await expect(page.getByText("Key •••• 2222")).toBeVisible();
  expect(createdBodies).toEqual([
    {
      display_name: "DeepSeek 日常",
      api_key: "sk-personal-primary-1111",
      model: "deepseek-v4-flash",
    },
    {
      display_name: "DeepSeek 备用",
      api_key: "sk-personal-backup-2222",
      model: "deepseek-v4-flash",
    },
  ]);
});

test("部分成功连接展示逐模型结果并可只重试失败模型", async ({ page }) => {
  await mockSettings(page, "user");
  let connection: Record<string, any> | null = null;
  const modelResult = (
    modelId: string,
    displayName: string,
    status: string,
    isDefault: boolean,
  ) => ({
    model_id: modelId,
    display_name: displayName,
    catalog_role: modelId.endsWith("flash") ? "balanced" : "quality",
    catalog_version: "2026-07-30.2",
    status,
    enabled: status === "available",
    is_default: isDefault,
    verified_at: "2026-07-30T12:00:00",
    error_code: status === "available" ? null : status,
    usage_status: "unknown",
  });
  await page.route("**/api/model-connections", (route) => route.fulfill({
    json: { items: connection ? [connection] : [] },
  }));
  await page.route("**/api/model-connections/presets/deepseek", (route) => {
    connection = {
      connection_id: "conn-multi-model",
      owner_scope: "user_personal",
      preset_id: "deepseek",
      display_name: "DeepSeek 主连接",
      model: "deepseek-v4-flash",
      default_model: "deepseek-v4-flash",
      available_model_count: 1,
      status: "verified",
      key_hint: "1234",
      models: [
        modelResult(
          "deepseek-v4-flash",
          "DeepSeek V4 Flash",
          "available",
          true,
        ),
        modelResult(
          "deepseek-v4-pro",
          "DeepSeek V4 Pro",
          "model_access_denied",
          false,
        ),
      ],
    };
    return route.fulfill({ status: 201, json: connection });
  });
  await page.route(
    "**/api/model-connections/conn-multi-model/models/retry",
    (route) => {
      connection!.available_model_count = 2;
      connection!.models = [
        modelResult(
          "deepseek-v4-flash",
          "DeepSeek V4 Flash",
          "available",
          true,
        ),
        modelResult(
          "deepseek-v4-pro",
          "DeepSeek V4 Pro",
          "available",
          false,
        ),
      ];
      return route.fulfill({ json: connection });
    },
  );
  await page.route(
    "**/api/model-connections/preferences/default",
    (route) => route.fulfill({ json: { preference: null } }),
  );

  await page.goto("/settings?section=models");
  await page.getByLabel("连接名称").fill("DeepSeek 主连接");
  await page.getByLabel("API Key").fill("sk-personal-multi-model-1234");
  await page.getByRole("button", { name: "保存并验证全部推荐模型" }).click();

  await expect(page.getByText("1 / 2 个模型可用")).toBeVisible();
  await expect(page.getByText("DeepSeek V4 Flash", { exact: true })).toBeVisible();
  await expect(page.getByText("无模型权限")).toBeVisible();
  await page.getByRole("button", { name: "重试 DeepSeek V4 Pro" }).click();
  await expect(page.getByText("2 / 2 个模型可用")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "设 DeepSeek V4 Pro 为默认" }),
  ).toBeVisible();
});

test("管理员同时拥有个人设置和平台治理入口", async ({ page }) => {
  await mockSettings(page, "admin");
  let presetConnection: Record<string, unknown> | null = null;
  await page.route("**/api/model-connections/managed/presets/deepseek", (route) => {
    presetConnection = route.request().postDataJSON();
    return route.fulfill({
      json: {
        connection_id: "managed-deepseek",
        owner_scope: "platform_shared",
        preset_id: "deepseek",
        display_name: "生产 DeepSeek",
        model: "deepseek-v4-pro",
        status: "verified",
      },
    });
  });
  await page.goto("/settings");

  await expect(page.getByRole("tab", { name: "我的设置" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "模型与连接" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "采集账号" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "平台配置" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "运行与诊断" })).toBeVisible();

  await page.getByRole("tab", { name: "模型与连接" }).click();
  await expect(page.getByRole("tab", { name: "个人连接" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "平台连接" })).toBeVisible();
  await page.getByRole("tab", { name: "平台连接" }).click();
  await expect(page.getByRole("button", { name: "添加平台连接" })).toBeVisible();
  await page.getByRole("button", { name: "添加平台连接" }).click();
  await expect(page.getByRole("button", { name: "Provider 预设（推荐）" }))
    .toHaveAttribute("aria-pressed", "true");
  await page.locator('[role="dialog"]').evaluate(async (dialog) => {
    await Promise.all(
      dialog.getAnimations({ subtree: true }).map((animation) => animation.finished),
    );
  });
  const platformDialogAccessibility = await new AxeBuilder({ page })
    .include('[role="dialog"]')
    .analyze();
  expect(
    platformDialogAccessibility.violations,
    "平台连接表单存在可访问性违规",
  ).toEqual([]);
  await page.getByLabel("连接名称").fill("生产 DeepSeek");
  await page.getByLabel("模型 Provider").selectOption("deepseek");
  await page.getByLabel("模型版本").selectOption("deepseek-v4-pro");
  await page.getByLabel("API Key").fill("sk-platform-secret-2468");
  await page.getByRole("button", { name: "验证并发布" }).click();
  expect(presetConnection).toEqual({
    display_name: "生产 DeepSeek",
    model: "deepseek-v4-pro",
    api_key: "sk-platform-secret-2468",
  });

  await page.getByRole("button", { name: "添加平台连接" }).click();
  await page.getByRole("button", { name: "自定义 / LAN" }).click();
  await expect(page.getByLabel("Base URL")).toBeVisible();
  await expect(page.getByLabel("API 格式")).toBeVisible();
  await expect(
    page.getByText("公网连接必须填写；无鉴权 LAN/本地服务可以留空。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();

  await page.getByRole("tab", { name: "平台配置" }).click();
  const legacy = page.locator("summary").filter({ hasText: "旧流程兼容" });
  await expect(legacy).toBeVisible();
  await expect(page.getByText("DeepSeek API Key")).not.toBeVisible();
  await legacy.click();
  await page.getByRole("button", { name: "模型 · DeepSeek" }).click();
  await expect(page.getByText("DeepSeek API Key")).toBeVisible();

  await page.getByRole("tab", { name: "采集账号" }).click();
  await expect(page.getByText("我的采集账号")).toBeVisible();
});

test("平台连接对话框支持键盘进入、Esc 关闭和焦点归还", async ({ page }) => {
  await mockSettings(page, "admin");
  await page.goto("/settings?section=models");
  await page.getByRole("tab", { name: "平台连接" }).click();
  const trigger = page.getByRole("button", { name: "添加平台连接" });
  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("button", { name: "Provider 预设（推荐）" }))
    .toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("导入连接网络失败后仍可再次验证且无需重填 Key", async ({ page }) => {
  await mockSettings(page, "user");
  let preference: Record<string, unknown> | null = null;
  let connection: Record<string, any> | null = null;
  const retryableModel = {
    model_id: "deepseek-v4-flash",
    display_name: "DeepSeek V4 Flash",
    catalog_role: "balanced",
    catalog_version: "2026-07-30.2",
    status: "network_unreachable",
    enabled: false,
    is_default: false,
    usage_status: "unknown",
  };
  await page.route("**/api/model-connections", (route) =>
    route.fulfill({ json: { items: connection ? [connection] : [] } }));
  await page.route("**/api/model-connections/imports/legacy", (route) => {
    connection = {
      connection_id: "imported-1",
      owner_scope: "user_personal",
      preset_id: "deepseek",
      display_name: "导入的 DeepSeek",
      model: "deepseek-v4-flash",
      status: "pending_validation",
      key_hint: "7788",
      available_model_count: 0,
      models: [retryableModel],
    };
    return route.fulfill({ json: { items: [connection] } });
  });
  await page.route("**/api/model-connections/imported-1/models/retry", (route) => {
    connection!.status = "verified";
    connection!.default_model = "deepseek-v4-flash";
    connection!.available_model_count = 1;
    connection!.models = [{ ...retryableModel, status: "available", enabled: true, is_default: true }];
    return route.fulfill({ json: connection });
  });
  await page.route("**/api/model-connections/preferences/default", (route) => {
    if (route.request().method() === "PUT") {
      preference = {
        ...route.request().postDataJSON(),
        available: true,
      };
      return route.fulfill({ json: preference });
    }
    return route.fulfill({ json: { preference } });
  });

  await page.goto("/settings?section=models");
  await page.getByRole("button", { name: "导入现有配置" }).click();
  await expect(page.getByText("验证并启用（Key 无需重填）")).toBeVisible();
  await page.getByRole("button", { name: "验证并启用（Key 无需重填）" }).click();
  await page.getByRole("button", { name: /导入的 DeepSeek.*新任务默认/ }).click();
  expect(preference).toMatchObject({
    connection_id: "imported-1",
    model_id: "deepseek-v4-flash",
  });
  await expect(page.locator('[data-model-tour="default-connection"]')).toContainText(
    "导入的 DeepSeek",
  );
  await expect(page.locator('[data-model-tour="default-connection"]')).toContainText(
    "DeepSeek V4 Flash",
  );
  await expect(page.locator('[data-model-tour="default-connection"]')).toContainText("可用");
});

test("超级管理员可发现四协议并手工覆盖最多八个模型", async ({ page }) => {
  await mockSettings(page, "super_admin");
  let published: Record<string, unknown> | null = null;
  await page.route("**/api/model-connections/managed/discover", (route) =>
    route.fulfill({
      json: {
        models: ["model-a", "model-b"],
        models_discovered: true,
        detected_api_formats: [
          "anthropic_messages",
          "openai_chat_completions",
          "openai_responses",
          "gemini_generate_content",
        ],
        recommended_api_format: "openai_responses",
        manual_models_required: false,
      },
    }));
  await page.route("**/api/model-connections/managed", (route) => {
    published = route.request().postDataJSON();
    return route.fulfill({ json: { connection_id: "custom-1", status: "verified" } });
  });

  await page.goto("/settings?section=models");
  await page.getByRole("tab", { name: "平台连接" }).click();
  await page.getByRole("button", { name: "添加平台连接" }).click();
  await page.getByRole("button", { name: "自定义 / LAN" }).click();
  await expect(page.getByLabel("API 格式").locator("option")).toHaveCount(4);
  await page.getByLabel("连接名称").fill("多协议网关");
  await page.getByLabel("Base URL").fill("https://gateway.example/v1");
  await page.getByLabel("API Key").fill("gateway-secret-1234");
  await page.getByRole("button", { name: "自动发现模型与协议" }).click();
  await expect(page.getByText(/已检测：/)).toContainText("openai_responses");
  await expect(page.getByLabel("API 格式")).toHaveValue("openai_responses");
  await page.getByLabel("默认模型").fill("model-b");
  await page.getByRole("button", { name: "验证并发布" }).click();
  expect(published).toMatchObject({
    display_name: "多协议网关",
    api_format: "openai_responses",
    model: "model-b",
    models: ["model-a", "model-b"],
  });
});

test("新手引导可跳过并从设置页重新播放", async ({ page }) => {
  await mockSettings(page, "user");
  const savedStates: string[] = [];
  await page.route("**/api/settings/onboarding/model-connections", (route) => {
    if (route.request().method() === "PUT") {
      const state = route.request().postDataJSON().state as string;
      savedStates.push(state);
      return route.fulfill({ json: { state } });
    }
    return route.fulfill({ json: { state: "not_started" } });
  });

  await page.goto("/settings?section=models");
  await expect(page.getByText("先确认新任务默认模型")).toBeVisible();
  await page.getByRole("button", { name: "跳过" }).click();
  await expect.poll(() => savedStates).toContain("skipped");

  await page.getByRole("button", { name: "播放新手引导" }).click();
  await expect(page.getByText("先确认新任务默认模型")).toBeVisible();
});

test("模型连接接口误返回网页时显示可恢复错误而不是 JSON 解析异常", async ({ page }) => {
  await mockSettings(page, "user");
  await page.route("**/api/model-connections/presets", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>Mangrove</title>",
    }));

  await page.goto("/settings?section=models");

  await expect(page.getByText("模型连接加载失败")).toBeVisible();
  await expect(
    page.getByRole("main").getByText(/服务返回了网页而不是 API 数据/),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "重新加载" })).toBeVisible();
  await expect(page.getByText(/Unexpected token/)).toHaveCount(0);
});

test("三角色明暗主题设置主视图没有自动化可访问性违规", async ({ page }) => {
  for (const role of ["user", "admin", "super_admin"] as const) {
    for (const theme of ["light", "dark"] as const) {
      await page.unrouteAll({ behavior: "wait" });
      await mockSettings(page, role, theme);
      await page.goto("/settings?section=models");
      if (theme === "dark") {
        await expect(page.locator("html")).toHaveClass(/dark/);
      } else {
        await expect(page.locator("html")).not.toHaveClass(/dark/);
      }
      const result = await new AxeBuilder({ page }).analyze();
      expect(
        result.violations,
        `${role}/${theme} 设置页存在可访问性违规`,
      ).toEqual([]);
    }
  }
});
