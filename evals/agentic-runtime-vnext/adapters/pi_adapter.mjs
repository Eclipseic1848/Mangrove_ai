// Pi Agent Core 0.80.10 可抛弃 Adapter。
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { Agent } from "@earendil-works/pi-agent-core";
import { Type } from "typebox";

const argv = process.argv.slice(2);
const valueOf = (name) => {
  const index = argv.indexOf(name);
  if (index < 0 || index + 1 >= argv.length) throw new Error(`缺少参数 ${name}`);
  return argv[index + 1];
};
const caseId = valueOf("--case-id");
const runDir = valueOf("--run-dir");
const promptFile = valueOf("--prompt-file");

const emit = (eventType, summary, payload = {}) => {
  process.stdout.write(
    `${JSON.stringify({ event_type: eventType, summary, payload })}\n`,
  );
};

const callToolHost = (toolName, args) =>
  new Promise((resolve, reject) => {
    const child = spawn(
      process.env.MANGROVE_BAKEOFF_PYTHON,
      [
        process.env.MANGROVE_BAKEOFF_TOOL_HOST,
        "--case-file",
        process.env.MANGROVE_BAKEOFF_CASE_FILE,
        "--case-id",
        caseId,
        "--run-dir",
        runDir,
        "call",
        toolName,
      ],
      { stdio: ["pipe", "pipe", "pipe"], windowsHide: true },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(stderr.trim() || "Tool Bridge 调用失败"));
        return;
      }
      resolve(JSON.parse(stdout));
    });
    child.stdin.end(JSON.stringify(args));
  });

const execute = (toolName) => async (_id, args) => {
  const result = await callToolHost(toolName, args);
  return {
    content: [{ type: "text", text: JSON.stringify(result) }],
    details: result,
  };
};

const tools = [
  {
    name: "observe_sources",
    label: "观察来源",
    description: "观察 GoalContract 允许的来源、可读位置和结构摘要。",
    parameters: Type.Object({}),
    executionMode: "sequential",
    execute: execute("observe_sources"),
  },
  {
    name: "read_source",
    label: "定向读取来源",
    description: "读取一个已观察到的来源位置，并返回 EvidenceRef。",
    parameters: Type.Object({
      source_id: Type.String(),
      locator: Type.String(),
    }),
    executionMode: "sequential",
    execute: execute("read_source"),
  },
  {
    name: "submit_candidate",
    label: "提交候选",
    description: "提交一个候选文件；这不会发布正式交付。",
    parameters: Type.Object({
      output_format: Type.String(),
      filename: Type.String(),
      content: Type.String(),
    }),
    executionMode: "sequential",
    execute: execute("submit_candidate"),
  },
  {
    name: "request_clarification",
    label: "请求用户确认",
    description: "提出一个最小必要问题，并给出 2 至 4 个可执行的真实操作。",
    parameters: Type.Object({
      question: Type.String(),
      options: Type.Array(Type.String()),
    }),
    executionMode: "sequential",
    execute: execute("request_clarification"),
  },
];

const model = {
  id: process.env.MANGROVE_BAKEOFF_MODEL,
  name: process.env.MANGROVE_BAKEOFF_MODEL,
  api: "openai-completions",
  provider: "mangrove-local",
  baseUrl: process.env.MANGROVE_BAKEOFF_BASE_URL,
  reasoning: true,
  input: ["text"],
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  contextWindow: 32768,
  maxTokens: 4096,
  compat: {
    supportsStore: false,
    supportsDeveloperRole: false,
    supportsReasoningEffort: false,
    supportsUsageInStreaming: true,
    maxTokensField: "max_tokens",
    thinkingFormat: "qwen-chat-template",
    supportsStrictMode: false,
  },
};

const promptPayload = JSON.parse(await fs.readFile(promptFile, "utf8"));
const nativeEvents = [];
const agent = new Agent({
  initialState: {
    systemPrompt: promptPayload.system_prompt,
    model,
    thinkingLevel: "medium",
    tools,
    messages: [],
  },
  getApiKey: async () => process.env.MANGROVE_BAKEOFF_API_KEY || "local",
  toolExecution: "sequential",
  beforeToolCall: async ({ toolCall }) => {
    if (!tools.some((tool) => tool.name === toolCall.name)) {
      return { block: true, reason: "工具不在本次 Tool Catalog" };
    }
  },
});

agent.subscribe(async (event) => {
  nativeEvents.push(event);
  if (event.type === "tool_execution_start") {
    emit("tool.started", `调用 ${event.toolName}`, { tool_name: event.toolName });
  } else if (event.type === "tool_execution_end") {
    emit("tool.completed", `${event.toolName} 已完成`, { tool_name: event.toolName });
    if (event.toolName === "submit_candidate") {
      emit("candidate.created", "候选产物已生成", {});
    } else if (event.toolName === "request_clarification") {
      emit("approval.required", "需要用户确认目标", {});
    }
  }
});

emit("run.started", "Pi Agent Core 开始执行", {
  candidate: "pi",
  framework_version: "0.80.10",
});
try {
  await agent.prompt(promptPayload.user_prompt);
  if (agent.state.errorMessage) {
    throw new Error(agent.state.errorMessage);
  }
  const lastMessage = agent.state.messages.at(-1);
  if (lastMessage?.role === "assistant" && lastMessage.stopReason === "error") {
    throw new Error(lastMessage.errorMessage || "Pi Provider 返回错误");
  }
  await fs.mkdir(runDir, { recursive: true });
  await fs.writeFile(
    path.join(runDir, "pi-native-events.json"),
    JSON.stringify(nativeEvents, null, 2),
    "utf8",
  );
  emit("adapter.finished", "Pi Agent Core Agent Loop 已结束");
} catch (error) {
  emit("run.failed", "Pi Agent Core 执行异常", {
    error_type: error?.constructor?.name || "Error",
    error: String(error),
  });
  process.exitCode = 1;
}
