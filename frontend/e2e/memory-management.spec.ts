import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function memoryFixture(page: Page, count = 23) {
  const state = {
    rows: Array.from({ length: count }, (_, i) => ({ id: i + 1, text: `报告偏好 ${i + 1}`, created_at: "2026-09-19T08:00:00+08:00" })),
    preferences: "## 共同偏好\n\n报告注明来源。\n\n![外部图片](https://memory-test.invalid/image.png)",
    failed: false, writes: 0, searches: [] as string[], external: [] as string[], errors: [] as string[],
  };
  page.on("pageerror", error => state.errors.push(error.message));
  await page.route("https://memory-test.invalid/**", route => { state.external.push(route.request().url()); return route.abort(); });
  await page.route("**/api/**", route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { user_id: "ux-state", username: "ux-state", role: "admin" } });
    if (url.pathname === "/api/memory" && request.method() === "GET") {
      if (state.failed) return route.fulfill({ status: 503, json: { detail: "合成读取失败" } });
      const query = url.searchParams.get("q") || "";
      state.searches.push(query);
      const size = Number(url.searchParams.get("page_size") || 10);
      const selected = state.rows.filter(row => row.text.includes(query));
      const current = Math.min(Number(url.searchParams.get("page") || 1), Math.max(1, Math.ceil(selected.length / size)));
      return route.fulfill({ json: { personal: selected.slice((current - 1) * size, current * size), total: selected.length, page: current, preferences: state.preferences, preferences_digest: "a".repeat(64) } });
    }
    if (url.pathname === "/api/memory/self" && request.method() === "POST") {
      state.writes++; const item = { id: 100, text: request.postDataJSON().text, created_at: "2026-09-19T09:00:00+08:00" };
      state.rows.unshift(item); return route.fulfill({ json: { ok: true, item } });
    }
    if (url.pathname.startsWith("/api/memory/self/") && request.method() === "DELETE") {
      state.writes++; state.rows = state.rows.filter(row => row.id !== Number(url.pathname.split("/").pop()));
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === "/api/memory" && request.method() === "PATCH") {
      state.writes++; state.preferences = request.postDataJSON().text; return route.fulfill({ json: { ok: true } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/memory");
  await expect(page.getByLabel("添加个人记忆")).toBeVisible();
  return state;
}

test("浏览器后退取消保留草稿，确认后才离开", async ({ page }) => {
  await memoryFixture(page);
  await page.getByRole("link", { name: "模板库", exact: true }).click();
  await page.getByRole("link", { name: "记忆", exact: true }).click();
  await page.getByLabel("添加个人记忆").fill("未保存的历史导航草稿");
  await page.evaluate(() => history.back());
  const dialog = page.getByRole("alertdialog", { name: "离开未保存的记忆？" });
  await expect(dialog).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page).toHaveURL(/\/memory$/);
  await expect(page.getByLabel("添加个人记忆")).toHaveValue("未保存的历史导航草稿");
  await page.evaluate(() => history.back());
  await expect(dialog).toBeVisible();
  await page.getByRole("button", { name: "放弃并离开", exact: true }).click();
  await expect(page).toHaveURL(/\/templates$/);
  await page.evaluate(() => history.forward());
  await expect(page.getByLabel("添加个人记忆")).toHaveValue("");
});

test("筛选下新增可定位，删除取消不写入，确认后刷新", async ({ page }) => {
  const state = await memoryFixture(page);
  await page.getByLabel("搜索个人记忆", { exact: true }).fill("不存在");
  await expect(page.getByText("没有匹配的个人记忆", { exact: true })).toBeVisible();
  await page.getByLabel("添加个人记忆").fill("新报告使用中文");
  await page.getByRole("button", { name: "记住", exact: true }).click();
  await page.getByRole("button", { name: "查看已保存记忆" }).click();
  await expect(page.getByText("新报告使用中文", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "删除记忆：新报告使用中文", exact: true }).click();
  await expect(page.getByRole("alertdialog").getByRole("button", { name: "取消" })).toBeFocused();
  await page.getByRole("alertdialog").getByRole("button", { name: "取消" }).click();
  expect(state.writes).toBe(1);
  await page.getByRole("button", { name: "删除记忆：新报告使用中文", exact: true }).click();
  await page.getByRole("button", { name: "删除记忆", exact: true }).click();
  await expect(page.getByText("没有匹配的个人记忆", { exact: true })).toBeVisible();
  expect(state.writes).toBe(2);
});

test("全局草稿前进保护，多步后退取消后历史链不变", async ({ page }) => {
  await memoryFixture(page);
  await page.getByRole("link", { name: "模板库", exact: true }).click();
  await page.getByRole("link", { name: "记忆", exact: true }).click();
  await page.evaluate(() => history.back());
  await expect(page).toHaveURL(/\/templates$/);
  await page.evaluate(() => history.back());
  await expect(page.getByLabel("添加个人记忆")).toBeVisible();
  await page.getByRole("tab", { name: "全局记忆", exact: true }).click();
  await page.getByLabel("添加全局记忆").fill("未保存共享偏好");
  await page.evaluate(() => history.forward());
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByLabel("添加全局记忆")).toHaveValue("未保存共享偏好");
  await page.evaluate(() => history.forward());
  await page.getByRole("button", { name: "放弃并离开", exact: true }).click();
  await expect(page).toHaveURL(/\/templates$/);
  await page.evaluate(() => history.forward());
  await page.getByLabel("添加个人记忆").fill("多步后退草稿");
  await page.evaluate(() => history.go(-2));
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByLabel("添加个人记忆")).toHaveValue("多步后退草稿");
  await page.evaluate(() => history.go(-2));
  await page.getByRole("button", { name: "放弃并离开", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.getByLabel("添加个人记忆")).toHaveValue("");
  await page.getByLabel("添加个人记忆").fill("再次编辑仍受保护");
  await page.evaluate(() => history.forward());
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByLabel("添加个人记忆")).toHaveValue("再次编辑仍受保护");
  await page.getByLabel("添加个人记忆").fill("");
  await page.evaluate(() => history.forward());
  await expect(page).toHaveURL(/\/templates$/);
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
});

