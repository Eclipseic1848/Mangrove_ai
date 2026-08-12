/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

export interface ParsedComment {
  platform: string;
  content_id: string;
  comment_id: string;
  author?: string;
  author_url?: string;
  content: string;
  time_raw?: string;
  is_op?: boolean;
  images?: string[];
  replies?: unknown[];
}

export interface ParsedContent {
  _id: string;
  platform: string;
  content_type: string;
  content_id: string;
  url?: string;
  title?: string;
  author?: {name?: string; url?: string};
  published_at?: string | null;
  published_raw?: string;
  body_text?: string;
  media?: {
    images?: string[];
    video_local_path?: string | null;
    video_url?: string | null;
  };
  tags?: string[];
  stats?: Record<string, number>;
  analysis?: {
    summary?: string | null;
    model?: string | null;
    usage?: unknown;
  };
  extracted_at?: string;
  source_label?: string;
  schema_version: number;
  parse_status: 'ok' | 'partial';
}

export interface ParseResult {
  platform: string;
  content: ParsedContent | null;
  comments: ParsedComment[];
  rawSourceKey: string;
  warnings: string[];
}
