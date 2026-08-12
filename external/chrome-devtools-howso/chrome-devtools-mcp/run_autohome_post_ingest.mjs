#!/usr/bin/env node
/**
 * 汽车之家：MCP 抓取 → MongoDB 入库
 *
 * 论坛帖：extract_autohome_post
 * 车家号：extract_autohome_chejiahao_info
 *
 *   export PATH="$HOME/.nvm/versions/node/v24.13.0/bin:$PATH"
 *   cd chrome-devtools-mcp && npm run build
 *   HEADLESS=1 node run_autohome_post_ingest.mjs "<url>"
 *
 * 论坛帖在无头模式下常被反爬，有桌面时请用：
 *   HEADLESS=0 SHOW_BROWSER=1 node run_autohome_post_ingest.mjs "<url>"
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

const url = process.argv[2]?.trim() || process.env.AUTOHOME_URL?.trim();
if (!url) {
  console.error(
    "用法: node run_autohome_post_ingest.mjs <汽车之家 URL>",
  );
  process.exit(1);
}

const isChejiahao = /chejiahao\.autohome\.com\.cn\/info\//i.test(url);
const extractTool = isChejiahao
  ? "extract_autohome_chejiahao_info"
  : "extract_autohome_post";

const slug = url.match(/\/(\d+)(?:-\d+)?\.html/i)?.[1] || "post";
const OUT =
  process.env.OUT ||
  join(__dirname, `autohome_post_${slug}_ingest.json`);

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

function normalizePayload(crawlData, pageUrl) {
  if (
    crawlData?.data &&
    typeof crawlData.data === "object" &&
    !Array.isArray(crawlData.data)
  ) {
    return { ...crawlData.data, url: crawlData.data.url || pageUrl };
  }
  return { ...crawlData, url: crawlData.url || pageUrl };
}

function extractIngestResult(text) {
  const start = text.indexOf("{");
  if (start < 0) return null;
  try {
    return JSON.parse(text.slice(start));
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
    { name: "autohome-post-ingest", version: "1.0.0" },
    { capabilities: {} },
  );

  await client.connect(transport);
  const report = { url, extractTool, steps: [] };

  try {
    console.error(`== 1) ${extractTool} ==`);
    const extractArgs = { url };
    if (extractTool === "extract_autohome_post") {
      extractArgs.initialWaitMs =
        Number(process.env.AUTOHOME_WAIT_MS) || 12000;
    }
    const extract = await client.callTool({
      name: extractTool,
      arguments: extractArgs,
    });
    const extractText = toolText(extract);
    if (extract.isError) {
      console.error(extractText);
      report.steps.push({ step: "extract", ok: false, error: extractText });
      writeFileSync(OUT, JSON.stringify(report, null, 2));
      process.exit(1);
    }

    const crawlData = extractJsonFromResponse(extractText);
    if (!crawlData) {
      report.steps.push({
        step: "extract",
        ok: false,
        error: "无法解析抓取 JSON",
        rawPreview: extractText.slice(0, 800),
      });
      writeFileSync(OUT, JSON.stringify(report, null, 2));
      process.exit(1);
    }

    const payloadForStore = normalizePayload(crawlData, url);
    console.error("标题:", payloadForStore.title?.slice(0, 60));
    console.error(
      "正文长度:",
      String(payloadForStore.content || "").length,
      "评论数:",
      (payloadForStore.comments || []).length,
    );
    report.crawl = {
      title: payloadForStore.title,
      author_name: payloadForStore.author_name,
      publish_time: payloadForStore.publish_time,
    };
    report.steps.push({ step: "extract", ok: true });

    console.error("\n== 2) voc_store_crawl_result ==");
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
    report.steps.push({ step: "ingest", ok: ingestResult?.ok === true });

    mkdirSync(dirname(OUT), { recursive: true });
    writeFileSync(
      OUT,
      JSON.stringify(
        { ...report, payloadForStore, ingestResult },
        null,
        2,
      ),
      "utf8",
    );
    console.error("\n报告:", OUT);

    if (!ingestResult?.ok) {
      process.exit(1);
    }
    console.error(
      `\n✓ 完成 platform=${ingestResult.platform} content_id=${ingestResult.content_id} comments=${ingestResult.comments_upserted}`,
    );
  } finally {
    await client.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
