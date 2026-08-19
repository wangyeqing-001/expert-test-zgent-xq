"""临时验证：测试点表格真实链路（LLM表格 → struct直写飞书）"""
import os
from dotenv import load_dotenv
load_dotenv()

from core.llm_client import LLMClient
from core.feishu_client import FeishuClient
from agents.test_point_generator import TestPointGenerator

llm = LLMClient(api_key=os.getenv('DASHSCOPE_API_KEY'), base_url=os.getenv('DASHSCOPE_BASE_URL'))
fs = FeishuClient(os.getenv('FEISHU_APP_ID'), os.getenv('FEISHU_APP_SECRET'))

# 构造12个场景（覆盖>8行表格拆分 + 三种测试类型）
scenarios = []
funcs = ['search_input', 'guess_like_search', 'result_rank']
types = [('normal', 'high'), ('edge_case', 'medium'), ('error_handling', 'high'), ('normal', 'low')]
i = 0
for f in funcs:
    for scen, pri in types:
        i += 1
        scenarios.append({
            'function': f,
            'scenario': scen,
            'description': f'测试{f}的{scen}场景{i}',
            'precondition': '系统正常运行，测试数据已准备',
            'steps': f'调用{f}，按{scen}条件输入并观察结果',
            'expected': '返回符合预期的结果，无崩溃无异常',
            'priority': pri,
            'test_points': ['功能逻辑', '数据校验']
        })

agent = TestPointGenerator(llm_client=llm, feishu_client=fs)
local_path, feishu_url = agent._save_and_publish_table(scenarios, 'AI搜索一期-测试点清单')

print('=' * 50)
print(f'本地文件: {local_path}')
print(f'飞书URL: {feishu_url}')
assert local_path and os.path.exists(local_path), '本地.md未生成'
assert feishu_url, '飞书文档未创建'
md = open(local_path).read()
assert '编号' in md and '优先级' in md, '表格列缺失'
assert '###' not in md or md.count('###') == 0, '存在Markdown残留标题'
print('✓ 验证通过')
