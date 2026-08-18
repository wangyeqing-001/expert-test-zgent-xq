"""Web服务 - Flask后端API"""
import os
import json
import logging
import threading
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from agents.requirement_analyzer import RequirementAnalyzer
from agents.test_point_generator import TestPointGenerator
from agents.test_generator import TestGeneratorAgent
from core.feishu_client import FeishuClient

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='web', static_url_path='')
CORS(app)

# 初始化Agent（全局单例）
from dotenv import load_dotenv
load_dotenv()

# 优先使用百炼,降级到DeepSeek/OpenAI
from core.llm_client import LLMClient
api_key = os.getenv('DASHSCOPE_API_KEY') or os.getenv('DEEPSEEK_API_KEY') or os.getenv('OPENAI_API_KEY')
if os.getenv('DASHSCOPE_API_KEY'):
    base_url = os.getenv('DASHSCOPE_BASE_URL')
elif api_key == os.getenv('DEEPSEEK_API_KEY'):
    base_url = os.getenv('DEEPSEEK_BASE_URL')
else:
    base_url = os.getenv('OPENAI_BASE_URL')

# 初始化飞书客户端（可选）
feishu_app_id = os.getenv('FEISHU_APP_ID')
feishu_app_secret = os.getenv('FEISHU_APP_SECRET')
feishu_client = FeishuClient(feishu_app_id, feishu_app_secret) if feishu_app_id and feishu_app_secret else None

llm_client = LLMClient(api_key=api_key, base_url=base_url) if api_key else None
req_agent = RequirementAnalyzer(llm_client=llm_client, feishu_client=feishu_client)
point_agent = TestPointGenerator(llm_client=llm_client, feishu_client=feishu_client)
gen_agent = TestGeneratorAgent(api_key=api_key, base_url=base_url, test_type='web')

# Agent调用锁（防止并发请求竞态修改Agent内部state）
_agent_lock = threading.Lock()

# 初始化飞书机器人（feishu_client存在即启用）
feishu_bot = None
if feishu_client:
    from core.feishu_bot import FeishuBot
    feishu_bot = FeishuBot(
        req_agent=req_agent,
        point_agent=point_agent,
        gen_agent=gen_agent,
        feishu_client=feishu_client,
        llm_client=llm_client
    )
    logger.info("飞书机器人已启用")


@app.route('/')
def index():
    """返回前端页面"""
    return send_from_directory('web', 'index.html')


