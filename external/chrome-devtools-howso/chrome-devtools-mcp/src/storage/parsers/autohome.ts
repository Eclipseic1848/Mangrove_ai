/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import {contentIdFromUrl} from './detect.js';
import type {ParseResult, ParsedComment, ParsedContent} from './types.js';

function forumContentId(url: string, bbsId: string): string {
  const thread = url.match(/\/bbs\/thread\/[^/]+\/(\d+)/i);
  if (thread?.[1]) {
    return thread[1];
  }
  const info = url.match(/\/info\/(\d+)/i);
  if (info?.[1]) {
    return info[1];
  }
  if (bbsId) {
    return bbsId;
  }
  return contentIdFromUrl(url) || 'unknown';
}

export function parseAutohome(
  payload: Record<string, unknown>,
  pageUrl?: string,
): ParseResult {
  const warnings: string[] = [];
  const url = String(payload.url || pageUrl || '');
  const bbsId = String(payload.bbs_id || payload.info_id || '');
  const contentId = forumContentId(url, bbsId);
  const contentType = url.includes('/bbs/thread/')
    ? 'forum_post'
    : url.includes('chejiahao')
      ? 'chejiahao_info'
      : 'post';

  const commentsRaw = Array.isArray(payload.comments)
    ? payload.comments
    : [];

  const content: ParsedContent = {
    _id: `autohome:${contentId}`,
    platform: 'autohome',
    content_type: contentType,
    content_id: contentId,
    url: url || undefined,
    title: String(payload.title || ''),
    author: {
      name: String(payload.author_name || ''),
    },
    published_at: String(payload.publish_time || '') || null,
    body_text: String(payload.content || ''),
    media: {
      images: Array.isArray(payload.imgList)
        ? payload.imgList.map(String)
        : [],
      video_local_path: null,
      video_url: String(
        (payload.video as Record<string, unknown>)?.bestUrl || '',
      ) || null,
    },
    stats: {
      comment_count: commentsRaw.length,
      bbs_id: Number(bbsId) || 0,
    },
    source_label: String(payload.source || '汽车之家'),
    schema_version: 1,
    parse_status: 'ok',
  };

  const comments: ParsedComment[] = commentsRaw.map((c: unknown, i: number) => {
    const row = c as Record<string, unknown>;
    const text = String(row.content || '');
    const user = String(row.username || row.author || '');
    const time = String(row.time || row.timeRaw || '');
    const commentId = `${contentId}_c_${i}_${hashComment(user, time, text)}`;
    return {
      platform: 'autohome',
      content_id: contentId,
      comment_id: commentId,
      author: user,
      content: text,
      time_raw: time,
      replies: Array.isArray(row.replies) ? row.replies : [],
    };
  });

  return {
    platform: 'autohome',
    content,
    comments,
    rawSourceKey: `autohome:${contentId}`,
    warnings,
  };
}

function hashComment(user: string, time: string, text: string): string {
  let h = 0;
  const s = `${user}|${time}|${text.slice(0, 80)}`;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h).toString(16).slice(0, 10);
}
