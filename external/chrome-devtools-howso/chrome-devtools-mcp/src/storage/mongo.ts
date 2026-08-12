/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import {
  type Collection,
  type Db,
  MongoClient,
  type Document,
} from 'mongodb';

import {loadStorageConfig} from './config.js';

let client: MongoClient | null = null;
let db: Db | null = null;

export async function getStorageDb(): Promise<Db> {
  if (db) {
    return db;
  }
  const cfg = loadStorageConfig();
  client = new MongoClient(cfg.mongoUri);
  await client.connect();
  db = client.db(cfg.mongoDb);
  await ensureStorageIndexes(db);
  return db;
}

async function ensureStorageIndexes(database: Db): Promise<void> {
  const content = database.collection('content_items');
  await content.createIndex(
    {platform: 1, content_id: 1},
    {unique: true, name: 'platform_content_id'},
  );
  await content.createIndex({url: 1}, {name: 'url'});
  await content.createIndex({ingested_at: -1}, {name: 'ingested_at'});

  const comments = database.collection('comments');
  await comments.createIndex(
    {platform: 1, comment_id: 1},
    {unique: true, sparse: true, name: 'platform_comment_id'},
  );
  await comments.createIndex(
    {platform: 1, content_id: 1},
    {name: 'content_comments'},
  );

  const raw = database.collection('raw_documents');
  await raw.createIndex({ingested_at: -1}, {name: 'raw_ingested_at'});
  await raw.createIndex(
    {platform: 1, source_key: 1},
    {unique: true, sparse: true, name: 'raw_source_key'},
  );
}

export function contentCollection(database: Db): Collection<Document> {
  return database.collection('content_items');
}

export function commentsCollection(database: Db): Collection<Document> {
  return database.collection('comments');
}

export function rawCollection(database: Db): Collection<Document> {
  return database.collection('raw_documents');
}
