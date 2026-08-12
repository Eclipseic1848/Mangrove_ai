#!/usr/bin/env python3
"""
汽车之家主贴提取工具（精简版）
从URL提取帖子正文、图片和评论数据，直接保存为JSON
"""

import sys
import json
import asyncio
import os
import re
import traceback
from pathlib import Path
from datetime import datetime

try:
    from mcp.client.stdio import stdio_client
    from mcp import ClientSession, StdioServerParameters
except ImportError:
    print("❌ 错误: 需要安装 mcp 库")
    print("   请运行: pip install mcp")
    sys.exit(1)


def normalize_image_urls(img_list):
    """标准化图片URL列表，确保所有URL都以https://开头"""
    normalized_list = []
    for img_url in img_list:
        if not img_url:
            continue
        img_url = str(img_url).strip().replace('\\"', '').replace('\\', '').strip('"').strip("'")
        if img_url.startswith('//'):
            img_url = 'https:' + img_url
        elif not img_url.startswith('http'):
            continue
        if 'club2.autoimg.cn' in img_url or 'club2' in img_url:
            normalized_list.append(img_url)
    return normalized_list


def normalize_content_images(content):
    """标准化content中的图片URL"""
    if not content:
        return content
    
    def replace_img_url(match):
        img_tag = match.group(0)
        src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
        if src_match:
            img_url = src_match.group(1)
            img_url = img_url.replace('\\"', '').replace('\\', '').strip('"').strip("'")
            if img_url.startswith('//'):
                img_url = 'https:' + img_url
            elif not img_url.startswith('http'):
                return img_tag
            if 'club2.autoimg.cn' in img_url or 'club2' in img_url:
                return f'<img src="{img_url}">'
        return img_tag
    content = re.sub(r'<img[^>]+>', replace_img_url, content)
    return content


def parse_json_result(result):
    """解析MCP返回的JSON结果"""
    if not result:
        return None
    
    if hasattr(result, 'content'):
        result_content = result.content if hasattr(result, 'content') else []
        for item in result_content:
            text = None
            if hasattr(item, 'type') and item.type == 'text':
                text = item.text
            
            if text:
                if '```json' in text:
                    json_start = text.find('```json') + 7
                    json_end = text.find('```', json_start)
                    if json_end != -1:
                        text = text[json_start:json_end].strip()
                
                json_start = text.find('{')
                if json_start != -1:
                    json_text = text[json_start:]
                    json_end = json_text.rfind('}')
                    if json_end != -1:
                        json_text = json_text[:json_end+1]
                        try:
                            return json.loads(json_text)
                        except:
                            pass
                
                json_start = text.find('[')
                if json_start != -1:
                    json_text = text[json_start:]
                    json_end = json_text.rfind(']')
                    if json_end != -1:
                        json_text = json_text[:json_end+1]
                        try:
                            return json.loads(json_text)
                        except:
                            pass
    
    return None


