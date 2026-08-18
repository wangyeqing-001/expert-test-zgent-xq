"""多Agent协作 - 客户端测试用例生成系统"""
import os
from dotenv import load_dotenv
load_dotenv()

from core.llm_client import LLMClient
from core.feishu_client import FeishuClient
from core.dispatcher import IntentDispatcher
from agents.orchestrator import AgentOrchestrator
from agents.requirement_analyzer import RequirementAnalyzer
from agents.test_point_generator import TestPointGenerator
from agents.test_generator import TestGeneratorAgent


def _init_clients():
    """初始化LLM和飞书客户端"""
    api_key = os.getenv('DASHSCOPE_API_KEY') or os.getenv('DEEPSEEK_API_KEY') or os.getenv('OPENAI_API_KEY')
    if os.getenv('DASHSCOPE_API_KEY'):
        base_url = os.getenv('DASHSCOPE_BASE_URL')
    elif api_key == os.getenv('DEEPSEEK_API_KEY'):
        base_url = os.getenv('DEEPSEEK_BASE_URL')
    else:
        base_url = os.getenv('OPENAI_BASE_URL')

    llm_client = LLMClient(api_key=api_key, base_url=base_url) if api_key else None

    feishu_app_id = os.getenv('FEISHU_APP_ID')
    feishu_app_secret = os.getenv('FEISHU_APP_SECRET')
    feishu_client = FeishuClient(feishu_app_id, feishu_app_secret) if feishu_app_id and feishu_app_secret else None

    return api_key, base_url, llm_client, feishu_client


def _print_agent_result(intent: str, result: dict):
    """按意图类型打印Agent结果"""
    if intent == 'requirement':
        print(f"✓ 本地文件: {result['local_path']}")
        if result.get('feishu_url'):
            print(f"✓ 飞书文档: {result['feishu_url']}")
        for line in result['markdown'].split('\n')[:5]:
            print(f"  {line}")
    elif intent == 'test_point':
        scenarios = result['scenarios']
        print(f"✓ 生成 {len(scenarios)} 个测试场景")
        for s in scenarios[:5]:
            print(f"  - [{s.get('priority', '?')}] {s['description']}")
        if len(scenarios) > 5:
            print(f"  ... 还有{len(scenarios)-5}个")
    else:
        print(f"✓ 测试代码已生成: {result['file_path']}")


def interactive_mode(api_key=None, base_url=None, llm_client=None, feishu_client=None):
    """交互式单独调用Agent模式（支持自然语言自动路由）"""
    print("\n" + "="*50)
    print("Agent单独调用模式（支持自然语言）")
    print("="*50)
    print("\n直接输入自然语言即可，系统自动识别意图调用对应Agent:")
    print("  • 分析需求： 分析demo/login.py的测试需求 / 分析需求：<飞书链接>")
    print("  • 测试场景： 根据login功能生成测试场景")
    print("  • 测试代码： 为login函数生成Web异常测试")
    print("\n也可用命令前缀显式指定（req/tp/gen），quit退出")
    print("="*50)
    
    # 初始化Agent
    req_agent = RequirementAnalyzer(llm_client=llm_client, feishu_client=feishu_client)
    tp_agent = TestPointGenerator(llm_client=llm_client, feishu_client=feishu_client)
    gen_agent = TestGeneratorAgent(api_key=api_key, base_url=base_url, test_type='web')
    dispatcher = IntentDispatcher(req_agent, tp_agent, gen_agent, llm_client=llm_client)
    
    while True:
        try:
            user_input = input("\n>>> ").strip()
            if not user_input:
                continue
            
            if user_input.lower() in ['quit', 'exit', 'q']:
                print("再见！")
                break
            
            parts = user_input.split(maxsplit=1)
            command = parts[0].lower()
            
            # 命令前缀显式调用（快捷方式）
            if command in ('req', 'tp', 'gen') and len(parts) >= 2:
                query = parts[1]
                if command == 'req':
                    print("\n[需求分析Agent]")
                    _print_agent_result('requirement', req_agent.process_query(query))
                elif command == 'tp':
                    print("\n[测试点生成Agent]")
                    _print_agent_result('test_point', tp_agent.process_query(query))
                else:
                    print("\n[测试生成Agent]")
                    _print_agent_result('generate', gen_agent.process_query(query))
            else:
                # 自然语言自动路由
                dispatched = dispatcher.dispatch(user_input)
                print(f"\n[{dispatched['intent']}]")
                _print_agent_result(dispatched['intent'], dispatched['result'])
        
        except KeyboardInterrupt:
            print("\n再见！")
            break
        except Exception as e:
            print(f"✗ 错误: {e}")


def main():
    print("=" * 50)
    print("多Agent协作 - 客户端测试生成系统")
    print("=" * 50)
    
    # 初始化客户端
    api_key, base_url, llm_client, feishu_client = _init_clients()
    
    # 选择运行模式
    print("\n运行模式:")
    print("1. 完整工作流（3步流水线：需求分析→测试点→测试代码）")
    print("2. 单独调用Agent（自然语言交互）")
    
    mode_choice = input("\n选择模式 (1/2, 默认1): ").strip()
    
    if mode_choice == '2':
        interactive_mode(api_key=api_key, base_url=base_url, llm_client=llm_client, feishu_client=feishu_client)
        return
    
    # 默认：完整工作流模式（3步流水线）
    # 选择测试类型
    print("\n测试类型:")
    print("1. Web UI测试 (Playwright/Selenium)")
    print("2. 移动端测试 (Appium)")
    print("3. API客户端测试")
    
    type_choice = input("\n选择测试类型 (1/2/3, 默认1): ").strip()
    test_type_map = {'1': 'web', '2': 'mobile', '3': 'api'}
    test_type = test_type_map.get(type_choice, 'web')
    
    target_file = input("\n请输入要测试的文件路径: ").strip()
    
    if not os.path.exists(target_file):
        print(f"✗ 错误: 文件 {target_file} 不存在")
        return
    
    # 初始化编排器（传入llm_client和feishu_client，支持需求分析+飞书写入）
    orchestrator = AgentOrchestrator(
        test_type=test_type,
        api_key=api_key,
        base_url=base_url,
        llm_client=llm_client,
        feishu_client=feishu_client
    )
    
    # 选择生成策略
    strategy = input("\n生成策略 (all=全部 / high=仅高优先级, 默认all): ").strip().lower()
    generate_all = strategy != 'high'
    
    # 执行3步工作流：需求分析→测试点→测试代码
    result = orchestrator.execute_workflow(
        file_path=target_file,
        generate_all=generate_all
    )
    
    # 显示Agent状态
    status = orchestrator.get_agent_status()
    print(f"\nAgent状态:")
    for agent_name, state in status.items():
        print(f"  • {agent_name}: {state}")
    
    print("\n提示: 运行 pytest generated_tests/ 执行测试")


if __name__ == '__main__':
    main()
