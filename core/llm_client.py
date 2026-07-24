"""LLM客户端 - 支持多种模型"""
import os


class LLMClient:
    def __init__(self, api_key=None, base_url=None):
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        self.base_url = base_url or os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
        self.model = os.getenv('LLM_MODEL', 'gpt-4')

        if not self.api_key:
            raise ValueError("请设置OPENAI_API_KEY环境变量或传入api_key参数")

    def generate(self, prompt, temperature=0.7, max_tokens=2000):
        """调用LLM生成测试用例"""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)

            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是专业的测试工程师"},
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content

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
            from client_templates import ClientTestTemplates
            return ClientTestTemplates.appium_mobile_test(func_name)
        elif 'Selenium' in prompt:
            from client_templates import ClientTestTemplates
            return ClientTestTemplates.selenium_web_test(func_name)
        elif 'requests' in prompt or 'api' in prompt.lower():
            from client_templates import ClientTestTemplates
            return ClientTestTemplates.api_client_test(func_name)
        else:
            # 默认Playwright Web测试
            from client_templates import ClientTestTemplates
            return ClientTestTemplates.playwright_web_test(func_name)
