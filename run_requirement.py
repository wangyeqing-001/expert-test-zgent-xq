"""独立脚本：飞书文档链接 → 需求分析
用法: python run_requirement.py <飞书文档链接>
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(override=True)


def run(feishu_url: str):
    from core.llm_client import LLMClient
    from core.feishu_client import FeishuClient
    from agents.requirement_analyzer import RequirementAnalyzer

    print("=" * 60)
    print("飞书文档 → 需求分析")
    print("=" * 60)

    # 1. 初始化客户端
    t0 = time.time()
    api_key = os.getenv('DASHSCOPE_API_KEY') or os.getenv('DEEPSEEK_API_KEY') or os.getenv('OPENAI_API_KEY')
    if not api_key:
        print("✗ 未配置LLM API Key，请检查 .env"); sys.exit(1)

    provider = '百炼DashScope' if os.getenv('DASHSCOPE_API_KEY') else ('DeepSeek' if api_key == os.getenv('DEEPSEEK_API_KEY') else 'OpenAI')
    if os.getenv('DASHSCOPE_API_KEY'):
        base_url = os.getenv('DASHSCOPE_BASE_URL')
    elif api_key == os.getenv('DEEPSEEK_API_KEY'):
        base_url = os.getenv('DEEPSEEK_BASE_URL')
    else:
        base_url = os.getenv('OPENAI_BASE_URL')

    print(f"\n[1/5] 初始化LLM客户端 ({provider})")
    print(f"  base_url = {base_url}")
    llm = LLMClient(api_key, base_url)

    feishu_app_id = os.getenv('FEISHU_APP_ID')
    feishu_app_secret = os.getenv('FEISHU_APP_SECRET')
    if not feishu_app_id or not feishu_app_secret:
        print("✗ 未配置飞书凭据，请检查 .env"); sys.exit(1)
    print(f"  飞书APP_ID = {feishu_app_id[:8]}...")
    feishu = FeishuClient(feishu_app_id, feishu_app_secret)
    print(f"  耗时: {time.time()-t0:.2f}s")

    # 2. 解析飞书文档
    t1 = time.time()
    print(f"\n[2/5] 解析飞书文档")
    print(f"  URL: {feishu_url}")
    token, doc_type = FeishuClient.parse_doc_url(feishu_url)
    print(f"  token = {token}")
    print(f"  type  = {doc_type}")

    title = feishu.get_doc_title(token, doc_type)
    print(f"  标题: {title or '(未获取到)'}")

    content = feishu.get_doc_content(token, doc_type)
    print(f"  文档内容: {len(content)} 字符")
    print(f"  耗时: {time.time()-t1:.2f}s")

    # 3. 需求分析
    t2 = time.time()
    agent = RequirementAnalyzer(llm_client=llm, feishu_client=feishu)
    print(f"\n[3/5] 开始需求分析...")
    result = agent.process_query(content, title=title)
    print(f"  需求分析耗时: {time.time()-t2:.2f}s")

    # 4. 输出结果
    print(f"\n[4/5] 输出结果")
    print(f"  本地文件: {result['local_path']}")
    if result.get('feishu_url'):
        print(f"  飞书文档: {result['feishu_url']}")
    md = result['markdown']
    print(f"  Markdown: {len(md)} 字符, {len(md.splitlines())} 行")

    # 5. 汇总
    print(f"\n[5/5] 汇总")
    print(f"  总耗时: {time.time()-t0:.2f}s")
    print("=" * 60)
    print(md)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python run_requirement.py <飞书文档链接>")
        print("示例: python run_requirement.py https://your_company.feishu.cn/docx/xxxxx")
        sys.exit(1)
    run(sys.argv[1])
