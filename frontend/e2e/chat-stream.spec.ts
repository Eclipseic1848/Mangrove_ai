import { expect, test, type Page } from "@playwright/test";

type StreamState = { done: number; errors: string[]; results: unknown[]; nodes: unknown[]; unhandled: string[]; transportStopped: boolean };

async function harness(page: Page, mode: string) {
  await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
  await page.route("**/stream-test-harness", (route) => route.fulfill({
    contentType: "text/html", body: '<html lang="zh-CN"><body>隔离流测试</body></html>',
  }));
  await page.goto("/stream-test-harness");
  await page.evaluate(async (scenario) => {
    const modulePath = "/src/lib/api.ts";
    const { streamChat } = await import(/* @vite-ignore */ modulePath);
    const state = { done: 0, errors: [] as string[], results: [] as unknown[], nodes: [] as unknown[], unhandled: [] as string[], transportStopped: false };
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const encoder = new TextEncoder();
    const originalFetch = window.fetch;
    window.addEventListener("unhandledrejection", (event) => {
      state.unhandled.push(String(event.reason)); event.preventDefault();
    });
    window.fetch = async (input, init) => {
      if (String(input) !== "/api/chat/stream") return originalFetch(input, init);
      if (scenario === "fetch-reject") throw new TypeError("synthetic fetch failure");
      if (scenario === "http-error") return new Response("synthetic unavailable", { status: 503 });
      if (scenario === "missing-body") return new Response(null, { status: 200 });
      if (scenario === "abort-before-response") {
        return new Promise<Response>((_resolve, reject) => {
          init!.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
        });
      }
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller;
          if (scenario === "done-eof") {
            controller.enqueue(encoder.encode('event: result\r\ndata: {"reply":"正常完成"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n'));
            controller.close();
          } else if (scenario === "events-after-done") {
            controller.enqueue(encoder.encode('event: done\ndata: {}\n\nevent: result\ndata: {"reply":"迟到正文"}\n\nevent: node\ndata: {"node":"late"}\n\nevent: error\ndata: {"message":"迟到错误"}\n\n'));
            controller.close();
          } else if (scenario === "utf8-crlf") {
            const bytes = encoder.encode('event: result\r\ndata: {"reply":"中文🌿完整正文"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n');
            // 每个字节独立一块，同时切断 UTF-8 字符与 CRLF 行分隔。
            for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
            controller.close();
          } else if (scenario === "server-error") {
            controller.enqueue(encoder.encode('event: error\ndata: {"message":"synthetic server error"}\n\nevent: done\ndata: {}\n\n'));
            controller.close();
          } else if (scenario !== "get-reader-error") {
            controller.enqueue(encoder.encode('event: node\ndata: {"node":"started","label":"正在读取"}\n\n'));
          }
        },
        cancel() { state.transportStopped = true; },
      });
      init?.signal?.addEventListener("abort", () => {
        state.transportStopped = true;
        streamController.error(new DOMException("aborted", "AbortError"));
      }, { once: true });
      const response = new Response(body, { headers: { "Content-Type": "text/event-stream" } });
      if (scenario === "get-reader-error") {
        Object.defineProperty(response.body, "getReader", { value: () => { throw new Error("synthetic reader failure"); } });
      }
      return response;
    };
    const cancel = streamChat({ content: "虚构请求" }, {
      onDone: () => { state.done += 1; },
      onError: (error: { message: string }) => state.errors.push(error.message),
      onResult: (result: unknown) => state.results.push(result),
      onNode: (node: unknown) => state.nodes.push(node),
    });
    Object.assign(window, { streamHarness: { state, cancel,
      fail: () => streamController.error(new TypeError("synthetic read failure")) } });
  }, mode);
}

async function state(page: Page): Promise<StreamState> {
  return page.evaluate(() => (window as unknown as { streamHarness: { state: StreamState } }).streamHarness.state);
}

