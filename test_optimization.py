#!/usr/bin/env python3
"""测试优化后的Agent流水线（需求分析→测试点生成）"""
import sys
import os

# 清除所有pyc缓存
for root, dirs, files in os.walk('.'):
    if '__pycache__' in dirs:
        import shutil
        shutil.rmtree(os.path.join(root, '__pycache__'))

from dotenv import load_dotenv
load_dotenv()

from agents.requirement_analyzer import RequirementAnalyzer
from agents.test_point_generator import TestPointGenerator
from core.llm_client import LLMClient

api_key = os.getenv('DEEPSEEK_API_KEY')
base_url = os.getenv('DEEPSEEK_BASE_URL')

llm = LLMClient(api_key=api_key, base_url=base_url)
req_agent = RequirementAnalyzer(llm_client=llm)
point_agent = TestPointGenerator(llm_client=llm)

query = '分析demo/login.py的测试需求'
print(f'查询: {query}\n')

try:
    # Step 1: 需求分析
    req_result = req_agent.process_query(query)
    requirements = req_result['requirements']
    print(f'✓ 需求数量: {len(requirements)}')
    
    # Step 2: 测试点生成
    point_result = point_agent.execute({
        'requirements': requirements,
        'test_type': 'web',
        'source': 'code'
    })
    scenarios = point_result['scenarios']
    print(f'✓ 场景数量: {len(scenarios)}\n')
    
    for i, scenario in enumerate(scenarios[:5], 1):
        print(f'{i}. [{scenario.get("priority", "N/A")}] {scenario.get("description", "N/A")}')
        if 'test_points' in scenario:
            print(f'   测试点: {", ".join(scenario["test_points"])}')
except Exception as e:
    print(f'✗ {type(e).__name__}: {str(e)[:200]}')
    import traceback
    traceback.print_exc()
