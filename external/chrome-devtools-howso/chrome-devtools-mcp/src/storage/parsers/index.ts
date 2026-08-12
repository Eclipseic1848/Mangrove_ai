/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import {detectPlatform} from './detect.js';
import {parseAutohome} from './autohome.js';
import {parseBilibili} from './bilibili.js';
import {parseDongchedi} from './dongchedi.js';
import {parseGeneric} from './generic.js';
import {parseQwenAnalysis} from './qwen_analysis.js';
import type {ParseResult} from './types.js';

export function parseCrawlPayload(
  payload: Record<string, unknown>,
  platformOverride?: string,
): ParseResult {
  const platform = detectPlatform(payload, platformOverride);

  switch (platform) {
    case 'dongchedi':
      return parseDongchedi(payload);
    case 'bilibili':
      return parseBilibili(payload);
    case 'qwen_analysis':
      return parseQwenAnalysis(payload);
    case 'autohome':
      return parseAutohome(payload);
    default:
      return parseGeneric(payload, platform);
  }
}

export type {ParseResult} from './types.js';
