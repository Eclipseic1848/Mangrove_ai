/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import {zod} from '../third_party/index.js';
import type {Frame, JSHandle, Page} from '../third_party/index.js';

import {ToolCategory} from './categories.js';
import {defineTool} from './ToolDefinition.js';

export const evaluateScript = defineTool({
  name: 'evaluate_script',
  description: `Evaluate a JavaScript function inside the currently selected page. Returns the response as JSON
so returned values have to JSON-serializable.`,
  annotations: {
    category: ToolCategory.DEBUGGING,
    readOnlyHint: false,
  },
  schema: {
    function: zod.string().describe(
      `A JavaScript function declaration to be executed by the tool in the currently selected page.
Example without arguments: \`() => {
  return document.title
}\` or \`async () => {
  return await fetch("example.com")
}\`.
Example with arguments: \`(el) => {
  return el.innerText;
}\`
`,
    ),
    args: zod
      .array(
        zod.object({
          uid: zod
            .string()
            .describe(
              'The uid of an element on the page from the page content snapshot',
            ),
        }),
      )
      .optional()
      .describe(`An optional list of arguments to pass to the function.`),
  },
  handler: async (request, response, context) => {
    const args: Array<JSHandle<unknown>> = [];
    try {
      const frames = new Set<Frame>();
      for (const el of request.params.args ?? []) {
        const handle = await context.getElementByUid(el.uid);
        frames.add(handle.frame);
        args.push(handle);
      }
      let pageOrFrame: Page | Frame;
      // We can't evaluate the element handle across frames
      if (frames.size > 1) {
        throw new Error(
          "Elements from different frames can't be evaluated together.",
        );
      } else {
        pageOrFrame = [...frames.values()][0] ?? context.getSelectedPage();
      }
      const fn = await pageOrFrame.evaluateHandle(
        `(${request.params.function})`,
      );
      args.unshift(fn);
      await context.waitForEventsAfterAction(async () => {
        const result = await pageOrFrame.evaluate(
          async (fn, ...args) => {
            // @ts-expect-error no types.
            return JSON.stringify(await fn(...args));
          },
          ...args,
        );
        response.appendResponseLine('Script ran on page and returned:');
        response.appendResponseLine('```json');
        response.appendResponseLine(`${result}`);
        response.appendResponseLine('```');
      });
    } finally {
      void Promise.allSettled(args.map(arg => arg.dispose()));
    }
  },
});

/**
 * Fetch Douyin video links with a single tool call.
 *
 * This tool optionally navigates to a URL, injects the network hook, waits for page/video load,
 * tries to autoplay videos to trigger requests, extracts URLs from <video> elements first,
 * and falls back to network-hooked requests when needed.
 */
export const fetchDouyinVideoLinks = defineTool({
  name: 'fetch_douyin_video_links',
  description: `Fetch Douyin video links in one call. Navigate to a URL (required), inject network hook, wait, autoplay, then extract links (fallback to network).`,
  annotations: {
    category: ToolCategory.DEBUGGING,
    readOnlyHint: false,
  },
  schema: {
    url: zod
      .string()
      .describe(
        'Required Douyin page URL to navigate to before extracting links.',
      ),
    initialWaitMs: zod
      .number()
      .int()
      .optional()
      .default(8000)
      .describe('Initial wait after navigation/injection to allow page to load. Default 8000ms.'),
    playWaitMs: zod
      .number()
      .int()
      .optional()
      .default(5000)
      .describe('Wait after autoplay to allow requests/URLs to appear. Default 5000ms.'),
    networkLimit: zod
      .number()
      .int()
      .optional()
      .default(20)
      .describe('How many recent network-hook requests to inspect for fallback. Default 20.'),
    includeAllVideos: zod
      .boolean()
      .optional()
      .default(false)
      .describe(
        'If true, include URLs from all <video> elements (not only currently playing). Default false (only currently playing).',
      ),
  },
  handler: async (request, response, context) => {
    const page = context.getSelectedPage();

    await context.waitForEventsAfterAction(async () => {
      const startedAt = Date.now();
      const steps: Array<{name: string; ok: boolean; detail?: any}> = [];

      // 0) Process URL if it contains "search" - convert to standard video URL
      let processedUrl = request.params.url;
      if (processedUrl && processedUrl.includes('search')) {
        try {
          const urlObj = new URL(processedUrl);
          const modalId = urlObj.searchParams.get('modal_id');
          if (modalId) {
            processedUrl = `https://www.douyin.com/video/${modalId}`;
            steps.push({
              name: 'url_conversion',
              ok: true,
              detail: {
                original: request.params.url,
                converted: processedUrl,
                modalId: modalId,
              },
            });
          } else {
            steps.push({
              name: 'url_conversion',
              ok: false,
              detail: {
                original: request.params.url,
                error: 'modal_id parameter not found in URL',
              },
            });
          }
        } catch (err) {
          steps.push({
            name: 'url_conversion',
            ok: false,
            detail: {
              original: request.params.url,
              error: String(err),
            },
          });
        }
      }

      // 1) Required navigation
      if (!processedUrl) {
        throw new Error('URL parameter is required');
      }
      
      try {
        await page.goto(processedUrl, {waitUntil: 'domcontentloaded'});
        steps.push({name: 'navigate', ok: true, detail: {url: processedUrl}});
      } catch (err) {
        steps.push({
          name: 'navigate',
          ok: false,
          detail: {url: processedUrl, error: String(err)},
        });
        throw err;
      }

      // 2) Inject hook (idempotent by design)
      try {
        await page.evaluate(() => {
          (function () {
            if ((window as any).__douyin_hooked__) return;
            (window as any).__douyin_hooked__ = true;
            (window as any).__douyin_videos__ = [];
            (window as any).__douyin_requests__ = [];

            function record(url: string, headers: any, method: string) {
              if (url && (url.includes('douyinvod.com') || url.includes('v3'))) {
                const requestInfo = {
                  url: url,
                  method: method || 'GET',
                  headers: headers || {},
                  time: Date.now(),
                  referer: document.referrer || window.location.href,
                };
                (window as any).__douyin_requests__.push(requestInfo);
                if (url.includes('v3')) {
                  (window as any).__douyin_videos__.push({url: url, time: Date.now()});
                }
              }
            }

            const _fetch = window.fetch;
            window.fetch = function (input: RequestInfo | URL, init?: RequestInit) {
              const url = (input as any)?.url || input;
              const options = init || {};
              const headers = options.headers || {};
              record(
                typeof url === 'string' ? url : url.toString(),
                headers as any,
                options.method || 'GET',
              );
              return _fetch.call(this, input, init);
            };

            const _open = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function (method: string, url: string) {
              (this as any)._method = method;
              (this as any)._url = url;
              (this as any)._headers = {};
              return _open.apply(this, arguments as any);
            };

            const _setRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
            XMLHttpRequest.prototype.setRequestHeader = function (name: string, value: string) {
              if (!(this as any)._headers) (this as any)._headers = {};
              (this as any)._headers[name] = value;
              return _setRequestHeader.apply(this, arguments as any);
            };

            const _send = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function (body?: Document | XMLHttpRequestBodyInit | null) {
              if ((this as any)._url) {
                record(
                  (this as any)._url,
                  (this as any)._headers || {},
                  (this as any)._method || 'GET',
                );
              }
              return _send.call(this, body);
            };
          })();
        });
        steps.push({name: 'inject_network_hook', ok: true});
      } catch (err) {
        steps.push({name: 'inject_network_hook', ok: false, detail: {error: String(err)}});
      }

      // 3) Initial wait
      if (request.params.initialWaitMs > 0) {
        await new Promise(resolve => setTimeout(resolve, request.params.initialWaitMs));
        steps.push({name: 'initial_wait', ok: true, detail: {ms: request.params.initialWaitMs}});
      }

      // 4) Autoplay (best-effort)
      try {
        const playResult = await page.evaluate(async () => {
          const videos = document.querySelectorAll('video');
          let attempted = 0;
          videos.forEach(video => {
            try {
              if (video.paused) {
                void video.play().catch(() => {});
                attempted++;
              }
            } catch {}
          });
          return {attempted, total: videos.length};
        });
        steps.push({name: 'autoplay', ok: true, detail: playResult});
      } catch (err) {
        steps.push({name: 'autoplay', ok: false, detail: {error: String(err)}});
      }

      // 5) Wait after autoplay
      if (request.params.playWaitMs > 0) {
        await new Promise(resolve => setTimeout(resolve, request.params.playWaitMs));
        steps.push({name: 'play_wait', ok: true, detail: {ms: request.params.playWaitMs}});
      }

      // 6) Extract from DOM first
      const domData = await page.evaluate((includeAllVideos: boolean) => {
        const videos = document.querySelectorAll('video');
        const fromPlaying: Array<{url: string; currentTime: number; inViewport: boolean}> = [];
        const fromAll: string[] = [];

        videos.forEach(video => {
          const sources = Array.from(video.querySelectorAll('source'));
          let videoUrl: string | null = null;

          if (video.currentSrc && video.currentSrc.includes('douyinvod.com')) {
            videoUrl = video.currentSrc;
          } else if (video.src && video.src.includes('douyinvod.com')) {
            videoUrl = video.src;
          } else {
            for (const source of sources) {
              const src = source.getAttribute('src') || (source as any).src;
              if (src && src.includes('douyinvod.com')) {
                videoUrl = String(src).replace(/&amp;/g, '&');
                break;
              }
            }
          }

          if (!videoUrl) return;
          
          // 检查是否正在播放
          const isPlaying = !video.paused && video.readyState >= 2;
          
          // 检查是否在视口中（更可能是当前视频）
          const rect = video.getBoundingClientRect();
          const inViewport = rect.top >= 0 && rect.left >= 0 && 
                            rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) && 
                            rect.right <= (window.innerWidth || document.documentElement.clientWidth);
          
          if (isPlaying) {
            fromPlaying.push({
              url: videoUrl,
              currentTime: video.currentTime,
              inViewport: inViewport,
            });
          }
          if (includeAllVideos) fromAll.push(videoUrl);
        });

        return {
          totalVideos: videos.length,
          fromPlaying,
          fromAll,
        };
      }, request.params.includeAllVideos);
      steps.push({name: 'extract_from_dom', ok: true, detail: domData});

      // Prefer playing, then all
      const urls: string[] = [];
      const pushUnique = (u: string) => {
        if (!u) return;
        if (!u.startsWith('http')) return;
        if (!u.includes('douyinvod.com')) return;
        if (!urls.includes(u)) urls.push(u);
      };
      
      // 优先选择正在播放且在视口中的视频，如果都没有则选择第一个正在播放的
      if (domData.fromPlaying.length > 0) {
        // 优先选择在视口中的视频
        const inViewportVideos = domData.fromPlaying.filter(v => v.inViewport);
        if (inViewportVideos.length > 0) {
          // 如果 includeAllVideos 为 false，只返回第一个
          const videosToAdd = request.params.includeAllVideos 
            ? inViewportVideos 
            : [inViewportVideos[0]];
          videosToAdd.forEach(v => pushUnique(v.url));
        } else {
          // 如果没有在视口中的，选择第一个正在播放的
          const videosToAdd = request.params.includeAllVideos 
            ? domData.fromPlaying 
            : [domData.fromPlaying[0]];
          videosToAdd.forEach(v => pushUnique(v.url));
        }
      }
      
      // 只有在 includeAllVideos 为 true 时才添加所有视频
      if (request.params.includeAllVideos) {
        domData.fromAll.forEach(pushUnique);
      }

      // 7) Fallback to network hook if still empty
      let networkData: {requests: any[]; count: number} | null = null;
      if (urls.length === 0) {
        networkData = await page.evaluate((limit: number) => {
          const requests = (window as any).__douyin_requests__ || [];
          const recentVideoRequests = requests
            .filter((req: any) => req.url && req.url.includes('douyinvod.com'))
            .slice(-limit);
          return {requests: recentVideoRequests, count: recentVideoRequests.length};
        }, request.params.networkLimit);
        steps.push({name: 'fallback_network', ok: true, detail: {count: networkData.count}});
        
        // 只取最新的一个请求（最可能是当前视频）
        if (networkData.requests.length > 0) {
          const latestRequest = networkData.requests[networkData.requests.length - 1];
          if (latestRequest?.url) {
            pushUnique(String(latestRequest.url));
          }
        }
      }

      const result = {
        url: processedUrl || request.params.url,
        elapsedMs: Date.now() - startedAt,
        totalVideos: domData.totalVideos,
        links: urls,
        count: urls.length,
        steps,
        network: networkData,
      };

      response.appendResponseLine('Douyin video links fetched:');
      response.appendResponseLine('```json');
      response.appendResponseLine(JSON.stringify(result, null, 2));
      response.appendResponseLine('```');
    });
  },
});