async def extract_post_content(session):
    """
    使用evaluate_script从DOM中提取主贴内容
    路径：post-wrap -> fn-cont-right -> post -> post-container
    按照DOM顺序提取所有文本和图片
    返回(content, imgList)元组
    """
    content = None
    img_list = []
    
    try:
        script = """() => {
            const content_parts = [];
            const img_list = [];
            const seen_imgs = new Set();
            
            // 1. 查找post-wrap元素
            let postWrap = document.querySelector('div.post-wrap, [class*="post-wrap"]');
            if (!postWrap) {
                return { content: '', imgList: [] };
            }
            
            // 2. 从post-wrap下获取fn-cont-right
            const fnContRight = postWrap.querySelector('.fn-cont-right, [class*="fn-cont-right"]');
            if (!fnContRight) {
                return { content: '', imgList: [] };
            }
            
            // 3. 在fn-cont-right下获取post
            const post = fnContRight.querySelector('.post, [class*="post"]');
            if (!post) {
                return { content: '', imgList: [] };
            }
            
            // 4. 在post下获取post-container
            const postContainer = post.querySelector('.post-container, [class*="post-container"]');
            if (!postContainer) {
                return { content: '', imgList: [] };
            }
            
            // 提取图片URL的辅助函数
            const extractImageUrl = (img) => {
                let imgUrl = img.src || 
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
                
                imgUrl = String(imgUrl).trim().replace(/^["']+|["']+$/g, '').replace(/\\\\/g, '').replace(/\\"/g, '');
                
                if (imgUrl.includes('blank.gif')) return null;
                
                if (imgUrl.startsWith('//')) {
                    imgUrl = 'https:' + imgUrl;
                } else if (!imgUrl.startsWith('http://') && !imgUrl.startsWith('https://')) {
                    return null;
                }
                
                if (imgUrl && (imgUrl.includes('club2.autoimg.cn') || imgUrl.includes('club2'))) {
                    return imgUrl.split('?')[0].split('#')[0];
                }
                return null;
            };
            
            // 处理图片的函数
            const processImage = (img) => {
                const imgUrl = extractImageUrl(img);
                if (imgUrl && !seen_imgs.has(imgUrl)) {
                    seen_imgs.add(imgUrl);
                    img_list.push(imgUrl);
                    content_parts.push(`<img src="${imgUrl}">`);
                }
            };
            
            // 查找post-container下的所有editor-paragraph和editor-image元素，按照DOM顺序
            const allElements = Array.from(postContainer.children).filter(elem => {
                const classes = elem.className || '';
                const classStr = Array.isArray(classes) ? classes.join(' ') : String(classes);
                return classStr.includes('editor-paragraph') || classStr.includes('editor-image');
            });
            
            // 按照DOM顺序处理每个元素
            allElements.forEach(elem => {
                const classes = elem.className || '';
                const classStr = Array.isArray(classes) ? classes.join(' ') : String(classes);
                
                // 处理editor-paragraph（文本段落）
                if (classStr.includes('editor-paragraph')) {
                    // 提取文本内容（先移除img标签，避免重复）
                    const paraCopy = elem.cloneNode(true);
                    const imgs = paraCopy.querySelectorAll('img');
                    imgs.forEach(img => img.remove());
                    
                    const text = paraCopy.textContent.trim();
                    if (text && text.length > 0) {
                        content_parts.push(text);
                    }
                    
                    // 提取editor-paragraph中的图片（如果有）
                    elem.querySelectorAll('img').forEach(processImage);
                }
                
                // 处理editor-image（图片）
                else if (classStr.includes('editor-image')) {
                    elem.querySelectorAll('img').forEach(processImage);
                    elem.querySelectorAll('[data-src]').forEach(dataElem => {
                        const img = dataElem.tagName === 'IMG' ? dataElem : dataElem.querySelector('img');
                        if (img) processImage(img);
                    });
                }
            });
            
            // 如果上面的方法没有提取到内容，尝试更简单的方法
            if (content_parts.length === 0) {
                // 直接获取post-container的文本内容
                const containerCopy = postContainer.cloneNode(true);
                const imgs = containerCopy.querySelectorAll('img');
                imgs.forEach(img => img.remove());
                const text = containerCopy.textContent.trim();
                if (text) {
                    content_parts.push(text);
                }
                
                // 获取所有图片
                postContainer.querySelectorAll('img').forEach(processImage);
            }
            
            return {
                content: content_parts.join('\\n'),
                imgList: img_list
            };
        }"""
        
        result = await session.call_tool(
            "evaluate_script",
            {
                "function": script
            }
        )
        
        # 解析返回结果
        if result and hasattr(result, 'content'):
            result_content = result.content if hasattr(result, 'content') else []
            for item in result_content:
                text = None
                if hasattr(item, 'type') and item.type == 'text':
                    text = item.text
                
                if text:
                    # 查找JSON部分
                    if '```json' in text:
                        json_start = text.find('```json') + 7
                        json_end = text.find('```', json_start)
                        if json_end != -1:
                            text = text[json_start:json_end].strip()
                    
                    # 查找对象
                    json_start = text.find('{')
                    if json_start != -1:
                        json_text = text[json_start:]
                        json_end = json_text.rfind('}')
                        if json_end != -1:
                            json_text = json_text[:json_end+1]
                            try:
                                data = json.loads(json_text)
                                content = data.get('content', '')
                                img_list = data.get('imgList', [])
                                break
                            except:
                                pass
        
        # 标准化图片URL
        if img_list:
            img_list = normalize_image_urls(img_list)
        
        # 标准化content中的图片URL
        if content:
            content = normalize_content_images(content)
        
    except Exception as e:
        print(f"      ⚠️  使用evaluate_script提取内容时出错: {e}")
        traceback.print_exc()
    
    return content, img_list