test.describe("聊天流恰一次收口", () => {
  for (const mode of ["fetch-reject", "http-error", "missing-body", "get-reader-error", "read-error", "server-error"]) {
    test(`${mode} 报错一次并结束一次`, async ({ page }) => {
      await harness(page, mode);
      if (mode === "read-error") {
        await expect.poll(async () => (await state(page)).nodes.length).toBe(1);
        await page.evaluate(() => (window as unknown as { streamHarness: { fail: () => void } }).streamHarness.fail());
      }
      await expect.poll(async () => (await state(page)).done, { timeout: 3000 }).toBe(1);
      const result = await state(page);
      expect(result.errors).toHaveLength(1);
      expect(result.unhandled).toEqual([]);
      if (mode === "get-reader-error") {
        await expect.poll(async () => (await state(page)).transportStopped, { timeout: 3000 }).toBe(true);
      }
    });
  }

  for (const mode of ["done-eof", "events-after-done", "utf8-crlf", "abort-before-response", "abort-during-read"]) {
    test(`${mode} 结束一次且无额外事件`, async ({ page }) => {
      await harness(page, mode);
      if (mode.startsWith("abort")) {
        if (mode === "abort-during-read") await expect.poll(async () => (await state(page)).nodes.length).toBe(1);
        await page.evaluate(() => {
          const harness = (window as unknown as { streamHarness: { cancel: () => void } }).streamHarness;
          harness.cancel(); harness.cancel();
        });
      }
      await expect.poll(async () => (await state(page)).done > 0, { timeout: 3000 }).toBe(true);
      const result = await state(page);
      expect.soft(result.done).toBe(1);
      expect(result.errors).toEqual([]);
      expect(result.unhandled).toEqual([]);
      if (mode === "done-eof") expect(result.results).toEqual([{ reply: "正常完成" }]);
      else if (mode === "utf8-crlf") expect(result.results).toEqual([{ reply: "中文🌿完整正文" }]);
      else expect(result.results).toEqual([]);
      if (mode === "events-after-done") expect(result.nodes).toEqual([]);
    });
  }

  test("真实聊天首包失败后可再次发送成功", async ({ page }) => {
    await page.route("**/api/**", (route) => route.fulfill({ status: 404, json: {} }));
    await page.addInitScript(() => localStorage.setItem("mangrove_token", "synthetic-token"));
    await page.route("**/api/auth/me", (route) => route.fulfill({ json: {
      user_id: "synthetic-owner", username: "tester", display_name: "测试用户", role: "user", access_token: "synthetic-token",
    } }));
    await page.route("**/api/conversations", (route) => route.fulfill({ json: [] }));
    await page.route("**/api/models", (route) => route.fulfill({ json: {
      options: [{ provider: "local", model: "synthetic-model", label: "虚构模型" }],
      default: { provider: "local", model: "synthetic-model" },
    } }));
    let requests = 0;
    await page.route("**/api/chat/stream", async (route) => {
      requests += 1;
      if (requests === 1) return route.abort("failed");
      await route.fulfill({ contentType: "text/event-stream", body:
        'event: meta\ndata: {"conv_id":"synthetic-conversation"}\n\nevent: result\ndata: {"reply":"第二次请求已完成","kind":"output"}\n\nevent: done\ndata: {}\n\n',
      });
    });
    const unhandled: string[] = [];
    page.on("pageerror", (error) => unhandled.push(error.message));
    await page.goto("/chat");
    const input = page.getByPlaceholder("描述你的采集/分析任务", { exact: false });
    await input.fill("第一次虚构请求");
    await input.press("Control+Enter");
    await expect(page.getByTitle("取消任务")).toHaveCount(0);
    await expect(page.getByText(/❌/)).toBeVisible();
    await input.fill("第二次虚构请求");
    await input.press("Control+Enter");
    await expect(page.getByText("第二次请求已完成", { exact: true })).toBeVisible();
    expect(requests).toBe(2);
    expect(unhandled).toEqual([]);
  });
});
