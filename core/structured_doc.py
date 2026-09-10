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


def _repair_struct_json(s: str) -> str:
    """修复 LLM 输出 structured JSON 的常见脏格式

    修复范围（只做确定安全的，不误伤值内容）：
    1. trailing comma：, ] 或 , }
    2. JSON 字符串内部的字面控制字符（换行 / 回车 / tab）→ 转义为 \\n / \\t
    3. 字符串值内部的裸双引号 → 替换成中文「」（既不破坏 JSON，又保留引号语义）

    判定逻辑（逐字符扫描，追踪 JSON string 边界）：
    - 不在 string 内 → 原样输出，遇到 " 进入 string
    - 在 string 内：
      - \\ 开头的转义序列 → 原样输出
      - 字面 \\n \\r \\t → 转义为 \\n \\t（JSON 合法的字符串内容）
      - " → 看后续是否为 `:` / `,}` / `]`，是则为 string 结束；否则为裸引号 → 「」
      - 其他 → 原样输出
    """
    if not s:
        return s
    # 1. trailing comma
    s = re.sub(r',\s*([}\]])', r'\1', s)

    # 2. 裸双引号修复 + 字符串内控制字符转义（单遍扫描）
    out = []
    in_string = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if not in_string:
            out.append(c)
            if c == '"':
                in_string = True
        else:
            if c == '\\':
                # 转义序列原样保留（下一字符也保留）
                out.append(c)
                i += 1
                if i < n:
                    out.append(s[i])
            elif c in ('\n', '\r', '\t'):
                # ⭐ JSON string 内的字面控制字符 → 转义
                if c == '\n':
                    out.append('\\n')
                elif c == '\r':
                    out.append('\\r')
                elif c == '\t':
                    out.append('\\t')
            elif c == '"':
                # 跳过空白后看首个非空白字符
                j = i + 1
                while j < n and s[j] in ' \t\n\r':
                    j += 1
                next_non_ws = s[j] if j < n else ''
                if next_non_ws == ':':
                    # JSON key 正常结束
                    out.append(c)
                    in_string = False
                elif next_non_ws in ',}]':
                    # value 正常结束
                    out.append(c)
                    in_string = False
                else:
                    # 值内部忘了转义的裸双引号 → 替换成「/」
                    last = '「' if (not out or out[-1] != '」') else '」'
                    out.append(last)
            elif ord(c) < 32:
                # 其他罕见控制字符（\x00-\x1f）→ 直接跳过
                pass
            else:
                out.append(c)
        i += 1
    return ''.join(out)


def _save_debug_raw(raw_text: str, error) -> None:
    """JSON 解析失败时保存原始 LLM 输出到 debug 文件，便于排查"""
    import time as _time, os
    ts = _time.strftime('%H%M%S')
    try:
        # 优先存到 generated_testpoints/ 目录
        from agents.test_point_generator.agent import _OUTPUT_DIR
        d = _OUTPUT_DIR
    except Exception:
        d = os.getcwd()
    path = os.path.join(d, f'_debug_struct_json_{ts}.txt')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'# 错误: {error}\n')
            f.write(f'# 长度: {len(raw_text)} 字符\n')
            f.write(f'# 时间: {_time.strftime("%Y-%m-%d %H:%M:%S")}\n')
            f.write('--- LLM 原始输出 ---\n')
            f.write(raw_text)
        logger.warning(f"💾 已保存解析失败的原始输出 → {path}")
    except Exception:
        pass


def parse_struct_json(text: str) -> list:
    """解析LLM输出的业务JSON为节点列表；解析/校验失败返回None
    
    容错策略（防御脏JSON）：
    1. 去代码块标记后json.loads → 失败则 repair + 截取 [..] 重试
    2. 过滤未知类型节点（告警不中断）
    3. table行列不对齐时自动补齐/截断（告警不中断）
    """
    t = _strip_fences(text)
    data = None
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        # 容错1：repair（trailing comma + 控制字符转义 + 裸双引号）
        repaired = _repair_struct_json(t)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            # 容错2：截取JSON数组区间重试
            start, end = repaired.find('['), repaired.rfind(']')
            if start >= 0 and end > start:
                try:
                    data = json.loads(repaired[start:end + 1])
                except json.JSONDecodeError as e:
                    # 保存完整原始输出用于 debug
                    _save_debug_raw(t, e)
                    logger.error(f"业务JSON解析失败(截取后): {e}")
                    return None
            else:
                _save_debug_raw(t, '无数组结构')
                logger.error(f"业务JSON格式异常(无数组结构)")
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
