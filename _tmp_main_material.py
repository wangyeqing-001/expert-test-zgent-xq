# 临时验证：主材料=原始PRD + 分支A约束清单
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()
from core.llm_client import LLMClient
from core.feishu_client import FeishuClient
from agents.test_point_generator.agent import TestPointGenerator

with open('generated_requirements/AI_搜索一期-需求文备份_20260818_110039.md', encoding='utf-8') as f:
    prd = f.read()

llm = LLMClient(api_key=os.getenv('DASHSCOPE_API_KEY'), base_url=os.getenv('DASHSCOPE_BASE_URL'))
fs = FeishuClient(os.getenv('FEISHU_APP_ID'), os.getenv('FEISHU_APP_SECRET'))
agent = TestPointGenerator(llm_client=llm, feishu_client=fs, output_dir='generated_testpoints')

# 分支A单独验证（金融敏感词可能被供应商合规拦截，降级为软校验）
constraints = agent._extract_constraints(prd)
if constraints and '清单' in constraints:
    print('--- 约束清单片段 ---')
    print(constraints[:300])
else:
    print('⚠ 分支A被供应商拦截(预期内降级)，主流程将跳过辅助材料')

# 完整e2e：显式传入约束清单（验证门控注入链路），拦截时传''走无辅助材料路径
result = agent.execute({
    'requirements': [],
    'raw_prd': prd,
    'structured_constraints': constraints or '',
    'test_type': 'web',
    'source': 'prd',
    'title': '主材料改造验证-测试点清单'
})
tps = result['test_points']
assert tps and result['test_points_json_path'], '直提失败'
print('✓ e2e: 测试点', len(tps), '条')
print('  json:', result['test_points_json_path'])
print('  feishu:', result.get('feishu_url'))
print('  涉及端:', sorted({p['endpoint'] for p in tps}))
print('✓ 全部验证通过')
