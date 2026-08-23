import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { expect, test, type APIResponse, type Download } from "@playwright/test";

const PUBLIC_CONTRACT = path.resolve(
  process.cwd(),
  "../tests/fixtures/semantic_harness/public/batch0/documents/contract.docx",
);

type TokenResponse = {
  access_token: string;
  user_id: string;
};

async function expectJson<T>(response: APIResponse, status = 200): Promise<T> {
  expect(response.status()).toBe(status);
  return response.json() as Promise<T>;
}

function bearer(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function expectNonEmptyDownload(
  download: Download,
  filename: RegExp,
): Promise<void> {
  expect(download.suggestedFilename()).toMatch(filename);
  expect(await download.failure()).toBeNull();
  const savedPath = await download.path();
  expect(savedPath).not.toBeNull();
  expect(fs.statSync(savedPath!).size).toBeGreaterThan(0);
}

test("真实服务完成登录、隔离、外部模型抽取和结果下载", async ({ page, request }) => {
  test.setTimeout(30 * 60 * 1_000);

  const suffix = process.env.PHASE4B_ACCEPTANCE_SUFFIX
    ?? crypto.randomUUID().replaceAll("-", "").slice(0, 12);
  expect(suffix).toMatch(/^[A-Za-z0-9]{6,20}$/);
  const admin = `g5_admin_${suffix}`;
  const userA = `g5_owner_a_${suffix}`;
  const userB = `g5_owner_b_${suffix}`;
  const concurrentUsers = Array.from(
    { length: 18 },
    (_, index) => `g5_vu_${String(index + 3).padStart(2, "0")}_${suffix}`,
  );
  const password = process.env.PHASE4B_ACCEPTANCE_PASSWORD
    ?? `G5-${crypto.randomUUID()}!`;
  const expectedModel = process.env.PHASE4B_ACCEPTANCE_MODEL_NAME;
  if (!expectedModel) {
    throw new Error("PHASE4B_ACCEPTANCE_MODEL_NAME is required");
  }

  const bootstrap = await expectJson<TokenResponse>(
    await request.post("/api/auth/register", {
      data: { username: admin, password, display_name: "G5 验收管理员" },
    }),
  );

  for (const username of [userA, userB, ...concurrentUsers]) {
    await expectJson(
      await request.post("/api/admin/users", {
        headers: bearer(bootstrap.access_token),
        data: { username, password, display_name: username, role: "user" },
      }),
    );
  }
  await expectJson(
    await request.post("/api/admin/users", {
      headers: bearer(bootstrap.access_token),
      data: { username: userA, password, display_name: userA, role: "user" },
    }),
    409,
  );

  const userBSession = await expectJson<TokenResponse>(
    await request.post("/api/auth/login", {
      data: { username: userB, password },
    }),
  );
  const userASession = await expectJson<TokenResponse>(
    await request.post("/api/auth/login", {
      data: { username: userA, password },
    }),
  );

  await page.goto("/login");
  await page.getByPlaceholder("至少 2 位").fill(userA);
  await page.getByPlaceholder("至少 6 位").fill(password);
  await page.locator('button[type="submit"]').click();
  await expect(page).not.toHaveURL(/\/login$/);
  await page.goto("/data-prep?legacy=1");

  await expect(page.getByLabel("文档抽取模型")).toHaveValue(
    `local::${expectedModel}`,
  );

  const uploadResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().endsWith("/api/data-sources/uploads")
  ));
  await page.locator('input[type="file"]').setInputFiles({
    name: "contract_public.docx",
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    buffer: fs.readFileSync(PUBLIC_CONTRACT),
  });
  const upload = await expectJson<{ upload_id: string }>(await uploadResponse);
  await expect(
    page.getByRole("heading", { name: "contract_public.docx", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("document-preview")).toContainText("付款条款");

  const intent = page.getByPlaceholder("例如：只提取付款节点、比例和收款账户");
  await intent.fill("提取合同付款节点和付款比例");
  const draftResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().endsWith("/api/data-tasks/document-drafts")
  ));
  await page.getByRole("button", { name: "发送需求" }).click();
  const draft = await expectJson<{ task_id: string }>(await draftResponse);

  const fieldNames = page.getByLabel("字段名称");
  await expect(fieldNames).toHaveCount(2);
  await expect.poll(() => fieldNames.evaluateAll((elements) => (
    elements.map((element) => (element as HTMLInputElement).value)
  ))).toEqual(
    expect.arrayContaining(["付款节点", "付款比例"]),
  );

  const extractionResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().endsWith(`/api/data-tasks/${draft.task_id}/extract`)
  ), { timeout: 25 * 60 * 1_000 });
  await page.getByRole("button", { name: "确认并开始抽取" }).click();
  await expectJson(await extractionResponse);
  await expect(page.getByRole("button", { name: "抽取已完成" })).toBeVisible();
  await expect(
    page.getByText(/验收通过后十五个工作日内|百分之六十|60%/).first(),
  ).toBeVisible();

  const crossUpload = await request.get(
    `/api/data-sources/uploads/${upload.upload_id}`,
    { headers: bearer(userBSession.access_token) },
  );
  expect(crossUpload.status()).toBe(404);
  const crossTask = await request.get(`/api/data-tasks/${draft.task_id}`, {
    headers: bearer(userBSession.access_token),
  });
  expect(crossTask.status()).toBe(404);

  const authoritativePromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载权威结果 JSON/JSONL" }).click();
  await expectNonEmptyDownload(
    await authoritativePromise,
    /^extracted_(fields|records)\.jsonl$/,
  );

  const xlsxPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 XLSX 查看副本" }).click();
  await expectNonEmptyDownload(await xlsxPromise, /^document_extraction\.xlsx$/);

  const manifestPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 Manifest" }).click();
  const manifest = await manifestPromise;
  await expectNonEmptyDownload(manifest, /^manifest\.json$/);
  const manifestPath = await manifest.path();
  const manifestJson = JSON.parse(fs.readFileSync(manifestPath!, "utf8"));
  expect(manifestJson.task_id).toBe(draft.task_id);

  const concurrentSessions = await Promise.all(concurrentUsers.map(async (username) => (
    expectJson<TokenResponse>(
      await request.post("/api/auth/login", { data: { username, password } }),
    )
  )));
  const sessions = [userASession, userBSession, ...concurrentSessions];
  expect(sessions).toHaveLength(20);

  const concurrentReads = await Promise.all(sessions.map(async (session, index) => {
    const headers = bearer(session.access_token);
    const responses = await Promise.all([
      request.get(`/api/data-sources/uploads/${upload.upload_id}`, { headers }),
      request.get(`/api/data-tasks/${draft.task_id}`, { headers }),
      request.get(`/api/data-tasks/${draft.task_id}/manifest`, { headers }),
      request.get(`/api/downloads/${draft.task_id}/manifest.json`, { headers }),
    ]);
    return { index, statuses: responses.map((response) => response.status()) };
  }));
  expect(concurrentReads[0].statuses).toEqual([200, 200, 200, 200]);
  for (const crossOwnerRead of concurrentReads.slice(1)) {
    expect(crossOwnerRead.statuses).toEqual([404, 404, 404, 404]);
  }

  const completedTaskBefore = await expectJson<Record<string, unknown>>(
    await request.get(`/api/data-tasks/${draft.task_id}`, {
      headers: bearer(userASession.access_token),
    }),
  );
  expect(completedTaskBefore.status).toBe("COMPLETED");
  const completedManifestBefore = await expectJson<{
    outputs: Array<{ path: string }>;
    [key: string]: unknown;
  }>(
    await request.get(`/api/data-tasks/${draft.task_id}/manifest`, {
      headers: bearer(userASession.access_token),
    }),
  );

  const repeatedExtractions = await Promise.all(Array.from({ length: 40 }, () => (
    request.post(`/api/data-tasks/${draft.task_id}/extract`, {
      headers: bearer(userASession.access_token),
    })
  )));
  expect(repeatedExtractions.every((response) => response.status() === 409)).toBe(true);
  const stableTask = await expectJson<Record<string, unknown>>(
    await request.get(`/api/data-tasks/${draft.task_id}`, {
      headers: bearer(userASession.access_token),
    }),
  );
  expect(stableTask).toEqual(completedTaskBefore);

  const stableManifest = await expectJson<{
    outputs: Array<{ path: string }>;
    [key: string]: unknown;
  }>(
    await request.get(`/api/data-tasks/${draft.task_id}/manifest`, {
      headers: bearer(userASession.access_token),
    }),
  );
  expect(stableManifest).toEqual(completedManifestBefore);
  const outputPaths = stableManifest.outputs.map((output) => output.path);
  expect(new Set(outputPaths).size).toBe(outputPaths.length);
  const ownerOutputReads = await Promise.all(outputPaths.map((outputPath) => (
    request.get(`/api/downloads/${outputPath}`, {
      headers: bearer(userASession.access_token),
    })
  )));
  expect(ownerOutputReads.every((response) => response.status() === 200)).toBe(true);
  const crossOwnerOutputReads = await Promise.all(outputPaths.map((outputPath) => (
    request.get(`/api/downloads/${outputPath}`, {
      headers: bearer(userBSession.access_token),
    })
  )));
  expect(crossOwnerOutputReads.every((response) => response.status() === 404)).toBe(true);
  await expectJson(await request.get("/api/readiness"));

  const resultPath = process.env.PHASE4B_ACCEPTANCE_RESULT_PATH;
  if (resultPath) {
    fs.writeFileSync(resultPath, `${JSON.stringify({
      schema_version: "phase4b-8b1-flow/v1",
      upload_id: upload.upload_id,
      task_id: draft.task_id,
      output_paths: outputPaths,
    })}\n`, { encoding: "utf8", flag: "wx" });
  }
});