/**
 * Fetch Douyin video links and download the first one in a single tool call.
 *
 * This tool reuses the same logic as fetch_douyin_video_links to:
 * - Navigate to the Douyin page
 * - Inject a network hook
 * - Wait for the page/video to load
 * - Autoplay videos
 * - Extract video URLs from the DOM (with network fallback)
 *
 * Then it **immediately downloads** the first found video URL to a file.
 */
export const fetchAndDownloadDouyinVideo = defineTool({
  name: 'fetch_and_download_douyin_video',
  description:
    'Fetch Douyin video links from a page and immediately download the first video to a local file.',
  annotations: {
    category: ToolCategory.DEBUGGING,
    readOnlyHint: false,
  },
  schema: {
    url: zod
      .string()
      .describe('Required Douyin page URL to navigate to before extracting links.'),
    initialWaitMs: zod
      .number()
      .int()
      .optional()
      .default(8000)
      .describe('Initial wait after navigation/injection to allow page to load. Default 8000ms.'),
    playWaitMs: zod
      .number()
      .int()
      .optional()
      .default(5000)
      .describe('Wait after autoplay to allow requests/URLs to appear. Default 5000ms.'),
    networkLimit: zod
      .number()
      .int()
      .optional()
      .default(20)
      .describe('How many recent network-hook requests to inspect for fallback. Default 20.'),
    includeAllVideos: zod
      .boolean()
      .optional()
      .default(false)
      .describe(
        'If true, include URLs from all <video> elements (not only currently playing). Default false (only currently playing).',
      ),
    filePath: zod
      .string()
      .optional()
      .describe(
        'Optional file path to save the downloaded video. If omitted, a filename is generated from the video ID or timestamp.',
      ),
    referer: zod
      .string()
      .optional()
      .default('https://www.douyin.com')
      .describe('Referer header value for the download request. Default is https://www.douyin.com.'),
  },
  handler: async (request, response, context) => {
    const page = context.getSelectedPage();

    await context.waitForEventsAfterAction(async () => {
      const startedAt = Date.now();
      const steps: Array<{name: string; ok: boolean; detail?: any}> = [];

      // 0) Process URL if it contains "search" - convert to standard video URL
      let processedUrl = request.params.url;
      if (processedUrl && processedUrl.includes('search')) {
        try {
          const urlObj = new URL(processedUrl);
          const modalId = urlObj.searchParams.get('modal_id');
          if (modalId) {
            processedUrl = `https://www.douyin.com/video/${modalId}`;
            steps.push({
              name: 'url_conversion',
              ok: true,
              detail: {
                original: request.params.url,
                converted: processedUrl,
                modalId: modalId,
              },
            });
          } else {
            steps.push({
              name: 'url_conversion',
              ok: false,
              detail: {
                original: request.params.url,
                error: 'modal_id parameter not found in URL',
              },
            });
          }
        } catch (err) {
          steps.push({
            name: 'url_conversion',
            ok: false,
            detail: {
              original: request.params.url,
              error: String(err),
            },
          });
        }
      }

      // 1) Required navigation
      if (!processedUrl) {
        throw new Error('URL parameter is required');
      }

      try {
        await page.goto(processedUrl, {waitUntil: 'domcontentloaded'});
        steps.push({name: 'navigate', ok: true, detail: {url: processedUrl}});
      } catch (err) {
        steps.push({
          name: 'navigate',
          ok: false,
          detail: {url: processedUrl, error: String(err)},
        });
        throw err;
      }

      // 2) Inject hook (idempotent by design)
      try {
        await page.evaluate(() => {
          (function () {
            if ((window as any).__douyin_hooked__) return;
            (window as any).__douyin_hooked__ = true;
            (window as any).__douyin_videos__ = [];
            (window as any).__douyin_requests__ = [];

            function record(url: string, headers: any, method: string) {
              if (url && (url.includes('douyinvod.com') || url.includes('v3'))) {
                const requestInfo = {
                  url: url,
                  method: method || 'GET',
                  headers: headers || {},
                  time: Date.now(),
                  referer: document.referrer || window.location.href,
                };
                (window as any).__douyin_requests__.push(requestInfo);
                if (url.includes('v3')) {
                  (window as any).__douyin_videos__.push({url: url, time: Date.now()});
                }
              }
            }

            const _fetch = window.fetch;
            window.fetch = function (input: RequestInfo | URL, init?: RequestInit) {
              const url = (input as any)?.url || input;
              const options = init || {};
              const headers = options.headers || {};
              record(
                typeof url === 'string' ? url : url.toString(),
                headers as any,
                options.method || 'GET',
              );
              return _fetch.call(this, input, init);
            };

            const _open = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function (method: string, url: string) {
              (this as any)._method = method;
              (this as any)._url = url;
              (this as any)._headers = {};
              return _open.apply(this, arguments as any);
            };

            const _setRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
            XMLHttpRequest.prototype.setRequestHeader = function (name: string, value: string) {
              if (!(this as any)._headers) (this as any)._headers = {};
              (this as any)._headers[name] = value;
              return _setRequestHeader.apply(this, arguments as any);
            };

            const _send = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function (body?: Document | XMLHttpRequestBodyInit | null) {
              if ((this as any)._url) {
                record(
                  (this as any)._url,
                  (this as any)._headers || {},
                  (this as any)._method || 'GET',
                );
              }
              return _send.call(this, body);
            };
          })();
        });
        steps.push({name: 'inject_network_hook', ok: true});
      } catch (err) {
        steps.push({name: 'inject_network_hook', ok: false, detail: {error: String(err)}});
      }

      // 3) Initial wait
      if (request.params.initialWaitMs > 0) {
        await new Promise(resolve => setTimeout(resolve, request.params.initialWaitMs));
        steps.push({name: 'initial_wait', ok: true, detail: {ms: request.params.initialWaitMs}});
      }

      // 4) Autoplay (best-effort)
      try {
        const playResult = await page.evaluate(async () => {
          const videos = document.querySelectorAll('video');
          let attempted = 0;
          videos.forEach(video => {
            try {
              if (video.paused) {
                void video.play().catch(() => {});
                attempted++;
              }
            } catch {}
          });
          return {attempted, total: videos.length};
        });
        steps.push({name: 'autoplay', ok: true, detail: playResult});
      } catch (err) {
        steps.push({name: 'autoplay', ok: false, detail: {error: String(err)}});
      }

      // 5) Wait after autoplay
      if (request.params.playWaitMs > 0) {
        await new Promise(resolve => setTimeout(resolve, request.params.playWaitMs));
        steps.push({name: 'play_wait', ok: true, detail: {ms: request.params.playWaitMs}});
      }

      // 6) Extract from DOM first
      const domData = await page.evaluate((includeAllVideos: boolean) => {
        const videos = document.querySelectorAll('video');
        const fromPlaying: Array<{url: string; currentTime: number; inViewport: boolean}> = [];
        const fromAll: string[] = [];

        videos.forEach(video => {
          const sources = Array.from(video.querySelectorAll('source'));
          let videoUrl: string | null = null;

          if (video.currentSrc && video.currentSrc.includes('douyinvod.com')) {
            videoUrl = video.currentSrc;
          } else if (video.src && video.src.includes('douyinvod.com')) {
            videoUrl = video.src;
          } else {
            for (const source of sources) {
              const src = source.getAttribute('src') || (source as any).src;
              if (src && src.includes('douyinvod.com')) {
                videoUrl = String(src).replace(/&amp;/g, '&');
                break;
              }
            }
          }

          if (!videoUrl) return;

          // 检查是否正在播放
          const isPlaying = !video.paused && video.readyState >= 2;

          // 检查是否在视口中（更可能是当前视频）
          const rect = video.getBoundingClientRect();
          const inViewport =
            rect.top >= 0 &&
            rect.left >= 0 &&
            rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
            rect.right <= (window.innerWidth || document.documentElement.clientWidth);

          if (isPlaying) {
            fromPlaying.push({
              url: videoUrl,
              currentTime: video.currentTime,
              inViewport: inViewport,
            });
          }
          if (includeAllVideos) fromAll.push(videoUrl);
        });

        return {
          totalVideos: videos.length,
          fromPlaying,
          fromAll,
        };
      }, request.params.includeAllVideos);
      steps.push({name: 'extract_from_dom', ok: true, detail: domData});

      // Prefer playing, then all
      const urls: string[] = [];
      const pushUnique = (u: string) => {
        if (!u) return;
        if (!u.startsWith('http')) return;
        if (!u.includes('douyinvod.com')) return;
        if (!urls.includes(u)) urls.push(u);
      };

      // 优先选择正在播放且在视口中的视频，如果都没有则选择第一个正在播放的
      if (domData.fromPlaying.length > 0) {
        // 优先选择在视口中的视频
        const inViewportVideos = domData.fromPlaying.filter(v => v.inViewport);
        if (inViewportVideos.length > 0) {
          const videosToAdd = request.params.includeAllVideos ? inViewportVideos : [inViewportVideos[0]];
          videosToAdd.forEach(v => pushUnique(v.url));
        } else {
          const videosToAdd = request.params.includeAllVideos ? domData.fromPlaying : [domData.fromPlaying[0]];
          videosToAdd.forEach(v => pushUnique(v.url));
        }
      }

      // 只有在 includeAllVideos 为 true 时才添加所有视频
      if (request.params.includeAllVideos) {
        domData.fromAll.forEach(pushUnique);
      }

      // 7) Fallback to network hook if still empty
      let networkData: {requests: any[]; count: number} | null = null;
      if (urls.length === 0) {
        networkData = await page.evaluate((limit: number) => {
          const requests = (window as any).__douyin_requests__ || [];
          const recentVideoRequests = requests
            .filter((req: any) => req.url && req.url.includes('douyinvod.com'))
            .slice(-limit);
          return {requests: recentVideoRequests, count: recentVideoRequests.length};
        }, request.params.networkLimit);
        steps.push({name: 'fallback_network', ok: true, detail: {count: networkData.count}});

        // 只取最新的一个请求（最可能是当前视频）
        if (networkData.requests.length > 0) {
          const latestRequest = networkData.requests[networkData.requests.length - 1];
          if (latestRequest?.url) {
            pushUnique(String(latestRequest.url));
          }
        }
      }

      // 如果仍然没有找到链接，直接返回
      if (urls.length === 0) {
        const resultNoLink = {
          url: processedUrl || request.params.url,
          elapsedMs: Date.now() - startedAt,
          totalVideos: domData.totalVideos,
          link: null,
          download: null,
          steps,
          network: networkData,
        };

        response.appendResponseLine('Douyin video link not found:');
        response.appendResponseLine('```json');
        response.appendResponseLine(JSON.stringify(resultNoLink, null, 2));
        response.appendResponseLine('```');
        return;
      }

      // 8) Download the first link using the same logic as download_douyin_video
      const videoUrl = urls[0];
      const referer = request.params.referer || 'https://www.douyin.com';
      response.appendResponseLine(
        `Downloading first Douyin video link: ${videoUrl.substring(0, 80)}...`,
      );

      let downloadResult: {
        success: boolean;
        filename?: string;
        fileSize?: number;
        fileSizeMB?: string;
        url?: string;
      } = {
        success: false,
      };

      try {
        const fetchResponse = await fetch(videoUrl, {
          headers: {
            Referer: referer,
            'User-Agent':
              'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            Accept: '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
          },
        });

        if (!fetchResponse.ok) {
          throw new Error(
            `Failed to download video: ${fetchResponse.status} ${fetchResponse.statusText}`,
          );
        }

        const arrayBuffer = await fetchResponse.arrayBuffer();
        const videoData = new Uint8Array(arrayBuffer);
        const fileSize = videoData.length;

        response.appendResponseLine(
          `Video downloaded: ${(fileSize / 1024 / 1024).toFixed(2)} MB`,
        );

        // 确定文件名
        let fileName = request.params.filePath;
        if (!fileName) {
          try {
            const urlParams = new URL(videoUrl).searchParams;
            const vid = urlParams.get('__vid');
            if (vid) {
              fileName = `douyin_video_${vid}.mp4`;
            } else {
              fileName = `douyin_video_${Date.now()}.mp4`;
            }
          } catch {
            fileName = `douyin_video_${Date.now()}.mp4`;
          }
        }

        const {filename} = await context.saveFile(videoData, fileName);
        response.appendResponseLine(`Video saved to: ${filename}`);
        response.appendResponseLine(
          `File size: ${(fileSize / 1024 / 1024).toFixed(2)} MB`,
        );

        downloadResult = {
          success: true,
          filename,
          fileSize,
          fileSizeMB: (fileSize / 1024 / 1024).toFixed(2),
          url: videoUrl,
        };

        steps.push({
          name: 'download',
          ok: true,
          detail: {
            filename,
            fileSize,
          },
        });
      } catch (err) {
        const errorMessage = err && 'message' in err ? (err as any).message : String(err);
        steps.push({
          name: 'download',
          ok: false,
          detail: {error: errorMessage},
        });
        downloadResult = {
          success: false,
          url: videoUrl,
        };
      }

      const result = {
        url: processedUrl || request.params.url,
        elapsedMs: Date.now() - startedAt,
        totalVideos: domData.totalVideos,
        link: videoUrl,
        download: downloadResult,
        steps,
        network: networkData,
      };

      response.appendResponseLine('Douyin video fetched and downloaded:');
      response.appendResponseLine('```json');
      response.appendResponseLine(JSON.stringify(result, null, 2));
      response.appendResponseLine('```');
    });
  },
});

