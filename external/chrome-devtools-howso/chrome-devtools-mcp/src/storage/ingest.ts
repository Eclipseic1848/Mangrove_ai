/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import {createHash} from 'node:crypto';
import {mkdirSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';

import {
  type AnyBulkWriteOperation,
  type Document,
  type UpdateFilter,
} from 'mongodb';

import {loadStorageConfig} from './config.js';
import {
  commentsCollection,
  contentCollection,
  getStorageDb,
  rawCollection,
} from './mongo.js';
import {parseCrawlPayload, type ParseResult} from './parsers/index.js';

export interface IngestOneResult {
  ok: boolean;
  platform: string;
  content_id?: string;
  content_upserted: boolean;
  comments_upserted: number;
  raw_stored: boolean;
  warnings: string[];
  error?: string;
}

export interface IngestBatchResult {
  ok: boolean;
  total: number;
  succeeded: number;
  failed: number;
  results: IngestOneResult[];
}

function todayDir(): string {
  return new Date().toISOString().slice(0, 10);
}

function maybeSaveRawJson(
  platform: string,
  sourceKey: string,
  payload: Record<string, unknown>,
  enabled: boolean,
): string | null {
  if (!enabled) {
    return null;
  }
  const cfg = loadStorageConfig();
  const dir = join(cfg.rawDataDir, todayDir(), platform);
  mkdirSync(dir, {recursive: true});
  const safeKey = sourceKey.replace(/[/:]/g, '_');
  const filePath = join(dir, `${safeKey}.json`);
  writeFileSync(filePath, JSON.stringify(payload, null, 2), 'utf8');
  return filePath;
}

function rawDocId(payload: Record<string, unknown>, sourceKey: string): string {
  const h = createHash('sha256')
    .update(JSON.stringify(payload))
    .digest('hex')
    .slice(0, 32);
  return `raw:${sourceKey}:${h}`;
}

export async function ingestCrawlOne(
  payload: Record<string, unknown>,
  options?: {
    platform?: string;
    saveRawJson?: boolean;
    storeRawInMongo?: boolean;
  },
): Promise<IngestOneResult> {
  const saveRawJson = options?.saveRawJson ?? true;
  const storeRawInMongo = options?.storeRawInMongo ?? true;

  try {
    const parsed: ParseResult = parseCrawlPayload(payload, options?.platform);
    const database = await getStorageDb();
    const now = new Date().toISOString();
    const warnings = [...parsed.warnings];

    let rawStored = false;
    if (storeRawInMongo) {
      const rawCol = rawCollection(database);
      const rawId = rawDocId(payload, parsed.rawSourceKey);
      await rawCol.updateOne(
        {
          platform: parsed.platform,
          source_key: parsed.rawSourceKey,
        } as Document,
        {
          $set: {
            platform: parsed.platform,
            source_key: parsed.rawSourceKey,
            payload,
            ingested_at: now,
          },
          $setOnInsert: {_id: rawId, first_seen_at: now},
        } as UpdateFilter<Document>,
        {upsert: true},
      );
      rawStored = true;
    }

    const jsonPath = maybeSaveRawJson(
      parsed.platform,
      parsed.rawSourceKey,
      payload,
      saveRawJson,
    );
    if (jsonPath) {
      warnings.push(`raw json: ${jsonPath}`);
    }

    let contentUpserted = false;
    let commentsUpserted = 0;

    if (parsed.content) {
      const col = contentCollection(database);
      const isAnalysisPatch =
        parsed.content.content_type === 'analysis_patch';

      const doc: Document = {
        ...parsed.content,
        ingested_at: now,
      };
      if (jsonPath) {
        doc.source_file = jsonPath;
      }

      if (isAnalysisPatch) {
        await col.updateOne(
          {_id: parsed.content._id} as Document,
          {
            $set: {
              analysis: parsed.content.analysis,
              ingested_at: now,
            },
          } as UpdateFilter<Document>,
          {upsert: false},
        );
        const matched = await col.findOne({_id: parsed.content._id} as Document);
        if (!matched) {
          warnings.push(
            'analysis patch: parent not found, upserting minimal doc',
          );
          await col.updateOne(
            {_id: parsed.content._id} as Document,
            {$set: doc} as UpdateFilter<Document>,
            {upsert: true},
          );
        }
      } else {
        await col.updateOne(
          {_id: parsed.content._id} as Document,
          {$set: doc} as UpdateFilter<Document>,
          {upsert: true},
        );
      }
      contentUpserted = true;
    }

    if (parsed.comments.length > 0) {
      const commentCol = commentsCollection(database);
      const ops: AnyBulkWriteOperation<Document>[] = parsed.comments
        .filter(c => c.comment_id)
        .map(c => ({
          updateOne: {
            filter: {
              platform: c.platform,
              comment_id: c.comment_id,
            } as Document,
            update: {
              $set: {...c, ingested_at: now},
            } as UpdateFilter<Document>,
            upsert: true,
          },
        }));

      if (ops.length > 0) {
        const bulk = await commentCol.bulkWrite(ops, {ordered: false});
        commentsUpserted =
          (bulk.upsertedCount || 0) + (bulk.modifiedCount || 0);
      }
    }

    return {
      ok: true,
      platform: parsed.platform,
      content_id: parsed.content?.content_id,
      content_upserted: contentUpserted,
      comments_upserted: commentsUpserted,
      raw_stored: rawStored,
      warnings,
    };
  } catch (err) {
    return {
      ok: false,
      platform: options?.platform || 'unknown',
      content_upserted: false,
      comments_upserted: 0,
      raw_stored: false,
      warnings: [],
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export async function ingestCrawlBatch(
  items: Record<string, unknown>[],
  options?: {
    platform?: string;
    saveRawJson?: boolean;
    storeRawInMongo?: boolean;
  },
): Promise<IngestBatchResult> {
  const results: IngestOneResult[] = [];
  let succeeded = 0;
  let failed = 0;

  for (const item of items) {
    const r = await ingestCrawlOne(item, options);
    results.push(r);
    if (r.ok) {
      succeeded++;
    } else {
      failed++;
    }
  }

  return {
    ok: failed === 0,
    total: items.length,
    succeeded,
    failed,
    results,
  };
}

export function parsePayloadJson(jsonText: string): Record<string, unknown> {
  const trimmed = jsonText.trim();
  const parsed: unknown = JSON.parse(trimmed);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('payloadJson must be a JSON object');
  }
  return parsed as Record<string, unknown>;
}
