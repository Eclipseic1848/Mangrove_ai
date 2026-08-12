/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

export interface StorageConfig {
  mongoUri: string;
  mongoDb: string;
  rawDataDir: string;
}

export function loadStorageConfig(): StorageConfig {
  return {
    mongoUri:
      process.env.MONGO_URI?.trim() || 'mongodb://192.168.1.30:27017',
    mongoDb: process.env.MONGO_DB?.trim() || 'voc_douyin',
    rawDataDir:
      process.env.VOC_RAW_DATA_DIR?.trim() || 'data/raw',
  };
}
