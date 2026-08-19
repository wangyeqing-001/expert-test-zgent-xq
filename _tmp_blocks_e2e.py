"""临时脚本：端到端验证 struct 直写通道（业务JSON → create_doc_from_struct）"""
import os
from dotenv import load_dotenv
load_dotenv()
from core.feishu_client import FeishuClient

fc = FeishuClient(os.getenv('FEISHU_APP_ID'), os.getenv('FEISHU_APP_SECRET'))

struct_blocks = [
    {"type": "h1", "text": "1. 需求全景"},
    {"type": "bullet_list", "items": ["业务背景与价值：验证struct直写通道格式完整性", "核心业务目标：零Markdown解析写入"]},
    {"type": "h1", "text": "2. 功能规格"},
    {"type": "h2", "text": "功能模块总览"},
    {"type": "table", "headers": ["模块", "核心功能描述", "输入来源", "业务优先级", "测试优先级建议"],
     "rows": [["SUG页", "新增猜你想搜模块", "搜索服务", "P0", "高"],
              ["结果页", "移除低效卡片", "配置中心", "P1", "中"]]},
    {"type": "h3", "text": "功能点1：猜你想搜展示"},
    {"type": "bullet_list", "items": ["业务规则：仅股票类query且灰度开关开启时展示"]},
    {"type": "h3", "text": "正向流程测试项"},
    {"type": "ordered_list", "items": ["灰度内用户搜索股票：已登录 -> 输入股票名 -> 展示模块"]},
    {"type": "h3", "text": "异常/边界测试项"},
    {"type": "ordered_list", "items": ["输入为空 -> 不展示模块 重要：需明确提示"]},
]

result = fc.create_doc_from_struct(
    title='[struct直写验证]可删除',
    folder_token=os.getenv('FEISHU_OUTPUT_FOLDER'),
    struct_blocks=struct_blocks
)
doc_id = result['document_id']
print('document_id:', doc_id)

# 读回验证
content = fc.get_doc_content(doc_id, 'doc')
print('读回长度:', len(content))
ok = True
for kw in ['需求全景', '业务背景与价值', '猜你想搜模块', '功能点1', '输入为空', '重要：']:
    hit = kw in content
    ok = ok and hit
    print(f'{"✓" if hit else "✗"} {kw}')

# 检查无JSON/markdown符号残留
for sym in ['{"type"', '**', '###', '```']:
    n = content.count(sym)
    print(f'残留{sym!r}: {n}处')
    if n:
        ok = False
print('端到端验证:', '通过' if ok else '失败')
