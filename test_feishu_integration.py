"""测试飞书PRD文档导入 + 需求分析 + 测试点生成 完整流程"""
import sys
import os
from dotenv import load_dotenv

load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(__file__))

from agents.requirement_analyzer import RequirementAnalyzer
from agents.test_point_generator import TestPointGenerator
from core.llm_client import LLMClient
from core.feishu_client import FeishuClient

# Step 1: 解析飞书文档
print("="*60)
print("Step 1: 解析飞书PRD文档")
print("="*60)

feishu_client = FeishuClient(
    app_id=os.getenv('FEISHU_APP_ID'),
    app_secret=os.getenv('FEISHU_APP_SECRET')
)

doc_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv('FEISHU_TEST_DOC_URL', '')
if not doc_url:
    print("用法: python test_feishu_integration.py <飞书文档URL>")
    print("或设置环境变量 FEISHU_TEST_DOC_URL")
    sys.exit(1)
doc_token, doc_type = FeishuClient.parse_doc_url(doc_url)
print(f"文档类型: {doc_type}")
print(f"文档Token: {doc_token}")

content = feishu_client.get_doc_content(doc_token, doc_type)
print(f"✓ 获取文档内容: {len(content)}字符\n")

# Step 2: 需求分析
print("="*60)
print("Step 2: 需求分析Agent - 提取结构化需求")
print("="*60)

api_key = os.getenv('DASHSCOPE_API_KEY')
base_url = os.getenv('DASHSCOPE_BASE_URL')
llm_client = LLMClient(api_key=api_key, base_url=base_url)

req_agent = RequirementAnalyzer(llm_client=llm_client)
point_agent = TestPointGenerator(llm_client=llm_client)

try:
    req_result = req_agent.process_query(content)
    requirements = req_result['requirements']
    print(f"✓ 需求数量: {len(requirements)}")
    
    print("\n主要需求:")
    for i, req in enumerate(requirements[:5], 1):
        func = req.get('function', 'N/A')[:50]
        complexity = req.get('complexity', 'N/A')
        print(f"  {i}. [{complexity}] {func}")
    
    # Step 3: 测试点生成
    print("\n" + "="*60)
    print("Step 3: 测试点生成Agent - 生成测试场景")
    print("="*60)
    
    point_result = point_agent.execute({
        'requirements': requirements,
        'source': 'prd'
    })
    scenarios = point_result['scenarios']
    print(f"✓ 场景数量: {len(scenarios)}")
    
    print("\n示例场景:")
    for i, scenario in enumerate(scenarios[:5], 1):
        func = scenario.get('function', 'N/A')[:30]
        desc = scenario.get('description', 'N/A')[:60]
        priority = scenario.get('priority', 'N/A')
        print(f"  {i}. [{priority}] {func}: {desc}")
        
except Exception as e:
    print(f"✗ 失败: {type(e).__name__}: {str(e)[:200]}")
    import traceback
    traceback.print_exc()

print("\n" + "="*60)
print("测试完成")
print("="*60)
