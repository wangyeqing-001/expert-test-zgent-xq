"""设计稿分析器 — 多模态 LLM 读图，提取 UI 元素清单注入测试用例生成

支持三种输入源：
1. 飞书 PRD 里内嵌的图片（从 blocks API 自动提取 → 下载 → 分析）
2. 手动提供的 Figma URL（公开分享模式 → 导出 PNG → 分析）
3. 直接的图片 URL（http/https）

输出：结构化 UI 元素清单，形如：
    [{页面名, 元素类型, 元素名, 状态, 交互说明, 备注}, ...]
再序列化为文本注入 {design_context} 占位。
"""

import os
import re
import time
import json
import logging
import hashlib
import tempfile
from typing import Optional
from urllib.parse import urlparse

import requests
import dashscope

logger = logging.getLogger(__name__)


# ============================================================
# 一、图片获取层
# ============================================================

_FIGMA_URL_RE = re.compile(
    r'https?://(?:www\.)?figma\.com/(?:design|file|proto)/[^\s<>"\']+',
    re.IGNORECASE,
)

# Figma URL → 本地图片路径缓存文件
_FIGMA_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'temp_design', 'figma_cache.json',
)


def _load_figma_cache() -> dict:
    """加载 Figma URL → 本地图片路径的映射"""
    try:
        if os.path.exists(_FIGMA_CACHE_FILE):
            with open(_FIGMA_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_figma_cache(cache: dict):
    """保存缓存到文件"""
    try:
        os.makedirs(os.path.dirname(_FIGMA_CACHE_FILE), exist_ok=True)
        with open(_FIGMA_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存 Figma 缓存失败: {e}")


def _figma_cache_get(url: str) -> Optional[str]:
    """查缓存：命中且文件存在则返回路径，否则返回 None"""
    cache = _load_figma_cache()
    key = hashlib.md5(url.encode('utf-8')).hexdigest()
    path = cache.get(key)
    if path and os.path.exists(path):
        logger.info(f"📋 Figma 缓存命中: {os.path.basename(path)}")
        return path
    return None


def _figma_cache_put(url: str, path: str):
    """写缓存"""
    cache = _load_figma_cache()
    key = hashlib.md5(url.encode('utf-8')).hexdigest()
    cache[key] = path
    _save_figma_cache(cache)


def extract_figma_urls_from_text(text: str) -> list[str]:
    """从任意文本中提取所有 Figma URL（去重保序）"""
    if not text:
        return []
    matches = _FIGMA_URL_RE.findall(text)
    seen, result = set(), []
    for m in matches:
        m = m.rstrip(',);>')  # 去掉常见的尾部标点
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


def download_image_from_url(url: str, save_dir: str = 'temp_design') -> Optional[str]:
    """从公开 URL 下载图片到本地，返回本地文件路径（失败返回 None）"""
    os.makedirs(save_dir, exist_ok=True)
    try:
        resp = requests.get(url, timeout=120, stream=True,
                            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
        resp.raise_for_status()
        # 从 URL 或 Content-Type 推断扩展名
        ct = resp.headers.get('Content-Type', '').split(';')[0]
        ext_map = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/jpg': '.jpg',
                   'image/webp': '.webp', 'image/gif': '.gif', 'image/bmp': '.bmp'}
        ext = ext_map.get(ct, '.png')
        fname = f"design_{int(time.time()*1000)}{ext}"
        fpath = os.path.join(save_dir, fname)
        with open(fpath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"✓ 图片下载成功: {fpath} ({os.path.getsize(fpath)} bytes)")
        return fpath
    except Exception as e:
        logger.warning(f"图片下载失败 {url}: {e}")
        return None


def extract_figma_file_key(url: str) -> Optional[dict]:
    """解析 Figma URL，提取 file_key / node_id / team_token 等
    返回: {'file_key': str, 'node_id': str, 'node_idx': str, 'team_token': str}
    """
    try:
        parsed = urlparse(url)
        if 'figma.com' not in parsed.netloc:
            return None
        parts = parsed.path.split('/')
        # /design/{file_key}/{name} 或 /file/{file_key}/{name}
        file_key = None
        for i, p in enumerate(parts):
            if p in ('design', 'file', 'proto') and i + 1 < len(parts):
                file_key = parts[i + 1]
                break
        if not file_key:
            return None
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        node_id = qs.get('node-id', [''])[0].replace('-', ':')
        team = qs.get('t', [''])[0]
        return {'file_key': file_key, 'node_id': node_id, 'team_token': team}
    except Exception:
        return None


def download_figma_image(url: str, save_dir: str = 'temp_design',
                          figma_token: str = '') -> Optional[str]:
    """Figma URL → PNG 图片（优先用 Figma API token，没有 token 则尝试公开分享链接）

    带文件缓存：同一 Figma URL 只请求 API 一次，后续直接返回本地图片路径。
    """
    info = extract_figma_file_key(url)
    if not info:
        logger.warning(f"不是有效的 Figma URL: {url}")
        return None

    # 缓存命中检查
    cached = _figma_cache_get(url)
    if cached:
        return cached

    os.makedirs(save_dir, exist_ok=True)

    if figma_token:
        # 路线 A：用 Figma API 导出 PNG（最稳定，能拿到精确节点）
        node_ids = info['node_id'] or ':0'
        api = f"https://api.figma.com/v1/images/{info['file_key']}"
        params = {'ids': node_ids, 'format': 'png', 'scale': '2'}
        headers = {'X-Figma-Token': figma_token}

        # Figma images API 对大文件渲染较慢，重试 2 次、超时 60s
        for attempt in range(1, 3):
            try:
                resp = requests.get(api, params=params, headers=headers, timeout=60)
                if resp.status_code == 200:
                    img_url = resp.json().get('images', {}).get(node_ids.lstrip(':')) or \
                              resp.json().get('images', {}).get(node_ids)
                    if img_url:
                        logger.info(f"✓ Figma API 导出成功: {img_url}")
                        path = download_image_from_url(img_url, save_dir)
                        if path:
                            _figma_cache_put(url, path)
                        return path
                    logger.warning(f"Figma API 返回空图片 URL，可能节点正在渲染，第{attempt}次重试")
                elif resp.status_code == 429:
                    # 限流：等 60s 再试一次，仍失败则放弃（避免打满限流窗口）
                    logger.warning(f"Figma API 限流(429) 第{attempt}次，等待60s重试")
                    time.sleep(60)
                    continue
                else:
                    logger.warning(f"Figma API 返回 {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"Figma API 异常 (attempt {attempt}): {e}")
            if attempt < 2:
                time.sleep(5)
    else:
        # 路线 B：无 token，尝试 Figma 公开分享的 oEmbed / 直接图片代理
        try:
            oembed = f"https://www.figma.com/oembed?url={url}"
            resp = requests.get(oembed, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
            })
            if resp.status_code == 200:
                thumbnail = resp.json().get('thumbnail_url')
                if thumbnail:
                    logger.info(f"✓ Figma oEmbed 缩略图: {thumbnail}")
                    path = download_image_from_url(thumbnail, save_dir)
                    if path:
                        _figma_cache_put(url, path)
                    return path
        except Exception as e:
            logger.warning(f"Figma oEmbed 失败: {e}")

    logger.warning(f"Figma 图片获取失败（可能是私有文件，需要 FIGMA_ACCESS_TOKEN）: {url}")
    return None


def extract_feishu_images(doc_token: str, doc_type: str, access_token: str,
                          session: requests.Session, save_dir: str = 'temp_design') -> list[str]:
    """从飞书文档 blocks API 提取所有图片 block → 下载到本地 → 返回文件路径列表
    
    飞书图片 block 结构: {"block_type": 27, "image": {"token": "imgcn_xxx", "width": N, "height": N}}
    下载 API: GET /open-apis/drive/v1/medias/{file_token}/download
    """
    os.makedirs(save_dir, exist_ok=True)
    auth = {'Authorization': f'Bearer {access_token}'}
    base = 'https://open.feishu.cn/open-apis'

    # 1. 拉 blocks（递归拿所有子 block）
    image_tokens: list[str] = []
    def _walk(blocks: list):
        for b in blocks:
            if not isinstance(b, dict):
                continue  # 防御：跳过非 dict 元素
            if b.get('block_type') == 27:  # 图片 block
                img = b.get('image') or {}
                token = img.get('token', '') if isinstance(img, dict) else ''
                if token:
                    image_tokens.append(token)
            children = b.get('children', [])
            if children:
                _walk(children)

    try:
        working_token = doc_token
        # wiki 需要先换 obj_token，再拿 document_id
        if doc_type == 'wiki':
            node_resp = session.get(
                f"{base}/wiki/v2/spaces/get_node?token={doc_token}",
                headers=auth, timeout=10,
            )
            try:
                node_data = node_resp.json()
            except Exception:
                logger.warning(f"飞书 wiki get_node 返回非 JSON: {node_resp.text[:200]}")
                return []
            if not isinstance(node_data, dict) or node_data.get('code') != 0:
                logger.warning(f"飞书 wiki get_node 失败: {node_data}")
                return []
            node = (node_data.get('data') or {}).get('node') or {}
            working_token = node.get('obj_token')
            if not working_token:
                logger.warning(f"飞书 wiki get_node 未返回 obj_token: {node_data}")
                return []

        # 取 document_id
        meta_resp = session.get(
            f"{base}/docx/v1/documents/{working_token}",
            headers=auth, timeout=10,
        )
        try:
            meta_data = meta_resp.json()
        except Exception:
            logger.warning(f"飞书 docx meta 返回非 JSON: {meta_resp.text[:200]}")
            return []
        if not isinstance(meta_data, dict) or meta_data.get('code') != 0:
            logger.warning(f"飞书 docx meta 失败: {meta_data}")
            return []
        document = (meta_data.get('data') or {}).get('document') or {}
        document_id = document.get('document_id')
        if not document_id:
            logger.warning(f"飞书 docx meta 未返回 document_id: {meta_data}")
            return []

        # 拉 blocks
        r = session.get(
            f"{base}/docx/v1/documents/{document_id}/blocks",
            headers=auth, params={'page_size': 500}, timeout=15,
        )
        if r.status_code != 200:
            logger.warning(f"飞书 blocks API {r.status_code}: {r.text[:200]}")
            return []
        resp_json = r.json()
        if not isinstance(resp_json, dict) or resp_json.get('code') != 0:
            logger.warning(f"飞书 blocks API 业务错误: {resp_json}")
            return []
        data = resp_json.get('data', {}).get('items', [])
        _walk(data)
    except Exception as e:
        logger.warning(f"飞书 blocks 提取失败: {e}")
        return []

    if not image_tokens:
        logger.info(f"飞书文档无图片 block (doc={doc_token})")
        return []

    logger.info(f"飞书文档发现 {len(image_tokens)} 张图片")

    # 2. 逐张下载
    local_paths = []
    for img_token in image_tokens[:10]:  # 最多 10 张防过量
        try:
            r = session.get(f"{base}/drive/v1/medias/{img_token}/download",
                            headers=auth, timeout=30, stream=True)
            if r.status_code != 200:
                logger.warning(f"飞书图片下载失败 {img_token}: {r.status_code}")
                continue
            fname = f"feishu_{img_token[-8:]}_{int(time.time()*1000)}.png"
            fpath = os.path.join(save_dir, fname)
            with open(fpath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            local_paths.append(fpath)
            logger.info(f"  ✓ {fpath} ({os.path.getsize(fpath)} bytes)")
        except Exception as e:
            logger.warning(f"飞书图片下载异常 {img_token}: {e}")

    return local_paths


# ============================================================
# 二、多模态分析层（dashscope qwen-vl-plus）
# ============================================================

_DESIGN_PROMPT = """你是一名资深 UI 测试工程师。请仔细分析这张 APP/Web 设计稿截图，提取：

1. 【页面结构】页面名称 / 所属模块 / 顶部导航
2. 【UI 元素清单】所有可见元素（按钮、输入框、标签、图片、图标、分割线、Tab、开关、滑块、列表项等）
   - 每个元素标注：类型、位置、文案/状态、交互预期（可点击？置灰？长按？）
3. 【状态变体】是否展示了加载态 / 空状态 / 错误态 / 置灰态 / 选中态 / 禁用态 等
4. 【边界约束】字数限制（如标题最多25字）、尺寸、间距、对齐、截断规则
5. 【无障碍】色盲友好、字体大小、对比度、动态字体支持

输出为 JSON 数组，每个元素格式：
{
  "page": "页面名",
  "module": "所属模块",
  "element_type": "按钮|输入框|标签|图片|图标|列表|Tab|开关|其他",
  "element_name": "元素名称（中文）",
  "text_content": "可见文案，无则填空字符串",
  "state": "默认|选中|置灰|禁用|加载中|空状态|错误态|hover|其他",
  "position": "顶部|中部|底部|左侧|右侧|全局浮层",
  "interaction": "可点击|可输入|可长按|可滑动|仅展示|需授权",
  "constraints": "字数≤25|必填|仅数字|正则xxx|无",
  "remarks": "深色模式需适配|国际化注意|其他备注"
}

只输出 JSON，不要任何解释。如果图里不是 APP/Web 界面，返回 []。"""


def analyze_image_with_vlm(image_path: str, api_key: str = '') -> list[dict]:
    """用多模态 LLM 分析单张图片，返回 UI 元素 JSON 列表

    统一走 LLMClient.generate_with_images，和项目里其他 LLM 调用共用一个入口。
    失败后再回退到 OpenAI 兼容模式兜底。
    """
    from core.llm_client import LLMClient

    # 路线 A：复用 LLMClient（多模态）
    try:
        client = LLMClient(model='qwen-vl-plus')
        raw_text = client.generate_with_images(
            prompt_text=_DESIGN_PROMPT,
            image_paths=[image_path],
            temperature=0.1,
            max_tokens=2000,
        )
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
        json_str = m.group(1) if m else raw_text.strip()
        data = json.loads(json_str)
        if isinstance(data, list):
            logger.info(f"✓ 设计稿分析完成 {os.path.basename(image_path)}: {len(data)} 个 UI 元素")
            return data
        logger.warning(f"设计稿分析结果不是数组: {type(data)}")
        return []
    except Exception as e:
        logger.warning(f"LLMClient 多模态分析失败: {e}，尝试 OpenAI 兼容模式兜底")

    # 路线 B：OpenAI 兼容模式兜底
    key = api_key or os.getenv('DASHSCOPE_API_KEY', '')
    if not key:
        logger.warning("DASHSCOPE_API_KEY 缺失，跳过设计稿分析")
        return []

    import base64
    try:
        ext = os.path.splitext(image_path)[1].lower().lstrip('.') or 'png'
        mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png', 'webp': 'webp', 'gif': 'gif'}.get(ext, 'png')
        with open(image_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        image_payload = f"data:image/{mime};base64,{b64}"
    except Exception as e:
        logger.warning(f"图片读取失败 {image_path}: {e}")
        return []

    try:
        from openai import OpenAI
        oai = OpenAI(api_key=key, base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
                     max_retries=0, timeout=120)
        resp = oai.chat.completions.create(
            model='qwen-vl-plus',
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_payload}},
                    {"type": "text", "text": _DESIGN_PROMPT},
                ]
            }],
            max_tokens=2000,
            temperature=0.1,
        )
        raw_text = resp.choices[0].message.content
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
        json_str = m.group(1) if m else raw_text.strip()
        data = json.loads(json_str)
        if isinstance(data, list):
            logger.info(f"✓ 设计稿分析完成 {os.path.basename(image_path)}: {len(data)} 个 UI 元素")
            return data
        logger.warning(f"设计稿分析结果不是数组: {type(data)}")
        return []
    except Exception as e:
        logger.warning(f"OpenAI 兼容模式分析失败: {e}")
        return []


