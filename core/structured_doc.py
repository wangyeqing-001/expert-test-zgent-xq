"""结构化文档翻译层 - LLM业务结构化JSON ↔ Markdown

设计目标：彻底消除Markdown解析风险，业务内容与飞书API解耦。
- LLM按prompts.md约定输出顶层JSON数组（7种业务对象，不感知飞书API细节）
- parse_struct_json() 解析+容错+校验（脏JSON防御），失败返回None由上层降级
- struct_to_markdown() 渲染标准Markdown（本地保存/下游Agent/Web展示）
- 飞书侧由 FeishuClient.create_doc_from_struct() 消费同一份JSON，零Markdown解析
"""
import json
import re
import logging

logger = logging.getLogger(__name__)

# 允许的业务节点类型（code: 保留换行/空格的纯文本块，承载对齐表格）
_VALID_TYPES = {'h1', 'h2', 'h3', 'paragraph', 'bullet_list', 'ordered_list', 'table', 'code'}


def is_struct_json(text: str) -> bool:
    """粗判：LLM输出是否为JSON数组（去除代码块标记后以[开头）"""
    return _strip_fences(text).startswith('[')


def _strip_fences(text: str) -> str:
    """去除LLM可能包裹的```json代码块标记"""
    t = text.strip()
    t = re.sub(r'^```\w*\n?', '', t)
    t = re.sub(r'\n?```$', '', t)
    return t.strip()


def parse_struct_json(text: str) -> list:
    """解析LLM输出的业务JSON为节点列表；解析/校验失败返回None
    
    容错策略（防御脏JSON）：
    1. 去代码块标记后json.loads；失败则截取首个[到末尾]重试
    2. 过滤未知类型节点（告警不中断）
    3. table行列不对齐时自动补齐/截断（告警不中断）
    """
    t = _strip_fences(text)
    data = None
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        # 容错：截取JSON数组区间重试（LLM前后可能带解释文字）
        start, end = t.find('['), t.rfind(']')
        if start >= 0 and end > start:
            try:
                data = json.loads(t[start:end + 1])
            except json.JSONDecodeError as e:
                logger.error(f"业务JSON解析失败: {e}, raw={t[:200]}")
                return None
        else:
            logger.error(f"业务JSON格式异常(无数组结构): {t[:200]}")
            return None

    if not isinstance(data, list) or not data:
        logger.error(f"业务JSON顶层必须是非空数组, got={type(data).__name__}")
        return None

    nodes = []
    for item in data:
        if not isinstance(item, dict):
            continue
        ntype = item.get('type')
        if ntype not in _VALID_TYPES:
            logger.warning(f"未知节点类型 {ntype}, 跳过")
            continue
        node = _normalize_node(item)
        if node:
            nodes.append(node)

    return nodes or None


def _normalize_node(item: dict):
    """节点标准化：文本字段去换行；table行列对齐修复"""
    ntype = item['type']

    if ntype in ('h1', 'h2', 'h3', 'paragraph'):
        text = _flat(str(item.get('text', '')))
        return {'type': ntype, 'text': text} if text else None

    if ntype == 'code':
        # 保留换行与空格（对齐表格依赖），仅去首尾空白
        text = str(item.get('text', '')).strip('\n').rstrip()
        return {'type': 'code', 'text': text} if text.strip() else None

    if ntype in ('bullet_list', 'ordered_list'):
        items = [_flat(str(x)) for x in item.get('items', []) if str(x).strip()]
        return {'type': ntype, 'items': items} if items else None

    if ntype == 'table':
        headers = [_flat(str(x)) for x in item.get('headers', [])]
        if not headers:
            logger.warning("table缺少headers, 跳过")
            return None
        h_len = len(headers)
        rows = []
        for r in item.get('rows', []):
            cells = [_flat(str(x)) for x in r]
            if len(cells) != h_len:
                logger.warning(f"table行列不匹配(headers={h_len}, row={len(cells)}), 自动补齐/截断")
                cells = cells[:h_len] + [''] * (h_len - len(cells)) if len(cells) < h_len else cells[:h_len]
            rows.append(cells)
        return {'type': 'table', 'headers': headers, 'rows': rows}

    return None


def _flat(s: str) -> str:
    """单元格/文本内部禁止换行，统一替换为空格"""
    return re.sub(r'[\r\n]+', ' ', s).strip()


def struct_to_markdown(nodes: list) -> str:
    """业务JSON节点渲染为Markdown（本地.md文件/下游Agent/Web UI展示用）"""
    lines = []
    for n in nodes:
        t = n['type']
        if t == 'h1':
            lines.append(f"# {n['text']}")
            lines.append('')
        elif t == 'h2':
            lines.append(f"## {n['text']}")
            lines.append('')
        elif t == 'h3':
            lines.append(f"### {n['text']}")
            lines.append('')
        elif t == 'paragraph':
            lines.append(n['text'])
            lines.append('')
        elif t == 'bullet_list':
            lines.extend(f"- {x}" for x in n['items'])
        elif t == 'ordered_list':
            lines.extend(f"{i}. {x}" for i, x in enumerate(n['items'], 1))
        elif t == 'table':
            headers, rows = n['headers'], n['rows']
            lines.append('| ' + ' | '.join(headers) + ' |')
            lines.append('| ' + ' | '.join([':---'] * len(headers)) + ' |')
            lines.extend('| ' + ' | '.join(str(c) if c is not None else '' for c in r) + ' |' for r in rows)
            lines.append('')
        elif t == 'code':
            lines.append('```')
            lines.append(n['text'])
            lines.append('```')
            lines.append('')
    return '\n'.join(lines).strip() + '\n'