/**
 * Download Douyin video from URL.
 */
export const downloadDouyinVideo = defineTool({
  name: 'download_douyin_video',
  description: `Download a Douyin video from a direct video URL. The video will be saved to the specified file path.`,
  annotations: {
    category: ToolCategory.DEBUGGING,
    readOnlyHint: false,
  },
  schema: {
    url: zod
      .string()
      .describe('The direct video URL to download (e.g., https://v26-web.douyinvod.com/...).'),
    filePath: zod
      .string()
      .optional()
      .describe(
        'The file path to save the video. If omitted, a filename will be auto-generated from the video ID in the URL.',
      ),
    referer: zod
      .string()
      .optional()
      .default('https://www.douyin.com')
      .describe('Referer header value. Default is https://www.douyin.com.'),
  },
  handler: async (request, response, context) => {
    const videoUrl = request.params.url;
    const referer = request.params.referer || 'https://www.douyin.com';

    await context.waitForEventsAfterAction(async () => {
      try {
        response.appendResponseLine(`Downloading video from: ${videoUrl.substring(0, 80)}...`);

        // 使用 fetch 下载视频
        const fetchResponse = await fetch(videoUrl, {
          headers: {
            'Referer': referer,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
          },
        });

        if (!fetchResponse.ok) {
          throw new Error(
            `Failed to download video: ${fetchResponse.status} ${fetchResponse.statusText}`,
          );
        }

        // 获取视频数据
        const arrayBuffer = await fetchResponse.arrayBuffer();
        const videoData = new Uint8Array(arrayBuffer);
        const fileSize = videoData.length;

        response.appendResponseLine(`Video downloaded: ${(fileSize / 1024 / 1024).toFixed(2)} MB`);

        // 确定文件名
        let fileName = request.params.filePath;
        if (!fileName) {
          // 尝试从 URL 中提取视频 ID
          const urlParams = new URL(videoUrl).searchParams;
          const vid = urlParams.get('__vid');
          if (vid) {
            fileName = `douyin_video_${vid}.mp4`;
          } else {
            // 使用时间戳作为文件名
            fileName = `douyin_video_${Date.now()}.mp4`;
          }
        }

        // 保存文件
        const {filename} = await context.saveFile(videoData, fileName);
        response.appendResponseLine(`Video saved to: ${filename}`);
        response.appendResponseLine(`File size: ${(fileSize / 1024 / 1024).toFixed(2)} MB`);

        const result = {
          success: true,
          filename: filename,
          fileSize: fileSize,
          fileSizeMB: (fileSize / 1024 / 1024).toFixed(2),
          url: videoUrl,
        };

        response.appendResponseLine('Download completed:');
        response.appendResponseLine('```json');
        response.appendResponseLine(JSON.stringify(result, null, 2));
        response.appendResponseLine('```');
      } catch (err) {
        const errorMessage = err && 'message' in err ? err.message : String(err);
        response.appendResponseLine(`Error downloading video: ${errorMessage}`);
        throw err;
      }
    });
  },
});

