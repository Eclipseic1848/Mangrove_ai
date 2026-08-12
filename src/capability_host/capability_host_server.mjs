import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { readFileSync, realpathSync } from "node:fs";
import { createRequire } from "node:module";
import { isAbsolute, join, relative, resolve } from "node:path";

const token = process.env.MANGROVE_CAPABILITY_TOKEN;
if (!token) throw new Error("Capability Host 缺少短期 Token");
const config = JSON.parse(readFileSync("/opt/mangrove-host/capability-host.json", "utf8"));
const capabilities = new Map(config.capabilities.map((item) => [item.manifest.name, item]));
const mcpSessions = new Map();
const MAX_BODY = 1024 * 1024;
const MAX_OUTPUT = 1024 * 1024;

async function withinTimeout(
  promise,
  seconds,
  onCancel = () => undefined,
  signal = undefined,
) {
  let timer;
  let abort;
  try {
    return await Promise.race([
      promise,
      new Promise((_resolve, reject) => {
        timer = setTimeout(() => {
          void Promise.resolve(onCancel())
            .catch(() => undefined)
            .finally(() => reject(new Error("能力调用超时")));
        }, seconds * 1000);
      }),
      new Promise((_resolve, reject) => {
        if (!signal) return;
        abort = () => {
          void Promise.resolve(onCancel())
            .catch(() => undefined)
            .finally(() => reject(new Error("能力调用已取消")));
        };
        if (signal.aborted) abort();
        else signal.addEventListener("abort", abort, { once: true });
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
    if (signal && abort) signal.removeEventListener("abort", abort);
  }
}

function boundedJson(value) {
  const serialized = JSON.stringify(value);
  if (Buffer.byteLength(serialized, "utf8") > MAX_OUTPUT) {
    throw new Error("能力输出超过 1 MiB 上限");
  }
  return serialized;
}

function inside(rootValue, value) {
  const root = realpathSync(rootValue);
  const target = realpathSync(resolve(root, value || "."));
  const rel = relative(root, target);
  if (rel.startsWith("..") || isAbsolute(rel)) throw new Error("能力路径越过冻结目录");
  return target;
}

function safeEnvironment(command) {
  const env = {};
  for (const key of ["LANG", "LC_ALL", "PATH", "TMPDIR"]) {
    if (process.env[key]) env[key] = process.env[key];
  }
  for (const [key, value] of command.environment || []) env[key] = value;
  return env;
}

async function runCommand(capability, extraArguments, selectedCommand, signal = undefined) {
  const command = selectedCommand || capability.manifest.entrypoint;
  const root = realpathSync(capability.root);
  const program = command.program.includes("/") || command.program.includes("\\")
    ? inside(root, command.program)
    : command.program;
  const child = spawn(program, [...(command.arguments || []), ...extraArguments], {
    cwd: inside(root, command.working_directory || "."),
    env: safeEnvironment(command), shell: false, stdio: ["ignore", "pipe", "pipe"],
    detached: true,
  });
  let stdout = Buffer.alloc(0);
  let stderr = Buffer.alloc(0);
  let exceeded = false;
  const collect = (current, chunk) => {
    const next = Buffer.concat([current, chunk]);
    if (next.length > MAX_OUTPUT) { exceeded = true; process.kill(-child.pid, "SIGKILL"); }
    return next.subarray(0, MAX_OUTPUT);
  };
  child.stdout.on("data", (chunk) => { stdout = collect(stdout, chunk); });
  child.stderr.on("data", (chunk) => { stderr = collect(stderr, chunk); });
  const kill = () => {
    try { process.kill(-child.pid, "SIGKILL"); } catch { /* 已退出即满足回收目标。 */ }
  };
  const abort = () => kill();
  if (signal?.aborted) kill();
  else signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(kill, (command.timeout_seconds || 120) * 1000);
  const code = await new Promise((done, reject) => { child.once("close", done); child.once("error", reject); });
  clearTimeout(timer);
  signal?.removeEventListener("abort", abort);
  if (signal?.aborted) throw new Error("能力调用已取消");
  if (exceeded) throw new Error("能力输出超过 1 MiB 上限");
  if (code !== 0) throw new Error(`能力进程退出码 ${code}：${stderr.toString("utf8").slice(0, 500)}`);
  return { stdout: stdout.toString("utf8"), stderr: stderr.toString("utf8") };
}

async function connectMcp(capability) {
  const root = realpathSync(capability.root);
  const requireFromPack = createRequire(join(root, "package.json"));
  let Client;
  let StdioClientTransport;
  let modern = false;
  try {
    ({ Client } = requireFromPack("@modelcontextprotocol/client"));
    ({ StdioClientTransport } = requireFromPack("@modelcontextprotocol/client/stdio"));
    modern = true;
  } catch {
    ({ Client } = requireFromPack("@modelcontextprotocol/sdk/client/index.js"));
    ({ StdioClientTransport } = requireFromPack("@modelcontextprotocol/sdk/client/stdio.js"));
  }
  const command = capability.manifest.entrypoint;
  const transport = new StdioClientTransport({
    command: command.program.includes("/") || command.program.includes("\\")
      ? inside(root, command.program)
      : command.program,
    args: command.arguments || [],
    cwd: inside(root, command.working_directory || "."),
    env: safeEnvironment(command),
    stderr: "pipe",
  });
  const client = new Client(
    { name: "mangrove-capability-host", version: "1.0.0" },
    modern
      ? { versionNegotiation: { mode: "auto", probe: { timeoutMs: 10000, maxRetries: 0 } } }
      : { capabilities: {} },
  );
  await withinTimeout(client.connect(transport), command.timeout_seconds || 120);
  if (!modern && typeof client.ping === "function") await client.ping();
  const listed = await withinTimeout(client.listTools(), command.timeout_seconds || 120);
  const tools = new Set(listed.tools.map((item) => String(item.name)));
  const state = { client, transport, tools };
  mcpSessions.set(capability.manifest.name, state);
  return state;
}

async function closeMcpSession(name) {
  const state = mcpSessions.get(name);
  if (!state) return;
  mcpSessions.delete(name);
  await state.client.close().catch(() => undefined);
  await state.transport.close().catch(() => undefined);
}

async function prepareCapabilities() {
  for (const capability of capabilities.values()) {
    if (capability.manifest.kind === "mcp_local") {
      await connectMcp(capability);
      continue;
    }
    await runCommand(
      capability,
      [],
      capability.manifest.healthcheck || capability.manifest.entrypoint,
    );
  }
}

async function body(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY) throw new Error("请求超过 1 MiB 上限");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

const server = createServer(async (request, response) => {
  response.setHeader("content-type", "application/json; charset=utf-8");
  if (request.headers.authorization !== `Bearer ${token}`) {
    response.writeHead(401); response.end(JSON.stringify({ error: "unauthorized" })); return;
  }
  try {
    if (request.method === "GET" && request.url === "/health") {
      response.end(JSON.stringify({
        status: "ok",
        capabilities: [...capabilities.values()].map((item) => ({
          name: item.manifest.name,
          kind: item.manifest.kind,
          tools: [...(mcpSessions.get(item.manifest.name)?.tools || [])],
        })),
      })); return;
    }
    if (request.method === "POST" && request.url === "/invoke") {
      const input = await body(request);
      const cancellation = new AbortController();
      request.once("aborted", () => cancellation.abort());
      response.once("close", () => {
        if (!response.writableEnded) cancellation.abort();
      });
      const capability = capabilities.get(String(input.capability || ""));
      if (!capability) throw new Error("能力不在冻结清单中");
      if (capability.manifest.kind === "mcp_local") {
        const state = mcpSessions.get(capability.manifest.name);
        if (!state || !state.tools.has(String(input.tool || ""))) {
          throw new Error("MCP 工具不在冻结 schema 中");
        }
        const result = await withinTimeout(
          state.client.callTool({
            name: input.tool,
            arguments: input.arguments || {},
          }),
          capability.manifest.entrypoint.timeout_seconds || 120,
          () => closeMcpSession(capability.manifest.name),
          cancellation.signal,
        );
        response.end(boundedJson({ stdout: JSON.stringify(result), stderr: "" })); return;
      }
      if (!Array.isArray(input.arguments) || input.arguments.some((item) => typeof item !== "string")) {
        throw new Error("arguments 必须是字符串数组");
      }
      response.end(boundedJson(
        await runCommand(capability, input.arguments, undefined, cancellation.signal),
      )); return;
    }
    response.writeHead(404); response.end(JSON.stringify({ error: "not_found" }));
  } catch (error) {
    response.writeHead(400); response.end(JSON.stringify({ error: String(error?.message || error) }));
  }
});
async function shutdown() {
  for (const name of [...mcpSessions.keys()]) await closeMcpSession(name);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 3000).unref();
}
process.on("SIGTERM", () => { void shutdown(); });
process.on("SIGINT", () => { void shutdown(); });

await prepareCapabilities();
server.listen(8765, "0.0.0.0");
