import path from "node:path";
import { expect, test } from "@playwright/test";

const PDF = path.resolve(
  process.cwd(),
  "../tests/fixtures/document_golden/contract_01_digital.pdf",
);

async function mockSession(page: import("@playwright/test").Page) {
  const units: Array<Record<string, unknown>> = [];
  let unitIndex = 0;
  await page.addInitScript(() => localStorage.setItem("mangrove_token", "e2e-token"));
  await page.route("**/api/auth/me", (route) => route.fulfill({
    json: {
      access_token: "e2e-token",
      user_id: "u1",
      username: "tester",
      display_name: "测试员",
      role: "admin",
    },
  }));
  await page.route("**/api/data-sources/uploads", (route) => route.fulfill({
    json: {
      upload_id: "upload-contract",
      original_name: "contract_01_digital.pdf",
      media_type: "application/pdf",
      size_bytes: 1024,
      sha256: "0".repeat(64),
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
          label: "deepseek · deepseek-chat",
        },
        {
          provider: "qwen",
          model: "qwen-plus",
          label: "qwen · qwen-plus",
        },
      ],
      available: ["local", "deepseek", "qwen"],
      default: {
        provider: "deepseek",
        model: "deepseek-chat",
        label: "deepseek · deepseek-chat",
      },
      document_default: {
        provider: "local",
        model: "Qwen3.6-35B-A3B",
        label: "本地模型 · Qwen3.6-35B-A3B",
      },
      document_default_source: "global",
    },
  }));
  await page.route("**/api/data-tasks", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/data-tasks/document-units", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ json: units });
    }
    if (route.request().method() === "DELETE") {
      return route.fulfill({ status: 405, json: { detail: "Method Not Allowed" } });
    }
    const payload = route.request().postDataJSON() as {
      unit_type: "single_file" | "file_set";
      name: string;
      business_type?: string;
      upload_ids: string[];
    };
    unitIndex += 1;
    const unit = {
      unit_id: `unit-${unitIndex}`,
      unit_type: payload.unit_type,
      name: payload.name,
      business_type: payload.business_type ?? null,
      upload_ids: payload.upload_ids,
      members: payload.upload_ids.map((uploadId) => ({
        upload_id: uploadId,
        original_name: payload.unit_type === "single_file" ? payload.name : uploadId,
        media_type: "application/octet-stream",
        size_bytes: 1024,
        sha256: "0".repeat(64),
      })),
      latest_task: null,
      run_count: 0,
      created_at: "2026-07-23T00:00:00Z",
      updated_at: "2026-07-23T00:00:00Z",
    };
    units.push(unit);
    return route.fulfill({ json: unit });
  });
  await page.route(
    "**/api/data-tasks/document-units/*/runs",
    (route) => route.fulfill({ json: [] }),
  );
  await page.route("**/api/data-tasks/document-units/*", (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    const unitId = route.request().url().split("/").at(-1);
    const index = units.findIndex((unit) => unit.unit_id === unitId);
    if (index >= 0) units.splice(index, 1);
    return route.fulfill({
      json: {
        ok: true,
        unit_id: unitId,
        retained_uploads: true,
        retained_history: true,
      },
    });
  });
  await page.route("**/api/data-tasks/document-workspace", (route) => route.fulfill({
    json: route.request().method() === "GET"
      ? {
          upload_ids: [],
          checked_upload_ids: [],
          active_task_id: null,
          active_unit_id: null,
          selected_upload_id: null,
          updated_at: "2026-07-23T00:00:00Z",
        }
      : {
          ...route.request().postDataJSON(),
          updated_at: "2026-07-23T00:00:00Z",
        },
  }));
  await page.route(
    "**/api/data-tasks/document-runs/by-upload/*",
    (route) => route.fulfill({ json: [] }),
  );
  const extractionSpec = {
    spec_version: "3",
    goal: {
      objective: "提取合同付款安排",
      document_types: ["document"],
      success_criteria: ["所有非空字段必须绑定原文证据"],
    },
    discovery: {
      artifact_ids: ["upload-contract"],
      pages: {},
      section_patterns: [],
    },
    fields: [{
      name: "付款比例",
      dtype: "string",
      required: false,
      description: "合同约定的付款比例",
      require_evidence: true,
      min_confidence: 0.9,
    }],
    result_contract: {
      shape: "fields",
      cardinality: "one",
      record_grain: null,
      renderer: "field_cards",
      output_formats: ["jsonl", "xlsx"],
      exhaustive: false,
    },
    conflict_policy: "review",
  };
  await page.route("**/api/data-tasks/document-drafts", (route) => route.fulfill({
    json: {
      task_id: "doc-e2e",
      status: "SPEC_DRAFT",
      model_selection: {
        provider: "local",
        model: "Qwen3.6-35B-A3B",
      },
      extraction_spec: extractionSpec,
    },
  }));
  await page.route("**/api/data-tasks/doc-e2e/intent-messages", (route) => route.fulfill({
    json: {
      task_id: "doc-e2e",
      status: "SPEC_DRAFT",
      model_selection: {
        provider: "local",
        model: "Qwen3.6-35B-A3B",
      },
      extraction_spec: {
        ...extractionSpec,
        fields: [
          ...extractionSpec.fields,
          {
            name: "付款节点",
            dtype: "string",
            required: false,
            description: "触发付款的时间或条件",
            require_evidence: true,
            min_confidence: 0.9,
          },
        ],
      },
    },
  }));
  await page.route("**/api/data-tasks/doc-e2e/extraction-spec", (route) => route.fulfill({
    json: {
      task_id: "doc-e2e",
      status: "READY",
      extraction_spec: extractionSpec,
    },
  }));
  await page.route("**/api/data-tasks/doc-e2e/model-selection", (route) => route.fulfill({
    json: {
      task_id: "doc-e2e",
      status: "SPEC_DRAFT",
      model_selection: route.request().postDataJSON(),
    },
  }));
  await page.route("**/api/data-tasks/doc-e2e/extract", (route) => route.fulfill({
    json: {
      task_id: "doc-e2e",
      status: "COMPLETED",
      artifacts: [{
        artifact_id: "raw-e2e",
        upload_id: "upload-contract",
        original_name: "contract_01_digital.pdf",
      }],
      fields: [{
        name: "付款比例",
        value: "30%",
        status: "found",
        evidence_refs: [{
          artifact_id: "raw-e2e",
          element_id: "el-e2e",
          page: 2,
          bbox: {
            x0: 100,
            y0: 120,
            x1: 420,
            y1: 180,
            coordinate_space: "normalized_1000",
          },
          quote: "付款比例为30%",
          confidence: 0.99,
          extractor: "mineru",
          extractor_version: "3.4.4",
        }],
        candidates: [],
        review_reason: null,
      }],
      review_tasks: [],
    },
  }));
  await page.route("**/api/downloads/doc-e2e/manifest.json", (route) => route.fulfill({
    body: JSON.stringify({ task_id: "doc-e2e", spec_version: "3" }),
    contentType: "application/json",
    headers: {
      "content-disposition": 'attachment; filename="manifest.json"',
    },
  }));
  await page.route(
    "**/api/downloads/doc-e2e/extraction/document_extraction.xlsx",
    (route) => route.fulfill({
      body: "xlsx-view-copy",
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      headers: {
        "content-disposition": 'attachment; filename="document_extraction.xlsx"',
      },
    }),
  );
}

