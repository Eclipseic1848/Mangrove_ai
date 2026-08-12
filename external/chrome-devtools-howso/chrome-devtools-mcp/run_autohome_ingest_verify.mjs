#!/usr/bin/env node
/**
 * 验证：汽车之家车家号抓取 → MongoDB 入库
 *
 *   cd chrome-devtools-mcp && npm run build
 *   HEADLESS=1 node run_autohome_ingest_verify.mjs
 *   HEADLESS=1 node run_autohome_ingest_verify.mjs "https://chejiahao.autohome.com.cn/info/25061145"
 */

import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

import { parseMcpExtraArgs } from "./scripts/mcp_stdio_defaults.mjs";
import { envForMcpChildProcess } from "./scripts/mcp_stdio_full_env.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const MCP_SERVER =
  process.env.MCP_SERVER || join(__dirname, "build/src/index.js");
const OUT = process.env.OUT || join(__dirname, "autohome_ingest_verify.json");

const url =
  process.argv[2]?.trim() ||
  process.env.AUTOHOME_URL?.trim() ||
  "https://chejiahao.autohome.com.cn/info/25061145";

function toolText(result) {
  const parts = [];
  for (const block of result.content ?? []) {
    if (block.type === "text" && typeof block.text === "string") {
      parts.push(block.text);
    }
  }
  return parts.join("\n");
}

function extractJsonFromResponse(text) {
  const fence = /```(?:json)?\s*([\s\S]*?)```/.exec(text);
  if (fence) {
    try {
      return JSON.parse(fence[1].trim());
    } catch {
      /* fall through */
    }
  }
  const lines = text.split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (line.startsWith("{")) {
      try {
        return JSON.parse(lines.slice(i).join("\n").trim());
      } catch {
        /* continue */
      }
    }
  }
  const start = text.indexOf("{");
  if (start >= 0) {
    try {
      return JSON.parse(text.slice(start));
    } catch {
      return null;
    }
  }
  return null;
}

function extractIngestResult(text) {
  const m = /# voc_store_crawl_result response\s*([\s\S]*)/.exec(text);
  const body = m ? m[1].trim() : text;
  const start = body.indexOf("{");
  if (start < 0) return null;
  try {
    return JSON.parse(body.slice(start));
  } catch {
    return null;
  }
}

async function main() {
  const transport = new StdioClientTransport({
    command: "node",
    args: [MCP_SERVER, ...parseMcpExtraArgs()],
    stderr: "inherit",
    env: {
      ...envForMcpChildProcess(),
      MONGO_URI: process.env.MONGO_URI || "mongodb://192.168.1.30:27017",
      MONGO_DB: process.env.MONGO_DB || "voc_douyin",
    },
  });

  const client = new Client(
    { name: "autohome-ingest-verify", version: "1.0.0" },
    { capabilities: {} },
  );

  await client.connect(transport);
  const report = { url, steps: [] };

  try {
    console.error("== 1) voc_mongo_ping ==");
    const ping = await client.callTool({
      name: "voc_mongo_ping",
      arguments: {},
    });
    const pingText = toolText(ping);
    console.error(pingText);
    report.steps.push({ step: "mongo_ping", ok: !ping.isError, text: pingText });

    console.error("\n== 2) extract_autohome_chejiahao_info ==");
    const extract = await client.callTool({
      name: "extract_autohome_chejiahao_info",
      arguments: { url },
    });
    const extractText = toolText(extract);
    if (extract.isError) {
      console.error(extractText);
      report.steps.push({ step: "extract", ok: false, error: extractText });
      writeFileSync(OUT, JSON.stringify(report, null, 2));
      process.exit(1);
    }

    let crawlData = extractJsonFromResponse(extractText);
    if (!crawlData) {
      report.steps.push({
        step: "extract",
        ok: false,
        error: "无法从 MCP 响应解析 JSON",
        rawPreview: extractText.slice(0, 500),
      });
      writeFileSync(OUT, JSON.stringify(report, null, 2));
      process.exit(1);
    }

    // 车家号工具返回 { success, data, savedFile }，入库用内层 data
    const payloadForStore =
      crawlData.data &&
      typeof crawlData.data === "object" &&
      !Array.isArray(crawlData.data)
        ? { ...crawlData.data, url: crawlData.data.url || url }
        : { ...crawlData, url: crawlData.url || url };

    console.error("抓取字段:", Object.keys(payloadForStore).join(", "));
    report.crawl = {
      source: payloadForStore.source,
      title: payloadForStore.title?.slice?.(0, 80),
      url: payloadForStore.url,
    };
    report.steps.push({ step: "extract", ok: true });

    console.error("\n== 3) voc_store_crawl_result ==");
    const store = await client.callTool({
      name: "voc_store_crawl_result",
      arguments: {
        payloadJson: JSON.stringify(payloadForStore),
        platform: "autohome",
        saveRawJson: true,
      },
    });
    const storeText = toolText(store);
    console.error(storeText);

    const ingestResult = extractIngestResult(storeText);
    report.ingest = ingestResult;
    report.steps.push({
      step: "ingest",
      ok: ingestResult?.ok === true,
    });

    mkdirSync(dirname(OUT), { recursive: true });
    writeFileSync(
      OUT,
      JSON.stringify(
        { ...report, crawlData, payloadForStore, ingestResult },
        null,
        2,
      ),
      "utf8",
    );
    console.error("\n报告已写入:", OUT);

    if (!ingestResult?.ok) {
      process.exit(1);
    }
    console.error("\n✓ 验证通过：汽车之家抓取 → MongoDB 入库");
  } finally {
    await client.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
