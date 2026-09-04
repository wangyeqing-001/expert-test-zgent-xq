"""Web服务 - Flask后端API"""
import os
import re
import json
import logging
import threading
import requests
from datetime import datetime
from collections import deque
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


def clean_control_chars(text: str) -> str:
    """清理 JSON 字符串中会导致前端 JSON.parse 失败的非法控制字符（保留 \n \r \t）"""
    if not text:
        return ''
    return ''.join(
        ch for ch in text
        if ch in ('\n', '\r', '\t') or ord(ch) >= 32
    )


# ---- 实时日志缓冲（供前端 /api/logs 增量拉取，展示在页面底部）----
_log_buffer = deque(maxlen=500)
_log_seq = 0
_log_lock = threading.Lock()


class _BufferLogHandler(logging.Handler):
    """把日志同步写入内存缓冲，前端轮询展示。"""
    def emit(self, record):
        global _log_seq
        try:
            text = self.format(record)
            with _log_lock:
                _log_seq += 1
                _log_buffer.append((_log_seq, text))
        except Exception:
            pass


_buf_handler = _BufferLogHandler()
_buf_handler.setLevel(logging.INFO)
_buf_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(name)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S'
))
logging.getLogger().addHandler(_buf_handler)
# 降低 werkzeug 请求日志级别，避免轮询刷屏
logging.getLogger('werkzeug').setLevel(logging.WARNING)

app = Flask(__name__, static_folder='web', static_url_path='')
CORS(app)

# 初始化Agent（全局单例）
from dotenv import load_dotenv
load_dotenv(override=True)

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

# ---- YAPI 接口详情拉取 ----
YAPI_INTERFACE_API = 'https://ugcqams.snowballfinance.com/internal/getInterfaceData'
_YAPI_ID_RE = re.compile(r'/interface/api/(\d+)')

