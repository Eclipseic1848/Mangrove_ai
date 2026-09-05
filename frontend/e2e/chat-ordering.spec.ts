import { expect, test, type Page } from "@playwright/test";

// 全部 API 均在浏览器内替换；屏障显式释放，不依赖真实服务或固定等待。
async function openChat(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("mangrove_token", "synthetic-chat-token");
    const original = window.fetch.bind(window);
    const state = {
      calls: [] as string[],
      held: {} as Record<string, boolean>,
      pending: {} as Record<string, Array<(body: any) => void>>,
      streams: [] as ReadableStreamDefaultController<Uint8Array>[],
      running: false,
    };
    (window as any).chatMock = state;
    window.fetch = async (input, options) => {
      const url = new URL(String(input), location.origin);
      if (!url.pathname.startsWith("/api/")) return original(input, options);
      const path = url.pathname + url.search;
      const key = `${options?.method || "GET"} ${path}`;
      state.calls.push(key);
      const json = (body: any) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
      if (state.held[key]) return new Promise<Response>((resolve) => {
        (state.pending[key] ||= []).push((body) => resolve(json(body)));
      });
      if (url.pathname === "/api/chat/stream") {
        const stream = new ReadableStream<Uint8Array>({ start(controller) { state.streams.push(controller); } });
        return new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
      }
      if (url.pathname === "/api/auth/me") return json({ user_id: "chat-owner", username: "虚构用户", role: "user", access_token: "synthetic-chat-token" });
      if (path === "/api/conversations") return json(["A", "B"].map((id) => ({ conv_id: id, title: `会话${id}`, updated_at: "2026-09-01" })));
      if (path === "/api/models") return json({ options: [{ provider: "offline", model: "fake", label: "离线模型" }], default: { provider: "offline", model: "fake" } });
      if (url.pathname.endsWith("/messages")) {
        const id = url.pathname.split("/")[3];
        return json([{ id: id === "A" ? 11 : 22, role: "assistant", content: `${id}的正文` }]);
      }
      if (url.pathname.startsWith("/api/chat/running/")) return json({ running: state.running });
      if (url.pathname === "/api/chat/feedback") return json({ feedback: {} });
      if (options?.method && options.method !== "GET") return json({ message: "操作完成" });
      return new Response("未模拟 API", { status: 404 });
    };
  });
  await page.goto("/chat");
  await expect(page.getByRole("button", { name: "会话A", exact: true })).toBeVisible();
}

async function hold(page: Page, key: string) {
  await page.evaluate((key) => { (window as any).chatMock.held[key] = true; }, key);
}
async function requested(page: Page, key: string) {
  await expect.poll(() => page.evaluate((key) => (window as any).chatMock.pending[key]?.length || 0, key)).toBeGreaterThan(0);
}
async function release(page: Page, key: string, body: any) {
  await page.evaluate(async ({ key, body }) => {
    const state = (window as any).chatMock;
    state.held[key] = false;
    state.pending[key].shift()(body);
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  }, { key, body });
}
async function emit(page: Page, index: number, event: string, data: any) {
  await page.evaluate(async ({ index, event, data }) => {
    (window as any).chatMock.streams[index].enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  }, { index, event, data });
}
async function send(page: Page, text: string, index: number) {
  await page.getByPlaceholder("描述你的采集/分析任务", { exact: false }).fill(text);
  await page.getByPlaceholder("描述你的采集/分析任务", { exact: false }).press("Enter");
  await expect.poll(() => page.evaluate(() => (window as any).chatMock.streams.length)).toBe(index + 1);
}

test("chat ordering: 迟到历史不能覆盖当前会话且切换立即清正文", async ({ page }) => {
  await openChat(page);
  await page.getByRole("button", { name: "会话A", exact: true }).click();
  await expect(page.getByText("A的正文", { exact: true })).toBeVisible();
  await hold(page, "GET /api/conversations/B/messages");
  await page.getByRole("button", { name: "会话B", exact: true }).click();
  await requested(page, "GET /api/conversations/B/messages");
  await expect(page.getByText("A的正文", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "会话A", exact: true }).click();
  await expect(page.getByText("A的正文", { exact: true })).toBeVisible();
  await release(page, "GET /api/conversations/B/messages", [{ id: 22, role: "assistant", content: "B的迟到正文" }]);
  await expect(page.getByText("B的迟到正文", { exact: true })).toHaveCount(0);
});

test("chat ordering: 同会话新流使迟到历史失效", async ({ page }) => {
  await openChat(page);
  await hold(page, "GET /api/conversations/A/messages");
  await page.getByRole("button", { name: "会话A", exact: true }).click();
  await requested(page, "GET /api/conversations/A/messages");
  await send(page, "新的问题", 0);
  await emit(page, 0, "result", { reply: "新的回答", message_id: 31 });
  await release(page, "GET /api/conversations/A/messages", [{ id: 11, role: "assistant", content: "旧历史" }]);
  await expect(page.getByText("新的回答", { exact: true })).toBeVisible();
  await expect(page.getByText("旧历史", { exact: true })).toHaveCount(0);
});