test.describe("文档智能抽取工作台", () => {
  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1920, height: 1080 },
  ]) {
    test(`${viewport.width}x${viewport.height} 下工作台不被裁切`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await mockSession(page);
      await page.goto("/data-prep?legacy=1");

      const workspace = page.getByTestId("document-workspace");
      await expect(workspace).toBeVisible();
      const box = await workspace.boundingBox();
      expect(box).not.toBeNull();
      expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(viewport.height);
      await expect(page.getByText("告诉我你想得到什么")).toBeVisible();
      await expect(page.getByRole("button", { name: "确认并开始抽取" })).toBeDisabled();
    });
  }

  test("上传、完整预览、需求确认和方案编辑可连续完成", async ({ page }) => {
    await mockSession(page);
    await page.goto("/data-prep?legacy=1");

    await page.locator('input[type="file"]').setInputFiles(PDF);
    await expect(page.getByText("contract_01_digital.pdf", { exact: true })).toBeVisible();
    const preview = page.getByTestId("document-preview");
    const canvas = preview.locator("canvas");
    await expect(canvas).toBeVisible();
    const previewBox = await preview.boundingBox();
    const canvasBox = await canvas.boundingBox();
    expect(previewBox).not.toBeNull();
    expect(canvasBox).not.toBeNull();
    expect((canvasBox?.y ?? 0) + (canvasBox?.height ?? 0))
      .toBeLessThanOrEqual((previewBox?.y ?? 0) + (previewBox?.height ?? 0) + 1);

    await page.getByPlaceholder("例如：只提取付款节点、比例和收款账户").fill(
      "只提取付款节点、比例和收款账户",
    );
    await page.getByRole("button", { name: "发送需求" }).click();
    await page.getByLabel("字段名称").first().fill("付款节点");
    await page.getByRole("button", { name: "确认并开始抽取" }).click();

    await expect(page.getByRole("button", { name: "抽取已完成" })).toBeVisible();
    await page.getByRole("button", { name: /付款比例/ }).click();
    const highlight = page.getByLabel("证据高亮");
    await expect(highlight).toBeVisible();
    const highlightBox = await highlight.boundingBox();
    const renderedPageBox = await canvas.boundingBox();
    expect(highlightBox).not.toBeNull();
    expect(renderedPageBox).not.toBeNull();
    expect(highlightBox?.x ?? 0).toBeGreaterThanOrEqual(renderedPageBox?.x ?? 0);
    expect((highlightBox?.x ?? 0) + (highlightBox?.width ?? 0))
      .toBeLessThanOrEqual(
        (renderedPageBox?.x ?? 0) + (renderedPageBox?.width ?? 0) + 1,
      );
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载 Manifest" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("manifest.json");
    const xlsxPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载 XLSX 查看副本" }).click();
    const xlsx = await xlsxPromise;
    expect(xlsx.suggestedFilename()).toBe("document_extraction.xlsx");
    expect(await xlsx.failure()).toBeNull();
  });

  test("零数据结果显示失败并阻止下载空权威文件", async ({ page }) => {
    await mockSession(page);
    await page.unroute("**/api/data-tasks/doc-e2e/extract");
    await page.route("**/api/data-tasks/doc-e2e/extract", (route) => route.fulfill({
      json: {
        task_id: "doc-e2e",
        status: "FAILED",
        artifacts: [{
          artifact_id: "raw-e2e",
          upload_id: "upload-contract",
          original_name: "contract_01_digital.pdf",
        }],
        fields: [],
        records: [],
        tables: [],
        coverage: {
          elements_processed: 12,
          elements_in_scope: 12,
          table_rows: 0,
        },
        review_tasks: [],
      },
    }));
    await page.goto("/data-prep?legacy=1");
    await page.locator('input[type="file"]').setInputFiles(PDF);
    await page.getByPlaceholder("例如：只提取付款节点、比例和收款账户").fill(
      "提取所有表格",
    );
    await page.getByRole("button", { name: "发送需求" }).click();
    await page.getByRole("button", { name: "确认并开始抽取" }).click();

    await expect(page.getByText("未产出有效数据", { exact: true })).toBeVisible();
    await expect(page.getByText(/系统已阻止下载空的权威结果和 XLSX/)).toBeVisible();
    await expect(
      page.getByRole("button", { name: "下载权威结果 JSON/JSONL" }),
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "下载 XLSX 查看副本" }),
    ).toBeDisabled();
    await expect(page.getByRole("button", { name: "下载质量报告" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "下载 Manifest" })).toBeEnabled();
  });

  test("局域网普通 HTTP 缺少 crypto.randomUUID 时仍可上传", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window.crypto, "randomUUID", {
        configurable: true,
        value: undefined,
      });
    });
    await mockSession(page);
    await page.goto("/data-prep?legacy=1");

    await page.locator('input[type="file"]').setInputFiles(PDF);

    await expect(page.getByText("contract_01_digital.pdf", { exact: true })).toBeVisible();
    await expect(page.getByText("上传失败", { exact: true })).toHaveCount(0);
  });

  test("DOCX 上传后立即显示结构化预览且隐藏 PDF 控件", async ({ page }) => {
    await mockSession(page);
    await page.unroute("**/api/data-sources/uploads");
    await page.route("**/api/data-sources/uploads", (route) => route.fulfill({
      json: {
        upload_id: "upload-docx",
        original_name: "付款合同.docx",
        media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes: 512,
        sha256: "1".repeat(64),
      },
    }));
    await page.route(
      "**/api/data-sources/uploads/upload-docx/document-preview",
      (route) => route.fulfill({
        json: {
          upload_id: "upload-docx",
          original_name: "付款合同.docx",
          status: "ready",
          elements: [
            {
              element_id: "el-docx-paragraph",
              artifact_id: "raw-docx",
              page: 1,
              element_type: "paragraph",
              text: "付款条件：验收后 30 日内付款",
              reading_order: 0,
              extractor: "python-docx",
              extractor_version: "1.2.0",
              metadata: {
                location: { kind: "docx_paragraph", paragraph: 1 },
              },
            },
            {
              element_id: "el-docx-table",
              artifact_id: "raw-docx",
              page: 1,
              element_type: "table",
              text: "订单号：PO-001；金额：1000 元",
              reading_order: 1,
              extractor: "python-docx",
              extractor_version: "1.2.0",
              metadata: {
                location: { kind: "docx_table_row", table: 1, row: 1 },
              },
            },
          ],
          rejects: [],
        },
      }),
    );
    await page.goto("/data-prep?legacy=1");

    await expect(page.getByText("AI 生成的抽取方案")).toHaveCount(0);
    await page.locator('input[type="file"]').setInputFiles({
      name: "付款合同.docx",
      mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      buffer: Buffer.from("mock-docx"),
    });

    await expect(page.getByText("结构化预览 · 2 个内容块")).toBeVisible();
    await expect(page.getByText("付款条件：验收后 30 日内付款")).toBeVisible();
    await expect(page.getByText("订单号：PO-001；金额：1000 元")).toBeVisible();
    await expect(page.getByRole("button", { name: "上一页" })).toHaveCount(0);
    await expect(page.getByText("说明目标", { exact: true })).toBeVisible();
  });

  test("不支持的文件类型会显示明确提示", async ({ page }) => {
    await mockSession(page);
    await page.goto("/data-prep?legacy=1");

    await page.locator('input[type="file"]').setInputFiles({
      name: "unsupported.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("unsupported"),
    });

    await expect(page.getByTestId("upload-error")).toContainText(
      "请选择 PDF、DOCX、ZIP、PNG、JPG、JPEG 或 WEBP 文件",
    );
  });

  test("后端上传失败会显示接口错误", async ({ page }) => {
    await mockSession(page);
    await page.unroute("**/api/data-sources/uploads");
    await page.route("**/api/data-sources/uploads", (route) => route.fulfill({
      status: 413,
      json: { detail: "文件超过上传大小限制" },
    }));
    await page.goto("/data-prep?legacy=1");

    await page.locator('input[type="file"]').setInputFiles(PDF);

    await expect(page.getByTestId("upload-error")).toContainText("文件超过上传大小限制");
    await expect(page.getByText("上传失败", { exact: true })).toBeVisible();
  });

  test("可按任务显式切换到云模型", async ({ page }) => {
    await mockSession(page);
    let draftPayload: Record<string, string> | null = null;
    await page.route("**/api/data-tasks/document-drafts", (route) => {
      draftPayload = route.request().postDataJSON();
      return route.fulfill({
        json: {
          task_id: "doc-e2e",
          status: "SPEC_DRAFT",
          model_selection: {
            provider: draftPayload.provider,
            model: draftPayload.model,
          },
          extraction_spec: {
            spec_version: "3",
            goal: {
              objective: "提取付款比例",
              document_types: ["document"],
              success_criteria: ["所有非空字段必须绑定原文证据"],
            },
            discovery: {
              artifact_ids: ["upload-contract"],
              pages: {},
              section_patterns: [],
            },
            fields: [{
              name: "付款比例",
              dtype: "string",
              required: false,
              description: "合同约定的付款比例",
              require_evidence: true,
              min_confidence: 0.9,
            }],
            result_contract: {
              shape: "fields",
              cardinality: "one",
              record_grain: null,
              renderer: "field_cards",
              output_formats: ["jsonl", "xlsx"],
              exhaustive: false,
            },
            conflict_policy: "review",
          },
        },
      });
    });
    await page.goto("/data-prep?legacy=1");
    page.once("dialog", async (dialog) => {
      expect(dialog.message()).toContain("文档文本会发送给该服务商");
      await dialog.accept();
    });
    await page.getByLabel("文档抽取模型").selectOption("deepseek::deepseek-chat");
    await page.locator('input[type="file"]').setInputFiles(PDF);
    await expect(page.getByText("范围：contract_01_digital.pdf")).toBeVisible();
    await page.getByPlaceholder("例如：只提取付款节点、比例和收款账户").fill("提取付款比例");
    await page.getByRole("button", { name: "发送需求" }).click();

    expect(draftPayload).toMatchObject({
      provider: "deepseek",
      model: "deepseek-chat",
    });
  });

  test("拒绝云模型风险提示后继续使用本地模型", async ({ page }) => {
    await mockSession(page);
    await page.goto("/data-prep?legacy=1");
    page.once("dialog", (dialog) => dialog.dismiss());

    await page.getByLabel("文档抽取模型").selectOption("deepseek::deepseek-chat");

    await expect(page.getByLabel("文档抽取模型")).toHaveValue(
      "local::Qwen3.6-35B-A3B",
    );
  });

  test("后续聊天在同一任务内修订方案", async ({ page }) => {
    await mockSession(page);
    await page.goto("/data-prep?legacy=1");
    await page.locator('input[type="file"]').setInputFiles(PDF);

    const input = page.getByPlaceholder("例如：只提取付款节点、比例和收款账户");
    await input.fill("提取付款比例");
    await page.getByRole("button", { name: "发送需求" }).click();
    await expect(page.getByLabel("字段名称")).toHaveCount(1);

    await input.fill("再增加付款节点");
    await page.getByRole("button", { name: "发送需求" }).click();

    await expect(page.getByLabel("字段名称")).toHaveCount(2);
    await expect(
      page.locator('input[aria-label="字段名称"][value="付款节点"]'),
    ).toBeVisible();
    await expect(page.getByText("再增加付款节点", { exact: true })).toBeVisible();
  });

  test("文件勾选只用于组合，独立文件仍按自己的任务单元执行", async ({ page }) => {
    await mockSession(page);
    await page.unroute("**/api/data-sources/uploads");
    let uploadIndex = 0;
    await page.route("**/api/data-sources/uploads", (route) => {
      uploadIndex += 1;
      return route.fulfill({
        json: {
          upload_id: `upload-${uploadIndex}`,
          original_name: uploadIndex === 1 ? "a.png" : "b.png",
          media_type: "image/png",
          size_bytes: 4,
          sha256: String(uploadIndex).repeat(64),
        },
      });
    });
    let draftPayload: { unit_id?: string } = {};
    await page.route("**/api/data-tasks/document-drafts", (route) => {
      draftPayload = route.request().postDataJSON();
      return route.fulfill({
        json: {
          task_id: "doc-e2e",
          status: "SPEC_DRAFT",
          model_selection: {
            provider: "local",
            model: "Qwen3.6-35B-A3B",
          },
          extraction_spec: {
            spec_version: "3",
            goal: {
              objective: "提取图片内容",
              document_types: ["document"],
              success_criteria: [],
            },
            discovery: {
              artifact_ids: ["upload-1"],
              pages: {},
              section_patterns: [],
            },
            fields: [{
              name: "内容",
              dtype: "string",
              required: false,
              description: "图片内容",
              require_evidence: true,
              min_confidence: 0.9,
            }],
            result_contract: {
              shape: "fields",
              cardinality: "one",
              record_grain: null,
              renderer: "field_cards",
              output_formats: ["jsonl", "xlsx"],
              exhaustive: false,
            },
            conflict_policy: "review",
          },
        },
      });
    });
    await page.goto("/data-prep?legacy=1");
    await page.locator('input[type="file"]').setInputFiles([
      {
        name: "a.png",
        mimeType: "image/png",
        buffer: Buffer.from([137, 80, 78, 71]),
      },
      {
        name: "b.png",
        mimeType: "image/png",
        buffer: Buffer.from([137, 80, 78, 71]),
      },
    ]);

    await page.getByLabel("选择 b.png 用于组合文件集").uncheck();
    await page.getByPlaceholder("例如：只提取付款节点、比例和收款账户").fill("提取内容");
    await page.getByRole("button", { name: "发送需求" }).click();

    expect(draftPayload.unit_id).toBe("unit-1");
    await expect(page.getByText("范围：a.png")).toBeVisible();
  });

  test("创建文件集后默认隐藏重复独立条目，并可从工作区移除", async ({ page }) => {
    await mockSession(page);
    await page.unroute("**/api/data-sources/uploads");
    let uploadIndex = 0;
    await page.route("**/api/data-sources/uploads", (route) => {
      uploadIndex += 1;
      return route.fulfill({
        json: {
          upload_id: `group-upload-${uploadIndex}`,
          original_name: uploadIndex === 1 ? "订单A.png" : "订单B.png",
          media_type: "image/png",
          size_bytes: 4,
          sha256: String(uploadIndex).repeat(64),
        },
      });
    });
    await page.goto("/data-prep?legacy=1");
    await page.locator('input[type="file"]').setInputFiles([
      {
        name: "订单A.png",
        mimeType: "image/png",
        buffer: Buffer.from([137, 80, 78, 71]),
      },
      {
        name: "订单B.png",
        mimeType: "image/png",
        buffer: Buffer.from([137, 80, 78, 71]),
      },
    ]);

    await page.getByRole("button", { name: /将已选 2 个文件组合为文件集/ }).click();
    await page.getByLabel("文件集名称").fill("客户订单集");
    await page.getByRole("button", { name: "创建文件集" }).click();

    await expect(page.getByText("客户订单集", { exact: true })).toBeVisible();
    await expect(page.getByLabel("选择 订单A.png 用于组合文件集")).toHaveCount(0);
    await expect(page.getByLabel("选择 订单B.png 用于组合文件集")).toHaveCount(0);
    await page.getByPlaceholder("例如：只提取付款节点、比例和收款账户").fill(
      "合并全部表格并输出 JSON",
    );
    await page.getByRole("button", { name: "发送需求" }).click();
    await expect(page.getByRole("button", { name: "确认并开始抽取" })).toBeEnabled();
    await page.getByRole("button", { name: /查看已归组的独立任务/ }).click();
    await expect(page.getByLabel("选择 订单A.png 用于组合文件集")).toBeVisible();
    await expect(page.getByLabel("选择 订单B.png 用于组合文件集")).toBeVisible();

    page.on("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "从工作区移除 客户订单集" }).click();
    await expect(page.getByText("客户订单集", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("选择 订单A.png 用于组合文件集")).toBeVisible();
    await page.getByRole("button", { name: "从工作区移除 订单A.png" }).click();
    await expect(page.getByText("订单A.png", { exact: true })).toHaveCount(0);
  });

  test("快速切换独立文件不会串历史，文件集成员切换保持合并结果", async ({ page }) => {
    await mockSession(page);
    const makeSpec = (uploadIds: string[], objective: string) => ({
      spec_version: "3",
      goal: {
        objective,
        document_types: ["document"],
        success_criteria: [],
      },
      discovery: {
        artifact_ids: uploadIds,
        pages: {},
        section_patterns: [],
      },
      fields: [{
        name: "结果",
        dtype: "string",
        required: false,
        description: objective,
        require_evidence: true,
        min_confidence: 0.9,
      }],
      result_contract: {
        shape: "fields",
        cardinality: "one",
        record_grain: null,
        renderer: "field_cards",
        output_formats: ["jsonl", "xlsx"],
        exhaustive: false,
      },
      conflict_policy: "review",
    });
    const makeTask = (
      taskId: string,
      unitId: string,
      uploadIds: string[],
      objective: string,
    ) => ({
      task_id: taskId,
      user_id: "u1",
      status: "COMPLETED",
      record_counts: {},
      quality: null,
      manifest_path: null,
      error: null,
      created_at: "2026-07-23T00:00:00Z",
      updated_at: "2026-07-23T00:00:00Z",
      spec: {
        task_type: "document_extraction",
        unit_id: unitId,
        upload_ids: uploadIds,
        intent_messages: [objective],
        model_selection: {
          provider: "local",
          model: "Qwen3.6-35B-A3B",
        },
        extraction_spec: makeSpec(uploadIds, objective),
      },
    });
    const taskA = makeTask("task-a", "unit-a", ["upload-a"], "A任务");
    const taskB = makeTask("task-b", "unit-b", ["upload-b"], "B任务");
    const taskSet = makeTask(
      "task-set",
      "unit-set",
      ["upload-a", "upload-b"],
      "文件集合并任务",
    );
    const member = (uploadId: string, name: string) => ({
      upload_id: uploadId,
      original_name: name,
      media_type: "application/pdf",
      size_bytes: 1024,
      sha256: uploadId === "upload-a" ? "a".repeat(64) : "b".repeat(64),
    });
    const units = [
      {
        unit_id: "unit-a",
        unit_type: "single_file",
        name: "A合同.pdf",
        business_type: null,
        upload_ids: ["upload-a"],
        members: [member("upload-a", "A合同.pdf")],
        latest_task: taskA,
        run_count: 1,
        created_at: "2026-07-23T00:00:00Z",
        updated_at: "2026-07-23T00:00:00Z",
      },
      {
        unit_id: "unit-b",
        unit_type: "single_file",
        name: "B订单.pdf",
        business_type: null,
        upload_ids: ["upload-b"],
        members: [member("upload-b", "B订单.pdf")],
        latest_task: taskB,
        run_count: 1,
        created_at: "2026-07-23T00:00:00Z",
        updated_at: "2026-07-23T00:00:00Z",
      },
      {
        unit_id: "unit-set",
        unit_type: "file_set",
        name: "客户资料集",
        business_type: "客户资料",
        upload_ids: ["upload-a", "upload-b"],
        members: [member("upload-a", "A合同.pdf"), member("upload-b", "B订单.pdf")],
        latest_task: taskSet,
        run_count: 1,
        created_at: "2026-07-23T00:00:00Z",
        updated_at: "2026-07-23T00:00:00Z",
      },
    ];
    await page.unroute("**/api/data-tasks/document-units");
    await page.route("**/api/data-tasks/document-units", (route) => route.fulfill({ json: units }));
    await page.unroute("**/api/data-tasks/document-units/*/runs");
    await page.route("**/api/data-tasks/document-units/*/runs", (route) => {
      const unitId = route.request().url().split("/").at(-2);
      const task = unitId === "unit-a" ? taskA : unitId === "unit-b" ? taskB : taskSet;
      return route.fulfill({ json: [task] });
    });
    await page.unroute("**/api/data-tasks/document-workspace");
    await page.route("**/api/data-tasks/document-workspace", (route) => route.fulfill({
      json: route.request().method() === "GET"
        ? {
            upload_ids: ["upload-a", "upload-b"],
            checked_upload_ids: [],
            active_task_id: "task-a",
            active_unit_id: "unit-a",
            selected_upload_id: "upload-a",
            updated_at: "2026-07-23T00:00:00Z",
          }
        : {
            ...route.request().postDataJSON(),
            updated_at: "2026-07-23T00:00:00Z",
          },
    }));
    for (const [uploadId, name] of [["upload-a", "A合同.pdf"], ["upload-b", "B订单.pdf"]]) {
      await page.route(`**/api/data-sources/uploads/${uploadId}`, (route) => route.fulfill({
        json: member(uploadId, name),
      }));
      await page.route(
        `**/api/data-sources/uploads/${uploadId}/content`,
        (route) => route.fulfill({ path: PDF, contentType: "application/pdf" }),
      );
    }
    const result = (taskId: string, value: string, uploadIds: string[]) => ({
      task_id: taskId,
      status: "COMPLETED",
      artifacts: uploadIds.map((uploadId) => ({
        artifact_id: `raw-${uploadId}`,
        upload_id: uploadId,
        original_name: uploadId,
      })),
      fields: [{
        name: "结果",
        value,
        status: "found",
        evidence_refs: [],
        candidates: [],
        review_reason: null,
      }],
      review_tasks: [],
    });
    await page.route("**/api/data-tasks/task-a/extraction-results", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      return route.fulfill({ json: result("task-a", "A历史结果", ["upload-a"]) });
    });
    await page.route(
      "**/api/data-tasks/task-b/extraction-results",
      (route) => route.fulfill({ json: result("task-b", "B历史结果", ["upload-b"]) }),
    );
    await page.route(
      "**/api/data-tasks/task-set/extraction-results",
      (route) => route.fulfill({
        json: result("task-set", "文件集合并结果", ["upload-a", "upload-b"]),
      }),
    );

    await page.goto("/data-prep?legacy=1");
    await page.getByRole("button", { name: /查看已归组的独立任务/ }).click();
    await page.getByText("B订单.pdf", { exact: true }).click();
    await expect(page.getByText("B历史结果", { exact: true })).toBeVisible();
    await page.waitForTimeout(600);
    await expect(page.getByText("A历史结果", { exact: true })).toHaveCount(0);

    await page.getByText("客户资料集", { exact: true }).click();
    await expect(page.getByText("文件集合并结果", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "B订单.pdf", exact: true }).click();
    await expect(page.getByText("文件集合并结果", { exact: true })).toBeVisible();
    await expect(page.getByText("范围：客户资料集 · 2 份文件")).toBeVisible();
  });

  test("多记录结果完整展示且完成后仍可创建新版本", async ({ page }) => {
    await mockSession(page);
    await page.route("**/api/data-tasks/doc-e2e/extract", (route) => route.fulfill({
      json: {
        task_id: "doc-e2e",
        status: "COMPLETED",
        artifacts: [{
          artifact_id: "raw-e2e",
          upload_id: "upload-contract",
          original_name: "contract_01_digital.pdf",
        }],
        fields: [],
        records: [
          {
            record_id: "record-1",
            fields: [],
            values: { 姓名: "张三", 工作内容: "需求分析" },
            status: "found",
            source_artifact_ids: ["raw-e2e"],
            review_required: false,
          },
          {
            record_id: "record-2",
            fields: [],
            values: { 姓名: "张三", 工作内容: "测试验收" },
            status: "found",
            source_artifact_ids: ["raw-e2e"],
            review_required: false,
          },
        ],
        tables: [],
        coverage: {
          elements_total: 10,
          elements_in_scope: 10,
          elements_processed: 10,
          records_extracted: 2,
        },
        review_tasks: [],
      },
    }));
    await page.goto("/data-prep?legacy=1");
    await page.locator('input[type="file"]').setInputFiles(PDF);
    const input = page.getByPlaceholder("例如：只提取付款节点、比例和收款账户");
    await input.fill("搜索张三所有工作内容并输出 Excel");
    await page.getByRole("button", { name: "发送需求" }).click();
    await page.getByRole("button", { name: "确认并开始抽取" }).click();

    await expect(page.getByText("需求分析", { exact: true })).toBeVisible();
    await expect(page.getByText("测试验收", { exact: true })).toBeVisible();
    await expect(page.getByText("抽取 2 条记录")).toBeVisible();
    await expect(input).toBeEnabled();
  });

  test("ZIP 文件可进入文档工作区", async ({ page }) => {
    await mockSession(page);
    await page.goto("/data-prep?legacy=1");

    const uploadRequest = page.waitForRequest("**/api/data-sources/uploads");
    await page.locator('input[type="file"]').setInputFiles({
      name: "contracts.zip",
      mimeType: "application/zip",
      buffer: Buffer.from("mock-zip"),
    });

    expect((await uploadRequest).method()).toBe("POST");
    await expect(page.getByTestId("upload-error")).toHaveCount(0);
  });

  test("连续文档结果使用独立正文视图", async ({ page }) => {
    await mockSession(page);
    await page.route("**/api/data-tasks/document-drafts", (route) => route.fulfill({
      json: {
        task_id: "doc-e2e",
        status: "SPEC_DRAFT",
        model_selection: {
          provider: "local",
          model: "Qwen3.6-35B-A3B",
        },
        extraction_spec: {
          spec_version: "3",
          goal: {
            objective: "输出连续文档",
            document_types: ["document"],
            success_criteria: ["正文片段必须绑定证据"],
          },
          discovery: {
            artifact_ids: ["upload-contract"],
            pages: {},
            section_patterns: [],
          },
          fields: [],
          result_contract: {
            shape: "document",
            cardinality: "one",
            record_grain: null,
            renderer: "document_view",
            output_formats: ["json", "xlsx"],
            exhaustive: false,
            merge_tables: false,
          },
          conflict_policy: "review",
        },
      },
    }));
    await page.route("**/api/data-tasks/doc-e2e/extract", (route) => route.fulfill({
      json: {
        task_id: "doc-e2e",
        status: "COMPLETED",
        artifacts: [{
          artifact_id: "raw-e2e",
          upload_id: "upload-contract",
          original_name: "contract_01_digital.pdf",
        }],
        fields: [],
        records: [],
        tables: [],
        documents: [{
          document_id: "document-1",
          title: "输出连续文档",
          content: "第一页正文\n\n第二页正文",
          source_artifact_ids: ["raw-e2e"],
          evidence_refs: [{
            artifact_id: "raw-e2e",
            element_id: "el-document",
            page: 1,
            quote: "第一页正文",
            extractor: "pdfplumber",
            extractor_version: "0.11.10",
            confidence: 0.99,
            location: {},
          }],
        }],
        aggregates: [],
        coverage: {
          elements_total: 2,
          elements_in_scope: 2,
          elements_processed: 2,
          documents_extracted: 1,
          document_chars: 12,
        },
        review_tasks: [],
      },
    }));
    await page.goto("/data-prep?legacy=1");
    await page.locator('input[type="file"]').setInputFiles(PDF);
    const input = page.getByPlaceholder("例如：只提取付款节点、比例和收款账户");
    await input.fill("输出连续文档");
    await page.getByRole("button", { name: "发送需求" }).click();
    await page.getByRole("button", { name: "确认并开始抽取" }).click();

    await expect(page.getByText("第一页正文", { exact: false })).toBeVisible();
    await expect(page.getByText("1 个证据片段", { exact: true })).toBeVisible();
    await expect(page.getByText(/生成 12 字连续正文/)).toBeVisible();
  });

  test("刷新后兼容旧版任务并恢复原件预览和抽取结果", async ({ page }) => {
    await mockSession(page);
    await page.addInitScript(() => {
      localStorage.setItem("mangrove_active_document_task", "doc-e2e");
    });
    const extractionSpec = {
      spec_version: "3",
      goal: {
        objective: "提取合同付款安排",
        document_types: ["document"],
        success_criteria: ["所有非空字段必须绑定原文证据"],
      },
      discovery: {
        artifact_ids: ["upload-contract"],
        pages: {},
        section_patterns: [],
      },
      fields: [{
        name: "付款比例",
        dtype: "string",
        required: false,
        description: "合同约定的付款比例",
        require_evidence: true,
        min_confidence: 0.9,
      }],
      conflict_policy: "review",
    };
    const historicalTask = {
        task_id: "doc-e2e",
        user_id: "u1",
        status: "COMPLETED",
        record_counts: {},
        quality: null,
        manifest_path: null,
        error: null,
        created_at: "2026-07-22T00:00:00Z",
        updated_at: "2026-07-22T00:00:00Z",
        spec: {
          task_type: "document_extraction",
          upload_ids: ["upload-contract"],
          intent_messages: ["提取合同付款安排"],
          model_selection: {
            provider: "local",
            model: "Qwen3.6-35B-A3B",
          },
          extraction_spec: extractionSpec,
        },
      };
    await page.unroute("**/api/data-tasks/document-units");
    await page.route("**/api/data-tasks/document-units", (route) => route.fulfill({
      json: [{
        unit_id: "unit-history",
        unit_type: "single_file",
        name: "contract_01_digital.pdf",
        business_type: null,
        upload_ids: ["upload-contract"],
        members: [{
          upload_id: "upload-contract",
          original_name: "contract_01_digital.pdf",
          media_type: "application/pdf",
          size_bytes: 1024,
          sha256: "0".repeat(64),
        }],
        latest_task: historicalTask,
        run_count: 1,
        created_at: "2026-07-22T00:00:00Z",
        updated_at: "2026-07-22T00:00:00Z",
      }],
    }));
    await page.unroute("**/api/data-tasks/document-units/*/runs");
    await page.route(
      "**/api/data-tasks/document-units/unit-history/runs",
      (route) => route.fulfill({ json: [historicalTask] }),
    );
    await page.route("**/api/data-tasks/document-workspace", (route) => route.fulfill({
      json: {
        upload_ids: ["upload-contract"],
        checked_upload_ids: ["upload-contract"],
        active_task_id: "doc-e2e",
        active_unit_id: "unit-history",
        selected_upload_id: "upload-contract",
        updated_at: "2026-07-22T00:00:00Z",
      },
    }));
    await page.route("**/api/data-sources/uploads/upload-contract", (route) => route.fulfill({
      json: {
        upload_id: "upload-contract",
        original_name: "contract_01_digital.pdf",
        media_type: "application/pdf",
        size_bytes: 1024,
        sha256: "0".repeat(64),
      },
    }));
    await page.route(
      "**/api/data-sources/uploads/upload-contract/content",
      (route) => route.fulfill({
        path: PDF,
        contentType: "application/pdf",
      }),
    );
    await page.route("**/api/data-tasks/doc-e2e/extraction-results", (route) => route.fulfill({
      json: {
        task_id: "doc-e2e",
        status: "COMPLETED",
        fields: [{
          name: "付款比例",
          value: "30%",
          status: "found",
          evidence_refs: [],
          candidates: [],
          review_reason: null,
        }],
        review_tasks: [],
        review_decisions: [],
      },
    }));

    await page.goto("/data-prep?legacy=1");

    await expect(page.getByText("contract_01_digital.pdf", { exact: true })).toBeVisible();
    await expect(page.getByTestId("document-preview").locator("canvas")).toBeVisible();
    await expect(page.getByText("提取合同付款安排", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "抽取已完成" })).toBeVisible();
  });

  test("待复核字段可裁决并显示审计已保存", async ({ page }) => {
    await mockSession(page);
    const evidence = {
      artifact_id: "raw-e2e",
      element_id: "el-e2e",
      page: 1,
      bbox: {
        x0: 60,
        y0: 80,
        x1: 220,
        y1: 110,
        coordinate_space: "pdf_points",
      },
      quote: "付款比例为30%",
      confidence: 0.6,
      extractor: "pdfplumber",
      extractor_version: "0.11.10",
    };
    const pendingResult = {
      task_id: "doc-e2e",
      status: "NEEDS_REVIEW",
      fields: [{
        name: "付款比例",
        value: "30%",
        status: "low_confidence",
        evidence_refs: [evidence],
        candidates: [{
          field_name: "付款比例",
          value: "30%",
          quote: "付款比例为30%",
          element_ids: ["el-e2e"],
          confidence: 0.6,
        }],
        review_reason: "置信度低于阈值",
      }],
      review_tasks: [{
        task_id: "review-e2e",
        artifact_id: "raw-e2e",
        page: 1,
        field_name: "付款比例",
        reasons: ["置信度低于阈值"],
        candidates: [{
          field_name: "付款比例",
          value: "30%",
          quote: "付款比例为30%",
          element_ids: ["el-e2e"],
          confidence: 0.6,
        }],
        status: "pending",
        resolution: null,
      }],
      review_decisions: [],
    };
    await page.route("**/api/data-tasks/doc-e2e/extract", (route) => route.fulfill({
      json: pendingResult,
    }));
    await page.route(
      "**/api/data-tasks/doc-e2e/review-decisions/review-e2e",
      (route) => route.fulfill({
        json: {
          ...pendingResult,
          status: "COMPLETED",
          fields: [{ ...pendingResult.fields[0], status: "found" }],
          review_tasks: [{
            ...pendingResult.review_tasks[0],
            status: "resolved",
            resolution: {
              decision: "accept_candidate",
              candidate_index: 0,
              value: "30%",
              note: null,
              user_id: "u1",
              decided_at: "2026-07-22T00:00:00Z",
            },
          }],
          review_decisions: [],
        },
      }),
    );
    await page.goto("/data-prep?legacy=1");
    await page.locator('input[type="file"]').setInputFiles(PDF);
    const input = page.getByPlaceholder("例如：只提取付款节点、比例和收款账户");
    await input.fill("提取付款比例");
    await page.getByRole("button", { name: "发送需求" }).click();
    await page.getByRole("button", { name: "确认并开始抽取" }).click();

    await expect(page.getByTestId("review-panel")).toBeVisible();
    await page.getByRole("button", { name: "接受候选" }).click();

    await expect(page.getByText("裁决已保存并写入审计记录")).toBeVisible();
    await expect(page.getByRole("button", { name: "抽取已完成" })).toBeVisible();
  });

  test("设置页可保存当前用户的文档抽取默认模型", async ({ page }) => {
    await mockSession(page);
    let savedValue = "";
    await page.route("**/api/overview", (route) => route.fulfill({
      json: {
        collectors: [],
        scheduler: { enabled: false, active_count: 0 },
        connectors: {
          email: false,
          slack: false,
          embedding: false,
          checkpoint: false,
        },
        connectors_enabled: {
          email: false,
          slack: false,
          embedding: false,
          checkpoint: false,
        },
      },
    }));
    await page.route("**/api/config", (route) => route.fulfill({ json: { groups: [] } }));
    await page.route("**/api/config/models", (route) => route.fulfill({
      json: {
        models: {},
        default_provider: "deepseek",
        available_providers: [],
        local_urls: {},
      },
    }));
    await page.route("**/api/config/self/document_extraction_model", (route) => {
      savedValue = route.request().postDataJSON().value;
      return route.fulfill({ json: { ok: true } });
    });

    await page.goto("/settings");
    await page.getByLabel("文档抽取默认模型").selectOption("qwen::qwen-plus");
    await page.getByRole("button", { name: "保存", exact: true }).click();

    expect(savedValue).toBe("qwen::qwen-plus");
    await expect(page.getByText("文档抽取默认模型已保存到当前用户")).toBeVisible();
  });
});
