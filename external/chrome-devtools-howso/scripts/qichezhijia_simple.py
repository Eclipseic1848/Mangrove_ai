#!/usr/bin/env python3
import sys
import json
import asyncio
from datetime import datetime
from mcp.client.stdio import stdio_client
from mcp import ClientSession, StdioServerParameters


def parse(result):
    if not result or not hasattr(result, "content"):
        return None
    for item in result.content:
        if hasattr(item, "text"):
            try:
                return json.loads(item.text)
            except:
                pass
    return None


# ---------------- 作者信息 ----------------
async def extract_author(session):
    js = """() => {
        const user =
          document.querySelector('.post-wrap .fn-cont-left .user');
        if (!user) return null;

        const avatar = user.querySelector('.user-avatar a')?.href || '';
        const uid = avatar.match(/\\/(\\d+)\\//)?.[1] || '';

        const counts = user.querySelectorAll('.count-item strong');

        const profile = {};
        user.querySelectorAll('.user-profile').forEach(p => {
            if (p.textContent.includes('来自')) {
                profile.location =
                  p.querySelector('.profile-text')?.textContent.trim() || '';
            }
            if (p.textContent.includes('注册')) {
                profile.registerTime =
                  p.querySelector('.profile-text')?.textContent.trim() || '';
            }
        });

        return {
            userId: uid,
            userName:
              user.querySelector('.user-name .name')?.textContent.trim() || '',
            postCount: counts[0]?.textContent || '',
            hotCount: counts[1]?.textContent || '',
            replyCount: counts[2]?.textContent || '',
            location: profile.location || '',
            registerTime: profile.registerTime || '',
            cars: Array.from(
              user.querySelectorAll('.profile-cars-item')
            ).map(e => e.textContent.trim())
        };
    }"""
    return parse(await session.call_tool("evaluate_script", {"function": js})) or {}


# ---------------- 主贴 ----------------
async def extract_post(session):
    js = """() => {
        const wrap = document.querySelector('.post-wrap');
        if (!wrap) return null;

        const title =
          wrap.querySelector('h1')?.textContent.trim() || '';

        const time =
          document.body.innerText
          .match(/\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2}/)?.[0] || '';

        const contentRoot =
          wrap.querySelector('[class*="post-content"], .post-container')
          || wrap;

        const parts = [];
        const imgs = [];

        contentRoot.querySelectorAll('p, img').forEach(el => {
            if (el.tagName === 'IMG') {
                let src = el.src || el.getAttribute('data-src') || '';
                if (src.startsWith('//')) src = 'https:' + src;
                if (src.includes('autoimg.cn')) {
                    imgs.push(src);
                    parts.push(`<img src="${src}">`);
                }
            } else {
                const t = el.textContent.trim();
                if (t) parts.push(t);
            }
        });

        return {
            title,
            publish_time: time,
            content: parts.join('\\n'),
            imgList: imgs
        };
    }"""
    return parse(await session.call_tool("evaluate_script", {"function": js})) or {}


# ---------------- 评论 ----------------
async def extract_comments(session):
    js = """() => {
        const out = [];
        document.querySelectorAll('li[class*="reply-floor"]').forEach(li => {
            const user =
              li.querySelector('.user-name .name')?.textContent.trim() || '';
            const time =
              li.innerText.match(
                /\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2}/
              )?.[0] || '';
            const cont =
              li.querySelector('.reply-detail')?.textContent.trim() || '';
            if (user || cont) {
                out.push({ username: user, time, content: cont });
            }
        });
        return out;
    }"""
    return parse(await session.call_tool("evaluate_script", {"function": js})) or []


# ---------------- 主流程 ----------------
async def main():
    url = sys.argv[1]

    server = StdioServerParameters(
        command="npx",
        args=[
            "-y",
            "chrome-devtools-mcp@latest",
            "--browser-url=http://127.0.0.1:9222"
        ]
    )

    async with stdio_client(server) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            await session.call_tool("navigate_page", {
                "type": "url",
                "url": url,
                "timeout": 30000
            })
            await asyncio.sleep(3)

            post = await extract_post(session)
            author = await extract_author(session)
            comments = await extract_comments(session)

            data = {
                "title": post.get("title", ""),
                "publish_time": post.get("publish_time", ""),
                "author": author,
                "content": post.get("content", ""),
                "imgList": post.get("imgList", []),
                "comments": comments,
                "source": "汽车之家",
                "crawl_time": datetime.now().isoformat()
            }

            print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