# ============================================================
# 三、汇总层：多图合并 + 序列化为 design_context 文本
# ============================================================

def _serialize_ui_elements(elements: list[dict]) -> str:
    """把 UI 元素 JSON 序列化为紧凑文本，注入 prompt"""
    if not elements:
        return ''

    # 按 page → module 分组
    by_page: dict[str, dict[str, list]] = {}
    for e in elements:
        p = e.get('page', '未命名页面')
        m = e.get('module', p)
        by_page.setdefault(p, {}).setdefault(m, []).append(e)

    lines = ['## 设计稿 UI 元素清单（自动从设计截图识别）\n']
    for page, modules in by_page.items():
        lines.append(f"### 📱 {page}")
        for mod, items in modules.items():
            lines.append(f"  📂 模块: {mod}")
            for it in items:
                etype = it.get('element_type', '')
                ename = it.get('element_name', '')
                text = it.get('text_content', '')
                state = it.get('state', '默认')
                pos = it.get('position', '')
                inter = it.get('interaction', '')
                cons = it.get('constraints', '无')
                remarks = it.get('remarks', '')
                summary = f"    • [{etype}] {ename}"
                if text:
                    summary += f" 文案:'{text}'"
                summary += f" | 状态={state} | 位置={pos} | 交互={inter} | 约束={cons}"
                if remarks and remarks != '无':
                    summary += f" | 备注={remarks}"
                lines.append(summary)
            lines.append('')
        lines.append('')
    return '\n'.join(lines)


