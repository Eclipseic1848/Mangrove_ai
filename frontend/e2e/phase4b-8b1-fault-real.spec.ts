import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

const PUBLIC_CONTRACT = path.resolve(
  process.cwd(),
  "../tests/fixtures/semantic_harness/public/batch0/documents/contract.docx",
);

test("模型超时后失败关闭且不由前端自动重试", async ({ page, request }) => {
  test.setTimeout(90_000);
  const suffix = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
  const username = `g5_fault_${suffix}`;
  const password = `G5-${crypto.randomUUID()}!`;
  const registration = await request.post("/api/auth/register", {
    data: { username, password, display_name: "G5 故障验收" },
  });
  expect(registration.status()).toBe(200);
  const session = await registration.json();

  await page.goto("/login");
  await page.getByPlaceholder("至少 2 位").fill(username);
  await page.getByPlaceholder("至少 6 位").fill(password);
  await page.locator('button[type="submit"]').click();
  await expect(page).not.toHaveURL(/\/login$/);
  await page.goto("/data-prep?legacy=1");

  await page.locator('input[type="file"]').setInputFiles({
    name: "contract_public.docx",
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    buffer: fs.readFileSync(PUBLIC_CONTRACT),
  });
  await expect(
    page.getByText("contract_public.docx", { exact: true }).first(),
  ).toBeVisible();

  let draftRequests = 0;
  page.on("request", (outbound) => {
    if (
      outbound.method() === "POST"
      && outbound.url().endsWith("/api/data-tasks/document-drafts")
    ) {
      draftRequests += 1;
    }
  });
  await page.getByPlaceholder("例如：只提取付款节点、比例和收款账户").fill(
    "提取合同付款节点和付款比例",
  );
  await expect(page.getByRole("button", { name: "发送需求" })).toBeEnabled({
    timeout: 15_000,
  });
  const failureResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().endsWith("/api/data-tasks/document-drafts")
  ));
  await page.getByRole("button", { name: "发送需求" }).click();
  const response = await failureResponse;
  expect(response.status()).toBe(502);
  const error = await response.json();
  expect(error.detail).toContain("抽取方案生成失败");
  expect(error.detail).not.toContain("host.docker.internal");
  expect(draftRequests).toBe(1);
  await expect(page.getByText(/抽取方案生成失败/)).toBeVisible();
  await expect(page.getByRole("button", { name: "发送需求" })).toBeEnabled();

  const tasks = await request.get("/api/data-tasks", {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  expect(tasks.status()).toBe(200);
  expect(await tasks.json()).toEqual([]);
  const readiness = await request.get("/api/readiness");
  expect(readiness.status()).toBe(200);
  expect((await readiness.json()).ready).toBe(true);
});
