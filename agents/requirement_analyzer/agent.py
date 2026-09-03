"""需求分析Agent - 解析业务需求，输出Markdown需求分析规格书"""
import os
import re
import time
from typing import Any
from datetime import datetime
from agents.base_agent import BaseAgent
from agents._prompt_utils import build_prompt
from utils.code_analyzer import CodeAnalyzer
from core.feishu_client import FeishuClient
from core.structured_doc import parse_struct_json, struct_to_markdown

_DIR = os.path.dirname(os.path.abspath(__file__))

# 双链路灰度开关：True=新链路（LLM输出业务JSON→create_doc_from_struct）；
# False=旧链路（LLM输出Markdown→create_doc解析）。环境变量USE_STRUCT_OUTPUT可关闭
USE_STRUCT_OUTPUT = os.getenv('USE_STRUCT_OUTPUT', 'true').lower() not in ('false', '0', 'off')


def _build_parse_prompt(prd_content: str, max_length: int = 4000, prompt_file: str = 'prompts.md') -> str:
    """将内容注入prompt模板（prompts.md=JSON版新链路，prompts_markdown.md=降级旧链路）"""
    return build_prompt(_DIR, prompt_file, prd_content=prd_content[:max_length])


class RequirementAnalyzer(BaseAgent):
    """需求分析Agent：从代码/PRD文档中生成Markdown需求分析规格书，
    同时保存本地.md文件并创建飞书文档"""
    
    def __init__(self, llm_client=None, feishu_client=None, output_dir='generated_requirements'):
        super().__init__(name="RequirementAnalyzer", llm_client=llm_client)
        self.feishu_client = feishu_client
        self.output_dir = output_dir
        self.feishu_folder = os.getenv('FEISHU_OUTPUT_FOLDER', '')
        os.makedirs(output_dir, exist_ok=True)
    
    def execute(self, input_data: dict) -> dict:
        """
        分析代码并生成需求分析规格书
        :param input_data: {'file_path': str, 'test_type': str, 'title': str}
        :return: {'markdown': str, 'local_path': str, 'feishu_url': str|None, 'metadata': dict}
        """
        if not self.validate_input(input_data):
            raise ValueError("Invalid input: file_path required")
        
        file_path = input_data['file_path']
        test_type = input_data.get('test_type', 'web')
        title = input_data.get('title') or f"需求分析-{os.path.basename(file_path)}"
        
        # 1. 代码结构分析
        analyzer = CodeAnalyzer(file_path)
        functions = analyzer.extract_functions()
        classes = analyzer.extract_classes()
        code_content = analyzer.get_code_content()
        
        # 2. LLM生成需求分析（结构化JSON + Markdown双路灰度）
        markdown, blocks = self._generate_markdown(code_content, functions, test_type)
        
        # 3. 保存本地 + 创建飞书文档
        local_path, feishu_url = self._save_and_publish(markdown, title, blocks)
        
        self.state = {
            'analyzed_file': file_path,
            'function_count': len(functions),
            'class_count': len(classes),
            'local_path': local_path,
            'feishu_url': feishu_url
        }
        
        return {
            'markdown': markdown,
            'local_path': local_path,
            'feishu_url': feishu_url,
            'metadata': self.state
        }
    
    def validate_input(self, input_data: Any) -> bool:
        if not isinstance(input_data, dict):
            return False
        return 'file_path' in input_data
    
    def process_query(self, query: str, title: str = None) -> dict:
        """自然语言处理入口。title: 外部传入的文档标题（如飞书文档标题）"""
        print(f"  [process_query] 输入长度={len(query)}字符")
        
        # 飞书文档链接：先拉取内容再按PRD分析
        feishu_doc_url = self._extract_feishu_url(query)
        if feishu_doc_url:
            return self._process_feishu_doc(feishu_doc_url, title)
        
        parsed = self._parse_query(query)
        print(f"  [process_query] 解析结果: is_prd={parsed.get('is_prd')}, file_path={parsed.get('file_path')}, test_type={parsed.get('test_type')}")
        
        # PRD文档内容：直接分析文本
        if not parsed.get('file_path') and (parsed.get('is_prd') or len(query) > 200):
            doc_title = title or parsed.get('title') or 'PRD文档'
            raw = self._clean_doc_content(query)
            markdown, blocks = self._analyze_prd_content(raw)
            local_path, feishu_url = self._save_and_publish(markdown, doc_title, blocks)
            return {
                'markdown': markdown,
                'local_path': local_path,
                'feishu_url': feishu_url,
                'raw_content': raw,  # 原始PRD全文，供测试点直提作主材料
                'metadata': {'source': 'prd_document', 'length': len(query)}
            }
        
        if not parsed.get('file_path'):
            raise ValueError(f"无法从查询中提取文件路径: {query}")
        
        return self.execute({
            'file_path': parsed['file_path'],
            'test_type': parsed.get('test_type', 'web')
        })
    
    def _extract_feishu_url(self, query: str) -> str:
        """从查询中提取飞书文档链接。仅当查询本身以飞书文档直链(docx/wiki/sheets)开头时才提取，
        避免误把PRD正文里引用的 /share/base/form/ 等非文档链接当成待分析文档去拉取。"""
        m = re.match(r'https?://[\w.-]*feishu\.cn/(?:docx|wiki|sheets)/[a-zA-Z0-9]+', query.strip())
        return m.group(0) if m else ''
    
    # 图片文件名行（如 image.png / img_v3_xxx.jpg）
    _IMAGE_LINE_RE = re.compile(r'^[^\w\u4e00-\u9fa5]*[\w.-]+\.(png|jpe?g|gif|bmp|webp|svg)[^\w\u4e00-\u9fa5]*$', re.IGNORECASE)
    # 纯@提及行（如 @王乐昕）
    _MENTION_LINE_RE = re.compile(r'^@[\w\u4e00-\u9fa5._\-]+$')
    # 纯URL行（jira/figma等对需求分析无价值的链接）
    _URL_LINE_RE = re.compile(r'^https?://\S+$')
    # PRD模板套话（文档状态后缀说明等样板文字）
    _BOILERPLATE_KEYWORDS = ('Title Alpha', 'Title Beta', 'Title Ready')
    
    def _clean_doc_content(self, content: str) -> str:
        """清洗需求文档内容（飞书文档/粘贴文本/上传PRD通用），去除对需求分析无价值的噪声
        
        清洗规则:
        1. 图片占位行（image.png / img_xxx.jpg）
        2. 纯@提及行（项目分工中的负责人）
        3. 纯URL行（jira/figma等外部链接）
        4. PRD模板套话（Title Alpha/Beta/Ready状态说明）
        5. 压缩连续空行为单个空行
        """
        lines = []
        for line in content.split('\n'):
            stripped = line.strip()
            if not stripped:
                # 空行只保留一个（避免大段留白）
                if lines and lines[-1] != '':
                    lines.append('')
                continue
            if self._IMAGE_LINE_RE.match(stripped):
                continue
            if self._MENTION_LINE_RE.match(stripped):
                continue
            if self._URL_LINE_RE.match(stripped):
                continue
            if any(kw in stripped for kw in self._BOILERPLATE_KEYWORDS):
                continue
            # 去除行内图片引用残留（如 功能说明 image.png xxx）
            line = re.sub(r'\b[\w.-]+\.(png|jpe?g|gif|bmp|webp|svg)\b', '', line, flags=re.IGNORECASE)
            lines.append(line.rstrip())
        
        cleaned = '\n'.join(lines).strip()
        removed = len(content) - len(cleaned)
        if removed > 0:
            print(f"  [_clean_doc] 文档清洗完成: {len(content)}字符/{len(content.splitlines())}行 → {len(cleaned)}字符/{len(cleaned.splitlines())}行, 去除噪声{removed}字符")
        else:
            print(f"  [_clean_doc] 文档无需清洗 ({len(content)}字符)")
        return cleaned
    
    def _process_feishu_doc(self, doc_url: str, title: str = None) -> dict:
        """拉取飞书文档内容并按PRD分析"""
        if not self.feishu_client:
            raise ValueError("解析飞书文档需要配置 FEISHU_APP_ID / FEISHU_APP_SECRET")
        
        print(f"  [process_query] 检测到飞书文档链接: {doc_url}")
        doc_token, doc_type = FeishuClient.parse_doc_url(doc_url)
        doc_title = title or self.feishu_client.get_doc_title(doc_token, doc_type) or '飞书文档'
        content = self.feishu_client.get_doc_content(doc_token, doc_type)
        if not content or not content.strip():
            raise ValueError(f"飞书文档内容为空，请检查文档权限: {doc_url}")
        print(f"  [process_query] 飞书文档拉取成功: 《{doc_title}》 {len(content)}字符")
        
        content = self._clean_doc_content(content)
        markdown, blocks = self._analyze_prd_content(content)
        local_path, feishu_url = self._save_and_publish(markdown, doc_title, blocks)
        return {
            'markdown': markdown,
            'local_path': local_path,
            'feishu_url': feishu_url,
            'raw_content': content,  # 原始飞书文档全文，供测试点直提作主材料
            'metadata': {'source': 'feishu_doc', 'doc_url': doc_url, 'title': doc_title}
        }
    
    def _analyze_prd_content(self, prd_text: str) -> tuple:
        """分析PRD文档内容（双链路灰度）
        :return: (markdown, struct_blocks)，struct_blocks为业务JSON节点列表（飞书直写用），
            旧链路/降级时为None
        """
        # 统一清洗入口（幂等）：无论来自飞书/粘贴/上传，送LLM前都保证已去噪
        prd_text = self._clean_doc_content(prd_text)
        print(f"  [_analyze_prd] 检测到PRD文档, 长度={len(prd_text)}字符, 链路={'JSON结构化' if USE_STRUCT_OUTPUT else 'Markdown旧版'}")
        
        if not self.llm:
            raise ValueError("PRD分析需要LLM支持")
        
        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = self._llm_generate_doc(prd_text, attempt)
                if result:
                    return result
            except Exception as e:
                print(f"  [_analyze_prd] 第{attempt+1}次失败: {type(e).__name__}: {str(e)[:100]}")
        
        raise ValueError("PRD文档分析失败，已重试2次")

    def _llm_generate_doc(self, prd_text: str, attempt: int) -> tuple:
        """双链路灰度生成：
        新链路（USE_STRUCT_OUTPUT=True）：prompts.md(JSON) → 解析校验 → (md, struct_blocks)
            失败降级：重新用旧prompt调LLM拿Markdown（绝不把JSON字符串喂给旧解析器）
        旧链路：prompts_markdown.md → (markdown, None)
        :return: (markdown, struct_blocks) 或 None（输出过短需重试）
        """
        if USE_STRUCT_OUTPUT:
            prompt = _build_parse_prompt(prd_text, prompt_file='prompts.md')
            print(f"  [_analyze_prd] 调用LLM-JSON链路 (第{attempt+1}次), Prompt {len(prompt)}字符...")
            t = time.time()
            # 需求分析8章节JSON输出很长，默认max_tokens=2000会截断导致JSON解析失败
            response = self.llm.generate(prompt, max_tokens=16000)
            print(f"  [_analyze_prd] LLM响应: {len(response)}字符, 耗时{time.time()-t:.2f}s")
            
            # 【强制校验】解析+table行列对齐（防御脏JSON），失败返回None触发降级
            nodes = parse_struct_json(response)
            if nodes:
                md = struct_to_markdown(nodes)
                n_table = sum(1 for n in nodes if n['type'] == 'table')
                print(f"  [_struct] JSON链路成功: {len(nodes)}个节点, {n_table}个表格")
                return md, nodes
            
            # 降级：JSON解析/校验失败 → 切旧prompt重新调LLM拿Markdown
            print(f"  [_struct] JSON链路失败, 降级: 重新调用LLM使用旧Markdown prompt")
            fallback_prompt = _build_parse_prompt(prd_text, prompt_file='prompts_markdown.md')
            fallback_resp = self.llm.generate(fallback_prompt, max_tokens=12000)
            fallback_md = self._strip_code_fence(fallback_resp)
            if len(fallback_md) > 50:
                print(f"  [_struct] 降级链路成功: {len(fallback_md)}字符")
                return fallback_md, None
            return None
        
        # 旧链路：Markdown直出
        prompt = _build_parse_prompt(prd_text, prompt_file='prompts_markdown.md')
        print(f"  [_analyze_prd] 调用LLM-Markdown链路 (第{attempt+1}次)...")
        response = self.llm.generate(prompt, max_tokens=12000)
        cleaned = self._strip_code_fence(response)
        if len(cleaned) > 50:
            print(f"  [_analyze_prd] 分析完成, 输出{len(cleaned)}字符")
            return cleaned, None
        print(f"  [_analyze_prd] 第{attempt+1}次: 输出过短({len(cleaned)}字符)")
        return None

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """去除LLM输出可能携带的代码块标记"""
        cleaned = re.sub(r'^```\w*\n?', '', text, flags=re.MULTILINE)
        cleaned = re.sub(r'\n?```$', '', cleaned, flags=re.MULTILINE)
        return cleaned.strip()
    
    def _parse_query(self, query: str) -> dict:
        """解析自然语言查询，提取参数"""
        result = {'file_path': None, 'test_type': 'web'}
        
        if self.llm:
            prompt = f"""你是参数提取助手。从用户查询中提取文件路径和测试类型。

用户查询: {query[:500]}

要求：
1. 判断是否为PRD文档内容（包含需求描述、功能说明等大段文本则is_prd=true）
2. PRD文档返回：{{"is_prd": true, "file_path": null}}
3. 文件分析请求提取文件路径
4. 识别测试类型：web/mobile/api（默认web）
5. 返回JSON：{{"is_prd": false, "file_path": "", "test_type": "web"}}

只返回JSON，无解释。"""
            
            try:
                import json
                print(f"  [_parse_query] 调用LLM识别输入类型...")
                response = self.llm.generate(prompt)
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group(0))
                    print(f"  [_parse_query] LLM返回: {parsed}")
                    if parsed.get('is_prd'):
                        return {'file_path': None, 'test_type': 'web', 'is_prd': True}
                    result.update(parsed)
                    return result
            except Exception as e:
                print(f"  [_parse_query] LLM解析失败，降级到规则匹配: {e}")
        
        # 降级：规则匹配
        path_pattern = r'[\w./-]+\.(?:py|js|ts|jsx|tsx|java|go)'
        matches = re.findall(path_pattern, query)
        if matches:
            for match in matches:
                if os.path.exists(match):
                    result['file_path'] = match
                    break
            if not result['file_path'] and matches:
                result['file_path'] = matches[-1]
        
        if any(kw in query.lower() for kw in ['移动端', 'appium', 'mobile', 'app']):
            result['test_type'] = 'mobile'
        elif any(kw in query.lower() for kw in ['api', '接口', 'http', 'request']):
            result['test_type'] = 'api'
        
        return result
    
    def _generate_markdown(self, code_content, functions, test_type) -> tuple:
        """用LLM从代码生成需求分析规格书（复用双链路灰度逻辑）
        :return: (markdown, struct_blocks)
        """
        if not self.llm:
            # 降级：规则生成简要Markdown
            return self._generate_fallback_markdown(functions, test_type), None
        
        try:
            result = self._llm_generate_doc(code_content, 0)
            if result:
                print(f"✓ [RequirementAnalyzer] LLM生成需求分析, {len(result[0])}字符")
                return result
        except Exception as e:
            print(f"⚠ [RequirementAnalyzer] LLM分析失败: {e}")
        
        print("降级到规则生成...")
        return self._generate_fallback_markdown(functions, test_type), None
    
    def _save_and_publish(self, markdown: str, title: str, struct_blocks: list = None) -> tuple:
        """保存本地.md文件 + 创建飞书文档
        :param struct_blocks: 业务JSON节点列表，存在时飞书侧struct直写（零Markdown解析）；
            None时走旧版create_doc Markdown解析链路（降级兜底）
        :return: (local_path, feishu_url)
        """
        # 1. 保存本地
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_title = re.sub(r'[^\w\u4e00-\u9fff-]', '_', title)[:50]
        filename = f"{safe_title}_{timestamp}.md"
        local_path = os.path.join(self.output_dir, filename)
        
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(f"# {title}\n\n")
            f.write(markdown)
        
        print(f"✓ [RequirementAnalyzer] 本地保存: {local_path}")
        
        # 2. 创建飞书文档（可选）：新链路struct直写，失败自动降级旧Markdown解析链路
        feishu_url = None
        if self.feishu_client and self.feishu_folder:
            print(f"  [_save_and_publish] 创建飞书文档: folder={self.feishu_folder}, 模式={'struct直写' if struct_blocks else 'Markdown解析'}")
            try:
                result = self._publish_feishu(title, markdown, struct_blocks)
                feishu_url = result['url']
                print(f"  [_save_and_publish] 飞书文档创建成功: {feishu_url}")
            except Exception as e:
                print(f"  [_save_and_publish] 飞书文档创建失败: {type(e).__name__}: {str(e)[:200]}")
        else:
            print(f"  [_save_and_publish] 跳过飞书文档: feishu_client={'已配置' if self.feishu_client else '未配置'}, feishu_folder='{self.feishu_folder}'")
        
        return local_path, feishu_url
    
    def _publish_feishu(self, title: str, markdown: str, struct_blocks: list) -> dict:
        """飞书写入双链路：struct直写失败（接口异常等）时降级create_doc，
        降级时传的是struct_to_markdown渲染的Markdown而非原始JSON字符串"""
        if struct_blocks:
            try:
                return self.feishu_client.create_doc_from_struct(
                    title=f"{title}-需求分析",
                    folder_token=self.feishu_folder,
                    struct_blocks=struct_blocks
                )
            except Exception as e:
                print(f"  [_publish_feishu] struct直写失败, 降级Markdown链路: {type(e).__name__}: {str(e)[:150]}")
        return self.feishu_client.create_doc(
            title=f"{title}-需求分析",
            folder_token=self.feishu_folder,
            content=markdown
        )
    
    def _generate_fallback_markdown(self, functions, test_type) -> str:
        """规则降级：生成简要Markdown需求分析"""
        lines = [
            "## 需求全景",
            f"- **分析来源**: 代码静态分析",
            f"- **测试类型**: {test_type}",
            f"- **识别功能数**: {len(functions)}",
            "",
            "## 功能规格与可测试项",
            "| 功能点 | 参数 | 复杂度 | 测试点 |",
            "| :--- | :--- | :--- | :--- |"
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
            "- 需补充业务规则和验收标准",
            "- 需与产品经理确认隐性需求"
        ])
        
        return '\n'.join(lines)
    
    def _assess_complexity(self, func_info):
        code_lines = func_info['code'].count('\n') + 1
        param_count = len(func_info['params'])
        if code_lines > 50 or param_count > 5:
            return 'high'
        elif code_lines > 20 or param_count > 3:
            return 'medium'
        return 'low'
    
    def _identify_test_points(self, func_info, test_type):
        test_points = []
        code = func_info['code'].lower()
        
        if func_info['params']:
            test_points.append('参数验证')
        
        type_keywords = {
            'web': {'click': 'UI交互', 'navigate': '页面导航'},
            'mobile': {'swipe': '手势操作', 'screen': '屏幕适配'},
            'api': {'request': 'HTTP调用', 'timeout': '超时处理'}
        }
        for keyword, point in type_keywords.get(test_type, {}).items():
            if keyword in code:
                test_points.append(point)
        
        if not test_points:
            test_points.append('功能逻辑')
        return test_points
