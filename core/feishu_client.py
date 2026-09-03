"""飞书API客户端 - 获取飞书文档内容"""
import os
import re
import time
import functools
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def retry(max_retries: int = 3, backoff_base: float = 1.0, backoff_max: float = 10.0,
          retry_on_exception: tuple = (requests.exceptions.RequestException,),
          retry_on_http_status: tuple = (500, 502, 503, 504)):
    """接口重试装饰器（仅用于读操作 + qadoc，写操作不重试避免重复创建）

    :param max_retries: 最大重试次数（不含首次）
    :param backoff_base: 指数退避基数（秒），第 n 次等待 backoff_base * 2^n
    :param backoff_max: 退避上限（秒）
    :param retry_on_exception: 哪些异常触发重试
    :param retry_on_http_status: 哪些 HTTP 状态码触发重试
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    result = func(self, *args, **kwargs)
                    # 如果返回是 Response 对象，检查状态码
                    if isinstance(result, requests.Response):
                        if result.status_code in retry_on_http_status:
                            raise requests.exceptions.HTTPError(
                                f"HTTP {result.status_code}", response=result
                            )
                    return result
                except retry_on_exception as e:
                    last_exc = e
                    if attempt < max_retries:
                        wait = min(backoff_base * (2 ** attempt), backoff_max)
                        logger.warning(
                            f"[重试] {func.__name__} 第{attempt+1}次失败: {e}, {wait:.1f}s后重试"
                        )
                        time.sleep(wait)
                    else:
                        logger.error(f"[重试] {func.__name__} 重试{max_retries}次后仍失败: {e}")
            raise last_exc
        return wrapper
    return decorator


class FeishuClient:
    """飞书开放平台API客户端"""
    
    # qadoc 内部网关：统一拉取飞书文档 Markdown + 关联文档列表（绕开飞书 OpenAPI 限制）
    _QADOC_URL = "https://qadoc.snowballfinance.com/ai/inter/queryReqByURL"

    def __init__(self, app_id: str, app_secret: str):
        """
        初始化飞书客户端
        :param app_id: 飞书应用ID
        :param app_secret: 飞书应用密钥
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self.access_token = None
        self._token_expires_at = 0  # token过期时间戳
        self.base_url = "https://open.feishu.cn/open-apis"
        self.domain = os.getenv('FEISHU_DOMAIN', '')
        # 统一会话：绕过系统代理直连（避免抓包代理导致SSL验证失败）
        self.session = requests.Session()
        self.session.proxies = {'http': None, 'https': None}
        self.session.trust_env = False
    
    @retry(max_retries=3)
    def _qadoc_fetch(self, doc_url: str) -> dict:
        """通过 qadoc 内部网关拉取飞书文档（Markdown + 关联文档列表）
        返回: {'content': str, 'related_urls': list, 'title': str}
        重试3次后仍失败抛异常（由外层 get_doc_content 降级到飞书OpenAPI）
        """
        r = self.session.get(
            self._QADOC_URL,
            params={'url': doc_url},
            timeout=15
        )
        # 非2xx让装饰器触发重试
        r.raise_for_status()
        data = r.json()
        if data.get('result_code') != 0:
            # 5xx 让装饰器重试，4xx 直接抛
            status = data.get('status') or data.get('code')
            if status and 500 <= int(status) < 600:
                raise requests.exceptions.HTTPError(
                    f"qadoc HTTP {status}: {data.get('error', '')}", response=r
                )
            logger.warning(f"qadoc返回非0 code(不重试): {data}")
            raise ValueError(f"qadoc返回非0 code: {data}")
        inner = data.get('data', {})
        content = inner.get('content_text', '')
        title = inner.get('title', '')
        sub_ids = inner.get('sub_document_ids', '')
        related_urls = []
        if sub_ids:
            related_urls = [u.strip() for u in sub_ids.split(',') if u.strip()]
        return {'content': content, 'related_urls': related_urls, 'title': title}

    @retry(max_retries=3)
    def get_access_token(self) -> str:
        """获取访问令牌（自动刷新过期token）"""
        # 提前60秒刷新，避免边界过期
        if self.access_token and time.time() < self._token_expires_at - 60:
            logger.debug("使用缓存的access_token")
            return self.access_token
        
        logger.info(f"正在获取飞书access_token, app_id={self.app_id[:8]}...")
        url = f"{self.base_url}/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }
        
        try:
            response = self.session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') != 0:
                error_msg = f"获取token失败: {data.get('msg')} (code={data.get('code')})"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            self.access_token = data['tenant_access_token']
            expire = data.get('expire', 7200)
            self._token_expires_at = time.time() + expire
            logger.info(f"成功获取access_token, 有效期{expire}秒")
            return self.access_token
        except requests.exceptions.RequestException as e:
            logger.error(f"请求飞书API失败: {str(e)}")
            raise
    
    def get_doc_content(self, doc_token: str, doc_type: str = 'doc', doc_url: str = None) -> str:
        """
        获取飞书文档内容
        :param doc_token: 文档token（从URL中提取）
        :param doc_type: 文档类型 (doc/sheet/wiki)
        :param doc_url: 完整飞书文档URL（qadoc 优先用）
        :return: 文档Markdown内容
        """
        logger.info(f"开始获取文档内容, token={doc_token}, type={doc_type}")

        # 优先走 qadoc 内部网关（docx/wiki 支持，返回 Markdown + 关联文档 URL 不丢失）
        qadoc_doc_url = doc_url
        if not qadoc_doc_url:
            # 构造标准 URL
            domain = self.domain or 'xueqiu.feishu.cn'
            type_path = {'doc': 'docx', 'wiki': 'wiki', 'sheet': 'sheets'}.get(doc_type, 'docx')
            qadoc_doc_url = f"https://{domain}/{type_path}/{doc_token}"

        if doc_type in ('doc', 'wiki'):
            try:
                qadoc_result = self._qadoc_fetch(qadoc_doc_url)
                if qadoc_result and qadoc_result['content']:
                    content = qadoc_result['content']
                    logger.info(f"qadoc拉取成功, 长度={len(content)}字符")
                    # 缓存关联文档 URL 供 get_related_doc_urls 使用
                    self._cached_related_urls = qadoc_result.get('related_urls', [])
                    return content
            except Exception as e:
                logger.warning(f"qadoc拉取失败(重试后): {e}, 降级飞书OpenAPI")

        # qadoc 降级：飞书 OpenAPI
        logger.info(f"降级飞书OpenAPI, token={doc_token}, type={doc_type}")
        token = self.get_access_token()
        try:
            if doc_type == 'doc':
                content = self._get_doc_text(doc_token, token)
            elif doc_type == 'sheet':
                content = self._get_sheet_content(doc_token, token)
            elif doc_type == 'wiki':
                content = self._get_wiki_content(doc_token, token)
            else:
                raise ValueError(f"不支持的文档类型: {doc_type}")

            logger.info(f"飞书OpenAPI拉取成功, 长度={len(content)}字符")
            return content
        except Exception as e:
            logger.error(f"获取文档内容失败: {str(e)}")
            raise

    def get_related_doc_urls(self, doc_token: str, doc_type: str = 'doc', doc_url: str = None) -> list:
        """获取关联文档 URL 列表
        优先从 qadoc 的 sub_document_ids 获取（最准），
        降级：用 blocks API 递归扫 mention_doc（飞书 raw_content 会丢 URL）
        """
        # 1. 优先用 get_doc_content 已缓存的 qadoc 结果
        cached = getattr(self, '_cached_related_urls', None)
        if cached is not None:
            return cached

        # 2. qadoc 独立拉取
        if doc_type in ('doc', 'wiki'):
            domain = self.domain or 'xueqiu.feishu.cn'
            type_path = {'doc': 'docx', 'wiki': 'wiki'}.get(doc_type, 'docx')
            url = doc_url or f"https://{domain}/{type_path}/{doc_token}"
            try:
                qadoc_result = self._qadoc_fetch(url)
                if qadoc_result:
                    return qadoc_result.get('related_urls', [])
            except Exception as e:
                logger.warning(f"qadoc拉取关联文档失败(重试后): {e}, 降级blocks API")

        # 3. 降级：blocks API 递归扫 mention_doc
        logger.info(f"qadoc不可用，降级blocks API提取关联文档, token={doc_token}")
        return self._extract_related_via_blocks(doc_token, doc_type)

    @retry(max_retries=3)
    def _extract_related_via_blocks(self, doc_token: str, doc_type: str) -> list:
        """从 blocks API 递归提取 mention_doc 内嵌文档卡片的 URL 列表（降级路径）"""
        logger.info(f"提取关联文档URL(blocks API), token={doc_token}, type={doc_type}")
        token = self.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        # wiki → 先拿 obj_token (docx)
        if doc_type == 'wiki':
            node_resp = self.session.get(
                f"{self.base_url}/wiki/v2/spaces/get_node?token={doc_token}", headers=headers, timeout=10
            )
            node_data = node_resp.json()
            if node_data.get('code') != 0:
                return []
            obj_token = node_data['data']['node']['obj_token']
            doc_type = 'doc'
            doc_token = obj_token

        if doc_type != 'doc':
            return []

        try:
            meta_resp = self.session.get(
                f"{self.base_url}/docx/v1/documents/{doc_token}", headers=headers, timeout=10
            )
            meta_data = meta_resp.json()
            if meta_data.get('code') != 0:
                return []
            document_id = meta_data['data']['document']['document_id']

            blocks_resp = self.session.get(
                f"{self.base_url}/docx/v1/documents/{document_id}/blocks?page_size=500",
                headers=headers, timeout=15
            )
            blocks_data = blocks_resp.json()
            if blocks_data.get('code') != 0:
                return []
            blocks = blocks_data.get('data', {}).get('items', [])

            def _walk(obj):
                found = []
                if isinstance(obj, dict):
                    md = obj.get('mention_doc')
                    if isinstance(md, dict) and md.get('url'):
                        found.append(md['url'])
                    for v in obj.values():
                        found.extend(_walk(v))
                elif isinstance(obj, list):
                    for item in obj:
                        found.extend(_walk(item))
                return found

            urls = list(dict.fromkeys(_walk(blocks)))
            logger.info(f"blocks API发现 {len(urls)} 个关联文档URL")
            return urls
        except Exception as e:
            logger.warning(f"blocks API提取关联文档URL失败: {str(e)}")
            return []

    def get_doc_title(self, doc_token: str, doc_type: str = 'doc') -> str:
        """获取飞书文档标题"""
        logger.info(f"获取文档标题, token={doc_token}, type={doc_type}")
        token = self.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        try:
            if doc_type == 'wiki':
                # wiki 需要先查节点获取 obj_token + obj_type
                node_resp = self.session.get(
                    f"{self.base_url}/wiki/v2/spaces/get_node?token={doc_token}", headers=headers, timeout=10
                )
                node_data = node_resp.json()
                if node_data.get('code') == 0:
                    return node_data['data']['node'].get('title', '')
                return ''
            elif doc_type == 'sheet':
                meta_resp = self.session.get(
                    f"{self.base_url}/sheets/v3/spreadsheets/{doc_token}", headers=headers, timeout=10
                )
                meta_data = meta_resp.json()
                if meta_data.get('code') == 0:
                    return meta_data['data']['spreadsheet'].get('title', '')
                return ''
            else:
                # docx: 先拿 document_id 再查 title
                meta_resp = self.session.get(
                    f"{self.base_url}/docx/v1/documents/{doc_token}", headers=headers, timeout=10
                )
                meta_data = meta_resp.json()
                if meta_data.get('code') == 0:
                    return meta_data['data']['document'].get('title', '')
                return ''
        except Exception as e:
            logger.error(f"获取文档标题失败: {str(e)}")
            return ''

    @retry(max_retries=3)
    def _get_doc_text(self, doc_token: str, token: str) -> str:
        """获取云文档文本内容"""
        logger.debug(f"获取云文档元数据, doc_token={doc_token}")
        # 1. 获取文档元数据
        meta_url = f"{self.base_url}/docx/v1/documents/{doc_token}"
        headers = {"Authorization": f"Bearer {token}"}
        
        try:
            meta_response = self.session.get(meta_url, headers=headers, timeout=10)
            meta_response.raise_for_status()
            meta_data = meta_response.json()
            
            if meta_data.get('code') != 0:
                error_msg = f"获取文档元数据失败: {meta_data.get('msg')}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            document_id = meta_data['data']['document']['document_id']
            logger.debug(f"获取到document_id={document_id}")
            
            # 2. 获取文档内容（迭代器方式）
            content_url = f"{self.base_url}/docx/v1/documents/{document_id}/raw_content"
            params = {"lang": 0}  # 0=Markdown格式
            
            logger.debug(f"获取文档内容, URL={content_url}")
            content_response = self.session.get(content_url, headers=headers, params=params, timeout=10)
            content_response.raise_for_status()
            content_data = content_response.json()
            
            if content_data.get('code') != 0:
                error_msg = f"获取文档内容失败: {content_data.get('msg')}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            # 提取Markdown内容
            markdown = content_data['data']['content']
            logger.debug(f"成功提取Markdown内容, 长度={len(markdown)}")
            return markdown
        except requests.exceptions.RequestException as e:
            logger.error(f"请求飞书文档API失败: {str(e)}")
            raise
    
    @retry(max_retries=3)
    def _get_sheet_content(self, sheet_token: str, token: str) -> str:
        """获取电子表格内容（简化版，返回CSV格式）"""
        logger.debug(f"获取电子表格元数据, sheet_token={sheet_token}")
        # 获取电子表格元数据
        meta_url = f"{self.base_url}/sheets/v2/spreadsheets/{sheet_token}"
        headers = {"Authorization": f"Bearer {token}"}
        
        try:
            meta_response = self.session.get(meta_url, headers=headers, timeout=10)
            meta_response.raise_for_status()
            meta_data = meta_response.json()
            
            if meta_data.get('code') != 0:
                error_msg = f"获取表格元数据失败: {meta_data.get('msg')}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            # 简化处理：只获取第一个工作表
            sheets = meta_data['data']['sheets']
            if not sheets:
                logger.warning("电子表格中没有工作表")
                return ""
            
            sheet_id = sheets[0]['sheet_id']
            logger.debug(f"获取到第一个工作表, sheet_id={sheet_id}")
            
            # 获取单元格数据
            range_url = f"{self.base_url}/sheets/v2/spreadsheets/{sheet_token}/values/{sheet_id}!A1:Z1000"
            logger.debug(f"获取单元格数据, 范围=A1:Z1000")
            range_response = self.session.get(range_url, headers=headers, timeout=10)
            range_response.raise_for_status()
            range_data = range_response.json()
            
            if range_data.get('code') != 0:
                error_msg = f"获取表格数据失败: {range_data.get('msg')}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            # 转换为CSV格式
            values = range_data['data']['value_range']['values']
            csv_lines = []
            for row in values:
                csv_lines.append(','.join(str(cell) for cell in row))
            
            result = '\n'.join(csv_lines)
            logger.debug(f"成功转换表格为CSV格式, 行数={len(csv_lines)}")
            return result
        except requests.exceptions.RequestException as e:
            logger.error(f"请求飞书表格API失败: {str(e)}")
            raise
    
    @retry(max_retries=3)
    def _get_wiki_content(self, wiki_token: str, token: str) -> str:
        """获取知识库节点内容"""
        logger.debug(f"获取知识库节点元数据, wiki_token={wiki_token}")
        # 获取知识库节点元数据
        meta_url = f"{self.base_url}/wiki/v2/spaces/get_node"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"token": wiki_token}
        
        try:
            meta_response = self.session.get(meta_url, headers=headers, params=params, timeout=10)
            meta_response.raise_for_status()
            meta_data = meta_response.json()
            
            if meta_data.get('code') != 0:
                error_msg = f"获取知识库节点失败: {meta_data.get('msg')}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            node = meta_data['data']['node']
            node_type = node.get('obj_type', 'doc')
            obj_token = node.get('obj_token')
            
            logger.debug(f"知识库节点类型={node_type}, obj_token={obj_token}")
            
            if not obj_token:
                logger.error("知识库节点无关联文档")
                raise Exception("知识库节点无关联文档")
            
            # 根据节点类型获取内容
            logger.info(f"知识库节点关联文档类型={node_type}, 开始获取内容")
            if node_type == 'doc':
                return self._get_doc_text(obj_token, token)
            elif node_type == 'sheet':
                return self._get_sheet_content(obj_token, token)
            else:
                # 尝试作为普通文档处理
                logger.warning(f"未知的知识库节点类型={node_type}, 尝试作为doc处理")
                return self._get_doc_text(obj_token, token)
        except requests.exceptions.RequestException as e:
            logger.error(f"请求飞书知识库API失败: {str(e)}")
            raise
    
    def create_doc(self, title: str, folder_token: str, content: str = '') -> dict:
        """
        在指定文件夹创建飞书文档并写入内容
        :param title: 文档标题
        :param folder_token: 目标文件夹token
        :param content: Markdown内容
        :return: {'document_id': str, 'url': str}
        """
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        logger.info(f"创建飞书文档: title={title}, folder={folder_token}")
        
        # 1. 创建空文档
        create_url = f"{self.base_url}/docx/v1/documents"
        payload = {"title": title, "folder_token": folder_token}
        
        try:
            resp = self.session.post(create_url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get('code') != 0:
                raise Exception(f"创建文档失败: {data.get('msg')} (code={data.get('code')})")
            
            document_id = data['data']['document']['document_id']
            # 构造文档URL（需要FEISHU_DOMAIN环境变量，未配置则只返回document_id）
            if self.domain:
                doc_url = f"https://{self.domain}/docx/{document_id}"
            else:
                doc_url = document_id
                logger.warning("FEISHU_DOMAIN未配置，返回document_id而非完整URL")
            logger.info(f"文档已创建: {doc_url}")
            
            # 2. 写入内容（按段落拆分为text blocks）
            if content:
                self._write_blocks(document_id, content, headers)
            
            return {'document_id': document_id, 'url': doc_url}
        except requests.exceptions.RequestException as e:
            logger.error(f"创建飞书文档失败: {str(e)}")
            raise
    
    def _write_blocks(self, document_id: str, markdown_content: str, headers: dict):
        """将Markdown内容转换为飞书block写入文档
        - # 标题 → 粗体文本
        - - 列表 → 前缀 "• "
        - 1. 有序 → 保留编号
        - --- → 跳过（用空行代替）
        - > 引用 → 去前缀按普通文本
        - |表格| → 飞书原生表格block（block_type=31）
        """
        lines = markdown_content.split('\n')
        items = []  # 元素: block dict 或 ('table', [cells_per_row])
        table_buffer = []
        
        def flush_table():
            if table_buffer:
                items.append(('table', self._parse_table_rows(table_buffer)))
                table_buffer.clear()
        
        for line in lines:
            stripped = line.strip()
            
            # 空行：flush表格buffer
            if not stripped:
                flush_table()
                continue
            
            # 分割线 --- → 跳过（空行已足够分隔）
            if stripped in ('---', '***', '___'):
                flush_table()
                continue
            
            # 引用块 > xxx → 去除前缀后按普通文本处理
            if stripped.startswith('>'):
                stripped = stripped.lstrip('>').strip()
                if not stripped:
                    continue
            
            # 表格行 |...|
            if stripped.startswith('|') and stripped.endswith('|'):
                # 跳过分隔行 |---|---|
                if re.match(r'^\|[\s\-:|]+\|$', stripped):
                    continue
                table_buffer.append(stripped)
                continue
            
            # flush表格
            flush_table()
            
            # 标题 # ## ### #### → 粗体文本
            heading_match = re.match(r'^(#{1,4})\s+(.+)', stripped)
            if heading_match:
                text = heading_match.group(2).strip()
                # 用粗体标记标题
                items.append({
                    "block_type": 2,
                    "text": {
                        "elements": [{"text_run": {"content": text, "text_element_style": {"bold": True}}}],
                        "style": {}
                    }
                })
                continue
            
            # 无序列表 - 或 * → 前缀 "• "
            bullet_match = re.match(r'^(\s*)[*-]\s+(.+)', line)
            if bullet_match:
                text = bullet_match.group(2)
                items.append({
                    "block_type": 2,
                    "text": {
                        "elements": [{"text_run": {"content": "• ", "text_element_style": {}}}] + self._parse_inline(text),
                        "style": {}
                    }
                })
                continue
            
            # 有序列表 1. 2. 等 → 保留编号前缀
            ordered_match = re.match(r'^(\s*)(\d+)[.)]\s+(.+)', line)
            if ordered_match:
                num = ordered_match.group(2)
                text = ordered_match.group(3)
                items.append({
                    "block_type": 2,
                    "text": {
                        "elements": [{"text_run": {"content": f"{num}. ", "text_element_style": {}}}] + self._parse_inline(text),
                        "style": {}
                    }
                })
                continue
            
            # 普通文本
            items.append({
                "block_type": 2,
                "text": {
                    "elements": self._parse_inline(stripped),
                    "style": {}
                }
            })
        
        # flush剩余表格
        flush_table()
        
        self._flush_items(document_id, items, headers)

    def _flush_items(self, document_id: str, items: list, headers: dict):
        """将block元素列表写入文档（普通block批量50个/批，表格单独创建）
        items元素: block dict 或 ('table', rows)
        """
        if not items:
            return
        
        blocks_url = f"{self.base_url}/docx/v1/documents/{document_id}/blocks/{document_id}/children"
        pending = []
        
        def flush_blocks():
            """批量写入累积的普通block（飞书API限制每次最多50个）"""
            for i in range(0, len(pending), 50):
                batch = pending[i:i+50]
                resp = self.session.post(blocks_url, json={"children": batch, "index": -1}, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                if data.get('code') != 0:
                    logger.warning(f"写入block批次失败: {data.get('msg')}")
                else:
                    logger.debug(f"写入block批次成功, {len(batch)}个块")
            pending.clear()
        
        for item in items:
            if isinstance(item, tuple) and item[0] == 'table':
                # 表格必须单独创建，才能拿到cell id并填充内容
                flush_blocks()
                self._write_table_block(document_id, blocks_url, item[1], headers)
            else:
                pending.append(item)
        flush_blocks()

    def create_doc_from_struct(self, title: str, folder_token: str, struct_blocks: list) -> dict:
        """【结构化优先链路】由业务结构化JSON直接生成飞书文档，彻底规避Markdown解析
        struct_blocks: 业务中间JSON数组（7种节点: h1/h2/h3/paragraph/bullet_list/ordered_list/table），
            不感知飞书API细节，由翻译层转换为飞书原生block
        :return: {'document_id': str, 'url': str}
        """
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        logger.info(f"[struct模式]创建飞书文档: title={title}, nodes={len(struct_blocks)}个")
        
        # 1. 创建空文档（复用原有逻辑）
        create_url = f"{self.base_url}/docx/v1/documents"
        payload = {"title": title, "folder_token": folder_token}
        
        try:
            resp = self.session.post(create_url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get('code') != 0:
                raise Exception(f"创建文档失败: {data.get('msg')} (code={data.get('code')})")
            
            document_id = data['data']['document']['document_id']
            if self.domain:
                doc_url = f"https://{self.domain}/docx/{document_id}"
            else:
                doc_url = document_id
                logger.warning("FEISHU_DOMAIN未配置，返回document_id而非完整URL")
            logger.info(f"[struct模式]文档已创建: {doc_url}")
            
            # 2. 翻译并写入block
            items = self._translate_struct_to_items(struct_blocks)
            self._flush_items(document_id, items, headers)
            logger.info("[struct模式]全部内容写入完成")
            return {'document_id': document_id, 'url': doc_url}
        except requests.exceptions.RequestException as e:
            logger.error(f"创建飞书文档失败: {str(e)}")
            raise

    def _translate_struct_to_items(self, struct_blocks: list) -> list:
        """翻译层：业务中间JSON → 飞书docx原生block元素列表
        - h1/h2/h3 → 原生标题block(block_type=3/4/5，文档目录可识别)
        - bullet_list/ordered_list → 原生列表block(block_type=12/13，逐项平铺)
        - paragraph → text block(复用_parse_inline处理少量行内格式)
        - code → 代码块block(block_type=14, language=1纯文本，保留换行空格，承载对齐表格)
        - table → ('table', rows)，由_flush_items调用已有_write_table_block
          （拆分>8行/逐格填充/429重试/降级全部复用）
        """
        # 飞书docx v1实测: heading1=3, heading2=4, heading3=5, bullet=12, ordered=13
        heading_types = {'h1': (3, 'heading1'), 'h2': (4, 'heading2'), 'h3': (5, 'heading3')}
        items = []
        for node in struct_blocks:
            ntype = node.get('type')
            if ntype in heading_types:
                bt, key = heading_types[ntype]
                items.append({"block_type": bt, key: {
                    "elements": [{"text_run": {"content": node.get('text', '')}}],
                    "style": {}}})
            elif ntype == 'paragraph':
                items.append({"block_type": 2, "text": {
                    "elements": self._parse_inline(node.get('text', '')),
                    "style": {}}})
            elif ntype == 'bullet_list':
                for item_text in node.get('items', []):
                    items.append({"block_type": 12, "bullet": {
                        "elements": self._parse_inline(str(item_text)),
                        "style": {}}})
            elif ntype == 'ordered_list':
                for item_text in node.get('items', []):
                    items.append({"block_type": 13, "ordered": {
                        "elements": self._parse_inline(str(item_text)),
                        "style": {}}})
            elif ntype == 'table':
                full_rows = [node.get('headers', [])] + node.get('rows', [])
                if full_rows[0]:
                    items.append(('table', full_rows))
            elif ntype == 'code':
                # 实测: code块block_type=14, style.language=1为PlainText，等宽字体保留对齐
                items.append({"block_type": 14, "code": {
                    "elements": [{"text_run": {"content": node.get('text', '')}}],
                    "style": {"language": 1}}})
            else:
                logger.warning(f"[struct模式]未知节点类型 {ntype}, 跳过")
        return items

    def _parse_table_rows(self, table_rows: list) -> list:
        """解析Markdown表格行为二维单元格数组
        单元格内未转义的|会导致分列过多，按表头列数合并多余列
        """
        parsed = []
        col_count = 0
        for row in table_rows:
            cells = [c.strip() for c in row.strip('|').split('|')]
            if not col_count:
                col_count = len(cells)
                parsed.append(cells)
            elif len(cells) == col_count:
                parsed.append(cells)
            elif len(cells) > col_count and col_count > 0:
                # 分列过多（单元格内含|）：多余的并入最后一列
                merged = cells[:col_count-1] + [' | '.join(cells[col_count-1:])]
                parsed.append(merged)
            else:
                # 列数不足：补空列
                parsed.append(cells + [''] * (col_count - len(cells)))
        return parsed

    # 飞书表格block创建限制：单次创建的表格单元格总数有上限（实测9x6=54格OK，10x6=60格报400）
    # 超过限制的大表格拆分为多个小表格（每段重复表头）依次写入
    _TABLE_MAX_ROWS_PER_BLOCK = 8
    
    def _write_table_block(self, document_id: str, blocks_url: str, rows: list, headers: dict):
        """创建飞书原生表格block并逐格填充内容；失败时降级为逐行文本
        大表格（超过_TABLE_MAX_ROWS_PER_BLOCK数据行）拆分为多段，每段重复表头
        """
        if not rows:
            return
        header, data_rows = rows[0], rows[1:]
        max_data = max(self._TABLE_MAX_ROWS_PER_BLOCK, 1)
        segments = []
        if len(data_rows) > max_data:
            for i in range(0, len(data_rows), max_data):
                segments.append([header] + data_rows[i:i+max_data])
            logger.info(f"表格过大({len(rows)}行)，拆分为{len(segments)}个表格写入")
        else:
            segments = [rows]
        
        for seg in segments:
            self._write_single_table(document_id, blocks_url, seg, headers)
    
    def _write_single_table(self, document_id: str, blocks_url: str, rows: list, headers: dict):
        """创建单个飞书原生表格block并逐格填充内容；失败时降级为逐行文本"""
        row_size, col_size = len(rows), max(len(r) for r in rows)
        
        try:
            payload = {"children": [{
                "block_type": 31,
                "table": {"property": {"row_size": row_size, "column_size": col_size}}
            }], "index": -1}
            resp = self.session.post(blocks_url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get('code') != 0:
                raise Exception(data.get('msg'))
            cells = data['data']['children'][0]['table']['cells']
        except Exception as e:
            logger.warning(f"创建表格block失败，降级为文本行: {e}")
            self._write_table_fallback(blocks_url, rows, headers)
            return
        
        # 逐格写入（首行表头加粗）；控制频率避免触发飞书429限流，429时退避重试
        for r, row in enumerate(rows):
            for c in range(col_size):
                cell_id = cells[r * col_size + c]
                text = row[c] if c < len(row) else ''
                if not text:
                    continue
                if r == 0:
                    # 表头整格加粗：先去掉markdown符号（**、`），避免原样显示
                    elements = [{"text_run": {"content": text.replace('`', '').replace('**', ''), "text_element_style": {"bold": True}}}]
                else:
                    elements = self._parse_inline(text)
                cell_url = f"{self.base_url}/docx/v1/documents/{document_id}/blocks/{cell_id}/children"
                cell_payload = {"children": [{"block_type": 2, "text": {"elements": elements, "style": {}}}], "index": -1}
                self._post_cell_with_retry(cell_url, cell_payload, headers, r, c)
                time.sleep(0.12)  # 限流保护：单元格写入间隔
        logger.info(f"原生表格写入完成: {row_size}行x{col_size}列")
    
    def _post_cell_with_retry(self, cell_url: str, payload: dict, headers: dict, r: int, c: int, max_retries: int = 3):
        """写入表格单元格，遇429限流退避重试"""
        for attempt in range(max_retries + 1):
            try:
                cr = self.session.post(cell_url, json=payload, headers=headers, timeout=10)
                if cr.status_code == 429:
                    if attempt < max_retries:
                        wait = 1.5 * (attempt + 1)
                        logger.warning(f"表格单元格写入限流[{r},{c}]，{wait:.1f}s后重试({attempt+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    logger.warning(f"表格单元格写入限流重试耗尽[{r},{c}]")
                    return
                cr.raise_for_status()
                if cr.json().get('code') != 0:
                    logger.warning(f"表格单元格写入失败[{r},{c}]: {cr.json().get('msg')}")
                return
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 429 and attempt < max_retries:
                    wait = 1.5 * (attempt + 1)
                    logger.warning(f"表格单元格写入限流[{r},{c}]，{wait:.1f}s后重试({attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                logger.warning(f"表格单元格写入异常[{r},{c}]: {e}")
                return
            except Exception as e:
                logger.warning(f"表格单元格写入异常[{r},{c}]: {e}")
                return

    def _write_table_fallback(self, blocks_url: str, rows: list, headers: dict):
        """表格降级方案：每行一个文本block"""
        blocks = []
        for r, row in enumerate(rows):
            content = '  |  '.join(row)
            if r == 0:
                elements = [{"text_run": {"content": content, "text_element_style": {"bold": True}}}]
            else:
                elements = self._parse_inline(content)
            blocks.append({"block_type": 2, "text": {"elements": elements, "style": {}}})
        for i in range(0, len(blocks), 50):
            batch = blocks[i:i+50]
            resp = self.session.post(blocks_url, json={"children": batch, "index": -1}, headers=headers, timeout=15)
            resp.raise_for_status()
            if resp.json().get('code') != 0:
                logger.warning(f"表格降级写入失败: {resp.json().get('msg')}")

    def _parse_inline(self, text: str) -> list:
        """解析行内Markdown格式(**bold**, `code`, *italic*)为飞书elements"""
        elements = []
        # 正则匹配 **bold**, `code`, *italic*
        pattern = r'(\*\*(.+?)\*\*|`([^`]+)`|\*(.+?)\*)'
        last_end = 0
        
        for m in re.finditer(pattern, text):
            # 匹配前的普通文本
            if m.start() > last_end:
                plain = text[last_end:m.start()]
                if plain:
                    elements.append({"text_run": {"content": plain, "text_element_style": {}}})
            
            if m.group(2):  # **bold**
                elements.append({"text_run": {"content": m.group(2), "text_element_style": {"bold": True}}})
            elif m.group(3):  # `code`
                elements.append({"text_run": {"content": m.group(3), "text_element_style": {"inline_code": True}}})
            elif m.group(4):  # *italic*
                elements.append({"text_run": {"content": m.group(4), "text_element_style": {"italic": True}}})
            
            last_end = m.end()
        
        # 剩余文本
        if last_end < len(text):
            remaining = text[last_end:]
            if remaining:
                elements.append({"text_run": {"content": remaining, "text_element_style": {}}})
        
        if not elements:
            elements.append({"text_run": {"content": text, "text_element_style": {}}})
        
        return elements

    def send_message(self, receive_id: str, msg_type: str, content: str, receive_id_type: str = 'chat_id') -> dict:
        """
        发送消息
        :param receive_id: 接收者ID（chat_id/user_id/open_id）
        :param msg_type: 消息类型（text/interactive）
        :param content: 消息内容JSON字符串
        :param receive_id_type: ID类型
        :return: API响应
        """
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        url = f"{self.base_url}/im/v1/messages"
        params = {"receive_id_type": receive_id_type}
        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content
        }
        
        try:
            resp = self.session.post(url, json=payload, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get('code') != 0:
                logger.error(f"发送消息失败: {data.get('msg')}")
                raise Exception(f"发送消息失败: {data.get('msg')}")
            logger.info(f"消息发送成功")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"发送飞书消息失败: {e}")
            raise
    
    def reply_message(self, message_id: str, msg_type: str, content: str) -> dict:
        """
        回复消息
        :param message_id: 要回复的消息ID
        :param msg_type: 消息类型（text/interactive）
        :param content: 消息内容JSON字符串
        :return: API响应
        """
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        url = f"{self.base_url}/im/v1/messages/{message_id}/reply"
        payload = {
            "msg_type": msg_type,
            "content": content
        }
        
        try:
            resp = self.session.post(url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get('code') != 0:
                logger.error(f"回复消息失败: {data.get('msg')}")
                raise Exception(f"回复消息失败: {data.get('msg')}")
            logger.info(f"消息回复成功")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"回复飞书消息失败: {e}")
            raise
    
    @staticmethod
    def format_card(title: str, content: str, scenarios: list = None) -> str:
        """
        构建飞书交互式卡片JSON
        :param title: 卡片标题
        :param content: 主内容文本
        :param scenarios: 测试场景列表（可选）
        :return: JSON字符串
        """
        import json as _json
        
        elements = []
        
        # 主内容
        if content:
            elements.append({
                "tag": "markdown",
                "content": content
            })
        
        # 场景列表
        if scenarios:
            elements.append({"tag": "hr"})
            for s in scenarios[:10]:
                priority = s.get('priority', 'medium')
                desc = s.get('description', '')
                points = ', '.join(s.get('test_points', []))
                elements.append({
                    "tag": "markdown",
                    "content": f"**[{priority}]** {desc}\n测试点: {points}"
                })
            if len(scenarios) > 10:
                elements.append({
                    "tag": "markdown",
                    "content": f"... 还有 {len(scenarios)-10} 个场景"
                })
        
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue"
            },
            "elements": elements
        }
        
        return _json.dumps(card, ensure_ascii=False)
    
    @staticmethod
    def parse_doc_url(url: str) -> tuple:
        """
        解析飞书文档URL，提取token和类型
        :param url: 飞书文档链接
        :return: (doc_token, doc_type)
        
        示例URL:
        - https://xxx.feishu.cn/docx/xxxxx
        - https://xxx.feishu.cn/sheets/xxxxx
        - https://xxx.feishu.cn/wiki/xxxxx
        """
        logger.debug(f"解析飞书URL: {url}")
        
        # 匹配wiki（知识库）
        wiki_match = re.search(r'/wiki/([a-zA-Z0-9]+)', url)
        if wiki_match:
            token = wiki_match.group(1)
            logger.info(f"识别为wiki文档, token={token}")
            return token, 'wiki'
        
        # 匹配docx
        docx_match = re.search(r'/docx/([a-zA-Z0-9]+)', url)
        if docx_match:
            token = docx_match.group(1)
            logger.info(f"识别为docx文档, token={token}")
            return token, 'doc'
        
        # 匹配sheets
        sheet_match = re.search(r'/sheets/([a-zA-Z0-9]+)', url)
        if sheet_match:
            token = sheet_match.group(1)
            logger.info(f"识别为sheet文档, token={token}")
            return token, 'sheet'
        
        # 匹配旧版docs
        docs_match = re.search(r'/docs?/([a-zA-Z0-9]+)', url)
        if docs_match:
            token = docs_match.group(1)
            logger.info(f"识别为旧版docs文档, token={token}")
            return token, 'doc'
        
        logger.error(f"无法解析飞书文档URL: {url}")
        raise ValueError(f"无法解析飞书文档URL: {url}")