def _fetch_yapi_interface(yapi_url: str) -> dict:
    """通过 YAPI URL 拉取接口详情，失败返回空 dict（含失败原因日志）"""
    m = _YAPI_ID_RE.search(yapi_url)
    if not m:
        logger.info(f"YAPI 链接无具体接口ID，跳过拉取: {yapi_url}")
        return {}
    yapi_id = m.group(1)
    try:
        resp = requests.get(YAPI_INTERFACE_API, params={'interfaceYapiId': yapi_id}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('code') == 0 or data.get('success'):
                return data.get('data') or data
            logger.warning(f"YAPI 接口返回业务错误 {yapi_url}: code={data.get('code')}, msg={data.get('message', '')}")
            return data
        elif resp.status_code == 404:
            logger.warning(f"YAPI 接口已删除 {yapi_url}: 404")
        elif resp.status_code in (401, 403):
            logger.warning(f"YAPI 接口鉴权失败 {yapi_url}: {resp.status_code}")
        else:
            logger.warning(f"YAPI 接口HTTP异常 {yapi_url}: {resp.status_code}")
    except requests.exceptions.Timeout:
        logger.warning(f"YAPI 接口拉取超时 {yapi_url}")
    except Exception as e:
        logger.warning(f"YAPI 接口拉取失败 {yapi_url}: {e}")
    return {}

def _fetch_yapi_interfaces(yapi_urls: list) -> list:
    """批量拉取 YAPI 接口详情并标准化

    降级策略：
    - 无 YAPI 链接（纯前端/设计改动）→ 返回 []，下游正常工作
    - 部分接口拉取失败（已删除/超时/鉴权）→ 跳过失败项，返回成功项
    - 全部失败 → 返回 []，下游收到"无接口数据"提示
    """
    if not yapi_urls:
        return []
    results = []
    failed = 0
    for url in yapi_urls:
        raw = _fetch_yapi_interface(url)
        if raw and (raw.get('path') or raw.get('title')):
            results.append(_normalize_yapi_interface(url, raw))
        else:
            failed += 1
    logger.info(f"YAPI 接口拉取完成: {len(results)}/{len(yapi_urls)} 成功, {failed} 失败/空")
    return results

def _normalize_yapi_interface(yapi_url: str, raw: dict) -> dict:
    """将 YAPI 原始响应标准化为下游可消费的结构

    输出字段：
    - api_path:  接口路径
    - method:    HTTP 方法
    - title:     接口名称
    - params:    入参定义（含必填、类型、校验规则）
    - response_schema: 出参结构
    - desc:      接口描述
    - change_type:      留空（由 AI 在测试点阶段分析）
    - related_requirement: 留空（由 AI 在测试点阶段分析）
    """
    # 入参标准化
    params = []
    # query 参数
    for p in (raw.get('req_query') or []):
        params.append({
            'name': p.get('name', ''),
            'type': p.get('type', 'string'),
            'required': p.get('required', 0) == 1,
            'desc': p.get('desc', p.get('example', '')),
        })
    # form 参数
    for p in (raw.get('req_body_form') or []):
        params.append({
            'name': p.get('name', ''),
            'type': p.get('type', 'string'),
            'required': p.get('required', 0) == 1,
            'desc': p.get('desc', ''),
        })
    # JSON body 参数（简化：保留原始 JSON schema 文本）
    body_other = raw.get('req_body_other') or ''
    if body_other and isinstance(body_other, str) and len(body_other) > 10:
        params.append({'name': '_body', 'type': 'json', 'required': True, 'desc': body_other[:500]})

    # 出参标准化
    res_body = raw.get('res_body') or raw.get('res_body_other') or ''
    if isinstance(res_body, dict):
        res_body = json.dumps(res_body, ensure_ascii=False)[:2000]
    elif res_body is None:
        res_body = ''

    return {
        'yapi_url': yapi_url,
        'api_path': raw.get('path', ''),
        'method': (raw.get('method', '') or '').upper(),
        'title': raw.get('title', raw.get('name', '')),
        'params': params,
        'response_schema': str(res_body)[:2000],
        'desc': (raw.get('desc') or '')[:300],
        'change_type': '',
        'related_requirement': '',
    }

def _fetch_yapi_interfaces(yapi_urls: list) -> list:
    """批量拉取 YAPI 接口详情并标准化"""
    if not yapi_urls:
        return []
    results = []
    for url in yapi_urls:
        raw = _fetch_yapi_interface(url)
        if raw:
            results.append(_normalize_yapi_interface(url, raw))
    logger.info(f"YAPI 接口拉取完成: {len(results)}/{len(yapi_urls)} 成功")
    return results

# ---- 需求分析历史记录持久化（供前端 /api/history 拉取展示可点击链接）----
HISTORY_FILE = os.path.join('generated_requirements', 'history.json')


def _append_history(record: dict):
    """追加一条需求分析历史记录到 JSON 文件（最新在前，最多保留100条）"""
    try:
        items = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                items = json.load(f)
        items.insert(0, record)
        items = items[:100]
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"写入历史记录失败: {e}")

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
        doc_title = data.get('doc_title')  # 飞书导入时携带的原文档标题
        doc_url = data.get('doc_url')      # 飞书原文链接（供后端关联文档整合）

        if not query:
            return jsonify({'error': '查询内容不能为空'}), 400

        with _agent_lock:
            result = req_agent.process_query(query, title=doc_title, feishu_url=doc_url)

        # 持久化历史记录：原始需求文档链接 + 生成的需求分析飞书文档链接 + YAPI接口链接
        meta = result.get('metadata') or {}
        task_id = f"REQ-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        # 标题/URL 优先级：后端 metadata（最准）> 前端 doc_title/doc_url > query 截断
        final_title = (meta.get('title') or doc_title or '').strip()
        final_doc_url = (meta.get('doc_url') or doc_url or '').strip()
        if not final_title:
            gen_title = result.get('feishu_title', '')
            if gen_title and gen_title.endswith('-需求分析'):
                final_title = gen_title.replace('-需求分析', '')
            else:
                final_title = query[:30]
        _append_history({
            'task_id': task_id,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source_doc_url': final_doc_url,
            'source_title': final_title,
            'feishu_url': result.get('feishu_url'),
            'local_path': result.get('local_path'),
            'yapi_urls': meta.get('yapi_urls', []),
        })

        return jsonify({
            'success': True,
            'task_id': task_id,
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
        task_id = data.get('task_id', '')
        prd_url = data.get('prd_url', '')
        raw_prd_text = data.get('raw_prd', '')
        
        # task_id 优先：根据 ID 从 history 找到需求分析 md，跳过需求分析直接出测试点
        if task_id:
            req_md_text = ''
            req_title = ''
            yapi_urls = []
            try:
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    items = json.load(f)
                for it in items:
                    if it.get('task_id') == task_id or task_id in it.get('local_path', ''):
                        local_path = it.get('local_path', '')
                        if local_path and os.path.exists(local_path):
                            with open(local_path, 'r', encoding='utf-8') as f:
                                req_md_text = f.read()
                        req_title = it.get('source_title', '')
                        yapi_urls = it.get('yapi_urls', [])
                        break
            except Exception as e:
                logger.warning(f"task_id 查找失败: {e}")
            if not req_md_text:
                return jsonify({'error': f'任务 {task_id} 未找到对应需求分析文档'}), 404
            logger.info(f"task_id={task_id}, 加载需求分析文档 {len(req_md_text)}字符, YAPI接口 {len(yapi_urls)}个")
            # 拉取 YAPI 接口详情（供 AI 在测试点生成时参考）
            yapi_interfaces = _fetch_yapi_interfaces(yapi_urls)
            with _agent_lock:
                result = point_agent.execute({
                    'requirements': [{'function': 'all', 'name': req_title, 'complexity': 'medium',
                                      'test_points': ['功能逻辑'], 'description': req_md_text[:6000]}],
                    'test_type': test_type,
                    'source': 'prd',
                    'raw_prd': req_md_text,
                    'title': req_title,
                    'source_doc_url': it.get('source_doc_url', ''),
                    'analysis_doc_url': it.get('feishu_url', ''),
                    'yapi_interfaces': yapi_interfaces,
                })
            return jsonify({
                'success': True,
                'task_id': task_id,
                'scenarios': result['scenarios'],
                'metadata': result.get('metadata', {}),
                'batches': result.get('batches', []),
                'feishu_url': result.get('feishu_url'),
                'local_path': result.get('local_path'),
                'json_path': result.get('json_path'),
            })

        # Debug: 直接传 prd_url 或 raw_prd 跳过需求分析
        if prd_url or raw_prd_text:
            if prd_url and not raw_prd_text:
                # 从飞书拉 PRD
                feishu_url = prd_url
                doc_type = 'wiki' if '/wiki/' in feishu_url else 'doc'
                token = feishu_url.split('/')[-1].split('?')[0]
                try:
                    raw_prd_text = feishu_client.get_doc_content(token, doc_type, doc_url=feishu_url)
                except Exception as e:
                    return jsonify({'error': f'飞书PRD拉取失败: {e}'}), 500
            with _agent_lock:
                result = point_agent.execute({
                    'requirements': [{'function': 'all', 'name': 'PRD文档', 'complexity': 'medium',
                                      'test_points': ['功能逻辑'], 'description': raw_prd_text[:6000]}],
                    'test_type': test_type,
                    'source': 'prd',
                    'raw_prd': raw_prd_text,
                    'title': 'PRD直提',
                })
            return jsonify({
                'success': True,
                'scenarios': result['scenarios'],
                'metadata': result.get('metadata', {}),
                'batches': result.get('batches', []),
                'feishu_url': result.get('feishu_url'),
                'local_path': result.get('local_path'),
            })

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
    """测试生成接口 - Step3: 测试场景→测试代码
    支持两种入口：
    1. task_id: 从 generated_testpoints JSON 加载 batches → execute_batch 逐批生成
    2. query + requirement_doc: 自然语言 → process_query（老路径）
    """
    try:
        data = request.json
        task_id = data.get('task_id', '')
        query = data.get('query', '')
        requirement_doc = data.get('requirement_doc', '')
        context = data.get('context', {})

        # ---- task_id 路径：从测试点 JSON 加载 batches 逐批生成 ----
        if task_id:
            # 从 history 找到该 task_id 对应的测试点 JSON 路径
            tp_json_path = None
            try:
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    items = json.load(f)
                for it in items:
                    if it.get('task_id') == task_id:
                        tp_json_path = it.get('testpoints_json')
                        break
            except Exception as e:
                logger.warning(f"task_id 查找测试点JSON失败: {e}")

            # 兜底：按 task_id 中的日期前缀在 generated_testpoints/ 目录搜索
            if not tp_json_path:
                tp_dir = 'generated_testpoints'
                if os.path.isdir(tp_dir):
                    for fn in sorted(os.listdir(tp_dir), reverse=True):
                        if fn.endswith('.json') and task_id.split('-')[-1][:6] in fn:
                            tp_json_path = os.path.join(tp_dir, fn)
                            break

            if not tp_json_path or not os.path.exists(tp_json_path):
                return jsonify({'error': f'任务 {task_id} 未找到测试点JSON，请先生成测试点'}), 404

            with open(tp_json_path, 'r', encoding='utf-8') as f:
                tp_data = json.load(f)
            batches = tp_data.get('batches', [])
            if not batches:
                return jsonify({'error': '测试点JSON中无batches数据'}), 400

            logger.info(f"task_id={task_id}, 加载测试点 {tp_data.get('total', 0)}个/{len(batches)}批 → 开始生成测试代码")
            generated_files = []
            with _agent_lock:
                for batch in batches:
                    try:
                        result = gen_agent.execute_batch(batch)
                        generated_files.append({
                            'path': result['file_path'],
                            'platform': batch.get('platform', ''),
                            'platform_label': batch.get('platform_label', ''),
                            'batch_index': batch.get('batch_index', 1),
                        })
                    except Exception as e:
                        logger.error(f"batch生成失败 [{batch.get('platform', '')}-{batch.get('batch_index', 1)}]: {e}")

            return jsonify({
                'success': True,
                'task_id': task_id,
                'generated_files': [f['path'] for f in generated_files],
                'test_files': generated_files,
                'total_batches': len(batches),
            })

        # ---- 自然语言路径（老路径） ----
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


@app.route('/api/history', methods=['GET'])
def get_history():
    """需求分析历史记录列表（支持分页：limit + offset）"""
    try:
        items = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                items = json.load(f)
        total = len(items)
        # 分页参数
        limit = request.args.get('limit', type=int)
        offset = request.args.get('offset', default=0, type=int)
        if limit is not None:
            items = items[offset:offset + limit]
        return jsonify({'items': items, 'total': total})
    except Exception as e:
        logger.warning(f"读取历史记录失败: {e}")
        return jsonify({'items': [], 'total': 0})


@app.route('/api/logs')
def get_logs():
    """前端底部日志面板拉取增量日志（?since=<seq>）"""
    try:
        since = int(request.args.get('since', '0'))
    except ValueError:
        since = 0
    with _log_lock:
        items = [(s, t) for s, t in _log_buffer if s > since]
        latest = _log_seq
    return jsonify({
        'logs': [{'seq': s, 'text': t} for s, t in items],
        'latest': latest
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
        
        # Step3: 测试代码生成（优先按端 TestBatch 路由，无 batches 退回逐 scenario）
        batches = point_result.get('batches', [])
        generated_tests = []

        if batches:
            if not generate_all:
                batches = [b for b in batches if b.get('priority') == 'P0']
                logger.info(f"仅生成高优先级批次 ({len(batches)}批)")
            for batch in batches:
                try:
                    gen_result = gen_agent.execute_batch(batch)
                    with open(gen_result['file_path'], 'r', encoding='utf-8') as f:
                        generated_tests.append({
                            'path': gen_result['file_path'],
                            'code': f.read(),
                            'platform': gen_result.get('platform'),
                            'batch_index': gen_result.get('batch_index'),
                        })
                except Exception as e:
                    logger.warning(f"测试生成失败: {e}")
        else:
            # 旧链路（code/降级）：逐 scenario 调 process_query
            if not generate_all:
                scenarios = [s for s in scenarios if s.get('priority') == 'high']
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
        
        content = feishu_client.get_doc_content(doc_token, doc_type, doc_url=doc_url)
        content_len = len(content) if content else 0
        # 获取文档原标题（供前端拼接生成文档命名，避免标题泛化）
        try:
            doc_title = feishu_client.get_doc_title(doc_token, doc_type)
        except Exception as e:
            logger.warning(f"获取文档标题失败: {e}")
            doc_title = ''
        logger.info(f"成功获取文档内容: 长度={content_len}字符, 标题={doc_title}")

        return jsonify({
            'success': True,
            'content': clean_control_chars(content),
            'doc_type': doc_type,
            'doc_token': doc_token,
            'title': clean_control_chars(doc_title)
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
    app.run(host='0.0.0.0', port=args.port, debug=True, threaded=True, use_reloader=False)
