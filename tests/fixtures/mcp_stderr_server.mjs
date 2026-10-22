import { createInterface } from "node:readline";
import { appendFileSync } from "node:fs";

const marker = "MANGROVE_SYNTHETIC_PRIVATE_STDERR";
const chunk = Buffer.alloc(64 * 1024, `${marker}\n`);
process.stdin.once("end", () => process.exit(0));
process.stderr.on("error", () => process.exit(0));

async function noisy() {
  // 等写入真正完成再响应，确保未消费的管道会复现反压。
  for (let index = 0; index < 128; index += 1) {
    await new Promise((resolve, reject) => {
      process.stderr.write(chunk, (error) => error ? reject(error) : resolve());
    });
  }
}

for await (const line of createInterface({ input: process.stdin })) {
  const message = JSON.parse(line);
  if (message.id === undefined) continue;
  let result;
  if (message.method === "initialize") {
    await noisy();
    result = {
      protocolVersion: message.params.protocolVersion,
      capabilities: { tools: {} },
      serverInfo: { name: "synthetic-stderr-fixture", version: "1.0.0" },
    };
  } else if (message.method === "ping") {
    result = {};
  } else if (message.method === "tools/list") {
    result = { tools: [{ name: "fixture", inputSchema: { type: "object" } }] };
  } else if (message.method === "tools/call") {
    await noisy();
    const action = message.params.arguments.action;
    appendFileSync(process.argv[2], `${action}\n`, "utf8");
    if (action === "hang") continue;
    if (action === "crash") process.exit(23);
    result = { content: [{ type: "text", text: "fixture-ok" }] };
  } else {
    process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "Method not found" } })}\n`);
    continue;
  }
  process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: message.id, result })}\n`);
}
