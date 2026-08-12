/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import type {ParseResult, ParsedContent} from './types.js';

export function parseBilibili(
  payload: Record<string, unknown>,
): ParseResult {
  const warnings: string[] = [];
  const bvid = String(payload.bvid || '');
  const contentId = bvid || String(payload.cid || 'unknown');

  if (!bvid) {
    warnings.push('missing bvid, using cid or unknown');
  }

  const parts = payload.parts as Array<{durationSec?: number}> | undefined;

  const content: ParsedContent = {
    _id: `bilibili:${contentId}`,
    platform: 'bilibili',
    content_type: 'video',
    content_id: contentId,
    url: String(payload.searchUrl || payload.url || ''),
    title: String(payload.title || payload.description || ''),
    body_text: String(payload.description || ''),
    media: {
      images: [],
      video_local_path: payload.videoLocalPath
        ? String(payload.videoLocalPath)
        : null,
      video_url: null,
    },
    tags: Array.isArray(payload.tags) ? payload.tags.map(String) : [],
    stats: {
      file_size: Number(payload.fileSize || 0),
      duration_sec: Number(parts?.[0]?.durationSec || 0),
    },
    source_label: 'bilibili',
    schema_version: 1,
    parse_status: payload.success === false ? 'partial' : 'ok',
  };

  return {
    platform: 'bilibili',
    content,
    comments: [],
    rawSourceKey: `bilibili:${contentId}`,
    warnings,
  };
}
