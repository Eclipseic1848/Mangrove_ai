/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import {contentIdFromUrl} from './detect.js';
import type {ParseResult, ParsedContent} from './types.js';

export function parseGeneric(
  payload: Record<string, unknown>,
  platform: string,
): ParseResult {
  const warnings = ['used generic parser'];
  const url = String(payload.url || payload.searchUrl || '');
  const contentId =
    contentIdFromUrl(url) ||
    String(payload.id || payload.postId || '') ||
    `hash_${hashString(JSON.stringify(payload)).slice(0, 12)}`;

  const post = payload.post as Record<string, unknown> | undefined;
  const body =
    String(payload.content || '') ||
    String(payload.text || '') ||
    String(post?.content || '') ||
    String(payload.summary || '');

  const content: ParsedContent = {
    _id: `${platform}:${contentId}`,
    platform,
    content_type: 'unknown',
    content_id: contentId,
    url: url || undefined,
    title: String(payload.title || ''),
    body_text: body,
    extracted_at: String(payload.extractedAt || ''),
    source_label: String(payload.source || platform),
    schema_version: 1,
    parse_status: 'partial',
  };

  return {
    platform,
    content,
    comments: [],
    rawSourceKey: `${platform}:${contentId}`,
    warnings,
  };
}

function hashString(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h).toString(16);
}