def analyze_design_sources(
    feishu_doc_token: str = '',
    feishu_doc_type: str = '',
    feishu_access_token: str = '',
    feishu_session: requests.Session = None,
    manual_urls: list[str] = None,
    save_dir: str = 'temp_design',
    figma_token: str = '',
) -> str:
    """一站式分析：飞书内嵌图片 + 手动 URL → 返回 design_context 文本
    
    返回空字符串 = 没有可用的设计稿信息
    """
    all_elements: list[dict] = []
    downloaded: list[str] = []

    # 源 1：飞书 PRD 内嵌图片
    if feishu_doc_token and feishu_access_token and feishu_session:
        paths = extract_feishu_images(
            feishu_doc_token, feishu_doc_type,
            feishu_access_token, feishu_session, save_dir
        )
        downloaded.extend(paths)

    # 源 2：手动 URL（Figma / 直接图片）
    for url in (manual_urls or []):
        url = url.strip()
        if not url:
            continue
        figma_info = extract_figma_file_key(url)
        if figma_info:
            p = download_figma_image(url, save_dir, figma_token)
        else:
            p = download_image_from_url(url, save_dir)
        if p:
            downloaded.append(p)

    if not downloaded:
        logger.info("未获取到任何设计稿图片")
        return ''

    logger.info(f"开始分析 {len(downloaded)} 张设计稿图片...")
    for p in downloaded:
        elems = analyze_image_with_vlm(p)
        all_elements.extend(elems)

    if not all_elements:
        logger.warning(f"设计稿分析完成但未识别到 UI 元素")
        return ''

    context = _serialize_ui_elements(all_elements)
    logger.info(f"✓ 设计稿分析汇总: {len(all_elements)} 个 UI 元素")
    return context
