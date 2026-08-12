/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import {contentIdFromUrl} from './detect.js';
import type {ParseResult, ParsedComment, ParsedContent} from './types.js';

export function parseDongchedi(
  payload: Record<string, unknown>,
): ParseResult {
  const warnings: string[] = [];
  const url = String(payload.url || '');
  const post = (payload.post || {}) as Record<string, unknown>;
  const allcomments = Array.isArray(payload.allcomments)
    ? payload.allcomments
    : [];

  let contentId =
    contentIdFromUrl(url) || String(post.groupId || '') || null;

  if (!contentId) {
    const m = url.match(/\/(?:ugc\/)?article\/(\d+)/);
    contentId = m?.[1] || 'unknown';
    warnings.push('content_id inferred from url or fallback');
  }

  const contentType = url.includes('/video/') ? 'video' : 'ugc_article';

  const content: ParsedContent = {
    _id: `dongchedi:${contentId}`,
    platform: 'dongchedi',
    content_type: contentType,
    content_id: contentId,
    url: url || undefined,
    title: String(post.title || ''),
    author: {
      name: String(post.author || ''),
      url: String(post.authorUrl || ''),
    },
    published_at: post.date ? String(post.date) : null,
    published_raw: String(post.timeRaw || post.publishedTo || ''),
    body_text: String(post.content || ''),
    media: {
      images: Array.isArray(post.images) ? post.images.map(String) : [],
      video_local_path: null,
      video_url: null,
    },
    stats: {comment_count: allcomments.length},
    extracted_at: String(payload.extractedAt || ''),
    source_label: String(payload.source || '懂车帝'),
    schema_version: 1,
    parse_status: 'ok',
  };

  const comments: ParsedComment[] = allcomments.map((c: unknown, i: number) => {
    const row = c as Record<string, unknown>;
    const commentId = String(row.commentId || `idx_${i}`);
    return {
      platform: 'dongchedi',
      content_id: contentId!,
      comment_id: commentId,
      author: String(row.author || ''),
      author_url: String(row.authorUrl || ''),
      content: String(row.content || ''),
      time_raw: String(row.timeRaw || ''),
      is_op: Boolean(row.isOP),
      images: Array.isArray(row.images) ? row.images.map(String) : [],
      replies: Array.isArray(row.replies) ? row.replies : [],
    };
  });

  return {
    platform: 'dongchedi',
    content,
    comments,
    rawSourceKey: `dongchedi:${contentId}`,
    warnings,
  };
}
