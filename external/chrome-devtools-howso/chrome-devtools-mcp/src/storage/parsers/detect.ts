/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

export function detectPlatform(
  payload: Record<string, unknown>,
  override?: string,
): string {
  if (override?.trim()) {
    return override.trim().toLowerCase();
  }

  const source = String(payload.source || '').toLowerCase();
  if (source.includes('懂车帝') || source.includes('dongchedi')) {
    return 'dongchedi';
  }
  if (source.includes('汽车之家') || source.includes('autohome')) {
    return 'autohome';
  }
  if (source.includes('抖音') || source.includes('douyin')) {
    return 'douyin';
  }
  if (source.includes('哔哩') || source.includes('bilibili')) {
    return 'bilibili';
  }
  if (source.includes('头条') || source.includes('toutiao')) {
    return 'toutiao';
  }
  if (source.includes('澎湃') || source.includes('thepaper')) {
    return 'thepaper';
  }
  if (source.includes('小红书') || source.includes('xiaohongshu')) {
    return 'xiaohongshu';
  }

  const preset = String(payload.contentPreset || '').toLowerCase();
  if (preset === 'bilibili') {
    return 'bilibili';
  }
  if (preset === 'toutiao') {
    return 'toutiao';
  }

  if (payload.bvid) {
    return 'bilibili';
  }
  if (payload.post && payload.allcomments !== undefined) {
    return 'dongchedi';
  }
  if (payload.success === true && payload.summary && !payload.url) {
    return 'qwen_analysis';
  }

  const url = String(payload.url || payload.searchUrl || '');
  if (url.includes('dongchedi.com')) {
    return 'dongchedi';
  }
  if (url.includes('autohome.com.cn')) {
    return 'autohome';
  }
  if (url.includes('douyin.com')) {
    return 'douyin';
  }
  if (url.includes('bilibili.com')) {
    return 'bilibili';
  }
  if (url.includes('toutiao.com')) {
    return 'toutiao';
  }
  if (url.includes('thepaper.cn')) {
    return 'thepaper';
  }
  if (url.includes('xiaohongshu.com')) {
    return 'xiaohongshu';
  }

  return 'unknown';
}

export function contentIdFromUrl(url: string): string | null {
  const patterns: Array<RegExp> = [
    /club\.autohome\.com\.cn\/bbs\/thread\/[^/]+\/(\d+)/i,
    /chejiahao\.autohome\.com\.cn\/info\/(\d+)/i,
    /dongchedi\.com\/(?:ugc\/)?article\/(\d+)/,
    /dongchedi\.com\/video\/(\d+)/,
    /douyin\.com\/video\/(\d+)/,
    /bilibili\.com\/video\/(BV[\w]+)/i,
    /autohome\.com\.cn\/.*\/(\d+)/,
    /thepaper\.cn\/.*\/(\d+)/,
  ];
  for (const re of patterns) {
    const m = url.match(re);
    if (m?.[1]) {
      return m[1];
    }
  }
  return null;
}
