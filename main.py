"""多Agent协作 - 客户端测试用例生成系统"""
import os
from agents.orchestrator import AgentOrchestrator


def main():
    print("=" * 50)
    print("多Agent协作 - 客户端测试生成系统")
    print("=" * 50)
    
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
    
    # 获取API Key（可选）
    api_key = os.getenv('OPENAI_API_KEY') or input("\n请输入OpenAI API Key（留空使用模板模式）: ").strip() or None
    
    # 初始化编排器（自动创建各Agent）
    orchestrator = AgentOrchestrator(test_type=test_type, api_key=api_key)
    
    # 选择生成策略
    strategy = input("\n生成策略 (all=全部 / high=仅高优先级, 默认all): ").strip().lower()
    generate_all = strategy != 'high'
    
    # 执行工作流
    generated_files = orchestrator.execute_workflow(
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
