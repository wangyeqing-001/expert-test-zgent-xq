"""飞书机器人 - 意图路由 + Agent调度"""
import json
import logging
import threading
from typing import Dict

from core.feishu_client import FeishuClient
from core.dispatcher import IntentDispatcher

logger = logging.getLogger(__name__)


class FeishuBot:
    """飞书聊天机器人：接收自然语言消息，路由到对应Agent"""
    
    def __init__(self, req_agent, point_agent, gen_agent, feishu_client: FeishuClient, llm_client=None):
        self.req_agent = req_agent
        self.point_agent = point_agent
        self.gen_agent = gen_agent
        self.feishu_client = feishu_client
        self.llm_client = llm_client
        # 复用通用意图路由器（CLI/Web/飞书共用同一套意图识别逻辑）
        self.dispatcher = IntentDispatcher(req_agent, point_agent, gen_agent, llm_client=llm_client)
        self._processed_events = set()  # event_id 去重
        self._lock = threading.Lock()
    
    def handle_event(self, event: dict) -> bool:
        """
        处理飞书事件回调
        :param event: 飞书事件对象
        :return: True=已处理, False=跳过
        """
        # event_id 去重
        event_id = event.get('event_id', '')
        if event_id:
            with self._lock:
                if event_id in self._processed_events:
                    logger.debug(f"重复事件，跳过: {event_id}")
                    return False
                self._processed_events.add(event_id)
                # 限制集合大小，避免内存泄漏
                if len(self._processed_events) > 10000:
                    self._processed_events = set(list(self._processed_events)[-5000:])
        
        # 提取消息内容
        msg = event.get('message', {})
        msg_type = msg.get('message_type', '')
        message_id = msg.get('message_id', '')
        chat_id = msg.get('chat_id', '')
        sender_id = msg.get('sender', {}).get('sender_id', {}).get('open_id', '')
        
        if msg_type != 'text':
            if message_id:
                self._reply_text(message_id, "目前仅支持文本消息，请直接发送文字描述你的需求。")
            return False
        
        # 解析文本
        try:
            text = json.loads(msg.get('content', '{}')).get('text', '')
        except (json.JSONDecodeError, AttributeError):
            return False
        
        if not text.strip():
            return False
        
        # 去除 @机器人 的前缀
        text = self._clean_at_mention(text)
        
        logger.info(f"收到消息: [{sender_id}] {text[:80]}...")
        
        # 异步处理（避免3秒超时）：先返回200，后台处理+主动回复
        thread = threading.Thread(
            target=self._process_and_reply,
            args=(text, message_id, chat_id),
            daemon=True
        )
        thread.start()
        
        return True
    
    def _process_and_reply(self, text: str, message_id: str, chat_id: str):
        """后台处理消息并回复（在独立线程中运行）"""
        try:
            result = self.handle_message(text)
            
            if result['type'] == 'card':
                self.feishu_client.reply_message(message_id, 'interactive', result['content'])
            else:
                self.feishu_client.reply_message(message_id, 'text', json.dumps({'text': result['content']}))
                
        except Exception as e:
            logger.error(f"处理消息失败: {e}", exc_info=True)
            try:
                self.feishu_client.reply_message(
                    message_id, 'text',
                    json.dumps({'text': f"处理失败: {str(e)[:200]}"})
                )
            except Exception:
                pass
    
    def handle_message(self, text: str) -> dict:
        """
        意图路由：分析用户消息，调用对应Agent，返回格式化结果
        :param text: 用户消息文本
        :return: {'type': 'text'|'card', 'content': str}
        """
        text_lower = text.lower().strip()
        
        # 帮助指令
        if text_lower in ('帮助', 'help', '/help', '?', '？'):
            return self._help_response()
        
        # 意图识别（关键词→LLM兜底→默认requirement，复用dispatcher）
        intent = self.dispatcher.detect_intent(text)
        
        logger.info(f"意图识别: {intent} <- '{text[:50]}'")
        
        # 分发到对应Agent
        try:
            if intent == 'requirement':
                return self._handle_requirement(text)
            elif intent == 'test_point':
                return self._handle_test_point(text)
            elif intent == 'generate':
                return self._handle_generate(text)
            else:
                return self._help_response()
        except Exception as e:
            logger.error(f"Agent处理失败: {e}", exc_info=True)
            return {'type': 'text', 'content': f"处理失败: {str(e)[:300]}"}
    
    def _handle_requirement(self, text: str) -> dict:
        """处理需求分析"""
        result = self.req_agent.process_query(text)
        
        markdown = result.get('markdown', '')
        local_path = result.get('local_path', '')
        feishu_url = result.get('feishu_url', '')
        
        content = f"**本地文件**: `{local_path}`"
        if feishu_url:
            content += f"\n**飞书文档**: [点击查看]({feishu_url})"
        content += f"\n\n{markdown[:1500]}"
        if len(markdown) > 1500:
            content += f"\n\n...(共{len(markdown)}字符，已截断)"
        
        card = self.feishu_client.format_card(
            title="需求分析结果",
            content=content
        )
        return {'type': 'card', 'content': card}
    
    def _handle_test_point(self, text: str) -> dict:
        """处理测试点生成"""
        result = self.point_agent.process_query(text)
        scenarios = result.get('scenarios', [])
        
        content = f"共生成 **{len(scenarios)}** 个测试场景"
        
        card = self.feishu_client.format_card(
            title="测试点生成结果",
            content=content,
            scenarios=scenarios
        )
        return {'type': 'card', 'content': card}
    
    def _handle_generate(self, text: str) -> dict:
        """处理测试代码生成"""
        result = self.gen_agent.process_query(text)
        
        file_path = result.get('file_path', '')
        test_case = result.get('test_case', {})
        test_code = test_case.get('test_code', '')
        
        content = f"**文件**: `{file_path}`\n**场景**: {test_case.get('scenario', '')} | **优先级**: {test_case.get('priority', '')}\n\n```python\n{test_code[:1200]}\n```"
        if len(test_code) > 1200:
            content += f"\n...(共{len(test_code)}字符，已截断)"
        
        card = self.feishu_client.format_card(
            title="测试代码生成结果",
            content=content
        )
        return {'type': 'card', 'content': card}
    
    def _help_response(self) -> dict:
        """帮助信息"""
        content = (
            "**支持的指令**:\n\n"
            "- **需求分析**: 发送 \"分析xxx.py的测试需求\" 或直接粘贴PRD文档\n"
            "- **测试点生成**: 发送 \"根据login功能生成测试场景\"\n"
            "- **测试代码生成**: 发送 \"为login函数生成Web测试用例\"\n"
            "- **帮助**: 发送 \"帮助\" 查看此说明\n\n"
            "系统会自动识别你的意图并调用对应的Agent。"
        )
        card = self.feishu_client.format_card(
            title="Agent测试生成系统 - 使用帮助",
            content=content
        )
        return {'type': 'card', 'content': card}
    
    @staticmethod
    def _clean_at_mention(text: str) -> str:
        """去除 @机器人 的前缀"""
        import re
        # 飞书@格式: @_user_1 或直接 @机器人名
        cleaned = re.sub(r'@_user_\d+\s*', '', text)
        cleaned = re.sub(r'@\S+\s*', '', cleaned)
        return cleaned.strip()
    
    def _reply_text(self, message_id: str, text: str):
        """快捷回复纯文本"""
        try:
            self.feishu_client.reply_message(
                message_id, 'text',
                json.dumps({'text': text})
            )
        except Exception as e:
            logger.error(f"回复文本失败: {e}")
