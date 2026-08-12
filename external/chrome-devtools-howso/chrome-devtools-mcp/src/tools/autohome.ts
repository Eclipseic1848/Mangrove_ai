/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import {zod} from '../third_party/index.js';

import {ToolCategory} from './categories.js';
import {defineTool} from './ToolDefinition.js';

/**
 * Extract Autohome post data and save as JSON file.
 */
export const extractAutohomePost = defineTool({
  name: 'extract_autohome_post',
  description: `Extract post data from Autohome (汽车之家) forum page and save as JSON file. 
The tool navigates to the URL, extracts post metadata, content, images, and comments, then saves everything to a JSON file.`,
  annotations: {
    category: ToolCategory.DEBUGGING,
    readOnlyHint: false,
  },
  schema: {
    url: zod
      .string()
      .describe('The Autohome forum post URL to extract data from.'),
    outputDir: zod
      .string()
      .optional()
      .describe(
        'The directory path to save the JSON file. If omitted, saves to the current working directory.',
      ),
  },
  handler: async (request, response, context) => {
    const page = context.getSelectedPage();
    const url = request.params.url;

    await context.waitForEventsAfterAction(async () => {
      // Navigate to the URL
      response.appendResponseLine(`Navigating to: ${url}`);
      await page.goto(url, {waitUntil: 'domcontentloaded'});
      await new Promise(resolve => setTimeout(resolve, 3000)); // Wait for page load

      // Extract post ID from URL
      const postIdMatch = url.match(/\/(\d+)-/);
      const postId = postIdMatch ? postIdMatch[1] : 'post';

      // Extract metadata
      response.appendResponseLine('Extracting metadata...');
      const metadata = await page.evaluate(() => {
        const isNewsPage =
          location.hostname.includes('autohome.com.cn') &&
          /^\/news\//.test(location.pathname);
        const result: any = {
          bbs_id: '',
          club_bbs_name: '',
          title: '',
          publish_time: '',
          author_name: '',
        };

        if (isNewsPage) {
          const norm = (s: any) => (s || '').toString().replace(/\s+/g, ' ').trim();
          const pageTitle =
            norm(
              document.querySelector('h1')?.textContent ||
              document.querySelector('.article-title, .news-title, [class*="article-title"], [class*="news-title"]')
                ?.textContent,
            ) || '';
          const metaTitle =
            (document.querySelector('meta[property="og:title"]') as HTMLMetaElement | null)?.content?.trim() ||
            '';
          result.title = pageTitle || metaTitle || norm(document.title);

          const bodyText = norm(document.body?.innerText || '');
          const timeMatch =
            bodyText.match(/(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})/) ||
            bodyText.match(/(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2})/);
          if (timeMatch) {
            result.publish_time = timeMatch[1].replace(/\./g, '-');
          }

          const authorCandidates = Array.from(
            document.querySelectorAll('a, span, div, p'),
          )
            .map(el => norm((el as HTMLElement).innerText || el.textContent || ''))
            .filter(Boolean)
            .filter(text => text.length >= 2 && text.length <= 12)
            .filter(text => !text.includes('汽车之家'))
            .filter(text => !text.includes('原创'))
            .filter(text => !text.includes('关注'))
            .filter(text => !text.includes('评论'))
            .filter(text => !text.includes('收藏'))
            .filter(text => !text.includes('分享'))
            .filter(text => !text.includes('举报'))
            .filter(text => !text.includes('更多'))
            .filter(text => !text.includes('进入主页'));

          const authorFromMeta =
            (document.querySelector('meta[name="author"]') as HTMLMetaElement | null)?.content?.trim() || '';
          result.author_name = authorFromMeta || authorCandidates[0] || '';
          result.club_bbs_name = '新闻';
          return result;
        }

        // Extract bbs_id from URL (will be done in handler)
        // Extract club_bbs_name
        const titleCon = document.querySelector(
          '.athm-bbs-title-con, [class*="athm-bbs-title-con"]',
        );
        if (titleCon) {
          const nameCont = titleCon.querySelector(
            '.name-cont, [class*="name-cont"]',
          );
          if (nameCont) {
            result.club_bbs_name = nameCont.textContent?.trim() || '';
          }
        }

        // Extract title
        const postWrap = document.querySelector(
          '.post-wrap, [class*="post-wrap"]',
        );
        if (postWrap) {
          const postTitle = postWrap.querySelector(
            '.post-title, [class*="post-title"]',
          );
          if (postTitle) {
            result.title = postTitle.textContent?.trim() || '';
          }

          // Extract author_name
          const fnContLeft = postWrap.querySelector(
            '.fn-cont-left, [class*="fn-cont-left"]',
          );
          if (fnContLeft) {
            const userInfo = fnContLeft.querySelector(
              '.user-info, [class*="user-info"]',
            );
            if (userInfo) {
              const nameElem = userInfo.querySelector('.name, [class*="name"]');
              if (nameElem) {
                result.author_name = nameElem.textContent?.trim() || '';
              } else {
                const userLink = userInfo.querySelector('a');
                if (userLink) {
                  result.author_name = userLink.textContent?.trim() || '';
                }
              }
            }
          }
        }

        // New forum thread layout: title/author/time live under `.fn-cont-left > .post`, not `.post-wrap`
        if (!result.title) {
          const titleLink = document.querySelector(
            '.post-title-container .post-title a[title], .post-title-container .post-title a',
          ) as HTMLAnchorElement | null;
          if (titleLink) {
            result.title =
              (titleLink.getAttribute('title') || '').trim() ||
              titleLink.textContent?.trim() ||
              '';
          }
          if (!result.title) {
            const titleEl = document.querySelector('.post-title-container .post-title');
            if (titleEl) {
              result.title = titleEl.textContent?.trim() || '';
            }
          }
        }
        if (!result.author_name) {
          const nameA = document.querySelector(
            '.post-user .user-brief-name a.name, .fn-cont-left .post-user a.name',
          );
          if (nameA) {
            result.author_name = nameA.textContent?.trim() || '';
          }
        }
        if (!result.publish_time) {
          const postInfo = document.querySelector('.post-info');
          if (postInfo) {
            const strongs = postInfo.querySelectorAll('strong');
            for (let i = strongs.length - 1; i >= 0; i--) {
              const t = strongs[i].textContent?.trim() || '';
              const m = t.match(/(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/);
              if (m) {
                result.publish_time = m[1];
                break;
              }
            }
          }
        }
        const w = window as unknown as {__TOPICINFO__?: Record<string, unknown>};
        const ti = w.__TOPICINFO__;
        if (ti && typeof ti === 'object') {
          const normStr = (v: unknown) =>
            v != null ? String(v).replace(/\s+/g, ' ').trim() : '';
          if (!result.title) {
            result.title =
              normStr(ti.topicTitle) || normStr(ti.title) || result.title;
          }
          if (!result.author_name) {
            result.author_name =
              normStr(ti.topicMemberName) ||
              normStr(ti.authorName) ||
              result.author_name;
          }
          if (!result.publish_time) {
            const pt = normStr(ti.publishTime) || normStr(ti.topicPublishTime);
            if (/\d{4}-\d{2}-\d{2}/.test(pt)) {
              result.publish_time = pt;
            }
          }
        }

        // Extract publish_time
        const postHandlePublish = document.querySelector(
          '.post-handle-publish, [class*="post-handle-publish"]',
        );
        if (postHandlePublish) {
          const publishText = postHandlePublish.textContent?.trim() || '';
          const timeMatch = publishText.match(
            /(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/,
          );
          if (timeMatch) {
            result.publish_time = timeMatch[1];
          } else {
            const strongTags = postHandlePublish.querySelectorAll('strong');
            for (let i = strongTags.length - 1; i >= 0; i--) {
              const strongText = strongTags[i].textContent?.trim() || '';
              const timeMatch = strongText.match(
                /(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/,
              );
              if (timeMatch) {
                result.publish_time = timeMatch[1];
                break;
              }
            }
          }
        }

        return result;
      });

      // Extract bbs_id from URL
      const bbsIdMatch = url.match(/\/(\d+)-/);
      if (bbsIdMatch) {
        metadata.bbs_id = bbsIdMatch[1];
      }

      // Extract post content
      response.appendResponseLine('Extracting post content...');
      const postContent = await page.evaluate(() => {
        const isNewsPage =
          location.hostname.includes('autohome.com.cn') &&
          /^\/news\//.test(location.pathname);
        const contentParts: string[] = [];
        const imgList: string[] = [];
        const seenImgs = new Set<string>();

        // Extract image URL helper
        const extractImageUrl = (img: HTMLImageElement): string | null => {
          let imgUrl =
            img.src ||
            img.getAttribute('data-src') ||
            img.getAttribute('data-original') ||
            img.getAttribute('data-lazy-src') ||
            img.getAttribute('data-lazy') ||
            img.getAttribute('data-url') ||
            '';

          if (!imgUrl && img.srcset) {
            const srcsetParts = img.srcset.split(',');
            if (srcsetParts.length > 0) {
              imgUrl = srcsetParts[0].trim().split(' ')[0];
            }
          }

          if (!imgUrl) return null;

          imgUrl = String(imgUrl)
            .trim()
            .replace(/^["']+|["']+$/g, '')
            .replace(/\\/g, '')
            .replace(/"/g, '');

          if (imgUrl.includes('blank.gif')) return null;

          if (imgUrl.startsWith('//')) {
            imgUrl = 'https:' + imgUrl;
          } else if (
            !imgUrl.startsWith('http://') &&
            !imgUrl.startsWith('https://')
          ) {
            return null;
          }

          const normalized = imgUrl.split('?')[0].split('#')[0];
          if (
            normalized &&
            (
              normalized.includes('club2.autoimg.cn') ||
              normalized.includes('club2') ||
              normalized.includes('autoimg.cn') ||
              normalized.includes('autohomeimg.com') ||
              normalized.includes('autohome.com.cn')
            )
          ) {
            return normalized;
          }
          return null;
        };

        // Process image function
        const processImage = (img: HTMLImageElement) => {
          const imgUrl = extractImageUrl(img);
          if (imgUrl && !seenImgs.has(imgUrl)) {
            seenImgs.add(imgUrl);
            imgList.push(imgUrl);
            contentParts.push(`<img src="${imgUrl}">`);
          }
        };

        if (isNewsPage) {
          const norm = (s: any) => (s || '').toString().replace(/\s+/g, ' ').trim();
          const stopTexts = [
            '文章标签',
            '向编辑',
            '作者其他作品',
            '进入主页',
            '相关视频',
            '论坛推荐',
            '大家都在问',
            '扫码下载汽车之家App',
            '© 2026 汽车之家',
          ];
          const ignoreExactTexts = new Set([
            '汽车之家',
            '点赞',
            '评论',
            '收藏',
            '分享',
            '问编辑',
            '关注',
            '更多',
            '举报/纠错',
          ]);

          const articleRoot =
            document.querySelector(
              '.article-content, .news-con, .news-content, .content-detail, .text-con, [class*="article-content"], [class*="news-content"]',
            ) ||
            Array.from(document.querySelectorAll('div, section, article'))
              .find(el => {
                const text = norm((el as HTMLElement).innerText || el.textContent || '');
                if (!text || text.length < 500) return false;
                if (!text.includes('文章标签') && !text.includes('作者其他作品')) return false;
                return true;
              }) ||
            document.body;

          const rootCopy = articleRoot.cloneNode(true) as HTMLElement;
          rootCopy.querySelectorAll(
            [
              'script',
              'style',
              'noscript',
              'iframe',
              'form',
              'button',
              'input',
              '.statement',
              '.related-video',
              '.recommend',
              '.more-article',
              '.ask-edit',
              '.tag-box',
              '.article-tags',
              '.editor-other',
              '.author-other',
              '.forum-recommend',
              '.video-box',
              '.video-wrap',
            ].join(','),
          ).forEach(el => el.remove());

          const orderedParts: string[] = [];
          const seenTexts = new Set<string>();
          let shouldStop = false;

          const pushText = (text: string) => {
            const cleaned = norm(text);
            if (!cleaned) return;
            if (ignoreExactTexts.has(cleaned)) return;
            if (stopTexts.some(t => cleaned.startsWith(t))) {
              shouldStop = true;
              return;
            }
            if (cleaned.length < 2) return;
            if (seenTexts.has(cleaned)) return;
            seenTexts.add(cleaned);
            orderedParts.push(cleaned);
          };

          const blockNodes = Array.from(
            rootCopy.querySelectorAll('p, h2, h3, h4, blockquote, table, ul, ol, img'),
          );
          for (const node of blockNodes) {
            if (shouldStop) break;
            if (node.tagName === 'IMG') {
              const imgUrl = extractImageUrl(node as HTMLImageElement);
              if (imgUrl && !seenImgs.has(imgUrl)) {
                seenImgs.add(imgUrl);
                imgList.push(imgUrl);
                orderedParts.push(`<img src="${imgUrl}">`);
              }
              continue;
            }

            if (node.tagName === 'TABLE') {
              const rows = Array.from(node.querySelectorAll('tr'))
                .map(tr =>
                  Array.from(tr.querySelectorAll('th, td'))
                    .map(td => norm((td as HTMLElement).innerText || td.textContent || ''))
                    .filter(Boolean)
                    .join(' | '),
                )
                .filter(Boolean);
              for (const row of rows) {
                pushText(row);
                if (shouldStop) break;
              }
              continue;
            }

            const text = norm((node as HTMLElement).innerText || node.textContent || '');
            pushText(text);
          }

          if (orderedParts.length === 0) {
            const bodyText = norm(rootCopy.innerText || rootCopy.textContent || '');
            const lines = bodyText
              .split(/(?=汽车之家)/)
              .map((s: string) => norm(s))
              .filter(Boolean);
            for (const line of lines) {
              pushText(line);
              if (shouldStop) break;
            }
          }

          if (imgList.length === 0) {
            articleRoot.querySelectorAll('img').forEach(img => {
              processImage(img as HTMLImageElement);
            });
          }

          return {
            content: orderedParts.join('\n'),
            imgList,
          };
        }

        // ===== Forum main post (new layout): `.tz-paragraph` / `.tz-picture` live in `.post-container`
        // above `#js-reply-list-container`. `data-floor="1"` inside the reply list is the first reply (沙发), not the topic.
        const replyListEl = document.getElementById('js-reply-list-container');
        const topicPostContainer = Array.from(
          document.querySelectorAll('.post-container'),
        ).find(
          el =>
            !(replyListEl && replyListEl.contains(el)) &&
            (el.querySelector('.tz-paragraph') || el.querySelector('.tz-picture')),
        ) as HTMLElement | null;

        if (topicPostContainer) {
          const norm = (s: any) => (s || '').toString().replace(/\s+/g, ' ').trim();
          const blocks = Array.from(
            topicPostContainer.querySelectorAll('.tz-paragraph, .tz-picture'),
          ) as HTMLElement[];

          for (const b of blocks) {
            if (b.classList?.contains('tz-picture')) {
              const img = b.querySelector('img') as HTMLImageElement | null;
              if (img) processImage(img);
              continue;
            }
            const text = norm(b.innerText || b.textContent || '');
            if (text) contentParts.push(text);
          }

          if (contentParts.length === 0) {
            const copy = topicPostContainer.cloneNode(true) as HTMLElement;
            copy.querySelectorAll('script, style, noscript').forEach(el => el.remove());
            copy.querySelectorAll('img').forEach(img => img.remove());
            const text = norm(copy.innerText || copy.textContent || '');
            if (text) contentParts.push(text);
            topicPostContainer.querySelectorAll('.tz-picture img').forEach(img => {
              processImage(img as HTMLImageElement);
            });
          }

          return {content: contentParts.join('\n'), imgList};
        }

        // Find post-wrap element (old layout)
        const postWrap = document.querySelector(
          'div.post-wrap, [class*="post-wrap"]',
        );
        if (!postWrap) {
          return {content: '', imgList: []};
        }

        // Get fn-cont-right
        const fnContRight = postWrap.querySelector(
          '.fn-cont-right, [class*="fn-cont-right"]',
        );
        if (!fnContRight) {
          return {content: '', imgList: []};
        }

        // Get post
        const post = fnContRight.querySelector('.post, [class*="post"]');
        if (!post) {
          return {content: '', imgList: []};
        }

        // Get post-container
        const postContainer = post.querySelector(
          '.post-container, [class*="post-container"]',
        );
        if (!postContainer) {
          return {content: '', imgList: []};
        }

        // Find all editor-paragraph and editor-image elements
        const allElements = Array.from(postContainer.children).filter(elem => {
          const classes = elem.className || '';
          const classStr = Array.isArray(classes)
            ? classes.join(' ')
            : String(classes);
          return (
            classStr.includes('editor-paragraph') ||
            classStr.includes('editor-image')
          );
        });

        // Process each element
        allElements.forEach(elem => {
          const classes = elem.className || '';
          const classStr = Array.isArray(classes)
            ? classes.join(' ')
            : String(classes);

          // Process editor-paragraph
          if (classStr.includes('editor-paragraph')) {
            const paraCopy = elem.cloneNode(true) as HTMLElement;
            const imgs = paraCopy.querySelectorAll('img');
            imgs.forEach(img => img.remove());

            const text = paraCopy.textContent?.trim() || '';
            if (text && text.length > 0) {
              contentParts.push(text);
            }

            elem.querySelectorAll('img').forEach(img => {
              processImage(img as HTMLImageElement);
            });
          }
          // Process editor-image
          else if (classStr.includes('editor-image')) {
            elem.querySelectorAll('img').forEach(img => {
              processImage(img as HTMLImageElement);
            });
            elem.querySelectorAll('[data-src]').forEach(dataElem => {
              const img =
                dataElem.tagName === 'IMG'
                  ? dataElem
                  : dataElem.querySelector('img');
              if (img) processImage(img as HTMLImageElement);
            });
          }
        });

        // Fallback: if no content found
        if (contentParts.length === 0) {
          const containerCopy = postContainer.cloneNode(true) as HTMLElement;
          const imgs = containerCopy.querySelectorAll('img');
          imgs.forEach(img => img.remove());
          const text = containerCopy.textContent?.trim() || '';
          if (text) {
            contentParts.push(text);
          }
          postContainer.querySelectorAll('img').forEach(img => {
            processImage(img as HTMLImageElement);
          });
        }

        // Normalize image URLs
        const normalizedImgList = imgList.filter(imgUrl => {
          if (!imgUrl) return false;
          const normalized = String(imgUrl)
            .trim()
            .replace(/\\"/g, '')
            .replace(/\\/g, '')
            .replace(/^["']+|["']+$/g, '');
          if (normalized.startsWith('//')) {
            return true;
          }
          if (!normalized.startsWith('http')) {
            return false;
          }
          return normalized.includes('club2.autoimg.cn') || normalized.includes('club2');
        }).map(imgUrl => {
          const normalized = String(imgUrl)
            .trim()
            .replace(/\\"/g, '')
            .replace(/\\/g, '')
            .replace(/^["']+|["']+$/g, '');
          if (normalized.startsWith('//')) {
            return 'https:' + normalized;
          }
          return normalized;
        });

        // Normalize content images
        let normalizedContent = contentParts.join('\n');
        normalizedContent = normalizedContent.replace(
          /<img[^>]+>/g,
          match => {
            const srcMatch = match.match(/src=["']([^"']+)["']/);
            if (srcMatch) {
              let imgUrl = srcMatch[1]
                .replace(/\\"/g, '')
                .replace(/\\/g, '')
                .replace(/^["']+|["']+$/g, '');
              if (imgUrl.startsWith('//')) {
                imgUrl = 'https:' + imgUrl;
              } else if (!imgUrl.startsWith('http')) {
                return match;
              }
              if (
                imgUrl.includes('club2.autoimg.cn') ||
                imgUrl.includes('club2')
              ) {
                return `<img src="${imgUrl}">`;
              }
            }
            return match;
          },
        );

        return {
          content: normalizedContent,
          imgList: normalizedImgList,
        };
      });

      const isNewsPage =
        url.includes('autohome.com.cn/news/') ||
        /\/news\//.test(new URL(url).pathname);

      // Scroll page to load all comments
      response.appendResponseLine('Scrolling page to load all comments...');
      await page.evaluate(async () => {
        const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
        let lastHeight = 0;
        let stableCount = 0;
        const maxLoops = 30;
        const stableThreshold = 3;
        
        for (let i = 0; i < maxLoops; i++) {
          // Scroll down
          window.scrollBy(0, Math.max(800, Math.floor(window.innerHeight * 0.9)));
          await sleep(300);
          
          // Check if page height changed
          const currentHeight = document.documentElement.scrollHeight || document.body.scrollHeight;
          const scrollY = window.scrollY + window.innerHeight;
          const nearBottom = scrollY >= currentHeight - 500;
          
          if (currentHeight === lastHeight) {
            stableCount++;
            if (stableCount >= stableThreshold && nearBottom) {
              break;
            }
          } else {
            stableCount = 0;
          }
          
          lastHeight = currentHeight;
        }
        
        // Scroll back to top
        window.scrollTo(0, 0);
        await sleep(500);
      });
      response.appendResponseLine('✓ Page scrolling completed');

      // Extract comments
      response.appendResponseLine('Extracting comments...');
      const comments = await page.evaluate((isNewsPage: boolean) => {
        const comments: any[] = [];

        if (isNewsPage) {
          return comments;
        }

        // Find reply-wrap container
        const fnContainer = document.querySelector(
          '.fn-container.fn-main, [class*="fn-container"][class*="fn-main"]',
        );
        if (!fnContainer) {
          return comments;
        }

        const replyWrap = fnContainer.querySelector(
          '#js-reply-list-container.reply-wrap, #js-reply-list-container[class*="reply-wrap"]',
        );
        if (!replyWrap) {
          return comments;
        }

        // Find all floor containers
        const floorContainers = replyWrap.querySelectorAll(
          '[class*="js-reply-floor-container"]',
        );

        floorContainers.forEach(container => {
          // Extract username
          const leftElem = container.querySelector(
            '.fn-cont-left, [class*="fn-cont-left"]',
          );
          let username = '';

          if (leftElem) {
            // 方法1：从js-user-info-container -> user -> user-info获取
            const userInfoContainer = leftElem.querySelector(
              '.js-user-info-container, [class*="js-user-info-container"]',
            );
            if (userInfoContainer) {
              const userFold = userInfoContainer.querySelector(
                '.user.fold, .user[class*="fold"], [class*="user"][class*="fold"]',
              );
              const user =
                userFold ||
                userInfoContainer.querySelector('.user, [class*="user"]');

              if (user) {
                const userInfo = user.querySelector(
                  '.user-info, [class*="user-info"]',
                );
                if (userInfo) {
                  const userNameDiv = userInfo.querySelector(
                    '.user-name, [class*="user-name"]',
                  );
                  if (userNameDiv) {
                    const nameElem = userNameDiv.querySelector(
                      '.name, [class*="name"]',
                    );
                    if (nameElem) {
                      username = nameElem.textContent?.trim() || '';
                    } else {
                      username = userNameDiv.textContent?.trim() || '';
                    }
                  }
                }
              }
            }

            // 方法2：如果方法1失败，查找所有包含用户主页链接的a标签
            if (!username) {
              const userLinks = leftElem.querySelectorAll(
                'a[href*="home.html"], a[href*="user"], a.name, a[class*="name"]',
              );
              for (let i = 0; i < userLinks.length && !username; i++) {
                const link = userLinks[i] as HTMLAnchorElement;
                const linkText = link.textContent?.trim() || '';
                const href = link.getAttribute('href') || '';
                if (
                  linkText &&
                  linkText.length > 0 &&
                  linkText.length < 50 &&
                  !linkText.includes('http') &&
                  !linkText.includes('展开') &&
                  !linkText.includes('回复') &&
                  !link.closest('.user-avatar') &&
                  !link.closest('.user-handle') &&
                  !link.closest('.user-about-count') &&
                  !link.closest('.user-profile')
                ) {
                  username = linkText;
                }
              }
            }
          }

          // 新版回复楼层：无 `.fn-cont-left` / `.fn-cont-right`，用户信息与 `.reply` 直接在 `li` 下
          if (!username) {
            const nameLink = container.querySelector(
              '.user-brief-name a.name, .user-info-line .user-brief a.name, .js-user-info-container a.name',
            );
            if (nameLink) {
              username = nameLink.textContent?.trim() || '';
            }
          }
          if (!username) {
            const userLinks = container.querySelectorAll(
              'a[href*="home.html"], a[href*="user"], a.name, a[class*="name"]',
            );
            for (let i = 0; i < userLinks.length && !username; i++) {
              const link = userLinks[i] as HTMLAnchorElement;
              const linkText = link.textContent?.trim() || '';
              if (
                linkText &&
                linkText.length > 0 &&
                linkText.length < 50 &&
                !linkText.includes('http') &&
                !linkText.includes('展开') &&
                !linkText.includes('回复') &&
                !link.closest('.user-avatar') &&
                !link.closest('.user-handle') &&
                !link.closest('.user-about-count') &&
                !link.closest('.user-profile')
              ) {
                username = linkText;
              }
            }
          }

          const rightElem = container.querySelector(
            '.fn-cont-right, [class*="fn-cont-right"]',
          );
          const replyRoot = rightElem || container;
          const reply = replyRoot.querySelector('.reply, [class*="reply"]');
          if (!reply) {
            return;
          }

          // Extract time
          let time = '';
          if (reply) {
            const replyTop = reply.querySelector(
              '.reply-top, [class*="reply-top"]',
            );
            if (replyTop) {
              const replyStaticTexts = replyTop.querySelectorAll(
                '.reply-static-text.fn-fl, [class*="reply-static-text"][class*="fn-fl"]',
              );
              for (let i = 0; i < replyStaticTexts.length; i++) {
                const replyStaticText = replyStaticTexts[i];
                const strongTags = replyStaticText.querySelectorAll('strong');
                for (let j = strongTags.length - 1; j >= 0; j--) {
                  const strongText = strongTags[j].textContent?.trim() || '';
                  const timeMatch = strongText.match(
                    /(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/,
                  );
                  if (timeMatch) {
                    time = timeMatch[1];
                    break;
                  }
                }
                if (time) break;
              }
            }
          }

          // Extract comment text
          let commentText = '';
          if (reply) {
            const replyDetail = reply.querySelector(
              '.reply-detail, [class*="reply-detail"]',
            );
            if (replyDetail) {
              const detailCopy = replyDetail.cloneNode(true) as HTMLElement;
              const unfoldComments = detailCopy.querySelectorAll(
                '.unfold-comment, [class*="unfold-comment"]',
              );
              unfoldComments.forEach(elem => elem.remove());
              commentText = detailCopy.textContent?.trim() || '';
            }
          }

          // Fallback: extract time from reply-bottom
          if (!time && reply) {
            const replyBottom = reply.querySelector(
              '.reply-bottom, [class*="reply-bottom"]',
            );
            if (replyBottom) {
              const replyBottomLast = replyBottom.querySelector(
                '.reply-bottom-last, [class*="reply-bottom-last"]',
              );
              const timeContainer = replyBottomLast || replyBottom;
              const timeText = timeContainer.textContent?.trim() || '';
              const timeMatch = timeText.match(
                /(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/,
              );
              if (timeMatch) {
                time = timeMatch[1];
              }
            }
          }

          // Extract replies
          const replies: any[] = [];
          if (reply) {
            const replySubWrap = reply.querySelector(
              '.reply-sub-wrap, [class*="reply-sub-wrap"]',
            );
            if (replySubWrap) {
              const replySubItems = replySubWrap.querySelectorAll(
                '[class*="reply-sub"], .reply-sub-item, [class*="reply-comment"]',
              );

              replySubItems.forEach(replySubItem => {
                // Extract reply username
                let replyUsername = '';
                const replySubUser = replySubItem.querySelector(
                  '.reply-sub-user, [class*="reply-sub-user"]',
                );
                if (replySubUser) {
                  const nameElem = replySubUser.querySelector(
                    '.name, [class*="name"]',
                  );
                  if (nameElem) {
                    replyUsername = nameElem.textContent?.trim() || '';
                  }
                }

                // Extract reply content
                let replyContent = '';
                const replySubCont = replySubItem.querySelector(
                  '.reply-sub-cont, [class*="reply-sub-cont"]',
                );
                if (replySubCont) {
                  const contCopy = replySubCont.cloneNode(true) as HTMLElement;
                  const unfoldComments = contCopy.querySelectorAll(
                    '.unfold-comment, [class*="unfold-comment"]',
                  );
                  unfoldComments.forEach(elem => elem.remove());
                  const imgs = contCopy.querySelectorAll('img');
                  imgs.forEach(img => img.remove());
                  replyContent = contCopy.textContent?.trim() || '';
                  replyContent = replyContent.replace(
                    /展开评论\s*[>＞]\s*/g,
                    '',
                  );
                  const lines = replyContent.split('\n');
                  const cleanedLines: string[] = [];
                  const seen = new Set<string>();
                  for (let i = 0; i < lines.length; i++) {
                    const line = lines[i].trim();
                    if (line && !line.includes('展开评论')) {
                      if (!seen.has(line)) {
                        cleanedLines.push(line);
                        seen.add(line);
                      }
                    }
                  }
                  replyContent = cleanedLines.join('\n').trim();
                  replyContent = replyContent.replace(/\n\s*\n/g, '\n');
                  replyContent = replyContent.replace(/\s+/g, ' ').trim();
                }

                // Extract reply time
                let replyTime = '';
                const replySubHandle = replySubItem.querySelector(
                  '.reply-sub-handle, [class*="reply-sub-handle"]',
                );
                if (replySubHandle) {
                  const handleTime = replySubHandle.textContent?.trim() || '';
                  const timeMatch = handleTime.match(
                    /(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/,
                  );
                  if (timeMatch) {
                    replyTime = timeMatch[1];
                  }
                }

                if (replyUsername || replyContent) {
                  replies.push({
                    username: replyUsername,
                    content: replyContent,
                    time: replyTime,
                  });
                }
              });
            }
          }

          if (username || commentText || replies.length > 0) {
            comments.push({
              username: username,
              time: time,
              content: commentText,
              replies: replies,
            });
          }
        });

        return comments;
      }, isNewsPage);

      // Prepare output data（业务数据部分）
      const data = {
        source: '汽车之家',
        bbs_id: metadata.bbs_id,
        club_bbs_name: metadata.club_bbs_name,
        title: metadata.title,
        publish_time: metadata.publish_time,
        author_name: metadata.author_name,
        content: postContent.content || '',
        imgList: postContent.imgList || [],
        comments: comments || [],
      };

      // Generate filename (简化格式，只使用postId)
      const filename = `autohome_${postId}.json`;

      // 业务数据 JSON 字符串（写文件和作为返回值用）
      const dataJsonString = JSON.stringify(data, null, 2);
      const jsonBytes = new TextEncoder().encode(dataJsonString);

      // Save file
      const filePath = request.params.outputDir
        ? `${request.params.outputDir}/${filename}`
        : filename;
      const {filename: savedPath} = await context.saveFile(jsonBytes, filePath);

      response.appendResponseLine(`✓ JSON file saved: ${savedPath}`);
      
      // 直接返回业务数据 JSON 字符串，便于上层作为字符串使用
      response.appendResponseLine(dataJsonString);
    });
  },
});

/**
 * Extract Autohome Chejiahao (车家号) "info" page data and save as JSON file.
 *
 * Example: https://chejiahao.autohome.com.cn/info/25061145
 *
 * Main fields:
 * - video real URL (best-effort: DOM currentSrc + network sniffing)
 * - main body text (intro/description)
 * - comments (best-effort: DOM extraction after scrolling)
 */
export const extractAutohomeChejiahaoInfo = defineTool({
  name: 'extract_autohome_chejiahao_info',
  description: `Extract data from Autohome Chejiahao (车家号) info page and save as JSON file.
Includes best-effort video real URL(s), main text/intro, and comments.`,
  annotations: {
    category: ToolCategory.DEBUGGING,
    readOnlyHint: false,
  },
  schema: {
    url: zod
      .string()
      .describe('The Chejiahao info URL to extract data from (e.g. https://chejiahao.autohome.com.cn/info/25061145).'),
    outputDir: zod
      .string()
      .optional()
      .describe(
        'The directory path to save the JSON file. If omitted, saves to the current working directory.',
      ),
    initialWaitMs: zod
      .number()
      .int()
      .optional()
      .default(4000)
      .describe('Initial wait after navigation to allow page scripts to load. Default 4000ms.'),
    scrollLoops: zod
      .number()
      .int()
      .optional()
      .default(18)
      .describe('How many scroll loops to try for loading comments. Default 18.'),
    networkLimit: zod
      .number()
      .int()
      .optional()
      .default(80)
      .describe('How many recent network-hook requests to inspect for video URLs. Default 80.'),
  },
  handler: async (request, response, context) => {
    const page = context.getSelectedPage();
    const url = request.params.url;

    await context.waitForEventsAfterAction(async () => {
      response.appendResponseLine(`Navigating to: ${url}`);
      await page.evaluateOnNewDocument(() => {
        (function () {
          const w = window as any;
          if (w.__autohome_chejiahao_hooked__) return;
          w.__autohome_chejiahao_hooked__ = true;
          w.__autohome_chejiahao_requests__ = [];

          function shouldRecord(u: string) {
            if (!u) return false;
            if (u.startsWith('blob:')) return false;
            const url = u.toLowerCase();
            return (
              url.includes('.m3u8') ||
              url.includes('.mp4') ||
              url.includes('.flv') ||
              url.includes('/m3u8') ||
              url.includes('vod') ||
              url.includes('video') ||
              url.includes('autohome')
            );
          }

          function normUrl(u: any) {
            if (!u) return '';
            let s = String(u).trim().replace(/&amp;/g, '&');
            if (s.startsWith('//')) s = 'https:' + s;
            return s;
          }

          function record(u: any, method?: string, extra?: any) {
            const url = normUrl(u);
            if (!shouldRecord(url)) return;
            w.__autohome_chejiahao_requests__.push({
              url,
              method: method || 'GET',
              time: Date.now(),
              referer: document.referrer || location.href,
              extra: extra || null,
            });
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
            (this as any)._ah_method = method;
            (this as any)._ah_url = u;
            return _open.apply(this, arguments as any);
          };
          const _send = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.send = function (body?: Document | XMLHttpRequestBodyInit | null) {
            try {
              record((this as any)._ah_url, (this as any)._ah_method || 'GET', {type: 'xhr'});
            } catch {}
            return _send.call(this, body);
          };
        })();
      });

      await page.goto(url, {waitUntil: 'domcontentloaded'});

      if ((request.params.initialWaitMs ?? 0) > 0) {
        await new Promise(resolve => setTimeout(resolve, request.params.initialWaitMs));
      }

      const infoIdMatch = url.match(/\/info\/(\d+)/);
      const vIdMatch = url.match(/\/v-(\d+)\.html/i);
      const infoId = infoIdMatch ? infoIdMatch[1] : (vIdMatch ? vIdMatch[1] : 'info');

      // Best-effort: try to trigger video loading/playback.
      try {
        await page.evaluate(async () => {
          const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
          // Try click on the first video or play-ish element
          const video = document.querySelector('video') as HTMLVideoElement | null;
          if (video) {
            try {
              video.muted = true;
              // Some sites require a user gesture; click before play.
              (video as any).click?.();
              await sleep(120);
              await video.play().catch(() => {});
            } catch {}
          }

          // Try to click common play buttons (best-effort selectors)
          const playCandidates = Array.from(document.querySelectorAll('button, div, a'))
            .filter(el => {
              const cls = (el as HTMLElement).className || '';
              const t = ((el as HTMLElement).innerText || '').trim();
              const s = `${cls} ${t}`.toLowerCase();
              return (
                s.includes('play') ||
                s.includes('播放') ||
                s.includes('icon-play') ||
                s.includes('video')
              );
            })
            .slice(0, 8) as HTMLElement[];
          for (const el of playCandidates) {
            try {
              el.click();
              await sleep(120);
            } catch {}
          }

          await sleep(600);
        });
      } catch {}

      // Scroll to load comments (best-effort).
      await page.evaluate(async (loops: number) => {
        const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
        let lastH = 0;
        let stable = 0;
        for (let i = 0; i < loops; i++) {
          window.scrollBy(0, Math.max(900, Math.floor(window.innerHeight * 0.9)));
          await sleep(260);
          const h = document.documentElement.scrollHeight || document.body.scrollHeight || 0;
          if (h === lastH) stable++;
          else stable = 0;
          lastH = h;
          if (stable >= 4) break;
        }
        // Keep near comment area (do not scroll to top here)
      }, request.params.scrollLoops ?? 18);

      // Extract data from DOM and network hook.
      const extracted = await page.evaluate((networkLimit: number) => {
        const w = window as any;
        const norm = (s: any) => (s || '').toString().replace(/\s+/g, ' ').trim();

        const getMeta = (nameOrProp: string) => {
          const byName = document.querySelector(`meta[name="${nameOrProp}"]`) as HTMLMetaElement | null;
          if (byName?.content) return byName.content.trim();
          const byProp = document.querySelector(`meta[property="${nameOrProp}"]`) as HTMLMetaElement | null;
          if (byProp?.content) return byProp.content.trim();
          return '';
        };

        const title =
          norm(document.querySelector('h1')?.textContent) ||
          getMeta('og:title') ||
          getMeta('twitter:title') ||
          norm(document.title);

        const publish_time = (() => {
          const text = norm(document.body?.innerText || '');
          // Prefer full datetime
          const m = text.match(/(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)/);
          if (m) return m[1];
          const m2 = text.match(/(\d{4}\/\d{1,2}\/\d{1,2}\s+\d{1,2}:\d{2})/);
          if (m2) return m2[1];
          return getMeta('article:published_time') || getMeta('og:updated_time') || '';
        })();

        // Author block: try structured areas before falling back to generic headings.
        let author_name = '';
        const authorCandidates = Array.from(
          document.querySelectorAll(
            'a[href*="chejiahao.autohome.com.cn/u/"], a[href*="/author/"], .author-name, [class*="author-name"], .user-name, [class*="user-name"]',
          ),
        ).map(el => norm((el as HTMLElement).innerText || el.textContent || ''));
        for (const a of authorCandidates) {
          if (a && a.length <= 24 && !a.includes('进入主页') && !a.includes('关注')) {
            author_name = a;
            break;
          }
        }
        if (!author_name) {
          const headings = Array.from(document.querySelectorAll('h2, h3, h4')).map(h => norm(h.textContent));
          for (const h of headings) {
            if (h && h.length > 0 && h.length <= 30 && !h.includes('相关推荐') && !h.includes('热门内容')) {
              author_name = h;
              break;
            }
          }
        }
        if (!author_name) {
          // fallback: first strong-ish link text
          const a = document.querySelector('a[href*="chejiahao.autohome.com.cn"]') as HTMLAnchorElement | null;
          author_name = norm(a?.textContent) || '';
        }

        // Full article content: prefer article/body container, keep full正文 instead of only简介.
        const bodyText = (document.body?.innerText || '').toString();
        const stopTokens = [
          '本内容来自汽车之家创作者',
          '文章标签',
          '车系标签',
          '点击进入',
          '点赞',
          '评论',
          '收藏',
          '分享',
          '举报/纠错',
          '文中提及',
          '本文作者',
          '作者其他作品',
          '进入主页',
          '热门内容',
          '精彩视频',
          '热门文章标签',
          '扫码下载汽车之家App',
        ];
        let content = '';
        const contentRoot =
          document.querySelector(
            'article, .article-content, .news-con, .content-detail, .detail-content, .pgc-details, [class*="article-content"], [class*="detail-content"]',
          ) ||
          Array.from(document.querySelectorAll('div, section'))
            .find(el => {
              const text = norm((el as HTMLElement).innerText || el.textContent || '');
              if (!text || text.length < 500) return false;
              return text.includes('本内容来自汽车之家创作者') || text.includes('文章标签');
            }) ||
          null;

        if (contentRoot) {
          const rootCopy = contentRoot.cloneNode(true) as HTMLElement;
          rootCopy.querySelectorAll('script, style, noscript, iframe, form, button, input').forEach(el => el.remove());

          const blocks = Array.from(rootCopy.querySelectorAll('p, h2, h3, h4, li, blockquote, div'))
            .map(el => norm((el as HTMLElement).innerText || el.textContent || ''))
            .filter(Boolean);
          const uniqueBlocks: string[] = [];
          const seen = new Set<string>();
          for (const block of blocks) {
            if (stopTokens.some(t => block.startsWith(t))) break;
            if (block.length < 2) continue;
            if (seen.has(block)) continue;
            seen.add(block);
            uniqueBlocks.push(block);
          }
          content = uniqueBlocks.join('\n');
        }

        if (!content) {
          let fallback = bodyText;
          const titleText = title ? title.replace(/_车家号_发现车生活_汽车之家$/, '').trim() : '';
          const titlePos = titleText ? fallback.indexOf(titleText) : -1;
          if (titlePos >= 0) {
            fallback = fallback.slice(titlePos + titleText.length);
          }
          const introPos = fallback.indexOf('简介：');
          if (introPos >= 0) {
            fallback = fallback.slice(introPos + '简介：'.length);
          }
          let stopPos = fallback.length;
          for (const t of stopTokens) {
            const p = fallback.indexOf(t);
            if (p >= 0) stopPos = Math.min(stopPos, p);
          }
          content = norm(fallback.slice(0, stopPos));
        }
        if (!content) {
          content =
            getMeta('description') ||
            getMeta('og:description') ||
            getMeta('twitter:description') ||
            '';
        }

        // Video URLs from DOM
        const video = document.querySelector('video') as HTMLVideoElement | null;
        const domVideoUrls: string[] = [];
        const pushUrl = (u: any) => {
          if (!u) return;
          let s = String(u).trim().replace(/&amp;/g, '&');
          if (!s) return;
          if (s.startsWith('blob:')) return;
          if (s.startsWith('//')) s = 'https:' + s;
          if (!/^https?:\/\//i.test(s)) return;
          if (!domVideoUrls.includes(s)) domVideoUrls.push(s);
        };
        if (video) {
          pushUrl((video as any).currentSrc);
          pushUrl((video as any).src);
          const sources = Array.from(video.querySelectorAll('source'));
          for (const s of sources) {
            pushUrl((s as any).src || s.getAttribute('src'));
          }
        }

        // Video URLs from network hook
        const reqs: any[] = Array.isArray(w.__autohome_chejiahao_requests__)
          ? w.__autohome_chejiahao_requests__.slice(-Math.max(10, networkLimit))
          : [];
        const networkVideoUrls: string[] = [];
        for (const r of reqs) {
          const u = (r && r.url) ? String(r.url) : '';
          if (!u) continue;
          const lower = u.toLowerCase();
          if (
            !(
              lower.includes('.mp4') ||
              lower.includes('.m3u8') ||
              lower.includes('.flv') ||
              (lower.includes('autohome') && lower.includes('video'))
            )
          ) continue;
          if (!networkVideoUrls.includes(u)) networkVideoUrls.push(u);
        }

        const metaVideoUrls = [
          getMeta('og:video'),
          getMeta('og:video:url'),
          getMeta('og:video:secure_url'),
          getMeta('twitter:player'),
        ]
          .map(u => norm(u))
          .filter(Boolean);

        const perfVideoUrls: string[] = [];
        try {
          const entries = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
          for (const e of entries) {
            const name = String(e?.name || '').trim();
            const lower = name.toLowerCase();
            if (!name) continue;
            if (
              lower.includes('.mp4') ||
              lower.includes('.m3u8') ||
              lower.includes('.flv') ||
              (lower.includes('autohome') && lower.includes('video'))
            ) {
              if (!perfVideoUrls.includes(name)) perfVideoUrls.push(name);
            }
          }
        } catch {}

        // Choose a "best" video url (prefer m3u8 then mp4, prefer longer url which often contains tokens)
        const all = [...domVideoUrls, ...networkVideoUrls, ...perfVideoUrls, ...metaVideoUrls];
        const uniqAll = all.filter((u, i) => all.indexOf(u) === i);
        const scored = uniqAll
          .map(u => {
            const lower = u.toLowerCase();
            let score = u.length;
            if (lower.includes('.m3u8')) score += 2000;
            if (lower.includes('.mp4')) score += 1500;
            if (lower.includes('token') || lower.includes('auth') || lower.includes('expires')) score += 300;
            if (lower.includes('preview') || lower.includes('thumb')) score -= 500;
            return {u, score};
          })
          .sort((a, b) => b.score - a.score);
        const bestVideoUrl = scored.length ? scored[0].u : '';

        // Comments (best-effort): look for elements with class name containing "comment"
        const comments: Array<{username: string; time: string; content: string; replies: any[]}> = [];
        const commentNodes = Array.from(document.querySelectorAll('[class*="comment"], [id*="comment"]'))
          .slice(0, 600) as HTMLElement[];

        const isLikelyComment = (el: HTMLElement) => {
          const text = norm(el.innerText || el.textContent || '');
          if (!text) return false;
          if (text.length < 3) return false;
          // Avoid nav/footer blocks
          if (text.includes('扫码下载') || text.includes('京ICP备') || text.includes('隐私协议')) return false;
          // Must contain some human-like content length
          return text.length <= 1200;
        };

        for (const el of commentNodes) {
          if (!isLikelyComment(el)) continue;
          const text = norm(el.innerText || el.textContent || '');

          // Heuristic: time pattern
          const tm =
            text.match(/(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)/) ||
            text.match(/(\d{2}-\d{2}\s+\d{2}:\d{2})/);
          const time = tm ? tm[1] : '';

          // Username heuristic: first short line
          const lines = (el.innerText || el.textContent || '').toString().split('\n').map(l => norm(l)).filter(Boolean);
          let username = '';
          for (const l of lines.slice(0, 6)) {
            if (l.length >= 2 && l.length <= 20 && !l.includes('评论') && !l.includes('全部')) {
              username = l;
              break;
            }
          }

          // Content heuristic: the longest line without obvious labels
          let content = '';
          const candidateLines = lines
            .filter(l => l.length >= 2)
            .filter(l => !l.includes('回复') && !l.includes('点赞') && !l.includes('展开') && !l.includes('收起'));
          if (candidateLines.length) {
            content = candidateLines.sort((a, b) => b.length - a.length)[0];
          } else {
            content = text;
          }

          // Dedupe by (username+content)
          const key = `${username}::${content}`;
          if (comments.some(c => `${c.username}::${c.content}` === key)) continue;

          // Drop huge non-comment blocks
          if (content.length > 500) continue;

          if (content && content.length >= 2) {
            comments.push({username, time, content, replies: []});
          }
        }

        return {
          url: location.href,
          title,
          publish_time,
          author_name,
          content,
          video: {
            bestUrl: bestVideoUrl,
            domUrls: domVideoUrls,
            networkUrls: networkVideoUrls,
            perfUrls: perfVideoUrls,
            metaUrls: metaVideoUrls,
          },
          comments,
        };
      }, request.params.networkLimit ?? 80);

      const data = {
        source: '汽车之家-车家号',
        type: 'chejiahao_info',
        info_id: infoId,
        ...extracted,
      };

      const filename = `autohome_chejiahao_${infoId}.json`;
      const dataJsonString = JSON.stringify(data, null, 2);
      const jsonBytes = new TextEncoder().encode(dataJsonString);

      const filePath = request.params.outputDir
        ? `${request.params.outputDir}/${filename}`
        : filename;
      const {filename: savedPath} = await context.saveFile(jsonBytes, filePath);

      const payload = {
        success: true,
        source: 'autohome_chejiahao',
        savedFile: savedPath,
        data,
      };

      response.appendResponseLine(JSON.stringify(payload, null, 2));
    });
  },
});
