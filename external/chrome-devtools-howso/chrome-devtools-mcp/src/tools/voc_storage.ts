/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 *
 * MongoDB 入库工具：在 extract_* / qwen_* 等爬取完成后调用，将 JSON 解析并批量 upsert。
 *
 * 环境变量：MONGO_URI（默认 mongodb://192.168.1.30:27017）、MONGO_DB、VOC_RAW_DATA_DIR
 */

import fs from 'node:fs';

import {zod} from '../third_party/index.js';
import {loadStorageConfig} from '../storage/config.js';
import {
  ingestCrawlBatch,
  ingestCrawlOne,
  parsePayloadJson,
} from '../storage/ingest.js';
import {getStorageDb} from '../storage/mongo.js';

import {ToolCategory} from './categories.js';
import {defineTool} from './ToolDefinition.js';

const storageAnnotations = {
  category: ToolCategory.DEBUGGING,
  readOnlyHint: false,
  requiresBrowser: false,
} as const;

export const vocStoreCrawlResult = defineTool({
  name: 'voc_store_crawl_result',
  description: `Parse a crawl/scrape JSON result and upsert into MongoDB (collections: content_items, comments, raw_documents).
Call after extract_dcd_by_url, extract_autohome_post, search_first_video_download, qwen_video_analyze, etc.
Pass the full tool output JSON as payloadJson.`,
  annotations: storageAnnotations,
  schema: {
    payloadJson: zod
      .string()
      .describe(
        'Full crawl result as JSON string (same as saved .json file content).',
      ),
    platform: zod
      .string()
      .optional()
      .describe(
        'Override platform: dongchedi, bilibili, douyin, autohome, qwen_analysis, etc.',
      ),
    saveRawJson: zod
      .boolean()
      .optional()
      .default(true)
      .describe('Also write to VOC_RAW_DATA_DIR/{date}/{platform}/.'),
    storeRawInMongo: zod
      .boolean()
      .optional()
      .default(true)
      .describe('Store full payload in raw_documents.'),
    parentPlatform: zod
      .string()
      .optional()
      .describe('For Qwen analysis: parent platform (e.g. bilibili).'),
    parentContentId: zod
      .string()
      .optional()
      .describe('For Qwen analysis: parent content id (e.g. BV id).'),
  },
  handler: async (request, response) => {
    const payload = parsePayloadJson(request.params.payloadJson);
    if (request.params.parentPlatform) {
      payload.parentPlatform = request.params.parentPlatform;
    }
    if (request.params.parentContentId) {
      payload.parentContentId = request.params.parentContentId;
    }

    const result = await ingestCrawlOne(payload, {
      platform: request.params.platform,
      saveRawJson: request.params.saveRawJson,
      storeRawInMongo: request.params.storeRawInMongo,
    });
    response.appendResponseLine(JSON.stringify(result, null, 2));
  },
});

export const vocStoreCrawlBatch = defineTool({
  name: 'voc_store_crawl_batch',
  description:
    'Batch ingest multiple crawl JSON objects. itemsJson is a JSON array string.',
  annotations: storageAnnotations,
  schema: {
    itemsJson: zod
      .string()
      .describe('JSON array of crawl result objects.'),
    platform: zod.string().optional(),
    saveRawJson: zod.boolean().optional().default(true),
    storeRawInMongo: zod.boolean().optional().default(true),
  },
  handler: async (request, response) => {
    const parsed: unknown = JSON.parse(request.params.itemsJson.trim());
    if (!Array.isArray(parsed)) {
      throw new Error('itemsJson must be a JSON array');
    }
    const result = await ingestCrawlBatch(
      parsed as Record<string, unknown>[],
      {
        platform: request.params.platform,
        saveRawJson: request.params.saveRawJson,
        storeRawInMongo: request.params.storeRawInMongo,
      },
    );
    response.appendResponseLine(JSON.stringify(result, null, 2));
  },
});

export const vocStoreFromJsonFile = defineTool({
  name: 'voc_store_from_json_file',
  description:
    'Read a local JSON file (object or array) and ingest into MongoDB.',
  annotations: storageAnnotations,
  schema: {
    filePath: zod.string().describe('Path to .json file.'),
    platform: zod.string().optional(),
    saveRawJson: zod.boolean().optional().default(false),
    storeRawInMongo: zod.boolean().optional().default(true),
  },
  handler: async (request, response) => {
    const raw = fs.readFileSync(request.params.filePath, 'utf8');
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      const result = await ingestCrawlBatch(
        parsed as Record<string, unknown>[],
        {
          platform: request.params.platform,
          saveRawJson: request.params.saveRawJson,
          storeRawInMongo: request.params.storeRawInMongo,
        },
      );
      response.appendResponseLine(
        JSON.stringify({filePath: request.params.filePath, ...result}, null, 2),
      );
      return;
    }
    const result = await ingestCrawlOne(parsed as Record<string, unknown>, {
      platform: request.params.platform,
      saveRawJson: request.params.saveRawJson,
      storeRawInMongo: request.params.storeRawInMongo,
    });
    response.appendResponseLine(
      JSON.stringify({filePath: request.params.filePath, ...result}, null, 2),
    );
  },
});

export const vocMongoPing = defineTool({
  name: 'voc_mongo_ping',
  description: 'Check MongoDB connection and list collection stats.',
  annotations: {
    ...storageAnnotations,
    readOnlyHint: true,
  },
  schema: {},
  handler: async (_request, response) => {
    const cfg = loadStorageConfig();
    const database = await getStorageDb();
    const collections = await database.listCollections().toArray();
    const contentCount = await database
      .collection('content_items')
      .estimatedDocumentCount();
    response.appendResponseLine(
      JSON.stringify(
        {
          ok: true,
          mongoUri: cfg.mongoUri.replace(
            /\/\/([^:]+):([^@]+)@/,
            '//$1:***@',
          ),
          database: cfg.mongoDb,
          collections: collections.map(
            (c: {name: string}) => c.name,
          ),
          content_items_count: contentCount,
        },
        null,
        2,
      ),
    );
  },
});
