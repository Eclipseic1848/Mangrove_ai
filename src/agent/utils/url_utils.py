"""
URL 处理工具函数

提供 URL 提取、规范化、验证等功能，支持多种 URL 格式。
"""
import re
from typing import Optional
from urllib.parse import urlparse


def extract_target_url_from_task(user_task: str) -> Optional[str]:
    """
    从任务描述中提取目标URL（通用性强，支持各种格式）
    
    支持的URL格式：
    - 完整URL: http://example.com, https://www.example.com/path
    - 带端口: http://192.168.1.1:8080, https://example.com:3000/path
    - 带路径: http://example.com/path/to/page, https://example.com:8080/api/v1
    - 域名: www.example.com, example.com
    - IP地址: 192.168.1.1, 192.168.1.1:8080
    - 混合格式: 各种中英文标点混合的情况
    
    提取策略：
    1. 优先匹配完整的 http:// 或 https:// URL（包括端口、路径、查询参数）
    2. 匹配 www. 开头的域名
    3. 匹配纯IP地址（带可选端口）
    4. 智能清理末尾标点，保留URL的有效部分
    
    Args:
        user_task: 用户任务描述
        
    Returns:
        提取到的完整URL（已规范化），如果未找到则返回None
    """
    # URL模式匹配规则（按优先级排序）
    patterns = [
        # 1. 完整URL（http:// 或 https:// 开头）
        r'https?://[^\s\u4e00-\u9fff，,。！!？?；;：]+',
        # 2. www. 开头的域名
        r'www\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*(?:/[^\s\u4e00-\u9fff，,。！!？?；;：:]*)?',
        # 3. IP地址格式（IPv4，带可选端口）
        r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b',
    ]
    
    for pattern in patterns:
        matches = list(re.finditer(pattern, user_task))
        for match in matches:
            target_url = match.group(0)
            target_url = clean_url_trailing_punctuation(target_url)
            normalized_url = normalize_url(target_url)
            
            if normalized_url and is_valid_url(normalized_url):
                return normalized_url
    
    return None


def clean_url_trailing_punctuation(url: str) -> str:
    """
    清理URL末尾的标点符号，但保留URL的有效部分
    
    Args:
        url: 原始URL字符串
        
    Returns:
        清理后的URL字符串
    """
    if not url:
        return url
    
    punctuation = r'[，,。！!？?；;：:]'
    port_pattern = r':(\d{1,5})([，,。！!？?；;：:]*)$'
    port_match = re.search(port_pattern, url)
    
    if port_match:
        trailing_punct = port_match.group(2)
        if trailing_punct:
            url = url[:-len(trailing_punct)]
    else:
        url = re.sub(f'{punctuation}+$', '', url)
    
    return url.strip()


def normalize_url(url: str) -> Optional[str]:
    """
    规范化URL，确保格式正确
    
    功能：
    - 为缺少协议的URL添加 http:// 前缀
    - 支持IP地址、域名、www.开头的URL
    
    Args:
        url: 原始URL字符串
        
    Returns:
        规范化后的URL，如果输入无效则返回None
    """
    if not url or not url.strip():
        return None
    
    url = url.strip()
    
    if url.startswith('http://') or url.startswith('https://'):
        return url
    
    # IP地址格式
    ip_pattern = r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d{1,5})?)(?:/.*)?$'
    if re.match(ip_pattern, url):
        return f"http://{url}"
    
    # www. 开头的域名
    if url.startswith('www.'):
        return f"http://{url}"
    
    # 纯域名
    domain_pattern = r'^[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+(?::\d{1,5})?(?:/.*)?$'
    if re.match(domain_pattern, url):
        return f"http://{url}"
    
    return url


# 404/错误页面 URL 路径模式（路径或文件名中含这些则视为无效页面）
_404_URL_PATH_PATTERNS = (
    r"/404\.html",
    r"/blank/404",
    r"/404/",
    r"/404$",
    r"/not-found",
    r"/error/404",
    r"/pages/404",
)


def is_404_page_url(url: str) -> bool:
    """
    判断 URL 是否为 404/错误页面
    
    通过路径模式检测，如汽车之家 404 页：
    https://s.autoimg.cn/club/bbs/pc/blank/404.html
    
    Args:
        url: 要检测的 URL 字符串
        
    Returns:
        若路径表明是 404 等无效页面则返回 True
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        path = (parsed.path or "").lower()
        if not path:
            return False
        for pattern in _404_URL_PATH_PATTERNS:
            if re.search(pattern, path, re.I):
                return True
        return False
    except Exception:
        return False


def is_valid_url(url: str) -> bool:
    """
    验证URL格式是否有效
    
    功能：
    - 检查URL是否以 http:// 或 https:// 开头
    - 验证网络位置（netloc）是否为有效的IP地址或域名
    Args:
        url: 要验证的URL字符串
        
    Returns:
        如果URL格式有效返回True，否则返回False
    """
    if not url:
        return False
    
    if not (url.startswith('http://') or url.startswith('https://')):
        return False
    
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return False
        
        netloc = parsed.netloc.split(':')[0]
        
        # IP地址格式
        ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if re.match(ip_pattern, netloc):
            parts = netloc.split('.')
            if all(0 <= int(part) <= 255 for part in parts):
                return True
        
        # 域名格式
        domain_pattern = r'^[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+$'
        if re.match(domain_pattern, netloc):
            return True
        
        return False
        
    except Exception:
        return False