async def extract_metadata(session, url):
    """提取帖子元数据"""
    metadata = {
        "bbs_id": "",
        "club_bbs_name": "",
        "title": "",
        "publish_time": "",
        "author_name": ""
    }
    
    # 从URL中提取bbs_id
    bbs_id_match = re.search(r'/(\d+)-', url)
    if bbs_id_match:
        metadata["bbs_id"] = bbs_id_match.group(1)
    
    try:
        script = """() => {
            const metadata = {};
            
            // 从athm-bbs-title-con获取name-cont作为club_bbs_name
            const titleCon = document.querySelector('.athm-bbs-title-con, [class*="athm-bbs-title-con"]');
            if (titleCon) {
                const nameCont = titleCon.querySelector('.name-cont, [class*="name-cont"]');
                if (nameCont) {
                    metadata.club_bbs_name = nameCont.textContent.trim();
                }
            }
            
            // 从post-wrap下的post-title获取title
            const postWrap = document.querySelector('.post-wrap, [class*="post-wrap"]');
            if (postWrap) {
                const postTitle = postWrap.querySelector('.post-title, [class*="post-title"]');
                if (postTitle) {
                    metadata.title = postTitle.textContent.trim();
                }
                
                // 从post-wrap下的fn-cont-left获取user-info的name作为author_name
                const fnContLeft = postWrap.querySelector('.fn-cont-left, [class*="fn-cont-left"]');
                if (fnContLeft) {
                    const userInfo = fnContLeft.querySelector('.user-info, [class*="user-info"]');
                    if (userInfo) {
                        const nameElem = userInfo.querySelector('.name, [class*="name"]');
                        if (nameElem) {
                            metadata.author_name = nameElem.textContent.trim();
                        } else {
                            // 备用：尝试从链接中获取
                            const userLink = userInfo.querySelector('a');
                            if (userLink) {
                                metadata.author_name = userLink.textContent.trim();
                            }
                        }
                    }
                }
            }
            
            // 从post-handle-publish下的时间获取publish_time
            const postHandlePublish = document.querySelector('.post-handle-publish, [class*="post-handle-publish"]');
            if (postHandlePublish) {
                // 查找时间格式
                const publishText = postHandlePublish.textContent.trim();
                const timeMatch = publishText.match(/(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/);
                if (timeMatch) {
                    metadata.publish_time = timeMatch[1];
                } else {
                    // 如果没有匹配到，尝试查找strong标签
                    const strongTags = postHandlePublish.querySelectorAll('strong');
                    for (let i = strongTags.length - 1; i >= 0; i--) {
                        const strongText = strongTags[i].textContent.trim();
                        const timeMatch = strongText.match(/(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/);
                        if (timeMatch) {
                            metadata.publish_time = timeMatch[1];
                            break;
                        }
                    }
                }
            }
            
            return metadata;
        }"""
        
        result = await session.call_tool("evaluate_script", {"function": script})
        data = parse_json_result(result)
        
        if data:
            metadata["club_bbs_name"] = data.get('club_bbs_name', '')
            metadata["title"] = data.get('title', '')
            metadata["publish_time"] = data.get('publish_time', '')
            metadata["author_name"] = data.get('author_name', '')
        
    except Exception as e:
        print(f"提取元数据时出错: {e}")
    
    return metadata


