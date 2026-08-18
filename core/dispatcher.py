"""意图路由器 - 自然语言消息识别意图并调用对应Agent（CLI/Web/飞书共用）"""
import logging

logger = logging.getLogger(__name__)


class IntentDispatcher:
    """意图识别 + Agent调度
    
    意图识别优先级：
    1. 关键词规则匹配（零延迟）
    2. LLM意图分类（兜底）
    3. 默认requirement
    """
    
    # 关键词意图映射（匹配顺序: requirement → test_point → generate，先命中先返回）
    INTENT_KEYWORDS = {
        'requirement': ['需求分析', '分析需求', '分析测试需求', 'PRD', '需求文档', '提取功能', '分析代码', '分析'],
        'test_point': ['测试点', '测试场景', '场景生成', '生成场景', '测试场景列表'],
        'generate': ['生成测试', '测试代码', '测试用例', '生成用例', '写测试', '编写测试', '生成'],
    }
    
    def __init__(self, req_agent, point_agent, gen_agent, llm_client=None):
        self.req_agent = req_agent
        self.point_agent = point_agent
        self.gen_agent = gen_agent
        self.llm_client = llm_client
    
    def detect_intent(self, text: str) -> str:
        """识别用户意图，返回 requirement/test_point/generate"""
        # 1. 关键词匹配
        intent = self._match_intent(text)
        if intent:
            return intent
        
        # 2. LLM分类兜底
        if self.llm_client:
            intent = self._classify_with_llm(text)
            if intent:
                return intent
        
        # 3. 默认需求分析
        return 'requirement'
    
    def dispatch(self, text: str) -> dict:
        """识别意图并调用对应Agent
        :param text: 用户自然语言输入
        :return: {'intent': str, 'result': dict}
        """
        intent = self.detect_intent(text)
        logger.info(f"意图识别: {intent} <- '{text[:50]}'")
        print(f"  [意图识别] {intent}")
        
        if intent == 'test_point':
            result = self.point_agent.process_query(text)
        elif intent == 'generate':
            result = self.gen_agent.process_query(text)
        else:
            result = self.req_agent.process_query(text)
        
        return {'intent': intent, 'result': result}
    
    def _match_intent(self, text: str) -> str:
        """关键词匹配意图"""
        for intent, keywords in self.INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    return intent
        return ''
    
    def _classify_with_llm(self, text: str) -> str:
        """用LLM分类用户意图"""
        prompt = f"""你是意图分类器。根据用户消息判断要执行的操作。

用户消息: {text[:300]}

分类选项:
- requirement: 需求分析、分析文档/代码/链接、提取功能点
- test_point: 生成测试场景、测试点列表
- generate: 生成测试代码、编写测试用例

只返回一个单词: requirement / test_point / generate"""
        
        try:
            response = self.llm_client.generate(prompt, max_tokens=50)
            result = response.strip().lower()
            if result in ('requirement', 'test_point', 'generate'):
                return result
        except Exception as e:
            logger.warning(f"LLM意图分类失败: {e}")
        
        return ''
