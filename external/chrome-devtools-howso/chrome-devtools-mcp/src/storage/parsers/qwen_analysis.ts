/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import type {ParseResult, ParsedContent} from './types.js';

export function parseQwenAnalysis(
  payload: Record<string, unknown>,
): ParseResult {
  const warnings: string[] = [];
  const parentPlatform = String(
    payload.parentPlatform || payload.platform || '',
  ).toLowerCase();
  const parentContentId = String(
    payload.parentContentId || payload.contentId || payload.bvid || '',
  );
  const parentUrl = String(payload.url || payload.parentUrl || '');

  if (!parentPlatform || !parentContentId) {
    warnings.push(
      'qwen_analysis without parentPlatform/parentContentId — standalone doc',
    );
    const fallbackId = `analysis_${Date.now()}`;
    const content: ParsedContent = {
      _id: `qwen_analysis:${fallbackId}`,
      platform: 'qwen_analysis',
      content_type: 'analysis',
      content_id: fallbackId,
      url: parentUrl || undefined,
      analysis: {
        summary: String(payload.summary || ''),
        model: String(payload.model || ''),
        usage: payload.usage,
      },
      schema_version: 1,
      parse_status: 'partial',
    };
    return {
      platform: 'qwen_analysis',
      content,
      comments: [],
      rawSourceKey: `qwen_analysis:${fallbackId}`,
      warnings,
    };
  }

  const content: ParsedContent = {
    _id: `${parentPlatform}:${parentContentId}`,
    platform: parentPlatform,
    content_type: 'analysis_patch',
    content_id: parentContentId,
    url: parentUrl || undefined,
    analysis: {
      summary: String(payload.summary || ''),
      model: String(payload.model || ''),
      usage: payload.usage,
    },
    schema_version: 1,
    parse_status: 'ok',
  };

  return {
    platform: parentPlatform,
    content,
    comments: [],
    rawSourceKey: `${parentPlatform}:${parentContentId}:analysis`,
    warnings,
  };
}