async def extract_comments(session):
    """提取评论数据"""
    comments = []
    debug_html = []  # 存储调试用的HTML片段
    
    try:
        script = """() => {
            const comments = [];
            const debugHtml = [];
            
            // 查找所有评论容器：查找li元素且class包含js-reply-floor-container
            const containerSet = new Set();
            document.querySelectorAll('li[class*="js-reply-floor-container"]').forEach(container => containerSet.add(container));
            
            // 转换为数组并按DOM顺序排序
            const floorContainers = Array.from(containerSet).sort((a, b) => {
                const position = a.compareDocumentPosition(b);
                if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
                if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
                return 0;
            });
            
            // 确保遍历所有找到的容器
            floorContainers.forEach((container, index) => {
                // 按照指定路径提取用户名：js-reply-floor-container -> fn-cont-left -> js-user-info-container -> user fold -> user-name -> name
                const leftElem = container.querySelector('.fn-cont-left, [class*="fn-cont-left"]');
                let username = '';
                
                if (leftElem) {
                    // 1. 从fn-cont-left获取js-user-info-container
                    const userInfoContainer = leftElem.querySelector('.js-user-info-container, [class*="js-user-info-container"]');
                    if (userInfoContainer) {
                        // 2. 优先从js-user-info-container获取user fold
                        let user = userInfoContainer.querySelector('.user.fold, .user[class*="fold"], [class*="user"][class*="fold"]');
                        // 如果没有user fold，尝试获取普通的user
                        if (!user) {
                            user = userInfoContainer.querySelector('.user, [class*="user"]');
                        }
                        
                        if (user) {
                            // 3. 从user获取user-name
                            const userNameDiv = user.querySelector('.user-name, [class*="user-name"]');
                            if (userNameDiv) {
                                // 4. 从user-name获取name
                                const nameElem = userNameDiv.querySelector('.name, [class*="name"]');
                                if (nameElem) {
                                    username = nameElem.textContent.trim();
                                } else {
                                    // 如果没有name元素，使用user-name的文本内容
                                    username = userNameDiv.textContent.trim();
                                }
                            } else {
                                // 如果没找到user-name，保存HTML用于调试
                                if (!username) {
                                    debugHtml.push({
                                        commentIndex: index + 1,
                                        step: '未找到user-name',
                                        html: user.outerHTML.substring(0, 1000)
                                    });
                                }
                            }
                        } else {
                            // 如果没找到user，保存HTML用于调试
                            if (!username) {
                                debugHtml.push({
                                    commentIndex: index + 1,
                                    step: '未找到user',
                                    html: userInfoContainer.outerHTML.substring(0, 1000)
                                });
                            }
                        }
                    } else {
                        // 如果没找到userInfoContainer，保存HTML用于调试
                        debugHtml.push({
                            commentIndex: index + 1,
                            step: '未找到userInfoContainer',
                            html: leftElem.outerHTML.substring(0, 1000)
                        });
                    }
                    
                    // 备用方案：如果上面的方法失败，直接从fn-cont-left下查找user-name
                    if (!username) {
                        const userNameDivs = leftElem.querySelectorAll('.user-name, [class*="user-name"]');
                        for (let i = 0; i < userNameDivs.length && !username; i++) {
                            const nameElem = userNameDivs[i].querySelector('.name, [class*="name"]');
                            if (nameElem) {
                                username = nameElem.textContent.trim();
                            } else {
                                const text = userNameDivs[i].textContent.trim();
                                // 过滤掉明显不是用户名的文本
                                if (text && text.length > 0 && text.length < 50 && 
                                    !text.includes('展开') && !text.includes('回复') && 
                                    !text.includes('http') && !text.includes('@')) {
                                    username = text;
                                }
                            }
                        }
                        
                        // 如果备用方案也失败，保存完整的leftElem HTML
                        if (!username) {
                            debugHtml.push({
                                commentIndex: index + 1,
                                step: '所有方法都失败',
                                html: leftElem.outerHTML.substring(0, 2000)
                            });
                        }
                    }
                } else {
                    // 如果没找到leftElem，保存container的HTML
                    debugHtml.push({
                        commentIndex: index + 1,
                        step: '未找到leftElem',
                        html: container.outerHTML.substring(0, 1000)
                    });
                }
                
                const rightElem = container.querySelector('.fn-cont-right, [class*="fn-cont-right"]');
                let time = '';
                if (rightElem) {
                    const replyTop = rightElem.querySelector('.reply-top, [class*="reply-top"]');
                    if (replyTop) {
                        const replyStaticTexts = replyTop.querySelectorAll('.reply-static-text.fn-fl, [class*="reply-static-text"][class*="fn-fl"]');
                        for (let i = 0; i < replyStaticTexts.length; i++) {
                            const replyStaticText = replyStaticTexts[i];
                            const strongTags = replyStaticText.querySelectorAll('strong');
                            for (let j = strongTags.length - 1; j >= 0; j--) {
                                const strongText = strongTags[j].textContent.trim();
                                const timeMatch = strongText.match(/(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/);
                                if (timeMatch) {
                                    time = timeMatch[1];
                                    break;
                                }
                            }
                            if (time) break;
                        }
                    }
                }
                
                const replyMain = container.querySelector('.reply-main, [class*="reply-main"]');
                let commentText = '';
                if (replyMain) {
                    const replyDetail = replyMain.querySelector('.reply-detail, [class*="reply-detail"]');
                    if (replyDetail) {
                        // 克隆元素，移除unfold-comment相关元素后再提取文本
                        const detailCopy = replyDetail.cloneNode(true);
                        // 移除unfold-comment元素
                        const unfoldComments = detailCopy.querySelectorAll('.unfold-comment, [class*="unfold-comment"]');
                        unfoldComments.forEach(elem => elem.remove());
                        commentText = detailCopy.textContent.trim();
                    }
                }
                
                const replies = [];
                const replyComments = container.querySelectorAll('.reply-comment, [class*="reply-comment"]');
                
                replyComments.forEach((replyComment, replyIndex) => {
                    const replySubUser = replyComment.querySelector('.reply-sub-user, [class*="reply-sub-user"]');
                    let replyUsername = '';
                    if (replySubUser) {
                        const nameElem = replySubUser.querySelector('.name, [class*="name"]');
                        if (nameElem) {
                            replyUsername = nameElem.textContent.trim();
                        }
                    }
                    
                    let replyContent = '';
                    let replyImages = [];
                    const replySubCont = replyComment.querySelector('.reply-sub-cont, [class*="reply-sub-cont"]');
                    if (replySubCont) {
                        const contCopy = replySubCont.cloneNode(true);
                        // 移除unfold-comment元素
                        const unfoldComments = contCopy.querySelectorAll('.unfold-comment, [class*="unfold-comment"]');
                        unfoldComments.forEach(elem => elem.remove());
                        const imgs = contCopy.querySelectorAll('img');
                        imgs.forEach(img => img.remove());
                        replyContent = contCopy.textContent.trim();
                        // 清理"展开评论"相关的文本
                        replyContent = replyContent.replace(/展开评论\s*[>＞]\s*/g, '');
                        // 清理重复的内容（如果内容重复了，只保留第一次出现）
                        const lines = replyContent.split('\\n');
                        const cleanedLines = [];
                        const seen = new Set();
                        for (let i = 0; i < lines.length; i++) {
                            const line = lines[i].trim();
                            if (line && !line.includes('展开评论')) {
                                // 检查是否是重复内容
                                if (!seen.has(line)) {
                                    cleanedLines.push(line);
                                    seen.add(line);
                                }
                            }
                        }
                        replyContent = cleanedLines.join('\\n').trim();
                        // 清理多余的空白和换行
                        replyContent = replyContent.replace(/\\n\\s*\\n/g, '\\n');
                        replyContent = replyContent.replace(/\\s+/g, ' ').trim();
                        
                        const replyImgs = replySubCont.querySelectorAll('.reply-sub-cont--img img, [class*="reply-sub-cont--img"] img, img');
                        replyImgs.forEach(img => {
                            let imgUrl = img.src || img.getAttribute('data-src') || img.getAttribute('data-original') || '';
                            if (imgUrl) {
                                imgUrl = String(imgUrl).trim().replace(/^["']+|["']+$/g, '');
                                if (imgUrl.startsWith('//')) {
                                    imgUrl = 'https:' + imgUrl;
                                }
                                if (imgUrl && imgUrl.includes('club2') && !replyImages.includes(imgUrl)) {
                                    replyImages.push(imgUrl);
                                }
                            }
                        });
                    }
                    
                    let replyTime = '';
                    const replySubHandle = replyComment.querySelector('.reply-sub-handle, [class*="reply-sub-handle"]');
                    if (replySubHandle) {
                        const handleTime = replySubHandle.textContent.trim();
                        const timeMatch = handleTime.match(/(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/);
                        if (timeMatch) {
                            replyTime = timeMatch[1];
                        }
                    }
                    
                    if (replyUsername || replyContent || replyImages.length > 0) {
                        replies.push({
                            username: replyUsername,
                            content: replyContent,
                            images: replyImages,
                            time: replyTime
                        });
                    }
                });
                
                if (username || commentText || replies.length > 0) {
                    comments.push({
                        username: username,
                        time: time,
                        content: commentText,
                        replies: replies
                    });
                }
            });
            
            return {
                comments: comments,
                debugHtml: debugHtml
            };
        }"""
        
        result = await session.call_tool("evaluate_script", {"function": script})
        data = parse_json_result(result)
        
        if isinstance(data, dict):
            # 新格式：包含comments和debugHtml
            if 'comments' in data:
                comments = data.get('comments', [])
                debug_html = data.get('debugHtml', [])
                
                # 打印调试信息
                if debug_html:
                    print("\n" + "="*60)
                    print("⚠️  用户名提取失败的评论HTML片段：")
                    print("="*60)
                    for debug_item in debug_html:
                        print(f"\n评论 {debug_item.get('commentIndex', '?')} - {debug_item.get('step', '未知步骤')}:")
                        print("-" * 60)
                        print(debug_item.get('html', ''))
                        print("-" * 60)
                    print("="*60 + "\n")
        elif isinstance(data, list):
            # 旧格式：直接是comments列表
            comments = data
        
    except Exception as e:
        print(f"提取评论时出错: {e}")
        traceback.print_exc()
    
    return comments