test("chat ordering: 旧后台轮询结束不能改写新会话", async ({ page }) => {
  await openChat(page);
  await page.evaluate(() => { (window as any).chatMock.running = true; });
  await page.getByRole("button", { name: "会话A", exact: true }).click();
  await expect(page.getByText("该会话有任务正在后台执行", { exact: false })).toBeVisible();
  await hold(page, "GET /api/chat/running/A");
  await requested(page, "GET /api/chat/running/A");
  await page.evaluate(() => { (window as any).chatMock.running = false; });
  await page.getByRole("button", { name: "会话B", exact: true }).click();
  await expect(page.getByText("B的正文", { exact: true })).toBeVisible();
  await release(page, "GET /api/chat/running/A", { running: false });
  await expect(page.getByText("B的正文", { exact: true })).toBeVisible();
  await expect(page.getByText("A的正文", { exact: true })).toHaveCount(0);
});

test("chat ordering: 旧取消响应不能收口新流，切换清理运行节点", async ({ page }) => {
  await openChat(page);
  await page.getByRole("button", { name: "会话A", exact: true }).click();
  await send(page, "旧任务", 0);
  await emit(page, 0, "node", { node: "planner", label: "旧运行节点", view: {} });
  await hold(page, "POST /api/chat/A/cancel");
  await page.getByTitle("取消任务", { exact: true }).click();
  await requested(page, "POST /api/chat/A/cancel");
  await page.getByRole("button", { name: "会话B", exact: true }).click();
  await expect(page.getByText("旧运行节点", { exact: true })).toHaveCount(0);
  await send(page, "新任务", 1);
  await release(page, "POST /api/chat/A/cancel", {});
  await expect(page.getByTitle("取消任务", { exact: true })).toBeVisible();
  await emit(page, 1, "result", { reply: "新任务结果", message_id: 32 });
  await emit(page, 1, "done", {});
  await expect(page.getByText("新任务结果", { exact: true })).toBeVisible();
  await expect(page.getByTitle("取消任务", { exact: true })).toHaveCount(0);
});

test("chat ordering: 反馈迟到不能按旧下标修改新消息", async ({ page }) => {
  await openChat(page);
  await page.getByRole("button", { name: "会话A", exact: true }).click();
  await expect(page.getByText("A的正文", { exact: true })).toBeVisible();
  await hold(page, "POST /api/chat/feedback");
  await page.getByTitle("点赞", { exact: true }).click();
  await requested(page, "POST /api/chat/feedback");
  await page.getByRole("button", { name: "会话B", exact: true }).click();
  await expect(page.getByText("B的正文", { exact: true })).toBeVisible();
  await release(page, "POST /api/chat/feedback", {});
  await expect(page.getByTitle("点赞", { exact: true })).not.toHaveClass(/text-green-500/);
});

test("chat ordering: 删除旧会话的响应不能清空新会话", async ({ page }) => {
  await openChat(page);
  await page.getByRole("button", { name: "会话A", exact: true }).click();
  await expect(page.getByText("A的正文", { exact: true })).toBeVisible();
  await hold(page, "DELETE /api/conversations/A");
  await page.getByTitle("删除会话", { exact: true }).first().click();
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await requested(page, "DELETE /api/conversations/A");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "会话B", exact: true }).click();
  await expect(page.getByText("B的正文", { exact: true })).toBeVisible();
  await release(page, "DELETE /api/conversations/A", {});
  await expect(page.getByText("B的正文", { exact: true })).toBeVisible();
});

test("chat ordering: 同会话取消后重发不受旧取消响应影响", async ({ page }) => {
  await openChat(page);
  await page.getByRole("button", { name: "会话A", exact: true }).click();
  await send(page, "第一次执行", 0);
  await hold(page, "POST /api/chat/A/cancel");
  await page.getByTitle("取消任务", { exact: true }).click();
  await requested(page, "POST /api/chat/A/cancel");
  await send(page, "第二次执行", 1);
  await release(page, "POST /api/chat/A/cancel", {});
  await expect(page.getByTitle("取消任务", { exact: true })).toBeVisible();
  await emit(page, 1, "result", { reply: "第二次执行结果", message_id: 42 });
  await emit(page, 1, "done", {});
  await expect(page.getByText("第二次执行结果", { exact: true })).toBeVisible();
});

test("chat ordering: 旧动作迟到不能消费新会话同下标按钮", async ({ page }) => {
  await openChat(page);
  await page.getByRole("button", { name: "新建会话", exact: true }).click();
  await send(page, "第一个带动作任务", 0);
  await emit(page, 0, "meta", { conv_id: "A" });
  await emit(page, 0, "result", { reply: "待确认A", task_id: "task-a", message_id: 51, actions: ["db"] });
  await emit(page, 0, "done", {});
  await hold(page, "POST /api/confirm/db");
  await page.getByRole("button", { name: "确认入库", exact: true }).click();
  await requested(page, "POST /api/confirm/db");
  await page.getByRole("button", { name: "新建会话", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认入库", exact: true })).toHaveCount(0);
  await send(page, "第二个带动作任务", 1);
  await emit(page, 1, "meta", { conv_id: "B" });
  await emit(page, 1, "result", { reply: "待确认B", task_id: "task-b", message_id: 52, actions: ["db"] });
  await emit(page, 1, "done", {});
  await release(page, "POST /api/confirm/db", { message: "A已完成" });
  await expect(page.getByRole("button", { name: "确认入库", exact: true })).toBeVisible();
  await expect(page.getByText("A已完成", { exact: true })).toHaveCount(0);
});