test("保存处理中后退不卸载页面，完成后恢复正常导航", async ({ page }) => {
  await memoryFixture(page);
  await page.getByRole("link", { name: "模板库", exact: true }).click();
  await page.getByRole("link", { name: "记忆", exact: true }).click();
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/memory/self", async route => { await held; await route.fallback(); });
  await page.getByLabel("添加个人记忆").fill("等待保存的偏好");
  await page.getByRole("button", { name: "记住", exact: true }).click();
  await page.evaluate(() => history.back());
  await expect(page.getByRole("alertdialog", { name: "操作尚未完成" })).toBeVisible();
  await expect(page.getByRole("alertdialog").getByRole("button", { name: "处理中…", exact: true })).toBeDisabled();
  release();
  await expect(page.getByRole("button", { name: "取消", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByLabel("添加个人记忆")).toHaveValue("");
  await page.evaluate(() => history.back());
  await expect(page).toHaveURL(/\/templates$/);
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
});

test("搜索防抖与组合输入，刷新失败保留正文", async ({ page }) => {
  const state = await memoryFixture(page);
  await expect(page.getByText("报告偏好 1", { exact: true })).toBeVisible();
  const search = page.getByLabel("搜索个人记忆", { exact: true });
  await search.dispatchEvent("compositionstart");
  await search.fill("报告偏好");
  await page.waitForTimeout(400);
  expect(state.searches.filter(Boolean)).toEqual([]);
  await search.dispatchEvent("compositionend");
  await expect.poll(() => state.searches.filter(Boolean)).toEqual(["报告偏好"]);
  state.failed = true;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByText("更新失败，当前显示上次读取的记忆。", { exact: true })).toBeVisible();
  await expect(page.getByText("报告偏好 1", { exact: true })).toBeVisible();
});

test("延迟保存不能恢复旧筛选，末页删除自动回退", async ({ page }) => {
  const state = await memoryFixture(page, 21);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/memory/self", async route => { await held; await route.fallback(); });
  await page.getByLabel("添加个人记忆").fill("新添加的记录");
  await page.getByRole("button", { name: "记住", exact: true }).click();
  await page.getByLabel("搜索个人记忆", { exact: true }).fill("偏好 17");
  await expect(page.getByText("报告偏好 17", { exact: true })).toBeVisible();
  await expect(page.getByText("报告偏好 1", { exact: true })).toHaveCount(0);
  release();
  await expect(page.getByRole("status").filter({ hasText: "已保存，后续相关任务会参考这条记忆。" })).toBeVisible();
  await expect(page.getByRole("button", { name: "记住", exact: true })).toBeDisabled();
  await expect(page.getByText("报告偏好 1", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "清除搜索" }).click();
  await page.getByLabel("每页条数").selectOption("20");
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  for (const name of ["报告偏好 20", "报告偏好 21"]) {
    await page.getByRole("button", { name: `删除记忆：${name}`, exact: true }).click();
    await page.getByRole("button", { name: "删除记忆", exact: true }).click();
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
  }
  await expect(page.getByText("1 / 1 页", { exact: true })).toBeVisible();
  expect(state.rows.length).toBe(20);
});

test("原位编辑可取消，离开链接保护草稿，全局新增草稿不被替换保存清空", async ({ page }, testInfo) => {
  await memoryFixture(page);
  await page.getByRole("button", { name: "编辑记忆：报告偏好 1", exact: true }).click();
  await expect(page.getByLabel("编辑内容", { exact: true })).toBeFocused();
  await page.getByLabel("编辑内容", { exact: true }).fill("未保存的修改");
  await page.getByRole("button", { name: "取消编辑", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "取消" }).click();
  await expect(page.getByLabel("编辑内容", { exact: true })).toHaveValue("未保存的修改");
  await page.getByRole("link", { name: "模板库", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toContainText("当前输入或修改尚未保存");
  await page.getByRole("alertdialog").getByRole("button", { name: "取消" }).click();
  await expect(page).toHaveURL(/\/memory$/);
  await page.screenshot({ path: testInfo.outputPath("memory-inline-edit.png") });
  await page.getByRole("tab", { name: "全局记忆", exact: true }).click();
  await page.getByLabel("添加全局记忆", { exact: true }).fill("独立的全局新增草稿");
  await page.getByRole("button", { name: "编辑全局记忆", exact: true }).click();
  await page.getByLabel("全局记忆内容", { exact: true }).fill("更新已有规范");
  await page.getByRole("button", { name: "保存全局记忆", exact: true }).click();
  await page.getByRole("button", { name: "确认保存", exact: true }).click();
  await expect(page.getByLabel("添加全局记忆", { exact: true })).toHaveValue("独立的全局新增草稿");
});

test("全局安全预览与保存确认，切换范围保留草稿", async ({ page }) => {
  const state = await memoryFixture(page);
  await page.getByLabel("添加个人记忆").fill("未保存个人偏好");
  await page.getByRole("tab", { name: "全局记忆", exact: true }).click();
  await expect(page.getByRole("link", { name: "查看图片：外部图片" })).toBeVisible();
  expect(state.external).toEqual([]);
  await page.getByRole("button", { name: "编辑全局记忆", exact: true }).click();
  await expect(page.getByLabel("全局记忆内容")).toBeFocused();
  await page.getByLabel("全局记忆内容").fill("更新后的全局内容");
  await page.getByText("预览修改后的内容", { exact: true }).click();
  await expect(page.getByRole("paragraph").filter({ hasText: /^更新后的全局内容$/ })).toBeVisible();
  await expect(page.getByText("报告注明来源。", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "保存全局记忆", exact: true }).click();
  expect(state.writes).toBe(0);
  await page.getByRole("button", { name: "确认保存", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  expect(state.writes).toBe(1);
  await page.getByRole("tab", { name: "我的记忆", exact: true }).click();
  await expect(page.getByLabel("添加个人记忆")).toHaveValue("未保存个人偏好");
  expect(state.errors).toEqual([]);
});

test("空态简洁，长输入限高，明暗窄屏与键盘可访问", async ({ page }, testInfo) => {
  const state = await memoryFixture(page, 0);
  await expect(page.getByText("添加一条常用偏好，下次不用重复说明。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("每页条数")).toHaveCount(0);
  await expect(page.getByLabel("搜索个人记忆", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "金额保留两位小数", exact: true }).click();
  await expect(page.getByLabel("添加个人记忆")).toHaveValue("金额保留两位小数");
  await page.getByLabel("添加个人记忆").fill("多行输入\n".repeat(70));
  const bounds = await page.getByLabel("添加个人记忆").boundingBox();
  expect(bounds!.height).toBeLessThanOrEqual(242);
  expect(await page.getByLabel("添加个人记忆").evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
  await page.getByLabel("添加个人记忆").fill("");
  for (const dark of [false, true]) {
    await page.evaluate(value => document.documentElement.classList.toggle("dark", value), dark);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`memory-new-${width}-${dark}.png`) });
      expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
    }
  }
  await page.getByRole("tab", { name: "我的记忆", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "全局记忆", exact: true })).toBeFocused();
  await expect(page.getByRole("region", { name: "全局记忆", exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test("编辑个人记忆时搜索不能静默丢失草稿", async ({ page }) => {
  await page.route("**/api/**", route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { user_id: "ux-test", username: "ux-test", role: "user" } });
    if (url.pathname === "/api/memory") return route.fulfill({ json: { personal: url.searchParams.get("q") ? [] : [{ id: 1, text: "报告使用表格", created_at: "2026-09-19T08:00:00+08:00" }], total: 1, preferences: "", preferences_digest: "a".repeat(64) } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/memory");
  await page.getByRole("button", { name: /(?:纠正记忆 1|编辑记忆：报告使用表格)/ }).click();
  const editor = page.getByRole("textbox", { name: /^(纠正内容|编辑内容)$/ });
  await editor.fill("尚未保存的报告偏好");
  await page.getByRole("textbox", { name: "搜索个人记忆" }).fill("其他");
  await expect(page.getByText("这条记忆不在当前页或筛选结果中，未保存的编辑已保留。", { exact: true })).toBeVisible();
  await expect(editor).toHaveValue("尚未保存的报告偏好");
});

for (const role of ["user", "admin", "super_admin"]) {
  test(`${role} 全局添加入口归属清晰且窄屏不溢出`, async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    const requests: { path: string; text: string }[] = [];
    let preferences = "## 共同偏好\n- 语言：简体中文。";
    await page.route("**/api/**", route => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "layout-test", username: "layout-test", role } });
      if (path === "/api/memory" && route.request().method() === "POST") {
        const { text } = route.request().postDataJSON();
        requests.push({ path, text });
        preferences += `\n- ${text}`;
        return route.fulfill({ json: { ok: true } });
      }
      if (path === "/api/memory") return route.fulfill({ json: { personal: [], total: 0, preferences, preferences_digest: "a".repeat(64) } });
      return route.fulfill({ json: {} });
    });
    await page.goto("/memory");
    await page.getByRole("tab", { name: "全局记忆", exact: true }).click();
    const global = page.getByRole("region", { name: "全局记忆", exact: true });
    await expect(global).toBeVisible();
    await expect(global.getByText("所有用户共享", { exact: true })).toBeVisible();
    if (role === "user") {
      await expect(global.getByLabel("添加全局记忆", { exact: true })).toHaveCount(0);
    } else {
      await global.getByLabel("添加全局记忆", { exact: true }).fill("所有报告标明数据来源");
      await global.getByRole("button", { name: "添加", exact: true }).click();
      await expect(global.getByText("所有报告标明数据来源", { exact: true })).toBeVisible();
      expect(requests).toEqual([{ path: "/api/memory", text: "所有报告标明数据来源" }]);
    }
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
      await page.screenshot({ path: testInfo.outputPath(`memory-layout-${role}-${width}.png`), fullPage: true });
    }
    expect(errors).toEqual([]);
  });
}

for (const role of ["user", "admin", "super_admin"]) {
  test(`${role} 全局记忆编辑权限与冲突保护`, async ({ page }) => {
    let writes = 0;
    await page.route("**/api/**", route => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "synthetic", username: "synthetic", role } });
      if (path === "/api/memory" && route.request().method() === "PATCH") {
        writes++;
        return route.fulfill({ status: 409, json: { detail: "平台规范已变化，请刷新后重新编辑" } });
      }
      if (path === "/api/memory") return route.fulfill({ json: { personal: [], total: 0, preferences: "报告保留来源", preferences_digest: "a".repeat(64) } });
      return route.fulfill({ json: {} });
    });
    await page.goto("/memory");
    await page.getByRole("tab", { name: "全局记忆", exact: true }).click();
    if (role === "user") {
      await expect(page.getByText("报告保留来源", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "编辑全局记忆" })).toHaveCount(0);
      expect(writes).toBe(0);
      return;
    }
    await page.getByRole("button", { name: "编辑全局记忆" }).click();
    await page.getByLabel("全局记忆内容").fill("报告保留来源及日期");
    await page.getByRole("button", { name: "保存全局记忆" }).click();
    await page.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("平台规范已变化");
    await expect(page.getByLabel("全局记忆内容")).toHaveValue("报告保留来源及日期");
    expect(writes).toBe(1);
  });
}

