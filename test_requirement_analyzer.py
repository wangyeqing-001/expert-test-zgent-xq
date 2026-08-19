"""测试需求分析Agent（优化后：仅需求提取，不含场景生成）"""
import sys
import os
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from agents.requirement_analyzer import RequirementAnalyzer
from agents.test_point_generator import TestPointGenerator
from core.llm_client import LLMClient

def test_pipeline():
    """测试3步流水线"""
    
    api_key = os.getenv('DASHSCOPE_API_KEY') or os.getenv('DEEPSEEK_API_KEY')
    base_url = os.getenv('DASHSCOPE_BASE_URL') if os.getenv('DASHSCOPE_API_KEY') else os.getenv('DEEPSEEK_BASE_URL')
    
    print("="*60)
    print("测试3步Agent流水线")
    print("="*60)
    
    llm_client = LLMClient(api_key=api_key, base_url=base_url) if api_key else None
    req_agent = RequirementAnalyzer(llm_client=llm_client)
    point_agent = TestPointGenerator(llm_client=llm_client)
    
    # 测试1: 代码需求分析
    print("\n【Step 1】需求分析 - 分析demo/login.py")
    try:
        req_result = req_agent.execute({
            'file_path': 'demo/login.py',
            'test_type': 'web'
        })
        requirements = req_result['requirements']
        print(f"✓ 需求数量: {len(requirements)}")
        if requirements:
            print(f"  示例需求: {requirements[0].get('function', 'N/A')}")
    except Exception as e:
        print(f"✗ 失败: {type(e).__name__}: {str(e)[:200]}")
        return
    
    # 测试2: 测试点生成
    print("\n【Step 2】测试点生成 - 基于需求生成测试场景")
    try:
        point_result = point_agent.execute({
            'requirements': requirements,
            'test_type': 'web',
            'source': 'code'
        })
        scenarios = point_result['scenarios']
        print(f"✓ 场景数量: {len(scenarios)}")
        if scenarios:
            print(f"  示例场景: {scenarios[0].get('description', 'N/A')}")
    except Exception as e:
        print(f"✗ 失败: {type(e).__name__}: {str(e)[:200]}")
    
    # 测试3: PRD文档分析 → 测试点生成
    print("\n【Step 1+2】PRD文档 → 需求提取 → 测试点生成")
    prd_sample = """
AI+搜索一期产品需求文档

一、项目背景
为了提升用户搜索体验，我们计划推出智能搜索功能，支持搜索建议、搜索结果聚合展示。

二、功能模块
1. Sug页（搜索建议）
   - 用户输入关键词时实时展示搜索建议
   - 支持历史搜索记录

2. 搜索结果页
   - 展示与关键词相关的搜索结果
   - 支持结果筛选和排序
"""
    try:
        prd_req = req_agent.process_query(prd_sample)
        prd_requirements = prd_req['requirements']
        print(f"✓ PRD需求数量: {len(prd_requirements)}")
        
        prd_points = point_agent.execute({
            'requirements': prd_requirements,
            'source': 'prd'
        })
        prd_scenarios = prd_points['scenarios']
        print(f"✓ PRD场景数量: {len(prd_scenarios)}")
        if prd_scenarios:
            print(f"  示例: {prd_scenarios[0].get('description', 'N/A')[:60]}")
    except Exception as e:
        print(f"✗ 失败: {type(e).__name__}: {str(e)[:200]}")
    
    print("\n" + "="*60)
    print("测试完成")
    print("="*60)

if __name__ == '__main__':
    test_pipeline()