@app.route('/api/requirement', methods=['POST'])
def analyze_requirement():
    """需求分析接口 - 生成Markdown需求分析规格书"""
    try:
        data = request.json
        query = data.get('query', '')
        
        if not query:
            return jsonify({'error': '查询内容不能为空'}), 400
        
        with _agent_lock:
            result = req_agent.process_query(query)
        
        return jsonify({
            'success': True,
            'markdown': result['markdown'],
            'local_path': result['local_path'],
            'feishu_url': result.get('feishu_url'),
            'metadata': result.get('metadata', {})
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/test_points', methods=['POST'])
def generate_test_points():
    """测试点生成接口 - Step2: 需求→测试场景（支持结构化输入和自然语言）"""
    try:
        data = request.json
        requirements = data.get('requirements', [])
        query = data.get('query', '')
        test_type = data.get('test_type', 'web')
        source = data.get('source', 'code')
        
        if requirements:
            # 结构化输入
            exec_input = {
                'requirements': requirements,
                'test_type': test_type,
                'source': source
            }
            # prd直提可选参数：原始PRD全文/约束清单
            if data.get('raw_prd'):
                exec_input['raw_prd'] = data['raw_prd']
            if data.get('structured_constraints'):
                exec_input['structured_constraints'] = data['structured_constraints']
            with _agent_lock:
                result = point_agent.execute(exec_input)
        elif query:
            # 自然语言输入
            with _agent_lock:
                result = point_agent.process_query(query, {'test_type': test_type, 'source': source})
        else:
            return jsonify({'error': '请提供 requirements 列表或 query 自然语言'}), 400
        
        return jsonify({
            'success': True,
            'scenarios': result['scenarios'],
            'metadata': result.get('metadata', {})
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/generate', methods=['POST'])
def generate_test():
    """测试生成接口 - Step3: 测试场景→测试代码"""
    try:
        data = request.json
        query = data.get('query', '')
        requirement_doc = data.get('requirement_doc', '')
        context = data.get('context', {})
        
        full_query = query
        if requirement_doc:
            if query:
                full_query = f"根据以下需求文档生成测试用例：\n\n{requirement_doc}\n\n补充说明：{query}"
            else:
                full_query = f"根据以下需求文档生成测试用例：\n\n{requirement_doc}"
        
        if not full_query:
            return jsonify({'error': '请输入需求文档或补充说明'}), 400
        
        with _agent_lock:
            result = gen_agent.process_query(full_query, context)
        
        with open(result['file_path'], 'r', encoding='utf-8') as f:
            test_code = f.read()
        
        return jsonify({
            'success': True,
            'file_path': result['file_path'],
            'test_code': test_code,
            'test_case': result['test_case']
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/status', methods=['GET'])
def get_status():
    """获取Agent状态"""
    return jsonify({
        'requirement_agent': req_agent.get_state(),
        'test_point_agent': point_agent.get_state(),
        'generator_agent': gen_agent.get_state()
    })


@app.route('/api/pipeline', methods=['POST'])
def run_pipeline():
    """完整流水线接口 - 需求分析→测试点→测试代码（3步串联）"""
    try:
        from agents.orchestrator import AgentOrchestrator
        
        data = request.json
        query = data.get('query', '')
        file_path = data.get('file_path', '')
        test_type = data.get('test_type', 'web')
        generate_all = data.get('generate_all', True)
        
        if not query and not file_path:
            return jsonify({'error': '请提供query或file_path'}), 400
        
        logger.info(f"启动完整流水线: query={query[:50]}..., file_path={file_path}, test_type={test_type}")
        
        # 如果提供的是query（自然语言/PRD），先通过需求分析
        requirement_result = None
        requirements = None
        
        if query and not file_path:
            # 自然语言模式：先做需求分析
            requirement_result = req_agent.process_query(query)
            logger.info(f"需求分析完成: local={requirement_result['local_path']}, feishu={requirement_result.get('feishu_url')}")
            
            # 从markdown提取结构化需求
            from agents.orchestrator.agent import AgentOrchestrator as Orch
            requirements = Orch._extract_requirements(requirement_result['markdown'])
        
        if file_path:
            # 文件模式：用编排器跑完整3步流水线
            orchestrator = AgentOrchestrator(
                test_type=test_type,
                api_key=api_key,
                base_url=base_url,
                llm_client=llm_client,
                feishu_client=feishu_client
            )
            result = orchestrator.execute_workflow(
                file_path=file_path,
                generate_all=generate_all
            )
            
            # 读取生成的测试代码
            test_files = []
            for fp in result['generated_files']:
                try:
                    with open(fp, 'r', encoding='utf-8') as f:
                        test_files.append({'path': fp, 'code': f.read()})
                except Exception:
                    test_files.append({'path': fp, 'code': ''})
            
            req_result = result.get('requirement_result')
            return jsonify({
                'success': True,
                'generated_files': result['generated_files'],
                'test_files': test_files,
                'requirement': {
                    'markdown': req_result.get('markdown') if req_result else None,
                    'local_path': req_result.get('local_path') if req_result else None,
                    'feishu_url': req_result.get('feishu_url') if req_result else None
                }
            })
        
        # query模式：需求分析 + 测试点 + 测试代码
        # Step2: 测试点生成（PRD/飞书需求走prd直提：主材料=原始文档全文）
        req_source = (requirement_result or {}).get('metadata', {}).get('source', '')
        raw_prd = None
        if req_source in ('prd_document', 'feishu_doc'):
            raw_prd = (requirement_result or {}).get('raw_content') \
                or (requirement_result or {}).get('markdown', '')
            point_requirements = [{
                'function': 'all', 'name': '需求文档', 'complexity': 'medium',
                'test_points': ['功能逻辑'],
                'description': raw_prd[:6000]
            }]
            point_source = 'prd'
        else:
            point_requirements = requirements or []
            point_source = 'code'
        point_input = {
            'requirements': point_requirements,
            'test_type': test_type,
            'source': point_source,
            'title': (requirement_result or {}).get('metadata', {}).get('title')
        }
        if raw_prd:
            point_input['raw_prd'] = raw_prd
        point_result = point_agent.execute(point_input)
        scenarios = point_result['scenarios']
        logger.info(f"测试点生成: {len(scenarios)}个场景")
        
        # Step3: 测试代码生成
        if not generate_all:
            scenarios = [s for s in scenarios if s.get('priority') == 'high']
        
        generated_tests = []
        for scenario in scenarios:
            try:
                gen_query = f"为{scenario.get('function', '')}生成{scenario.get('description', '')}的测试用例"
                gen_result = gen_agent.process_query(gen_query, {})
                with open(gen_result['file_path'], 'r', encoding='utf-8') as f:
                    generated_tests.append({
                        'path': gen_result['file_path'],
                        'code': f.read(),
                        'scenario': scenario
                    })
            except Exception as e:
                logger.warning(f"测试生成失败: {e}")
        
        return jsonify({
            'success': True,
            'generated_files': [t['path'] for t in generated_tests],
            'test_files': generated_tests,
            'requirement': {
                'markdown': requirement_result.get('markdown') if requirement_result else None,
                'local_path': requirement_result.get('local_path') if requirement_result else None,
                'feishu_url': requirement_result.get('feishu_url') if requirement_result else None
            },
            'scenarios': scenarios
        })
    
    except Exception as e:
        logger.error(f"流水线执行失败: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/feishu/parse', methods=['POST'])
def parse_feishu_doc():
    """解析飞书文档URL，提取内容"""
    try:
        data = request.json
        doc_url = data.get('doc_url', '')
        
        logger.info(f"收到飞书文档解析请求: {doc_url}")
        
        if not doc_url:
            logger.warning("请求中缺少doc_url参数")
            return jsonify({'error': '请提供飞书文档链接'}), 400
        
        if not feishu_client:
            logger.error("飞书API客户端未初始化")
            return jsonify({
                'error': '飞书API未配置，请在.env中设置FEISHU_APP_ID和FEISHU_APP_SECRET'
            }), 503
        
        doc_token, doc_type = FeishuClient.parse_doc_url(doc_url)
        logger.info(f"URL解析成功: token={doc_token}, type={doc_type}")
        
        content = feishu_client.get_doc_content(doc_token, doc_type)
        content_len = len(content) if content else 0
        logger.info(f"成功获取文档内容: 长度={content_len}字符")
        
        return jsonify({
            'success': True,
            'content': content,
            'doc_type': doc_type,
            'doc_token': doc_token
        })
    
    except ValueError as e:
        logger.error(f"URL解析失败: {str(e)}")
        return jsonify({'error': f'URL解析失败: {str(e)}'}), 400
    except Exception as e:
        logger.error(f"获取文档失败: {str(e)}", exc_info=True)
        return jsonify({'error': f'获取文档失败: {str(e)}'}), 500


@app.route('/api/feishu/event', methods=['POST'])
def feishu_event():
    """飞书事件订阅回调"""
    data = request.json
    
    # 1. URL验证（challenge）
    if 'challenge' in data:
        return jsonify({'challenge': data['challenge']})
    
    # 2. 检查机器人是否启用
    if not feishu_bot:
        logger.warning("飞书机器人未启用，忽略事件")
        return jsonify({'code': 0})
    
    # 3. 处理消息事件
    try:
        header = data.get('header', {})
        event_type = header.get('event_type', '')
        event = data.get('event', {})
        
        if event_type == 'im.message.receive_v1':
            feishu_bot.handle_event(event)
    except Exception as e:
        logger.error(f"处理飞书事件失败: {e}", exc_info=True)
    
    return jsonify({'code': 0})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5001, help='服务端口')
    args = parser.parse_args()
    
    print("="*50)
    print("启动Web服务")
    print(f"访问地址: http://localhost:{args.port}")
    print(f"日志级别: INFO")
    print("="*50)
    logger.info(f"Web服务启动, 端口={args.port}")
    app.run(host='0.0.0.0', port=args.port, debug=True)