test("记忆读取失败不会冒充空状态，刷新可恢复完整正文", async ({ page }, testInfo) => {
  let failed = true;
  const content = "报告优先按部门整理。".repeat(60) + "保留最后一段内容";
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "synthetic", username: "synthetic", role: "user" } });
    if (path === "/api/memory") return route.fulfill(failed
      ? { status: 503, json: { detail: "内部存储错误不可直接展示" } }
      : { json: { preferences: "", personal: [{ id: 1, text: content, created_at: "2026-09-19T08:00:00+08:00" }] } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/memory");
  await expect(page.getByRole("alert")).toContainText("记忆加载失败，请刷新重试。");
  await expect(page.getByText("还没有个人记忆")).toHaveCount(0);
  await expect(page.getByText(/还没有全局记忆/)).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  const body = page.getByText(content, { exact: true });
  await expect(body).toBeVisible();
  expect(await body.evaluate(el => getComputedStyle(el).whiteSpace)).toBe("pre-wrap");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("memory-mobile.png") });
});

test("个人记忆搜索分页与纠正冲突保留输入", async ({ page }) => {
  let rows = Array.from({ length: 23 }, (_, i) => ({ id: i + 1, text: `偏好 ${i + 1}`, created_at: "2026-09-19T08:00:00+08:00" }));
  let conflict = true;
  await page.route("**/api/**", route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { user_id: "synthetic", username: "synthetic", role: "user" } });
    if (url.pathname === "/api/memory") {
      const size = Number(url.searchParams.get("page_size") || 10);
      const selected = rows.filter(row => row.text.includes(url.searchParams.get("q") || ""));
      const current = Math.min(Number(url.searchParams.get("page") || 1), Math.max(1, Math.ceil(selected.length / size)));
      return route.fulfill({ json: { preferences: "", personal: selected.slice((current - 1) * size, current * size), total: selected.length, page: current, page_size: size } });
    }
    if (url.pathname === "/api/memory/self/11" && route.request().method() === "PATCH") {
      const body = route.request().postDataJSON();
      if (conflict) return route.fulfill({ status: 409, json: { detail: "记忆已变化，请刷新后再纠正" } });
      rows = rows.map(row => row.id === 11 ? { ...row, text: body.text } : row);
      return route.fulfill({ json: { ok: true } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/memory");
  await expect(page.getByLabel("每页条数")).toHaveValue("10");
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await page.getByRole("button", { name: "编辑记忆：偏好 11", exact: true }).click();
  await page.getByLabel("编辑内容").fill("报告按部门汇总");
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByText("记忆已变化，请刷新后再纠正", { exact: true })).toBeVisible();
  await expect(page.getByLabel("编辑内容")).toHaveValue("报告按部门汇总");
  conflict = false;
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByText("报告按部门汇总", { exact: true })).toBeVisible();
  await page.getByLabel("搜索个人记忆").fill("报告");
  await expect(page.getByText("共 1 条 · 1–1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "上一页", exact: true })).toBeDisabled();
});
