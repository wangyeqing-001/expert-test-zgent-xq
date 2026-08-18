"""LLM客户端 - 支持多种模型"""
import os


class LLMClient:
    def __init__(self, api_key=None, base_url=None):
        # 支持多种LLM提供商
        self.api_key = api_key or os.getenv('DASHSCOPE_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('DEEPSEEK_API_KEY')
        
        # 自动识别API提供商
        if not base_url:
            if os.getenv('DASHSCOPE_API_KEY'):
                # 阿里云百炼默认配置
                self.base_url = os.getenv('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
                self.model = os.getenv('DASHSCOPE_MODEL', 'qwen-plus')
            elif os.getenv('DEEPSEEK_API_KEY') and not os.getenv('OPENAI_API_KEY'):
                # DeepSeek默认配置
                self.base_url = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
                self.model = os.getenv('DEEPSEEK_MODEL', 'deepseek-v4-flash')
            else:
                self.base_url = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
                self.model = os.getenv('LLM_MODEL', 'gpt-4')
        else:
            self.base_url = base_url
            # 根据base_url判断模型
            if 'dashscope' in base_url or 'aliyun' in base_url:
                self.model = os.getenv('DASHSCOPE_MODEL', 'qwen-plus')
            elif 'deepseek' in base_url:
                self.model = os.getenv('DEEPSEEK_MODEL', 'deepseek-v4-flash')
            else:
                self.model = os.getenv('LLM_MODEL', 'gpt-4')

        if not self.api_key:
            raise ValueError("请设置API Key：\n" 
                           "• 阿里云百炼: export DASHSCOPE_API_KEY=sk-xxx\n"
                           "• OpenAI: export OPENAI_API_KEY=sk-xxx\n"
                           "• DeepSeek: export DEEPSEEK_API_KEY=sk-xxx")

    def generate(self, prompt, temperature=0.7, max_tokens=2000):
        """调用LLM生成测试用例"""
        try:
            from openai import OpenAI
            import httpx
            # 直连API，绕过系统代理（避免抓包代理导致SSL验证失败）
            http_client = httpx.Client(trust_env=False, timeout=120)
            client = OpenAI(api_key=self.api_key, base_url=self.base_url, http_client=http_client)

            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是专业的测试工程师"},
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )
            content = response.choices[0].message.content or ''
            # 截断告警：输出被max_tokens截断时，JSON等结构化输出必然解析失败
            finish_reason = response.choices[0].finish_reason
            if finish_reason == 'length':
                print(f"  [LLM] ⚠ 输出被max_tokens={max_tokens}截断(finish_reason=length)，内容可能不完整")
            # 日志：打印大模型返回的完整内容
            print(f"  [LLM] 模型返回内容 (model={self.model}, {len(content)}字符):")
            print(content)
            return content

        except ImportError:
            return self._mock_generate(prompt)

    def _mock_generate(self, prompt):
        """无API时的模拟实现 - 客户端测试场景"""
        import re

        func_match = re.search(r'功能名: (\w+)', prompt)
        
        if not func_match:
            return ""
        
        func_name = func_match.group(1)
        
        # 根据prompt判断测试类型
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
            # 默认Playwright Web测试
            from utils.template_loader import ClientTestTemplates
            return ClientTestTemplates.playwright_web_test(func_name)
