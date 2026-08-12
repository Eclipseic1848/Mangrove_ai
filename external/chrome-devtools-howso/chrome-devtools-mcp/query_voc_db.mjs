#!/usr/bin/env node
/**
 * 查询 voc_douyin MongoDB
 *
 *   export PATH="$HOME/.nvm/versions/node/v24.13.0/bin:$PATH"
 *   cd chrome-devtools-mcp
 *
 *   node query_voc_db.mjs                          # 最近 10 条主贴
 *   node query_voc_db.mjs --stats                  # 各平台统计
 *   node query_voc_db.mjs --platform autohome --id 115008929
 *   node query_voc_db.mjs --platform autohome --id 115008929 --comments
 *   node query_voc_db.mjs --platform autohome --id 115008929 --raw
 *   node query_voc_db.mjs --json --platform autohome --id 115008929
 *
 * 环境变量：MONGO_URI（默认 mongodb://192.168.1.30:27017）、MONGO_DB（voc_douyin）
 */

import { MongoClient } from "mongodb";

const MONGO_URI =
  process.env.MONGO_URI?.trim() || "mongodb://192.168.1.30:27017";
const MONGO_DB = process.env.MONGO_DB?.trim() || "voc_douyin";

function parseArgs(argv) {
  const opts = {
    stats: false,
    comments: false,
    raw: false,
    json: false,
    limit: 10,
    platform: "",
    id: "",
    url: "",
    title: "",
  };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--stats") opts.stats = true;
    else if (a === "--comments") opts.comments = true;
    else if (a === "--raw") opts.raw = true;
    else if (a === "--json") opts.json = true;
    else if (a === "--help" || a === "-h") opts.help = true;
    else if (a === "--platform" || a === "-p") opts.platform = argv[++i] || "";
    else if (a === "--id") opts.id = argv[++i] || "";
    else if (a === "--url") opts.url = argv[++i] || "";
    else if (a === "--title") opts.title = argv[++i] || "";
    else if (a === "--limit" || a === "-n") {
      opts.limit = Math.max(1, Number.parseInt(argv[++i] || "10", 10) || 10);
    } else {
      console.error(`未知参数: ${a}`);
      opts.help = true;
    }
  }
  return opts;
}

function printHelp() {
  console.log(`用法: node query_voc_db.mjs [选项]

选项:
  (无)              列出最近入库的主贴
  --stats           按 platform 统计 content_items 数量
  -p, --platform    平台，如 autohome、dongchedi、bilibili
  --id              内容 ID（与 platform 组合为 _id，如 autohome:115008929）
  --url             URL 模糊匹配
  --title           标题模糊匹配
  -n, --limit       列表条数，默认 10
  --comments        同时输出该帖评论（需 --platform + --id）
  --raw             输出 raw_documents 中的 payload 摘要（需 --platform + --id）
  --json            完整 JSON 输出（否则为可读摘要）
  -h, --help        帮助

环境:
  MONGO_URI=${MONGO_URI}
  MONGO_DB=${MONGO_DB}
`);
}

function preview(text, max = 200) {
  const s = String(text || "").replace(/\s+/g, " ");
  return s.length <= max ? s : `${s.slice(0, max)}…`;
}

function summarizeContent(doc) {
  return {
    _id: doc._id,
    platform: doc.platform,
    content_id: doc.content_id,
    content_type: doc.content_type,
    title: doc.title,
    author: doc.author?.name || doc.author,
    published_at: doc.published_at,
    url: doc.url,
    body_preview: preview(doc.body_text, 300),
    body_length: (doc.body_text || "").length,
    comment_count_stat: doc.stats?.comment_count,
    parse_status: doc.parse_status,
    ingested_at: doc.ingested_at,
    analysis_summary: doc.analysis?.summary
      ? preview(doc.analysis.summary, 120)
      : undefined,
  };
}