async def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("使用方法: python3 qichezhijia-zhutie.py <URL>")
        sys.exit(1)
    
    url = sys.argv[1]
    if not url.startswith('http://') and not url.startswith('https://'):
        print("错误: 请输入有效的URL")
        sys.exit(1)
    
    # 从URL提取帖子ID
    post_id_match = re.search(r'/(\d+)-', url)
    post_id = post_id_match.group(1) if post_id_match else "post"
    
    # 设置MCP服务器
    use_npx = os.getenv("USE_NPX_MCP", "false").lower() == "true"
    
    if use_npx:
        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "chrome-devtools-mcp@latest", "--browser-url=http://127.0.0.1:9222"]
        )
    else:
        script_dir = Path(__file__).parent
        mcp_dir = script_dir.parent / "chrome-devtools-mcp"
        index_js = mcp_dir / "build" / "src" / "index.js"
        
        if not index_js.exists():
            print("❌ 错误: 本地编译文件不存在")
            print(f"   请先运行: cd chrome-devtools-mcp && npm run build")
            sys.exit(1)
        
        server_params = StdioServerParameters(
            command="node",
            args=[str(index_js), "--browser-url=http://127.0.0.1:9222"]
        )
    
    # 提取数据的通用函数
    async def extract_data(session):
        await session.initialize()
        await session.call_tool("navigate_page", {
            "type": "url",
            "url": url,
            "timeout": 30000,
        })
        await asyncio.sleep(3)
        
        # 下载HTML
        script_dir = Path(__file__).parent
        html_data_dir = script_dir / "html_data"
        html_data_dir.mkdir(parents=True, exist_ok=True)
        
        # 获取页面HTML
        html_script = """() => {
            return document.documentElement.outerHTML;
        }"""
        
        html_result = await session.call_tool("evaluate_script", {"function": html_script})
        html_content = ""
        
        if html_result and hasattr(html_result, 'content'):
            result_content = html_result.content if hasattr(html_result, 'content') else []
            for item in result_content:
                text = None
                if hasattr(item, 'type') and item.type == 'text':
                    text = item.text
                elif isinstance(item, dict) and 'text' in item:
                    text = item['text']
                
                if text:
                    # 查找JSON部分
                    if '```json' in text:
                        json_start = text.find('```json') + 7
                        json_end = text.find('```', json_start)
                        if json_end != -1:
                            text = text[json_start:json_end].strip()
                    
                    # 查找字符串（HTML内容）
                    if text.startswith('"') and text.endswith('"'):
                        try:
                            html_content = json.loads(text)
                        except:
                            html_content = text.strip('"')
                    else:
                        html_content = text
        
        # 保存HTML文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_file = html_data_dir / f"post_{post_id}_{timestamp}.html"
        if html_content:
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f"✓ HTML文件已保存: {html_file}")
        
        metadata = await extract_metadata(session, url)
        content, img_list = await extract_post_content(session)
        comments = await extract_comments(session)
        
        output_data = {
            "bbs_id": metadata.get("bbs_id", ""),
            "club_bbs_name": metadata.get("club_bbs_name", ""),
            "title": metadata.get("title", ""),
            "publish_time": metadata.get("publish_time", ""),
            "author_name": metadata.get("author_name", ""),
            "content": content or "",
            "imgList": img_list or [],
            "comments": comments or []
        }
        
        script_dir = Path(__file__).parent
        json_data_dir = script_dir / "json_data"
        json_data_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = json_data_dir / f"post_{post_id}_{timestamp}.json"
        
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        print(f"✓ JSON文件已保存: {json_file}")
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await extract_data(session)
    except Exception as e:
        print(f"❌ 错误: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断操作")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        traceback.print_exc()
        sys.exit(1)
