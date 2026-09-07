"""LLM客户端 - 百炼 DashScope 原生 API 优先 + OpenAI 兼容模式 fallback

调用方式：
- 纯文本（默认）: dashscope.Generation.call → 原生 API，最快最稳
- 多模态（图片+文本）: dashscope.MultiModalConversation.call → 原生 API
- fallback: 无 dashscope 时走 OpenAI SDK + 兼容模式 URL

.env 只需一行：DASHSCOPE_API_KEY=sk-xxx
"""
import os


class LLMClient:
    def __init__(self, api_key=None, base_url=None, model=None):
        # 百炼原生优先
        self.api_key = api_key or os.getenv('DASHSCOPE_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('DEEPSEEK_API_KEY')
        self.model = model or os.getenv('DASHSCOPE_MODEL', 'qwen-plus')
        self.base_url = base_url  # 仅 OpenAI fallback 时用

        if not self.api_key:
            raise ValueError(
                "请设置 API Key：\n"
                "  • 阿里云百炼（推荐）: export DASHSCOPE_API_KEY=sk-xxx\n"
                "  • OpenAI fallback:    export OPENAI_API_KEY=sk-xxx"
            )

        # dashscope SDK 全局设 key
        os.environ.setdefault('DASHSCOPE_API_KEY', self.api_key)

    # ============================================================
    # 核心方法：纯文本生成（百炼原生 Generation.call）
    # ============================================================
    def generate(self, prompt, temperature=0.7, max_tokens=2000, timeout=None, system_prompt=None):
        """纯文本 LLM 调用 —— 百炼原生 Generation.call 优先"""
        try:
            return self._dashscope_generate(prompt, temperature, max_tokens, timeout, system_prompt)
        except Exception as e:
            if 'dashscope' in str(type(e).__module__).lower() or 'ImportError' in str(type(e).__name__):
                # dashscope 不可用或失败，fallback OpenAI 兼容模式
                try:
                    return self._openai_generate(prompt, temperature, max_tokens, timeout, system_prompt)
                except Exception as e2:
                    raise RuntimeError(f"LLM 调用失败（dashscope + openai 都挂了）: dashscope={e}, openai={e2}")
            raise

    # ============================================================
    # 多模态：文字 + 图片（百炼原生 MultiModalConversation.call）
    # ============================================================
    def generate_with_images(self, prompt_text, image_paths=None, temperature=0.7, max_tokens=2000):
        """多模态生成 —— 文字 + 本地图片

        :param prompt_text: 文字 prompt
        :param image_paths: 本地图片路径列表
        """
        from dashscope import MultiModalConversation
        import base64, mimetypes

        content = [{'text': prompt_text}]
        for img_path in (image_paths or []):
            mime, _ = mimetypes.guess_type(img_path)
            mime = mime or 'image/png'
            with open(img_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            content.append({'image': f'data:{mime};base64,{b64}'})

        messages = [{'role': 'user', 'content': content}]

        resp = MultiModalConversation.call(
            api_key=self.api_key,
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            result_format='message',
        )
        if resp.status_code != 200:
            raise RuntimeError(f"MultiModalConversation 失败: status={resp.status_code}, msg={resp.message}")

        raw = resp.output.choices[0].message.content
        # content 可能是 [{'text': '...'}] 列表（VL 模式）
        if isinstance(raw, list):
            parts = []
            for item in raw:
                if isinstance(item, dict) and 'text' in item:
                    parts.append(item['text'])
            content = '\n'.join(parts) if parts else str(raw)
        else:
            content = str(raw)

        print(f"  [LLM-VL→{self.model}] 返回 {len(content)} 字符")
        return content

    # ============================================================
    # 内部：百炼原生 Generation.call
    # ============================================================
    def _dashscope_generate(self, prompt, temperature, max_tokens, timeout, system_prompt):
        from dashscope import Generation

        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})

        resp = Generation.call(
            api_key=self.api_key,
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            result_format='message',
        )
        if resp.status_code != 200:
            raise RuntimeError(f"dashscope Generation 失败: status={resp.status_code}, msg={resp.message}")

        content = resp.output.choices[0].message.content or ''
        finish_reason = resp.output.choices[0].get('finish_reason', 'stop')

        if finish_reason == 'length':
            print(f"  [LLM] ⚠ 输出被 max_tokens={max_tokens} 截断（finish_reason=length）")

        print(f"  [LLM→{self.model}] 返回 {len(content)} 字符 (finish={finish_reason})")
        return content

    # ============================================================
    # 内部：OpenAI 兼容模式 fallback
    # ============================================================
    def _openai_generate(self, prompt, temperature, max_tokens, timeout, system_prompt):
        from openai import OpenAI
        import httpx

        base_url = self.base_url or os.getenv('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        _to = timeout or 120
        http_client = httpx.Client(trust_env=False, timeout=_to)
        client = OpenAI(api_key=self.api_key, base_url=base_url, http_client=http_client, max_retries=0)

        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ''
        finish_reason = response.choices[0].finish_reason
        if finish_reason == 'length':
            print(f"  [LLM] ⚠ 输出被 max_tokens={max_tokens} 截断（finish_reason=length）")
        print(f"  [LLM(fallback)→{self.model}] 返回 {len(content)} 字符 (finish={finish_reason})")
        return content

    # ============================================================
    # 兼容旧接口的 mock
    # ============================================================
    def _mock_generate(self, prompt):
        """无 API 时的模拟实现"""
        import re
        func_match = re.search(r'功能名: (\w+)', prompt)
        if not func_match:
            return ""
        func_name = func_match.group(1)
        if 'Appium' in prompt or 'mobile' in prompt.lower():
            from utils.template_loader import ClientTestTemplates
            return ClientTestTemplates.appium_mobile_test(func_name)
        elif 'Selenium' in prompt:
            from utils.template_loader import ClientTestTemplates
            return ClientTestTemplates.selenium_web_test(func_name)
        elif 'requests' in prompt or 'api' in prompt.lower():
            from utils.template_loader import ClientTestTemplates
            return ClientTestTemplates.api_client_test(func_name)
        else:
            from utils.template_loader import ClientTestTemplates
            return ClientTestTemplates.playwright_web_test(func_name)
