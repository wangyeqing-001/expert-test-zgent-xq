"""需求分析Agent - 解析业务需求，输出Markdown需求分析规格书"""

import os
import re
import json
import time
from typing import Any
from datetime import datetime

from agents.base_agent import BaseAgent
from agents._prompt_utils import build_prompt
from utils.code_analyzer import CodeAnalyzer
from core.feishu_client import FeishuClient
from core.structured_doc import parse_struct_json, struct_to_markdown

_DIR = os.path.dirname(os.path.abspath(__file__))

# 灰度开关：是否使用结构化 JSON 输出（新链路）
USE_STRUCT_OUTPUT = os.getenv('USE_STRUCT_OUTPUT', 'true').lower() not in ('false', '0', 'off')


def _build_parse_prompt(prd_content: str, max_length: int = 4000, prompt_file: str = 'prompts.md') -> str:
    """构建解析 Prompt，截断过长内容防止 token 超限"""
    return build_prompt(_DIR, prompt_file, prd_content=prd_content[:max_length])


class RequirementAnalyzer(BaseAgent):
    """需求分析Agent：从飞书文档/PRD文本/代码文件中提取需求，生成结构化需求规格书"""

    def __init__(self, llm_client=None, feishu_client=None, output_dir='generated_requirements'):
        super().__init__(name="RequirementAnalyzer", llm_client=llm_client)
        self.feishu_client = feishu_client
        self.output_dir = output_dir
        self.feishu_folder = os.getenv('FEISHU_OUTPUT_FOLDER', '')
        os.makedirs(output_dir, exist_ok=True)

    # ==================== 核心执行入口 ====================

    def execute(self, input_data: dict) -> dict:
        """结构化输入执行入口（代码文件分析链路）"""
        if not self.validate_input(input_data):
            raise ValueError("Invalid input: file_path required")

        file_path = input_data['file_path']
        test_type = input_data.get('test_type', 'web')
        title = input_data.get('title') or f"需求分析-{os.path.basename(file_path)}"

        # 代码静态分析
        analyzer = CodeAnalyzer(file_path)
        functions = analyzer.extract_functions()
        classes = analyzer.extract_classes()
        code_content = analyzer.get_code_content()

        # LLM 生成需求分析文档
        markdown, blocks = self._generate_markdown(code_content, functions, test_type)

        # 保存与发布
        local_path, feishu_url = self._save_and_publish(markdown, title, blocks)

        self.state = {
            'file_path': file_path,
            'test_type': test_type,
            'function_count': len(functions),
            'class_count': len(classes),
        }

        return {
            'markdown': markdown,
            'local_path': local_path,
            'feishu_url': feishu_url,
            'metadata': self.state,
        }

    def validate_input(self, input_data: Any) -> bool:
        """校验输入数据"""
        if not isinstance(input_data, dict):
            return False
        return 'file_path' in input_data

    # ==================== 自然语言入口 ====================

    def process_query(self, query: str, title: str = None, feishu_url: str = None) -> dict:
        """
        自然语言入口：自动识别输入类型（飞书链接 / PRD文本 / 代码路径）
        并路由到对应处理链路

        :param feishu_url: 外部已解析的飞书文档链接（run_requirement.py 等入口手动拉内容时传入），
                          用于在纯文本路径下也能拉取 mention_doc 关联文档
        """
        # 1. 优先检查是否为飞书文档链接
        feishu_doc_url = self._extract_feishu_url(query) or feishu_url
        if feishu_doc_url:
            return self._process_feishu_doc(feishu_doc_url, title)

        # 2. 用 LLM 解析用户意图
        parsed = self._parse_query(query)

        # 3. 判断是否为 PRD 文档内容
        if not parsed.get('file_path') and (parsed.get('is_prd') or len(query) > 200):
            doc_title = title or "需求分析文档"
            raw = self._clean_doc_content(query)
            # 优先从 blocks API 提取 mention_doc（raw_content Markdown 会丢内嵌文档 URL）
            related_urls = []
            if feishu_url and self.feishu_client:
                try:
                    token, dtype = FeishuClient.parse_doc_url(feishu_url)
                    related_urls = self.feishu_client.get_related_doc_urls(token, dtype)
                except Exception as e:
                    print(f"[RequirementAnalyzer] 提取关联文档URL失败: {e}")
            raw = self._extract_and_merge_related_docs(raw, related_urls)
            markdown, blocks = self._analyze_prd_content(raw)
            local_path, feishu_url_out = self._save_and_publish(markdown, doc_title, blocks)

            return {
                'markdown': markdown,
                'local_path': local_path,
                'feishu_url': feishu_url_out,
                'raw_content': raw,  # 保留清洗后的 PRD 全文，供下游测试点生成使用
            }

        # 4. 判断是否为代码文件路径
        if parsed.get('file_path'):
            return self.execute({
                'file_path': parsed['file_path'],
                'test_type': parsed.get('test_type', 'web'),
                'title': title,
            })

        raise ValueError("无法识别输入类型，请提供飞书文档链接、PRD文档内容或代码文件路径")

    # ==================== 飞书文档处理 ====================

    def _process_feishu_doc(self, doc_url: str, title: str = None) -> dict:
        """拉取飞书文档内容并分析"""
        if not self.feishu_client:
            raise ValueError("解析飞书文档需要配置 FEISHU_APP_ID / FEISHU_APP_SECRET")

        doc_token, doc_type = FeishuClient.parse_doc_url(doc_url)
        doc_title = title or self.feishu_client.get_doc_title(doc_token, doc_type) or '飞书文档'
        # 传 doc_url：让 FeishuClient 优先走 qadoc 拉取（Markdown 保留内嵌链接 + 关联文档列表）
        content = self.feishu_client.get_doc_content(doc_token, doc_type, doc_url=doc_url)

        if not content or not content.strip():
            raise ValueError(f"飞书文档内容为空，请检查文档权限: {doc_url}")

        # 清洗 + 拉取关联文档（qadoc 优先，已缓存 related_urls）
        content = self._clean_doc_content(content)
        related_urls = self.feishu_client.get_related_doc_urls(doc_token, doc_type, doc_url=doc_url)
        # 收集成功拉取的关联文档（标题+URL），用于最终飞书文档尾部追加索引
        fetched_related = self._extract_and_merge_related_docs(content, related_urls)
        # 从 merged 结果和原始 content 差异中提取关联文档索引
        related_index = self._extract_related_index(content, related_urls)

        markdown, blocks = self._analyze_prd_content(fetched_related)
        # 在 markdown 尾部追加关联文档索引（LLM 分析已整合关联文档信息，索引提供原文回溯入口）
        if related_index:
            markdown = markdown.rstrip() + '\n\n' + self._build_related_index_md(related_index)
            # blocks 末尾也追加关联文档索引节点
            blocks = blocks + self._build_related_index_blocks(related_index)
        local_path, feishu_url = self._save_and_publish(markdown, doc_title, blocks)

        return {
            'markdown': markdown,
            'local_path': local_path,
            'feishu_url': feishu_url,
            'raw_content': fetched_related,
            'related_docs': related_index,
        }

    # ==================== PRD 分析核心 ====================

    def _analyze_prd_content(self, prd_text: str) -> tuple:
        """PRD 分析核心方法，封装重试 + 双链路灰度逻辑"""
        prd_text = self._clean_doc_content(prd_text)
        # 注意：关联文档已在外部拉取，这里不再重复拉取，避免死循环或重复拼接。

        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = self._llm_generate_doc(prd_text, attempt)
                if result:
                    return result
            except Exception as e:
                print(f"[RequirementAnalyzer] 分析失败 (attempt {attempt + 1}): {e}")

        raise ValueError("PRD文档分析失败，已重试2次")

    # ==================== LLM 双链路生成 ====================

    def _llm_generate_doc(self, prd_text: str, attempt: int = 0) -> tuple:
        """
        双链路灰度生成：
        - 新链路（默认）：JSON 结构化输出 → 解析 → 渲染 Markdown
        - 旧链路（降级）：Markdown 直出
        """
        if USE_STRUCT_OUTPUT:
            # === 新链路：JSON 结构化输出 ===
            prompt = _build_parse_prompt(prd_text, prompt_file='prompts.md')
            response = self.llm.generate(prompt, max_tokens=16000)

            try:
                nodes = parse_struct_json(response)
                if nodes:
                    md = struct_to_markdown(nodes)
                    return md, nodes
            except Exception as e:
                print(f"[RequirementAnalyzer] JSON解析失败，降级到Markdown链路: {e}")

            # 降级：JSON 解析/校验失败 → 切旧 Prompt 重新调 LLM 拿 Markdown
            fallback_prompt = _build_parse_prompt(prd_text, prompt_file='prompts_markdown.md')
            fallback_resp = self.llm.generate(fallback_prompt, max_tokens=12000)
            fallback_md = self._strip_code_fence(fallback_resp)
            if len(fallback_md) > 50:
                return fallback_md, None

        else:
            # === 旧链路：Markdown 直出 ===
            prompt = _build_parse_prompt(prd_text, prompt_file='prompts_markdown.md')
            response = self.llm.generate(prompt, max_tokens=12000)
            cleaned = self._strip_code_fence(response)
            if len(cleaned) > 50:
                return cleaned, None

        # 输出过短，触发上层重试
        print(f"[RequirementAnalyzer] LLM输出过短 (attempt {attempt + 1})，将重试")
        return None

    # ==================== 工具方法 ====================

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """去除 LLM 输出中可能包裹的 Markdown 代码块标记"""
        cleaned = re.sub(r'^```\w*\n?', '', text, flags=re.MULTILINE)
        cleaned = re.sub(r'\n?```$', '', cleaned, flags=re.MULTILINE)
        return cleaned.strip()

    def _extract_feishu_url(self, query: str) -> str:
        """从用户输入开头提取飞书文档链接（只匹配 docx/wiki/sheets）"""
        m = re.match(
            r'https?://[\w.-]*feishu\.cn/(?:docx|wiki|sheets)/[a-zA-Z0-9]+',
            query.strip()
        )
        return m.group(0) if m else ''

    # ==================== 文档清洗 ====================

    # 预编译正则模式
    _IMAGE_LINE_RE = re.compile(
        r'^[^\w\u4e00-\u9fa5]*[\w.-]+\.(png|jpe?g|gif|bmp|webp|svg)[^\w\u4e00-\u9fa5]*$',
        re.IGNORECASE
    )
    _MENTION_LINE_RE = re.compile(r'^@[\w\u4e00-\u9fa5._\-]+$')
    _URL_LINE_RE = re.compile(r'^https?://\S+$')
    _BOILERPLATE_KEYWORDS = ('Title Alpha', 'Title Beta', 'Title Ready')

    def _clean_doc_content(self, content: str) -> str:
        """
        清洗文档内容：去除噪声行、压缩空行、清理行内图片引用
        幂等设计，可安全多次调用
        """
        lines = []
        for line in content.split('\n'):
            stripped = line.strip()

            # 空行压缩
            if not stripped:
                if lines and lines[-1] != '':
                    lines.append('')
                continue

            # 跳过纯图片文件名行
            if self._IMAGE_LINE_RE.match(stripped):
                continue

            # 跳过纯 @提及行
            if self._MENTION_LINE_RE.match(stripped):
                continue

            # 纯 URL 行处理：保留飞书文档链接，删除其他
            if self._URL_LINE_RE.match(stripped):
                if 'feishu.cn/' in stripped and any(
                        kw in stripped for kw in ['/docx/', '/wiki/', '/sheets/']
                ):
                    pass  # 保留飞书文档链接（后续需要拉取关联文档）
                else:
                    continue

            # 跳过 PRD 模板套话
            if any(kw in stripped for kw in self._BOILERPLATE_KEYWORDS):
                continue

            # 清理行内图片引用
            line = re.sub(
                r'\b[\w.-]+\.(png|jpe?g|gif|bmp|webp|svg)\b', '', line, flags=re.IGNORECASE
            )
            lines.append(line.rstrip())

        cleaned = '\n'.join(lines).strip()
        removed = len(content) - len(cleaned)
        if removed > 0:
            print(f"[RequirementAnalyzer] 文档清洗完成，移除 {removed} 字符噪声")

        return cleaned

    # ==================== 关联文档拉取（新增） ====================

    def _extract_and_merge_related_docs(self, content: str, related_urls: list = None) -> str:
        """
        从文档正文中提取关联飞书文档链接，拉取内容后拼接到主文档末尾。
        只做一层抓取，不递归；单个关联文档最多 3000 字符；拉取失败不中断。

        :param content: 主文档内容（可能已被 raw_content 降级丢失 mention_doc URL）
        :param related_urls: blocks API 提取的 mention_doc URL 列表（优先使用）
        """
        if not self.feishu_client:
            return content

        # 优先用 blocks API 提取的 URL（mention_doc 不丢失）
        urls = list(related_urls or [])

        # 兜底：从 content 正则匹配显式 URL
        link_pattern = re.compile(
            r'https?://[\w.-]*feishu\.cn/(?:docx|wiki|sheets)/[a-zA-Z0-9]+',
            re.IGNORECASE
        )
        fallback_urls = list(dict.fromkeys(link_pattern.findall(content)))
        for u in fallback_urls:
            if u not in urls:
                urls.append(u)

        if not urls:
            return content

        merged_parts = [content, '\n\n---\n\n## 关联文档内容\n']
        fetched = 0

        for url in urls:
            try:
                doc_token, doc_type = FeishuClient.parse_doc_url(url)
                title = self.feishu_client.get_doc_title(doc_token, doc_type) or '未命名文档'
                doc_content = self.feishu_client.get_doc_content(doc_token, doc_type)

                if not doc_content or not doc_content.strip():
                    print(f"[RequirementAnalyzer] 关联文档内容为空，跳过: {url}")
                    continue

                # 清洗关联文档内容（复用已有清洗逻辑）
                cleaned = self._clean_doc_content(doc_content)

                # 截断防止 token 爆炸（单个关联文档最多 3000 字符）
                if len(cleaned) > 3000:
                    cleaned = cleaned[:3000] + '\n\n...（内容过长，已截断）'

                merged_parts.append(f'### {title}\n> 来源: {url}\n\n{cleaned}\n')
                fetched += 1
                print(f"[RequirementAnalyzer] 成功拉取关联文档: {title}")

            except Exception as e:
                print(f"[RequirementAnalyzer] 拉取关联文档失败，跳过: {url}, 原因: {e}")
                continue

        if fetched == 0:
            return content

        print(f"[RequirementAnalyzer] 共拉取 {fetched} 篇关联文档，已合并到主文档")
        return '\n'.join(merged_parts)

    def _extract_related_index(self, content_before: str, related_urls: list) -> list:
        """从 qadoc content 或 blocks API 提取关联文档的标题+URL 索引
        :return: [{'title': str, 'url': str}, ...] 去重保序
        """
        if not related_urls:
            return []
        found = []
        seen_urls = set()
        # 1. 优先从 content 的 Markdown 链接 [标题](URL) 提取（qadoc content_text 内嵌）
        url_set = set(related_urls)
        md_link_pattern = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+feishu\.cn/(?:docx|wiki|sheets)/[a-zA-Z0-9]+)\)')
        for match in md_link_pattern.finditer(content_before):
            title, url = match.group(1).strip(), match.group(2).strip()
            if url in url_set and url not in seen_urls:
                seen_urls.add(url)
                # 清洗 Markdown 转义残留（\| → |, \. → ., \& → &, &amp; → &）
                title = re.sub(r'\\([|.`*_&])', r'\1', title)
                title = title.replace('&amp;', '&')
                found.append({'title': title, 'url': url})
        # 2. 补充：content 里没匹配到的，尝试用 FeishuClient 拉标题
        for url in related_urls:
            if url in seen_urls:
                continue
            title = None
            if self.feishu_client:
                try:
                    tk, dt = FeishuClient.parse_doc_url(url)
                    title = self.feishu_client.get_doc_title(tk, dt)
                except Exception:
                    pass
            found.append({'title': title or os.path.basename(url.rstrip('/')), 'url': url})
            seen_urls.add(url)
        return found

    @staticmethod
    def _build_related_index_md(related_index: list) -> str:
        """构建关联文档索引 Markdown 段落（追加到 LLM 分析产出尾部）"""
        lines = ['---', '', '## 关联文档索引', '', '以下文档在需求分析时已拉取并整合：', '']
        for i, item in enumerate(related_index, 1):
            lines.append(f'{i}. [{item["title"]}]({item["url"]})')
        return '\n'.join(lines)

    @staticmethod
    def _build_related_index_blocks(related_index: list) -> list:
        """构建关联文档索引的 struct_blocks（用于飞书直写）"""
        blocks = [
            {'type': 'h1', 'text': '关联文档索引'},
            {'type': 'paragraph', 'text': '以下文档在需求分析时已拉取并整合到分析结论中，点击标题可跳转原文：'},
        ]
        for item in related_index:
            blocks.append({
                'type': 'bullet_list',
                'items': [f"[{item['title']}]({item['url']})"]
            })
        return blocks

    # ==================== 意图解析 ====================

    def _parse_query(self, query: str) -> dict:
        """用 LLM（或降级规则）解析用户输入意图：PRD 文档 or 代码文件路径"""
        result = {'file_path': None, 'test_type': 'web', 'is_prd': False}

        # 尝试 LLM 解析
        if self.llm:
            try:
                prompt = (
                    f"你是参数提取助手。从以下用户输入中提取结构化参数，返回 JSON。\n"
                    f"字段：is_prd(bool), file_path(str|null), test_type(web|mobile|api)\n"
                    f"用户输入：{query[:500]}\n"
                    f"只返回 JSON，不要解释。"
                )
                response = self.llm.generate(prompt)
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group(0))
                    if parsed.get('is_prd'):
                        return {'file_path': None, 'test_type': 'web', 'is_prd': True}
                    if parsed.get('file_path'):
                        result['file_path'] = parsed['file_path']
                    if parsed.get('test_type'):
                        result['test_type'] = parsed['test_type']
                    return result
            except Exception:
                pass  # LLM 解析失败，降级到规则匹配

        # 规则降级：正则匹配代码文件路径
        path_pattern = r'[\w./-]+\.(?:py|js|ts|jsx|tsx|java|go)'
        matches = re.findall(path_pattern, query)
        for match in matches:
            if os.path.exists(match):
                result['file_path'] = match
                break

        # 关键词匹配测试类型
        q_lower = query.lower()
        if any(kw in q_lower for kw in ['移动端', 'appium', 'mobile', 'app']):
            result['test_type'] = 'mobile'
        elif any(kw in q_lower for kw in ['api', '接口', 'http', 'request']):
            result['test_type'] = 'api'

        return result

    # ==================== 代码分析链路 ====================

    def _generate_markdown(self, code_content, functions, test_type) -> tuple:
        """代码分析场景下的 Markdown 生成入口"""
        if not self.llm:
            return self._generate_fallback_markdown(functions, test_type), None

        result = self._llm_generate_doc(code_content, 0)
        if result:
            return result

        return self._generate_fallback_markdown(functions, test_type), None

    # ==================== 保存与发布 ====================

    def _save_and_publish(self, markdown: str, title: str, struct_blocks: list = None) -> tuple:
        """保存本地 Markdown + 发布飞书文档"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_title = re.sub(r'[^\w\u4e00-\u9fff-]', '_', title)[:50]
        filename = f"{safe_title}_{timestamp}.md"
        local_path = os.path.join(self.output_dir, filename)

        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(f"# {title}\n\n")
            f.write(markdown)

        print(f"[RequirementAnalyzer] 本地保存: {local_path}")

        feishu_url = None
        if self.feishu_client and self.feishu_folder:
            try:
                result = self._publish_feishu(title, markdown, struct_blocks)
                feishu_url = result.get('url')
            except Exception as e:
                print(f"[RequirementAnalyzer] 飞书发布失败: {e}")

        return local_path, feishu_url

    def _publish_feishu(self, title: str, markdown: str, struct_blocks: list) -> dict:
        """飞书发布（双链路降级：struct 直写优先，失败降级 Markdown）"""
        # 优先尝试结构化直写
        if struct_blocks:
            try:
                return self.feishu_client.create_doc_from_struct(
                    title=title,
                    folder_token=self.feishu_folder,
                    struct_blocks=struct_blocks,
                )
            except Exception as e:
                print(f"[RequirementAnalyzer] 飞书结构化直写失败，降级Markdown: {e}")

        # 降级：Markdown 文本创建文档
        return self.feishu_client.create_doc(
            title=title,
            folder_token=self.feishu_folder,
            content=markdown,
        )

    # ==================== 规则降级生成 ====================

    def _generate_fallback_markdown(self, functions, test_type) -> str:
        """LLM 不可用时的纯规则降级：从代码静态分析结果生成简要 Markdown"""
        lines = [
            "## 需求全景",
            f"- **来源**: 代码静态分析",
            f"- **测试类型**: {test_type}",
            f"- **功能数量**: {len(functions)}",
            "",
            "## 功能规格",
            "| 函数名 | 参数 | 复杂度 | 测试点 |",
            "|--------|------|--------|--------|",
        ]

        for func in functions:
            name = func['name']
            params = ', '.join(func['params']) if func['params'] else '无'
            complexity = self._assess_complexity(func)
            test_points = ', '.join(self._identify_test_points(func, test_type))
            lines.append(f"| `{name}` | {params} | {complexity} | {test_points} |")

        lines.extend([
            "",
            "## 待办项",
            "- [ ] 补充详细需求描述",
            "- [ ] 确认边界条件",
            "- [ ] 完善异常处理测试点",
        ])

        return '\n'.join(lines)

    def _assess_complexity(self, func_info) -> str:
        """简单复杂度评估"""
        code_lines = func_info['code'].count('\n') + 1
        param_count = len(func_info['params'])
        if code_lines > 50 or param_count > 5:
            return 'high'
        elif code_lines > 20 or param_count > 3:
            return 'medium'
        return 'low'

    def _identify_test_points(self, func_info, test_type) -> list:
        """从函数代码中按关键词识别测试点"""
        test_points = []
        code = func_info['code'].lower()

        if func_info['params']:
            test_points.append('参数验证')

        type_keywords = {
            'web': {'click': 'UI交互', 'navigate': '页面导航', 'form': '表单提交'},
            'mobile': {'swipe': '手势操作', 'screen': '屏幕适配', 'permission': '权限处理'},
            'api': {'request': 'HTTP调用', 'timeout': '超时处理', 'response': '响应校验'},
        }

        for keyword, point in type_keywords.get(test_type, {}).items():
            if keyword in code:
                test_points.append(point)

        if 'error' in code or 'exception' in code or 'try' in code:
            test_points.append('异常处理')

        if not test_points:
            test_points.append('功能逻辑')

        return test_points