async function main() {
  const opts = parseArgs(process.argv);
  if (opts.help) {
    printHelp();
    process.exit(0);
  }

  const client = new MongoClient(MONGO_URI);
  await client.connect();
  const db = client.db(MONGO_DB);

  try {
    if (opts.stats) {
      const rows = await db
        .collection("content_items")
        .aggregate([
          { $group: { _id: "$platform", count: { $sum: 1 } } },
          { $sort: { count: -1 } },
        ])
        .toArray();
      console.log(`\n=== ${MONGO_DB}.content_items 按平台统计 ===\n`);
      for (const r of rows) {
        console.log(`  ${r._id || "(empty)"}: ${r.count}`);
      }
      const total = await db.collection("content_items").estimatedDocumentCount();
      const comments = await db.collection("comments").estimatedDocumentCount();
      const raw = await db.collection("raw_documents").estimatedDocumentCount();
      console.log(`\n合计: content_items=${total}, comments=${comments}, raw_documents=${raw}`);
      return;
    }

    if (opts.platform && opts.id) {
      const _id = `${opts.platform}:${opts.id}`;
      const doc = await db.collection("content_items").findOne({ _id });
      if (!doc) {
        console.error(`未找到 content_items: ${_id}`);
        process.exit(1);
      }

      if (opts.json) {
        console.log(JSON.stringify(doc, null, 2));
      } else {
        console.log(`\n=== 主贴 ${_id} ===\n`);
        console.log(JSON.stringify(summarizeContent(doc), null, 2));
      }

      if (opts.comments) {
        const comments = await db
          .collection("comments")
          .find({ platform: opts.platform, content_id: opts.id })
          .sort({ ingested_at: -1 })
          .limit(50)
          .toArray();
        console.log(`\n=== 评论 (${comments.length} 条，最多显示 50) ===\n`);
        if (opts.json) {
          console.log(JSON.stringify(comments, null, 2));
        } else {
          for (const [i, c] of comments.entries()) {
            console.log(
              `[${i + 1}] ${c.author || "?"} | ${c.time_raw || ""}\n    ${preview(c.content, 150)}\n`,
            );
          }
        }
      }

      if (opts.raw) {
        const raw = await db.collection("raw_documents").findOne({
          platform: opts.platform,
          source_key: `${opts.platform}:${opts.id}`,
        });
        if (!raw) {
          console.log("\n(raw_documents 中无匹配 source_key)");
        } else if (opts.json) {
          console.log("\n=== raw_documents ===\n");
          console.log(JSON.stringify(raw.payload, null, 2));
        } else {
          const p = raw.payload || {};
          console.log("\n=== raw_documents.payload 字段 ===\n");
          console.log(
            JSON.stringify(
              {
                source_key: raw.source_key,
                ingested_at: raw.ingested_at,
                keys: Object.keys(p),
                title: p.title,
                source: p.source,
                content_length: String(p.content || "").length,
                comments_count: Array.isArray(p.comments)
                  ? p.comments.length
                  : Array.isArray(p.allcomments)
                    ? p.allcomments.length
                    : 0,
              },
              null,
              2,
            ),
          );
        }
      }
      return;
    }

    const filter = {};
    if (opts.platform) filter.platform = opts.platform;
    if (opts.url) filter.url = { $regex: opts.url, $options: "i" };
    if (opts.title) filter.title = { $regex: opts.title, $options: "i" };

    const docs = await db
      .collection("content_items")
      .find(filter)
      .sort({ ingested_at: -1 })
      .limit(opts.limit)
      .toArray();

    console.log(`\n=== ${MONGO_DB}.content_items (最近 ${docs.length} 条) ===\n`);
    if (docs.length === 0) {
      console.log("(无数据)");
      return;
    }

    if (opts.json) {
      console.log(JSON.stringify(docs, null, 2));
      return;
    }

    for (const doc of docs) {
      const s = summarizeContent(doc);
      console.log(
        `${s._id}\n  标题: ${s.title || "(无)"}\n  作者: ${s.author || "-"}\n  入库: ${s.ingested_at}\n  正文: ${s.body_length} 字 | ${s.body_preview}\n`,
      );
    }
    console.log(
      `提示: 查看单条详情  node query_voc_db.mjs -p <platform> --id <content_id> [--comments] [--raw]`,
    );
  } finally {
    await client.close();
  }
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