/**
 * Extract Dongchedi (懂车帝) community post and comments from URL(s).
 * 
 * This tool opens URL(s) in a new page, runs the extraction script to extract:
 * - Post information (author, content, images, time)
 * - All comments with replies (threaded structure)
 * - Images from posts and comments
 * 
 * The tool automatically scrolls, expands buttons, and navigates through pages
 * to extract all available content.
 */
export const extractDcdByUrl = defineTool({
  name: 'extract_dcd_by_url',
  description:
    'Extract post data from Dongchedi (懂车帝) community page and save as JSON file. This tool opens URL in a new page, runs the pre-defined extraction script to extract post information, comments, and images, then saves everything to a JSON file.',
  annotations: {
    category: ToolCategory.DEBUGGING,
    readOnlyHint: false,
  },
  schema: {
    url: zod
      .string()
      .describe('The Dongchedi community post URL to extract data from.'),
    outputDir: zod
      .string()
      .optional()
      .describe(
        'The directory path to save the JSON file. If omitted, saves to the current working directory.',
      ),
  },
  handler: async (request, response, context) => {
    const page = await context.newPage();
    const url = request.params.url;

    // 标准化URL：确保使用正确的URL格式
    let normalizedUrl = url;
    // 如果URL包含 /article/ 但没有 /ugc/，可能需要添加
    if (url.includes('/article/') && !url.includes('/ugc/')) {
      // 尝试转换为 /ugc/article/ 格式
      normalizedUrl = url.replace('/article/', '/ugc/article/');
    }
    
    try {
      response.appendResponseLine(`正在打开页面: ${normalizedUrl}`);
      await context.waitForEventsAfterAction(async () => {
        await page.goto(normalizedUrl, {
          timeout: 120000, // 增加到120秒
          waitUntil: 'domcontentloaded', // 等待DOM内容加载完成即可，不需要等待所有资源
        });
      });

      // 额外等待一下，确保页面内容加载
      await new Promise(resolve => setTimeout(resolve, 3000));
      response.appendResponseLine(`✅ 页面加载完成`);
    } catch (error) {
      response.appendResponseLine(`⚠️ 导航到 ${normalizedUrl} 时出错: ${error}`);
      // 如果第一次失败，尝试原始URL
      if (normalizedUrl !== url) {
        try {
          response.appendResponseLine(`尝试使用原始URL: ${url}`);
          await context.waitForEventsAfterAction(async () => {
            await page.goto(url, {
              timeout: 120000,
              waitUntil: 'domcontentloaded',
            });
          });
          await new Promise(resolve => setTimeout(resolve, 3000));
          response.appendResponseLine(`✅ 使用原始URL加载成功`);
        } catch (error2) {
          response.appendResponseLine(`⚠️ 原始URL也失败: ${error2}`);
          response.appendResponseLine('```json');
          response.appendResponseLine(
            JSON.stringify({error: `Navigation failed: ${error2}`, url, normalizedUrl}, null, 2),
          );
          response.appendResponseLine('```');
          return;
        }
      } else {
        response.appendResponseLine('```json');
        response.appendResponseLine(
          JSON.stringify({error: `Navigation failed: ${error}`, url}, null, 2),
        );
        response.appendResponseLine('```');
        return;
      }
    }

      const one = await page.evaluate(async () => {
        // ========= utils =========
        const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
        const norm = (s: any) => (s || '').toString().replace(/\s+/g, ' ').trim();
        const getText = (el: any) => norm(el?.innerText || el?.textContent || '');
        const hrefAbs = (a: any) =>
          a ? new URL((a as Element).getAttribute('href') || '', location.origin).toString() : '';

        // ========= image helpers =========
        const isHttpUrl = (u: string) => /^https?:\/\//i.test(u);
        const isDataUrl = (u: string) => /^data:/i.test(u);
        const isImageExt = (u: string) => /\.(jpe?g|png|webp)(\?|#|$)/i.test(u);

        // 你举的两类图都在 toutiaoimg / byteimg 生态里（更稳）
        const isLikelyRealCdnImage = (u: string) => {
          try {
            const url = new URL(u);
            const host = url.hostname;
            // 允许 toutiaoimg / byteimg 及其子域
            const okHost = host.endsWith('toutiaoimg.com') || host.endsWith('byteimg.com');
            return okHost && isImageExt(u);
          } catch {
            return false;
          }
        };

        // ========= next page =========
        async function goNextPage(): Promise<boolean> {
          const icon = document.querySelector('i.DCD_Icon.icon_into_12') as HTMLElement | null;

          if (!icon) return false;

          const link = icon.closest('a') as HTMLElement | null;
          if (!link) return false;

          try {
            link.scrollIntoView({block: 'center'});
            link.click();
            return true;
          } catch {
            return false;
          }
        }

        // ========= extract image =========
        function extractImages(container: Element | null): string[] {
          if (!container) return [];

          const toAbs = (u: string) => {
            if (!u) return '';
            try {
              return new URL(u, location.origin).toString();
            } catch {
              return u;
            }
          };

          const raw = Array.from(container.querySelectorAll('img'))
            .map((img) => {
              const el = img as HTMLImageElement;
              return (
                el.getAttribute('src') ||
                el.getAttribute('data-src') ||
                el.getAttribute('data-original') ||
                el.getAttribute('data-lazy-src') ||
                el.currentSrc ||
                ''
              );
            })
            .map((u) => toAbs(u))
            .map((u) => (u || '').trim())
            .filter(Boolean);

          // ✅ 过滤掉：data:image/svg+xml;base64,... 之类的占位
          // ✅ 只保留：http(s) 且 jpg/png/webp 且域名在 toutiaoimg/byteimg（与你需求一致）
          const filtered = raw.filter((u) => {
            if (!u) return false;
            if (isDataUrl(u)) return false; // 干掉 data:（包括 svg base64）
            if (!isHttpUrl(u)) return false; // 只要 http(s)
            if (!isLikelyRealCdnImage(u)) return false; // 只留真实 cdn 图（含 jpg/png/webp）
            return true;
          });

          // 去重
          const seen = new Set<string>();
          const out: string[] = [];
          for (const u of filtered) {
            if (seen.has(u)) continue;
            seen.add(u);
            out.push(u);
          }
          return out;
        }

        // ========= expand =========
        function isVisible(el: Element | null) {
          const r = (el as HTMLElement | null)?.getBoundingClientRect?.();
          return !!(r && r.width > 0 && r.height > 0);
        }

        function findExpandButtons(): HTMLElement[] {
          return Array.from(document.querySelectorAll('button.tw-text-common-blue'))
            .filter((b) => isVisible(b))
            .filter((b) => {
              const t = getText(b);
              if (!t) return false;
              if (t.includes('收起')) return false;
              return (
                t.includes('条回复') ||
                t.includes('全部') ||
                t.includes('展开') ||
                t.includes('更多')
              );
            })
            .map((b) => b as HTMLElement);
        }

        async function clickAllExpands(rounds = 10) {
          for (let i = 0; i < rounds; i++) {
            const btns = findExpandButtons();
            if (!btns.length) break;

            const seen = new Set<string>();
            for (const b of btns) {
              const key = (b.outerHTML || '').slice(0, 180);
              if (seen.has(key)) continue;
              seen.add(key);
              try {
                b.scrollIntoView({block: 'center'});
                b.click();
                await sleep(40);
              } catch {}
            }
            await sleep(120);
          }
        }

        async function autoScroll({
          maxLoops = 60,
          pauseMs = 220,
          stableRounds = 5,
          bottomGapPx = 1200,
        } = {}) {
          let stable = 0;
          let lastH = 0;
          for (let i = 0; i < maxLoops; i++) {
            window.scrollBy(0, Math.max(700, Math.floor(window.innerHeight * 0.9)));
            await sleep(pauseMs);
            await clickAllExpands(2);
            // 4) 判断是否到底/是否还在增长
            const h = document.documentElement.scrollHeight || document.body.scrollHeight || 0;
            const y = window.scrollY + window.innerHeight;
            const nearBottom = y >= h - bottomGapPx;

            if (h === lastH) stable++;
            else stable = 0;

            lastH = h;

            // 连续 stableRounds 次不增长，并且接近底部 => 停
            if (stable >= stableRounds && nearBottom) break;
          }
        }

        // ========= post =========
        function extractPost() {
          const authorA = document.querySelector(
            'p.tw-truncate a[href^="/user/"]',
          ) as HTMLAnchorElement | null;
          const timeP = document.querySelector('div.user p') as HTMLElement | null;
          const contentSpan = document.querySelector(
            'div.content p.article-content span',
          ) as HTMLElement | null;
          const contentDiv = document.querySelector('div.content') as HTMLElement | null;

          const timeRaw = getText(timeP);
          let date = '',
            publishedTo = '';
          if (timeRaw) {
            const m = timeRaw.match(/^(\d{2}-\d{2})/);
            if (m) date = m[1];
            const idx = timeRaw.indexOf('发布于：');
            if (idx >= 0) publishedTo = timeRaw.slice(idx + '发布于：'.length).trim();
          }

          const postRoot =
            contentDiv?.closest("div[class*='content']")?.parentElement ||
            contentDiv?.parentElement ||
            document.body;

          return {
            author: getText(authorA),
            authorUrl: authorA ? hrefAbs(authorA) : '',
            timeRaw,
            date,
            publishedTo,
            content: getText(contentSpan) || getText(contentDiv),
            images: extractImages(postRoot),
          };
        }

        // ========= id parser =========
        function parseDataLogView(card: Element | null) {
          let groupId = '',
            commentId = '';
          const dlv = card?.getAttribute?.('data-log-view');
          if (!dlv) return {groupId, commentId};

          const fixed = dlv.includes('&quot;') ? dlv.replace(/&quot;/g, '"') : dlv;

          try {
            const obj = JSON.parse(fixed);
            groupId = obj?.params?.group_id || '';
            commentId = obj?.params?.comment_id || '';
            return {groupId, commentId};
          } catch {}

          const m1 = fixed.match(/group_id"\s*:\s*"(\d+)"/);
          const m2 = fixed.match(/comment_id"\s*:\s*"(\d+)"/);
          if (m1) groupId = m1[1];
          if (m2) commentId = m2[1];
          return {groupId, commentId};
        }

        // ========= find thread roots =========
        function findThreadRoots(): Element[] {
          const roots: Element[] = [];
          const candidates = Array.from(document.querySelectorAll('div.tw-flex'));

          for (const root of candidates) {
            const left = root.querySelector(':scope > div.tw-w-232');
            const right = root.querySelector(':scope > div.tw-flex-1');
            if (!left || !right) continue;

            const mainCard = right.querySelector('section.community-card[data-log-view]');
            if (!mainCard) continue;

            const hasCommentMeta = Array.from(
              right.querySelectorAll('span.tw-text-video-shallow-gray'),
            ).some((sp) => getText(sp).includes('评论发表于'));

            const hasReplyList = !!right.querySelector('ul > li');
            if (!hasCommentMeta && !hasReplyList) continue;

            const len = getText(right).length;
            if (len < 5 || len > 15000) continue;

            roots.push(root);
          }

          // 去重
          const out: Element[] = [];
          const seen = new Set<string>();
          for (const r of roots) {
            const right = r.querySelector(':scope > div.tw-flex-1');
            const mainCard =
              right?.querySelector('section.community-card[data-log-view]') || null;
            const {commentId} = parseDataLogView(mainCard);
            const key = commentId ? `cid:${commentId}` : (r.outerHTML || '').slice(0, 140);
            if (seen.has(key)) continue;
            seen.add(key);
            out.push(r);
          }
          return out;
        }

        // ========= extract threads + replies =========
        function extractThreadedComments() {
          const roots = findThreadRoots();
          const threads: any[] = [];

          for (const root of roots) {
            const left = root.querySelector(':scope > div.tw-w-232') as HTMLElement | null;
            const right = root.querySelector(':scope > div.tw-flex-1') as HTMLElement | null;
            if (!left || !right) continue;

            const authorA = left.querySelector(
              "p.tw-truncate a[href^='/user/']",
            ) as HTMLAnchorElement | null;
            const author = getText(authorA);
            const authorUrl = authorA ? hrefAbs(authorA) : '';

            const isOP = Array.from(left.querySelectorAll('span')).some((sp) =>
              getText(sp).includes('楼主'),
            );

            const mainCard = right.querySelector('section.community-card[data-log-view]');
            const mainContentSpan = mainCard?.querySelector(
              'span.tw-text-common-black',
            ) as HTMLElement | null;
            const content = getText(mainContentSpan);

            let timeRaw = '';
            for (const sp of Array.from(right.querySelectorAll('span.tw-text-video-shallow-gray'))) {
              const t = getText(sp);
              if (t.includes('评论发表于')) {
                timeRaw = t;
                break;
              }
            }

            const {groupId, commentId} = parseDataLogView(mainCard);

            const topImages = extractImages(right);

            // ✅ 关键修复：只抓第一层 replies 的 li，避免把嵌套 li 全抓进来
            const replyLis = Array.from(right.querySelectorAll(':scope > ul > li')) as HTMLElement[];

            const replies: any[] = [];
            const replySeen = new Set<string>(); // ✅ commentId 去重

            for (const li of replyLis) {
              const replyOuter =
                (li.querySelector(':scope section.community-card[data-log-view]') ||
                  li.querySelector('section.community-card[data-log-view]')) as HTMLElement | null;
              if (!replyOuter) continue;

              const {commentId: replyCommentId} = parseDataLogView(replyOuter);

              const dedupeKey = replyCommentId
                ? `rid:${replyCommentId}`
                : (li.outerHTML || '').slice(0, 180);
              if (replySeen.has(dedupeKey)) continue;
              replySeen.add(dedupeKey);

              const replyAuthorLink = replyOuter.querySelector(
                ":scope a[href^='/user/']",
              ) as HTMLAnchorElement | null;
              const replyAuthor =
                getText(
                  replyOuter.querySelector(":scope a[href^='/user/'] span.tw-text-black"),
                ) || getText(replyAuthorLink);
              const replyAuthorUrl = replyAuthorLink ? hrefAbs(replyAuthorLink) : '';

              const inner =
                (li.querySelector(':scope section.tw-pl-56') as HTMLElement | null) || li;

              let replyContent = getText(inner.querySelector('span.tw-text-common-black'));
              if (!replyContent) {
                const spans = Array.from(inner.querySelectorAll('span.tw-text-common-black')).filter(
                  (sp) => !(sp as HTMLElement).closest('div.jsx-1055894087'),
                );
                replyContent = getText(spans[0] as any);
              }

              let replyTimeRaw = '';
              for (const sp of Array.from(inner.querySelectorAll('span.tw-text-video-shallow-gray'))) {
                const t = getText(sp);
                if (t.includes('回复发表于') || t.includes('回发表于')) {
                  replyTimeRaw = t;
                  break;
                }
              }

              if (replyAuthor && replyContent) {
                replies.push({
                  commentId: replyCommentId,
                  author: replyAuthor,
                  authorUrl: replyAuthorUrl,
                  timeRaw: replyTimeRaw,
                  content: replyContent,
                  images: extractImages(li), // ✅ 回复图通常在 li 内
                });
              }
            }

            if (!author || !content) continue;

            threads.push({
              groupId,
              commentId,
              isOP,
              author,
              authorUrl,
              timeRaw,
              content,
              images: topImages,
              replies,
            });
          }

          return threads;
        }

        // ========= run =========
        const allcomments: any[] = [];
        const post = extractPost();

        // 只允许尝试翻页刷新一次，避免页面被无限刷新
        let pageLoops = 0;
        const maxPageLoops = 1;

        while (true) {
          await clickAllExpands(5);
          await autoScroll({maxLoops: 22, pauseMs: 220});
          await clickAllExpands(5);

          const comments = extractThreadedComments();
          allcomments.push(...comments);
          
          // 如果已经尝试过翻页（刷新）了，就不再继续，直接退出循环
          if (pageLoops >= maxPageLoops) {
            break;
          }

          const cangonextpage = await goNextPage();
          pageLoops++;
          if (!cangonextpage) {
            break;
          }
        }

        return {
          url: location.href,
          extractedAt: new Date().toISOString(),
          post,
          allcomments,
        };
      });

      // Extract article ID from URL for filename (支持 /article/ 和 /ugc/article/ 格式)
      const articleIdMatch = url.match(/\/(?:ugc\/)?article\/(\d+)/);
      const articleId = articleIdMatch ? articleIdMatch[1] : 'article';

      // Prepare output data（业务数据部分）
      const data = {
        source: '懂车帝',
        ...one,
      };

      // Generate filename (简化格式，只使用文章ID)
      const filename = `dcd_${articleId}.json`;

      // 业务数据 JSON 字符串（写文件和作为返回值用）
      const dataJsonString = JSON.stringify(data, null, 2);
      const jsonBytes = new TextEncoder().encode(dataJsonString);

      // Save file (如果没有指定outputDir，使用当前工作目录)
      const filePath = request.params.outputDir
        ? `${request.params.outputDir}/${filename}`
        : filename;
      const {filename: savedPath} = await context.saveFile(jsonBytes, filePath);
      
      response.appendResponseLine(`✓ JSON文件已保存: ${savedPath}`);
      
      // 直接返回业务数据 JSON 字符串，便于上层作为字符串使用
      response.appendResponseLine(dataJsonString);
  },
});

/**
 * Extract Dongchedi (懂车帝) video page data and save as JSON file.
 *
 * Example: https://www.dongchedi.com/video/7567211858810159659
 *
 * Main fields:
 * - video real URL(s) (best-effort: DOM currentSrc + network sniffing)
 * - title / author / publish_time / play_count
 * - comments (best-effort)
 */
export const extractDcdVideo = defineTool({
  name: 'extract_dcd_video',
  description:
    'Extract data from Dongchedi (懂车帝) video page and save as JSON file. Includes best-effort video real URL(s), metadata, and comments.',
  annotations: {
    category: ToolCategory.DEBUGGING,
    readOnlyHint: false,
  },
  schema: {
    url: zod
      .string()
      .describe('The Dongchedi video URL to extract data from (e.g. https://www.dongchedi.com/video/7567211858810159659).'),
    outputDir: zod
      .string()
      .optional()
      .describe('The directory path to save the JSON file. If omitted, saves to the current working directory.'),
    initialWaitMs: zod
      .number()
      .int()
      .optional()
      .default(5000)
      .describe('Initial wait after navigation/injection to allow page to load. Default 5000ms.'),
    scrollLoops: zod
      .number()
      .int()
      .optional()
      .default(22)
      .describe('How many scroll loops to try for loading comments. Default 22.'),
    networkLimit: zod
      .number()
      .int()
      .optional()
      .default(120)
      .describe('How many recent network-hook requests to inspect for video URLs. Default 120.'),
  },
  handler: async (request, response, context) => {
    const page = context.getSelectedPage();
    const url = request.params.url;

    await context.waitForEventsAfterAction(async () => {
      response.appendResponseLine(`Navigating to: ${url}`);
      // 在首屏请求发出前注入 fetch/XHR 钩子，否则首包视频 m3u8/mp4 会漏记
      await page.evaluateOnNewDocument(() => {
        (function () {
          const w = window as any;
          if (w.__dcd_video_hooked__) return;
          w.__dcd_video_hooked__ = true;
          w.__dcd_video_requests__ = [];
          w.__dcd_api_requests__ = [];

          function normUrl(u: any) {
            if (!u) return '';
            let s = String(u).trim().replace(/&amp;/g, '&');
            if (s.startsWith('//')) s = 'https:' + s;
            return s;
          }

          function isLikelyMedia(u: string) {
            if (!u) return false;
            if (u.startsWith('blob:')) return false;
            const lower = u.toLowerCase();
            return (
              lower.includes('.m3u8') ||
              lower.includes('.mp4') ||
              lower.includes('.flv') ||
              lower.includes('/m3u8') ||
              lower.includes('video') ||
              lower.includes('vod') ||
              lower.includes('playwm') ||
              lower.includes('play')
            );
          }

          function isLikelyCommentApi(u: string) {
            if (!u) return false;
            const lower = u.toLowerCase();
            return (
              lower.includes('comment') ||
              lower.includes('comments') ||
              lower.includes('reply') ||
              lower.includes('replies') ||
              (lower.includes('api') &&
                (lower.includes('dongchedi') || lower.includes('dcar')))
            );
          }

          function record(u: any, method?: string, extra?: any) {
            const url = normUrl(u);
            const meta = {
              url,
              method: method || 'GET',
              time: Date.now(),
              referer: document.referrer || location.href,
              extra: extra || null,
            };
            if (isLikelyMedia(url)) {
              w.__dcd_video_requests__.push(meta);
              if (w.__dcd_video_requests__.length > 300) w.__dcd_video_requests__.shift();
            }
            if (isLikelyCommentApi(url)) {
              w.__dcd_api_requests__.push(meta);
              if (w.__dcd_api_requests__.length > 300) w.__dcd_api_requests__.shift();
            }
          }

          const _fetch = window.fetch;
          window.fetch = function (input: RequestInfo | URL, init?: RequestInit) {
            try {
              const u = (input as any)?.url || input;
              record(u, (init as any)?.method || 'GET', {type: 'fetch'});
            } catch {}
            return _fetch.call(this, input, init);
          };

          const _open = XMLHttpRequest.prototype.open;
          XMLHttpRequest.prototype.open = function (method: string, u: string) {
            (this as any)._dcd_method = method;
            (this as any)._dcd_url = u;
            return _open.apply(this, arguments as any);
          };
          const _send = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.send = function (body?: Document | XMLHttpRequestBodyInit | null) {
            try {
              record((this as any)._dcd_url, (this as any)._dcd_method || 'GET', {type: 'xhr'});
            } catch {}
            return _send.call(this, body);
          };
        })();
      });

      // 勿用 `load`：懂车帝等 SPA 常因长轮询/埋点导致 window load 长期不触发，120s 仍超时
      await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 120000});

      if ((request.params.initialWaitMs ?? 0) > 0) {
        await new Promise(resolve => setTimeout(resolve, request.params.initialWaitMs));
      }

      // Best-effort: open the comment panel/section if there is a "评论" tab/button.
      try {
        await page.evaluate(async () => {
          const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
          const candidates = Array.from(document.querySelectorAll('button, a, div, span'))
            .filter(el => {
              const t = ((el as HTMLElement).innerText || '').trim();
              if (!t) return false;
              if (!t.includes('评论')) return false;
              // avoid "相关推荐/热门内容"
              if (t.includes('相关推荐') || t.includes('热门')) return false;
              return t.length <= 10;
            })
            .slice(0, 8) as HTMLElement[];
          for (const el of candidates) {
            try {
              el.scrollIntoView({block: 'center'});
              el.click();
              await sleep(200);
            } catch {}
          }
          await sleep(500);
        });
      } catch {}

      // Best-effort: trigger video loading/playback.
      try {
        await page.evaluate(async () => {
          const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
          const video = document.querySelector('video') as HTMLVideoElement | null;
          if (video) {
            try {
              video.muted = true;
              (video as any).click?.();
              await sleep(120);
              await video.play().catch(() => {});
            } catch {}
          }

          // Click some likely play buttons
          const btns = Array.from(document.querySelectorAll('button, div, a'))
            .filter(el => {
              const cls = ((el as HTMLElement).className || '').toString().toLowerCase();
              const t = (((el as HTMLElement).innerText || '').trim()).toLowerCase();
              const s = `${cls} ${t}`;
              return s.includes('play') || s.includes('播放') || s.includes('icon') || s.includes('video');
            })
            .slice(0, 10) as HTMLElement[];
          for (const b of btns) {
            try {
              b.click();
              await sleep(120);
            } catch {}
          }
          await sleep(700);
        });
      } catch {}

      // 评论加载：与 extract_dcd_by_url（懂车帝主贴）一致 —— 展开「条回复/展开/更多」+ 滚动到底，再抽取
      await page.evaluate(async (scrollLoops: number) => {
        const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
        const norm = (s: any) => (s || '').toString().replace(/\s+/g, ' ').trim();
        const getText = (el: any) => norm(el?.innerText || el?.textContent || '');
        function isVisible(el: Element | null) {
          const r = (el as HTMLElement | null)?.getBoundingClientRect?.();
          return !!(r && r.width > 0 && r.height > 0);
        }
        function findExpandButtons(): HTMLElement[] {
          return Array.from(document.querySelectorAll('button.tw-text-common-blue'))
            .filter((b) => isVisible(b))
            .filter((b) => {
              const t = getText(b);
              if (!t) return false;
              if (t.includes('收起')) return false;
              return (
                t.includes('条回复') ||
                t.includes('全部') ||
                t.includes('展开') ||
                t.includes('更多')
              );
            })
            .map((b) => b as HTMLElement);
        }
        async function clickAllExpands(rounds = 10) {
          for (let i = 0; i < rounds; i++) {
            const btns = findExpandButtons();
            if (!btns.length) break;
            const seen = new Set<string>();
            for (const b of btns) {
              const key = (b.outerHTML || '').slice(0, 180);
              if (seen.has(key)) continue;
              seen.add(key);
              try {
                b.scrollIntoView({block: 'center'});
                b.click();
                await sleep(40);
              } catch {}
            }
            await sleep(120);
          }
        }
        async function autoScroll({
          maxLoops = 22,
          pauseMs = 220,
          stableRounds = 5,
          bottomGapPx = 1200,
        } = {}) {
          let stable = 0;
          let lastH = 0;
          for (let i = 0; i < maxLoops; i++) {
            window.scrollBy(0, Math.max(700, Math.floor(window.innerHeight * 0.9)));
            await sleep(pauseMs);
            await clickAllExpands(2);
            const h = document.documentElement.scrollHeight || document.body.scrollHeight || 0;
            const y = window.scrollY + window.innerHeight;
            const nearBottom = y >= h - bottomGapPx;
            if (h === lastH) stable++;
            else stable = 0;
            lastH = h;
            if (stable >= stableRounds && nearBottom) break;
          }
        }
        await clickAllExpands(5);
        await autoScroll({maxLoops: scrollLoops, pauseMs: 220});
        await clickAllExpands(5);
      }, request.params.scrollLoops ?? 22);

      const extracted = await page.evaluate(async (networkLimit: number) => {
        const w = window as any;
        const norm = (s: any) => (s || '').toString().replace(/\s+/g, ' ').trim();
        const getText = (el: any) => norm(el?.innerText || el?.textContent || '');
        const hrefAbs = (a: any) =>
          a ? new URL((a as Element).getAttribute('href') || '', location.origin).toString() : '';

        function parseLdVideoObject(): {
          name?: string;
          description?: string;
          embedUrl?: string;
          thumbnailUrl?: string | string[];
        } | null {
          const scripts = document.querySelectorAll('script[type="application/ld+json"]');
          for (const s of Array.from(scripts)) {
            const raw = (s.textContent || '').trim();
            if (!raw) continue;
            try {
              const j = JSON.parse(raw) as Record<string, any>;
              const items: any[] = [];
              if (Array.isArray(j)) items.push(...j);
              else if (Array.isArray(j['@graph'])) items.push(...j['@graph']);
              else items.push(j);
              for (const item of items) {
                const typ = String(item?.['@type'] || '').toLowerCase();
                if (typ === 'videoobject') {
                  return {
                    name: item.name,
                    description: item.description,
                    embedUrl: item.embedUrl,
                    thumbnailUrl: item.thumbnailUrl,
                  };
                }
              }
            } catch {
              /* ignore */
            }
          }
          return null;
        }

        function stripDcdTitleSuffix(t: string) {
          return t.replace(/[_\s]*懂车帝\s*$/u, '').trim();
        }

        function isGenericDcdTitle(t: string) {
          if (!t) return true;
          const x = t.trim();
          if (x === '懂车帝') return true;
          if (x.includes('说真的还得懂车帝')) return true;
          if (/^懂车帝\s*[-–—]\s*说真的还得懂车帝/.test(x)) return true;
          return false;
        }

        const ldVideo = parseLdVideoObject();
        const ogTitle =
          (document.querySelector('meta[property="og:title"]') as HTMLMetaElement | null)?.content?.trim() ||
          '';
        const ogDesc =
          (document.querySelector('meta[property="og:description"]') as HTMLMetaElement | null)?.content?.trim() ||
          '';

        let title = '';
        if (ldVideo?.name) {
          title = stripDcdTitleSuffix(norm(ldVideo.name));
        } else if (ogTitle && !isGenericDcdTitle(ogTitle)) {
          title = stripDcdTitleSuffix(ogTitle);
        } else {
          const h1v =
            norm(
              document.querySelector('.video-detail h1')?.textContent ||
                document.querySelector('main h1')?.textContent ||
                document.querySelector('h1')?.textContent,
            ) || '';
          if (h1v && !isGenericDcdTitle(h1v)) title = stripDcdTitleSuffix(h1v);
          else if (ogTitle) title = stripDcdTitleSuffix(ogTitle);
          else {
            const dt = stripDcdTitleSuffix(norm(document.title));
            title = isGenericDcdTitle(dt) ? (ldVideo?.name ? stripDcdTitleSuffix(norm(ldVideo.name)) : '') : dt;
          }
        }
        if (!title && ldVideo?.name) title = stripDcdTitleSuffix(norm(ldVideo.name));

        // Main content (正文): JSON-LD / og 优先（避免 Hydration 后只剩站点通用文案）
        const content = (() => {
          if (ldVideo?.description) return norm(ldVideo.description);
          if (ogDesc && !ogDesc.includes('懂车帝是一个汽车资讯平台')) return norm(ogDesc);

          const pick = (sel: string) =>
            Array.from(document.querySelectorAll(sel))
              .map(el => norm((el as HTMLElement).innerText || el.textContent))
              .filter(Boolean)
              .join('\n')
              .trim();

          const c1 =
            pick('article') ||
            pick('[class*="article-content"]') ||
            pick('p.article-content') ||
            pick('[class*="content"] p') ||
            '';

          if (c1 && c1.length >= 20 && !c1.includes('懂车帝是一个汽车资讯平台')) return c1;

          const metaDesc =
            (document.querySelector('meta[name="description"]') as HTMLMetaElement | null)?.content?.trim() ||
            '';
          if (metaDesc && metaDesc.length >= 10) {
            const cleaned = metaDesc.replace(/^懂车帝提供/, '').trim();
            return cleaned || metaDesc;
          }

          return '';
        })();

        // Author: try common patterns (avatar/name blocks).
        let author_name = '';
        const authorCandidates = Array.from(document.querySelectorAll('a, span, p'))
          .map(el => norm((el as HTMLElement).innerText || el.textContent))
          .filter(t => t && t.length >= 2 && t.length <= 20);
        // Prefer the first one near "关注/粉丝" area if exists.
        const bodyText = (document.body?.innerText || '').toString();
        if (bodyText.includes('粉丝') && authorCandidates.length) {
          author_name = authorCandidates[0] || '';
        }

        // Publish time: match patterns from page text
        const pubMatch =
          bodyText.match(/发布于\s*(\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*\d{1,2}:\d{2})/) ||
          bodyText.match(/(\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*\d{1,2}:\d{2})/);
        const publish_time = pubMatch ? pubMatch[1] : '';

        // Play count: match "万次播放" or "次播放"
        const playMatch =
          bodyText.match(/([\d.]+)\s*万次播放/) ||
          bodyText.match(/(\d+)\s*次播放/);
        const play_count = playMatch ? playMatch[0].replace(/\s+/g, '') : '';

        // Video URLs from DOM（页面可能有多个 video 节点）
        const domUrls: string[] = [];
        const pushUrl = (u: any) => {
          if (!u) return;
          let s = String(u).trim().replace(/&amp;/g, '&');
          if (!s) return;
          if (s.startsWith('blob:')) return;
          if (s.startsWith('//')) s = 'https:' + s;
          if (!/^https?:\/\//i.test(s)) return;
          if (!domUrls.includes(s)) domUrls.push(s);
        };
        for (const video of Array.from(document.querySelectorAll('video'))) {
          const ve = video as HTMLVideoElement;
          pushUrl((ve as any).currentSrc);
          pushUrl((ve as any).src);
          for (const s of Array.from(ve.querySelectorAll('source'))) {
            pushUrl((s as any).src || (s as any).getAttribute?.('src'));
          }
        }

        const embedUrlFromLd = ldVideo?.embedUrl ? String(ldVideo.embedUrl).trim() : '';

        // Performance 里常有最终 m3u8/mp4（不依赖钩子时机）
        const perfUrls: string[] = [];
        try {
          const entries = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
          for (const e of entries) {
            const n = e?.name || '';
            if (!n) continue;
            if (/\.(m3u8|mp4|flv)(\?|#|$)/i.test(n)) {
              if (!perfUrls.includes(n)) perfUrls.push(n);
            }
          }
        } catch {
          /* ignore */
        }

        function looksLikeCapturedVideoUrl(u: string) {
          const lower = u.toLowerCase();
          if (lower.includes('.mp4') || lower.includes('.m3u8') || lower.includes('.flv')) return true;
          if (
            lower.includes('vod') &&
            (lower.includes('byte') ||
              lower.includes('bytedance') ||
              lower.includes('ixigua') ||
              lower.includes('snssdk') ||
              lower.includes('toutiao'))
          ) {
            return true;
          }
          return false;
        }

        // Video URLs from network hook（钩子已提前注入，可覆盖首包请求）
        const reqs: any[] = Array.isArray(w.__dcd_video_requests__)
          ? w.__dcd_video_requests__.slice(-Math.max(10, networkLimit))
          : [];
        const networkUrls: string[] = [];
        for (const r of reqs) {
          const u = r?.url ? String(r.url) : '';
          if (!u || !looksLikeCapturedVideoUrl(u)) continue;
          if (!networkUrls.includes(u)) networkUrls.push(u);
        }

        const all = [...domUrls, ...perfUrls, ...networkUrls].filter((u, i, a) => a.indexOf(u) === i);
        let bestUrl = all
          .map(u => {
            const lower = u.toLowerCase();
            let score = u.length;
            if (lower.includes('.m3u8')) score += 2000;
            if (lower.includes('.mp4')) score += 1500;
            if (lower.includes('token') || lower.includes('auth') || lower.includes('expires')) score += 300;
            if (lower.includes('thumb') || lower.includes('cover')) score -= 500;
            return {u, score};
          })
          .sort((a, b) => b.score - a.score)[0]?.u || '';

        if (!bestUrl && embedUrlFromLd) {
          bestUrl = embedUrlFromLd;
        }

        // ========= 评论：与 extract_dcd_by_url（懂车帝主贴）同一套 DOM 方案 =========
        const isHttpUrl = (u: string) => /^https?:\/\//i.test(u);
        const isDataUrl = (u: string) => /^data:/i.test(u);
        const isImageExt = (u: string) => /\.(jpe?g|png|webp)(\?|#|$)/i.test(u);
        const isLikelyRealCdnImage = (u: string) => {
          try {
            const url = new URL(u);
            const host = url.hostname;
            const okHost = host.endsWith('toutiaoimg.com') || host.endsWith('byteimg.com');
            return okHost && isImageExt(u);
          } catch {
            return false;
          }
        };
        function extractImages(container: Element | null): string[] {
          if (!container) return [];
          const toAbs = (u: string) => {
            if (!u) return '';
            try {
              return new URL(u, location.origin).toString();
            } catch {
              return u;
            }
          };
          const raw = Array.from(container.querySelectorAll('img'))
            .map((img) => {
              const el = img as HTMLImageElement;
              return (
                el.getAttribute('src') ||
                el.getAttribute('data-src') ||
                el.getAttribute('data-original') ||
                el.getAttribute('data-lazy-src') ||
                el.currentSrc ||
                ''
              );
            })
            .map((u) => toAbs(u))
            .map((u) => (u || '').trim())
            .filter(Boolean);
          const filtered = raw.filter((u) => {
            if (!u) return false;
            if (isDataUrl(u)) return false;
            if (!isHttpUrl(u)) return false;
            if (!isLikelyRealCdnImage(u)) return false;
            return true;
          });
          const seen = new Set<string>();
          const out: string[] = [];
          for (const u of filtered) {
            if (seen.has(u)) continue;
            seen.add(u);
            out.push(u);
          }
          return out;
        }

        function parseDataLogView(card: Element | null) {
          let groupId = '',
            commentId = '';
          const dlv = card?.getAttribute?.('data-log-view');
          if (!dlv) return {groupId, commentId};
          const fixed = dlv.includes('&quot;') ? dlv.replace(/&quot;/g, '"') : dlv;
          try {
            const obj = JSON.parse(fixed);
            groupId = obj?.params?.group_id || '';
            commentId = obj?.params?.comment_id || '';
            return {groupId, commentId};
          } catch {}
          const m1 = fixed.match(/group_id"\s*:\s*"(\d+)"/);
          const m2 = fixed.match(/comment_id"\s*:\s*"(\d+)"/);
          if (m1) groupId = m1[1];
          if (m2) commentId = m2[1];
          return {groupId, commentId};
        }

        function findThreadRoots(): Element[] {
          const roots: Element[] = [];
          const candidates = Array.from(document.querySelectorAll('div.tw-flex'));
          for (const root of candidates) {
            const left = root.querySelector(':scope > div.tw-w-232');
            const right = root.querySelector(':scope > div.tw-flex-1');
            if (!left || !right) continue;
            const mainCard = right.querySelector('section.community-card[data-log-view]');
            if (!mainCard) continue;
            const hasCommentMeta = Array.from(
              right.querySelectorAll('span.tw-text-video-shallow-gray'),
            ).some((sp) => getText(sp).includes('评论发表于'));
            const hasReplyList = !!right.querySelector('ul > li');
            if (!hasCommentMeta && !hasReplyList) continue;
            const len = getText(right).length;
            if (len < 5 || len > 15000) continue;
            roots.push(root);
          }
          const out: Element[] = [];
          const seen = new Set<string>();
          for (const r of roots) {
            const right = r.querySelector(':scope > div.tw-flex-1');
            const mainCard =
              right?.querySelector('section.community-card[data-log-view]') || null;
            const {commentId} = parseDataLogView(mainCard);
            const key = commentId ? `cid:${commentId}` : (r.outerHTML || '').slice(0, 140);
            if (seen.has(key)) continue;
            seen.add(key);
            out.push(r);
          }
          return out;
        }

        function extractThreadedComments() {
          const roots = findThreadRoots();
          const threads: any[] = [];
          for (const root of roots) {
            const left = root.querySelector(':scope > div.tw-w-232') as HTMLElement | null;
            const right = root.querySelector(':scope > div.tw-flex-1') as HTMLElement | null;
            if (!left || !right) continue;
            const authorA = left.querySelector(
              "p.tw-truncate a[href^='/user/']",
            ) as HTMLAnchorElement | null;
            const author = getText(authorA);
            const authorUrl = authorA ? hrefAbs(authorA) : '';
            const isOP = Array.from(left.querySelectorAll('span')).some((sp) =>
              getText(sp).includes('楼主'),
            );
            const mainCard = right.querySelector('section.community-card[data-log-view]');
            const mainContentSpan = mainCard?.querySelector(
              'span.tw-text-common-black',
            ) as HTMLElement | null;
            const content = getText(mainContentSpan);
            let timeRaw = '';
            for (const sp of Array.from(right.querySelectorAll('span.tw-text-video-shallow-gray'))) {
              const t = getText(sp);
              if (t.includes('评论发表于')) {
                timeRaw = t;
                break;
              }
            }
            const {groupId, commentId} = parseDataLogView(mainCard);
            const topImages = extractImages(right);
            const replyLis = Array.from(right.querySelectorAll(':scope > ul > li')) as HTMLElement[];
            const replies: any[] = [];
            const replySeen = new Set<string>();
            for (const li of replyLis) {
              const replyOuter =
                (li.querySelector(':scope section.community-card[data-log-view]') ||
                  li.querySelector('section.community-card[data-log-view]')) as HTMLElement | null;
              if (!replyOuter) continue;
              const {commentId: replyCommentId} = parseDataLogView(replyOuter);
              const dedupeKey = replyCommentId
                ? `rid:${replyCommentId}`
                : (li.outerHTML || '').slice(0, 180);
              if (replySeen.has(dedupeKey)) continue;
              replySeen.add(dedupeKey);
              const replyAuthorLink = replyOuter.querySelector(
                ":scope a[href^='/user/']",
              ) as HTMLAnchorElement | null;
              const replyAuthor =
                getText(
                  replyOuter.querySelector(":scope a[href^='/user/'] span.tw-text-black"),
                ) || getText(replyAuthorLink);
              const replyAuthorUrl = replyAuthorLink ? hrefAbs(replyAuthorLink) : '';
              const inner =
                (li.querySelector(':scope section.tw-pl-56') as HTMLElement | null) || li;
              let replyContent = getText(inner.querySelector('span.tw-text-common-black'));
              if (!replyContent) {
                const spans = Array.from(inner.querySelectorAll('span.tw-text-common-black')).filter(
                  (sp) => !(sp as HTMLElement).closest('div.jsx-1055894087'),
                );
                replyContent = getText(spans[0] as any);
              }
              let replyTimeRaw = '';
              for (const sp of Array.from(inner.querySelectorAll('span.tw-text-video-shallow-gray'))) {
                const t = getText(sp);
                if (t.includes('回复发表于') || t.includes('回发表于')) {
                  replyTimeRaw = t;
                  break;
                }
              }
              if (replyAuthor && replyContent) {
                replies.push({
                  commentId: replyCommentId,
                  author: replyAuthor,
                  authorUrl: replyAuthorUrl,
                  timeRaw: replyTimeRaw,
                  content: replyContent,
                  images: extractImages(li),
                });
              }
            }
            if (!author || !content) continue;
            threads.push({
              groupId,
              commentId,
              isOP,
              author,
              authorUrl,
              timeRaw,
              content,
              images: topImages,
              replies,
            });
          }
          return threads;
        }

        const threads = extractThreadedComments();
        let comments: any[] = threads.map((t: any) => ({
          username: t.author,
          authorUrl: t.authorUrl,
          commentId: t.commentId,
          time: t.timeRaw,
          content: t.content,
          replies: (t.replies || []).map((r: any) => ({
            username: r.author,
            authorUrl: r.authorUrl,
            time: r.timeRaw,
            content: r.content,
          })),
        }));

        // 兜底：与汽车之家类似的「无结构化楼层」时，尝试接口 JSON，再尝试简单 li
        function pushCommentFlat(u: string, t: string, c: string) {
          const username = norm(u);
          const time = norm(t);
          const content = norm(c);
          if (!content) return;
          const key = `${username}::${time}::${content}`;
          if (comments.some((x: any) => `${x.username}::${x.time}::${x.content}` === key)) return;
          comments.push({username, time, content, replies: []});
        }

        if (comments.length === 0) {
          const apiReqs: any[] = Array.isArray(w.__dcd_api_requests__)
            ? w.__dcd_api_requests__.slice(-Math.max(20, networkLimit))
            : [];
          const apiUrls = apiReqs
            .map(r => (r?.url ? String(r.url) : ''))
            .filter(Boolean)
            .filter(u => u.toLowerCase().includes('comment'))
            .filter((u, i, a) => a.indexOf(u) === i)
            .slice(-8);
          for (const apiUrl of apiUrls) {
            try {
              const resp = await fetch(apiUrl, {credentials: 'include'});
              if (!resp.ok) continue;
              const json: any = await resp.json().catch(() => null);
              if (!json) continue;
              const lists: any[] = [];
              if (Array.isArray(json?.data?.comments)) lists.push(json.data.comments);
              if (Array.isArray(json?.data?.comment_list)) lists.push(json.data.comment_list);
              if (Array.isArray(json?.comments)) lists.push(json.comments);
              if (Array.isArray(json?.data)) lists.push(json.data);
              for (const list of lists) {
                for (const item of list.slice(0, 200)) {
                  const username =
                    item?.user?.name ||
                    item?.user?.nickname ||
                    item?.user_name ||
                    item?.username ||
                    '';
                  const c =
                    item?.text ||
                    item?.content ||
                    item?.comment?.text ||
                    item?.comment_text ||
                    '';
                  const tm =
                    item?.create_time ||
                    item?.createTime ||
                    item?.time ||
                    item?.publish_time ||
                    '';
                  if (c) pushCommentFlat(username, String(tm), c);
                }
              }
              if (comments.length > 0) break;
            } catch {}
          }
        }

        if (comments.length === 0) {
          const root = Array.from(document.querySelectorAll('section, div'))
            .map(el => el as HTMLElement)
            .find(el => {
              const t = norm((el.innerText || el.textContent || '').toString());
              return t.includes('提交评论') && t.includes('评论');
            });
          const scope: ParentNode = root || document;
          const nodes = Array.from(scope.querySelectorAll('li'))
            .slice(0, 300) as HTMLElement[];
          for (const el of nodes) {
            const raw = (el.innerText || el.textContent || '').toString();
            if (!raw) continue;
            const tm =
              raw.match(/(\d{4}-\d{2}-\d{2})/) ||
              raw.match(/(\d{4}[./]\d{1,2}[./]\d{1,2})/) ||
              raw.match(/(\d{2}-\d{2})/);
            if (!tm) continue;
            const time = tm[1];
            const lines = raw.split('\n').map(l => norm(l)).filter(Boolean);
            const username = lines[0] || '';
            const c = lines.find(l => l.length >= 2 && l !== username && !l.includes(time)) || '';
            if (c) pushCommentFlat(username, time, c);
            if (comments.length >= 50) break;
          }
        }

        return {
          url: location.href,
          title,
          author_name,
          publish_time,
          play_count,
          content,
          video: {
            bestUrl,
            embedUrl: embedUrlFromLd,
            domUrls,
            perfUrls,
            networkUrls,
          },
          comments,
        };
      }, request.params.networkLimit ?? 120);

      const videoIdMatch = url.match(/\/video\/(\d+)/);
      const articleIdMatch = url.match(/\/article\/(\d+)/);
      const videoId = videoIdMatch ? videoIdMatch[1] : 'video';
      const articleId = articleIdMatch ? articleIdMatch[1] : '';

      const canonicalUrl = (() => {
        try {
          if (videoIdMatch) return `https://www.dongchedi.com/video/${videoIdMatch[1]}`;
          if (articleIdMatch) return `https://www.dongchedi.com/article/${articleIdMatch[1]}`;
        } catch {
          /* ignore */
        }
        return url;
      })();

      const data = {
        source: '懂车帝',
        type: articleId ? 'article' : 'video',
        video_id: articleId ? undefined : videoId,
        article_id: articleId || undefined,
        ...extracted,
        url: canonicalUrl,
      };

      const filename = articleId ? `dcd_article_${articleId}.json` : `dcd_video_${videoId}.json`;
      const dataJsonString = JSON.stringify(data, null, 2);
      const jsonBytes = new TextEncoder().encode(dataJsonString);

      const filePath = request.params.outputDir ? `${request.params.outputDir}/${filename}` : filename;
      const {filename: savedPath} = await context.saveFile(jsonBytes, filePath);

      const payload = {
        success: true,
        source: 'dongchedi_video',
        savedFile: savedPath,
        data,
      };

      response.appendResponseLine(JSON.stringify(payload, null, 2));
    });
  },
});
