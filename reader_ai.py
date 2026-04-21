from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from reader_core import (
    DEFAULT_READER_AI_MODEL,
    DEFAULT_READER_AI_TOKEN,
    DEFAULT_READER_AI_URL,
    build_work_record,
    infer_categories,
    infer_tags,
    load_work_text_from_relpath,
    normalize_spaces,
    safe_json_loads,
    split_paragraphs,
)

ALLOWED_PRIMARY_CATEGORIES = [
    '虐文',
    '骨科文',
    '黄文',
    '甜宠',
    '豪门总裁',
    '青梅竹马',
    '先婚后爱',
    '替身追妻',
    '强制爱',
    '古言宫廷',
    '江湖武侠',
    '穿越重生',
    '校园青春',
    '悬疑灵异',
    '耽美百合',
    '都市言情',
]
DEFAULT_WHOLE_CHAR_LIMIT = 30_000
DEFAULT_SPREAD_CHUNK_COUNT = 5
DEFAULT_SPREAD_CHUNK_CHAR_LIMIT = 4_500
SUMMARY_MAX_CHARS = 260
INTRO_MAX_CHARS = 120
SOURCE_SYNOPSIS_MAX_CHARS = 280
NO_SPOILER_TAIL_GUARD_RATIO = 0.92
NO_SPOILER_REPLACEMENTS = {
    '从互怼到心动': '在互怼与心动边缘拉扯',
    '从冲突到心动': '在冲突与心动边缘拉扯',
    '从针锋相对到心意相通': '在针锋相对与情感试探中拉扯',
    '从欢喜冤家到暧昧升温': '在欢喜冤家式互动中暧昧拉扯',
    '到心意相通': '并不断试探彼此心意',
    '心意相通': '情感拉扯',
    '终成眷属': '情感走向',
    '最后': '后续',
    '最终': '后续',
    '结局': '收束',
    '真相是': '谜团牵动关系',
    '原来': '背后似有隐情',
}
NO_SPOILER_CLAUSE_WORDS = (
    '最后',
    '最终',
    '结局',
    '真相',
    '原来',
    '揭露',
    '揭晓',
    '早已',
    '其实',
    '真凶',
    '死亡',
    '死去',
    '身亡',
    '亡魂',
    '怀孕',
    '懷孕',
    '流产',
    '流產',
    '车祸',
    '車禍',
    '失忆',
    '失憶',
    '失去记忆',
    '失去記憶',
    '绑架',
    '綁架',
    '自杀',
    '自殺',
    '第三者',
    '身世',
    '身份揭晓',
    '大结局',
)
_MODELS_CACHE: dict[tuple[str, str], list[str]] = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Use a local OpenAI-compatible endpoint to score a work.')
    parser.add_argument('--file', required=True, help='Absolute or workspace-relative file path to the work')
    parser.add_argument('--title', default='', help='Work title')
    parser.add_argument('--author', default='', help='Author name')
    parser.add_argument('--relpath', default='', help='Repo-relative path for heuristics')
    parser.add_argument('--categories', default='[]', help='JSON list of initial categories')
    parser.add_argument('--tags', default='[]', help='JSON list of initial tags')
    parser.add_argument('--url', default=DEFAULT_READER_AI_URL, help='OpenAI-compatible base URL')
    parser.add_argument('--model', default=DEFAULT_READER_AI_MODEL, help='Preferred model name')
    parser.add_argument('--token', default=DEFAULT_READER_AI_TOKEN, help='Bearer token')
    parser.add_argument('--timeout', type=int, default=180, help='Network timeout in seconds')
    parser.add_argument('--mode', choices=('auto', 'whole', 'spread'), default='auto', help='Input strategy')
    parser.add_argument('--whole-char-limit', type=int, default=DEFAULT_WHOLE_CHAR_LIMIT, help='Whole-text strategy limit')
    parser.add_argument('--spread-chunk-count', type=int, default=DEFAULT_SPREAD_CHUNK_COUNT, help='Spread strategy chunk count')
    parser.add_argument('--spread-chunk-char-limit', type=int, default=DEFAULT_SPREAD_CHUNK_CHAR_LIMIT, help='Spread strategy chunk size')
    parser.add_argument('--sample-profile', choices=('focused', 'segmented', 'weighted'), default='focused', help='Spread sampling profile')
    return parser.parse_args()


def normalize_api_base(url: str) -> str:
    base = (url or '').strip().rstrip('/')
    if not base:
        raise RuntimeError('AI URL is empty.')
    if base.endswith('/chat/completions'):
        return base[:-17]
    if base.endswith('/models'):
        return base[:-7]
    if base.endswith('/v1'):
        return base
    return base + '/v1'


def api_url(base_url: str, path: str) -> str:
    return normalize_api_base(base_url) + path


def http_json(url: str, token: str, payload: dict[str, object] | None, timeout: int, method: str) -> dict[str, object]:
    headers = {'Authorization': f'Bearer {token}'}
    data = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f'AI HTTP {exc.code}: {body}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'AI request failed: {exc.reason}') from exc


def fetch_models(base_url: str, token: str, timeout: int) -> list[str]:
    cache_key = (normalize_api_base(base_url), token)
    cached = _MODELS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    data = http_json(api_url(base_url, '/models'), token, None, timeout, 'GET')
    models = data.get('data')
    if not isinstance(models, list):
        return []
    resolved = [str(item.get('id')).strip() for item in models if isinstance(item, dict) and str(item.get('id') or '').strip()]
    _MODELS_CACHE[cache_key] = resolved
    return resolved


def model_fingerprint(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', (name or '').lower())


def resolve_model_name(requested: str, available_models: list[str]) -> str:
    if not available_models:
        return requested
    if requested in available_models:
        return requested

    lowered = {item.lower(): item for item in available_models}
    if requested.lower() in lowered:
        return lowered[requested.lower()]

    requested_fp = model_fingerprint(requested)
    for item in available_models:
        if model_fingerprint(item) == requested_fp:
            return item
    for item in available_models:
        item_fp = model_fingerprint(item)
        if requested_fp and (requested_fp in item_fp or item_fp in requested_fp):
            return item
    if len(available_models) == 1:
        return available_models[0]
    raise RuntimeError(f"Model '{requested}' not found. Available models: {', '.join(available_models)}")


def load_work_text(file_path: Path, relpath: str) -> tuple[str, str]:
    if relpath:
        return load_work_text_from_relpath(relpath)
    record = build_work_record(file_path)
    return load_work_text_from_relpath(record['relpath'])


def window_to_paragraph(text: str, start: int, chunk_limit: int) -> str:
    safe_start = max(0, min(start, max(len(text) - 1, 0)))
    left = text.rfind('\n\n', max(0, safe_start - 300), safe_start)
    if left >= 0:
        safe_start = left + 2
    safe_end = min(len(text), safe_start + chunk_limit)
    right = text.find('\n\n', safe_end, min(len(text), safe_end + 300))
    if right >= 0:
        safe_end = right
    return text[safe_start:safe_end].strip()


def focused_sample_ratios(chunk_count: int) -> list[tuple[str, float]]:
    if chunk_count <= 1:
        return [('开篇设定', 0.02)]
    if chunk_count == 2:
        return [('开篇设定', 0.02), ('核心冲突', 0.50)]
    if chunk_count == 3:
        return [('开篇设定', 0.02), ('核心冲突', 0.52), ('高潮前段', 0.82)]
    if chunk_count == 4:
        return [('开篇设定', 0.02), ('关系启动', 0.28), ('核心冲突', 0.56), ('高潮前段', 0.84)]
    return [
        ('开篇设定', 0.02),
        ('关系启动', 0.24),
        ('核心冲突', 0.48),
        ('情绪拉扯', 0.64),
        ('高潮前段', 0.84),
    ][:chunk_count]


def weighted_sample_windows(chunk_count: int, chunk_char_limit: int) -> list[tuple[str, float, int]]:
    total_budget = max(1600, chunk_count * chunk_char_limit)
    head_limit = max(500, min(900, round(total_budget * 0.16)))
    tail_limit = max(500, min(900, round(total_budget * 0.16)))
    middle_budget = max(800, total_budget - head_limit - tail_limit)

    if chunk_count <= 2:
        return [('开篇钩子', 0.02, head_limit), ('核心冲突', 0.54, middle_budget)]
    if chunk_count == 3:
        return [
            ('开篇钩子', 0.02, head_limit),
            ('核心冲突', 0.54, middle_budget),
            ('高潮前段', 0.82, tail_limit),
        ]

    middle_count = chunk_count - 2
    labels = ['中段关系', '核心冲突', '情绪拉扯', '危机升级', '高潮铺垫']
    if middle_count == 2:
        ratios = [0.42, 0.62]
        raw_limits = [round(middle_budget * 0.45), middle_budget - round(middle_budget * 0.45)]
    else:
        ratios = [0.42] if middle_count == 1 else [0.36 + (0.34 * idx / max(1, middle_count - 1)) for idx in range(middle_count)]
        weights = [1.0 + idx * 0.12 for idx in range(middle_count)]
        weight_total = sum(weights)
        raw_limits = [max(300, round(middle_budget * weight / weight_total)) for weight in weights]
        if sum(raw_limits) > middle_budget:
            scale = middle_budget / sum(raw_limits)
            raw_limits = [max(200, round(limit * scale)) for limit in raw_limits]
        raw_limits[-1] += middle_budget - sum(raw_limits)
    middle_windows = []
    for idx in range(middle_count):
        ratio = ratios[idx]
        limit = max(200, raw_limits[idx])
        label = labels[idx] if idx < len(labels) else f'中段取样{idx + 1}'
        middle_windows.append((label, ratio, limit))

    return [('开篇钩子', 0.02, head_limit), *middle_windows, ('高潮前段', 0.82, tail_limit)]


def segmented_sample_windows(chunk_count: int, chunk_char_limit: int) -> list[tuple[str, float, int]]:
    if chunk_count <= 1:
        return [('开篇设定', 0.02, chunk_char_limit)]
    start_ratio = 0.02
    end_ratio = 0.84
    labels = ['开篇设定', '前段推进', '中段关系', '核心冲突', '情绪转折', '高潮前段']
    windows = []
    for idx in range(chunk_count):
        ratio = start_ratio + (end_ratio - start_ratio) * (idx / max(1, chunk_count - 1))
        label = labels[idx] if idx < len(labels) else f'分段取样{idx + 1}'
        windows.append((label, ratio, chunk_char_limit))
    return windows


def build_spread_excerpt(text: str, chunk_count: int, chunk_char_limit: int, sample_profile: str) -> tuple[str, int]:
    if len(text) <= chunk_char_limit:
        return text, len(text)

    segments: list[str] = []
    seen = set()
    default_tail_guard = max(0, round(len(text) * NO_SPOILER_TAIL_GUARD_RATIO) - chunk_char_limit)
    default_max_start = max(0, min(len(text) - chunk_char_limit, default_tail_guard))
    if sample_profile == 'weighted':
        samples = weighted_sample_windows(chunk_count, chunk_char_limit)
    elif sample_profile == 'segmented':
        samples = segmented_sample_windows(chunk_count, chunk_char_limit)
    else:
        samples = [(label, ratio, chunk_char_limit) for label, ratio in focused_sample_ratios(chunk_count)]

    for idx, (label, ratio, sample_limit) in enumerate(samples, 1):
        tail_guard = max(0, round(len(text) * NO_SPOILER_TAIL_GUARD_RATIO) - sample_limit)
        max_start = max(0, min(len(text) - sample_limit, tail_guard, default_max_start))
        start = min(round(len(text) * ratio), max_start)
        segment = window_to_paragraph(text, start, sample_limit)
        compact = normalize_spaces(segment)
        if not compact or compact in seen:
            continue
        seen.add(compact)
        segments.append(f'【片段 {idx}：{label}，约 {round(ratio * 100)}% 处，约 {sample_limit} 字】\n{segment}')

    joined = '\n\n'.join(segments)
    return joined, len(joined)


def build_analysis_payload(
    text: str,
    mode: str,
    whole_char_limit: int,
    spread_chunk_count: int,
    spread_chunk_char_limit: int,
    sample_profile: str,
) -> tuple[str, str, int]:
    strategy = mode
    if strategy == 'auto':
        strategy = 'whole' if len(text) <= whole_char_limit else 'spread'

    if strategy == 'whole':
        return 'whole', text, len(text)

    spread_text, source_char_count = build_spread_excerpt(text, spread_chunk_count, spread_chunk_char_limit, sample_profile)
    return f'spread:{sample_profile}', spread_text, source_char_count


def clean_source_synopsis(value: str) -> str:
    text = normalize_spaces(value)
    text = re.sub(r'^[：:【\[\]】\s]+', '', text)
    text = re.sub(r'^《[^》]{1,80}》(?:（[^）]{1,40}）)?(?:作者[：:]?[^，。；\s]{1,30})?\s*(?:TXT下载|TXT全集下载)?', ' ', text)
    text = re.sub(r'^[^，。！？；]{1,60}(?:作者[：:]?[^，。！？；\s]{1,30})\s*(?:TXT下载|TXT全集下载)?', ' ', text)
    text = re.sub(r'[【\[]?(?:书名|書名|作者|内容简介|內容簡介|作品简介|作品簡介|书籍简介|簡介|简介|文案)[】\]]?[：:]?', ' ', text)
    text = re.sub(r'[【\[]?[^】\]\n]{0,40}(?:内容简介|內容簡介|作品简介|作品簡介|书籍简介|簡介|简介|文案)[】\]]?', ' ', text)
    text = re.sub(r'(更多好书|TXT全集|完结TXT|www\.|http|本图书由).*', '', text, flags=re.IGNORECASE)
    text = sanitize_no_spoiler_text(text, SOURCE_SYNOPSIS_MAX_CHARS)
    if len(text) < 45:
        return ''
    if len(text) > SOURCE_SYNOPSIS_MAX_CHARS:
        text = sanitize_no_spoiler_text(text, SOURCE_SYNOPSIS_MAX_CHARS)
    noisy_patterns = (
        r'^作者有话',
        r'^(序|自序|前言|后记|後記|作者序)\b',
        r'^(番外|出版|编编|交稿|读者)',
        r'系列的最后一本',
    )
    if any(re.search(pattern, text[:80]) for pattern in noisy_patterns):
        return ''
    return text


def strip_front_matter_lines(text: str) -> list[str]:
    lines = []
    for raw_line in text.replace('\r', '\n').split('\n'):
        line = normalize_spaces(raw_line)
        if not line:
            continue
        if re.fullmatch(r'[《<【\\[]?.{1,60}[》>】\\]]?(TXT全集)?', line) and ('作者' not in line):
            if len(line) <= 40 and any(mark in line for mark in ('《', '<', 'TXT全集')):
                continue
        if re.match(r'^(作者|整理|录入|校对|书名|出版社)[：:]', line):
            continue
        if '更多好书' in line or 'www.' in line.lower() or 'http' in line.lower():
            continue
        lines.append(line)
    return lines


def extract_source_synopsis(text: str) -> dict[str, object]:
    head = text[:6000].replace('\r', '\n')
    label_match = re.search(
        r'(?im)^\s*[【\[]?[^】\]\n]{0,40}(?:内容简介|內容簡介|作品简介|作品簡介|书籍简介|簡介|简介|文案)[】\]]?[：:]?\s*\n?',
        head,
    )
    stop_pattern = r'(?im)(?:正文\s*)?(?:楔子|序章|序幕|第一[章回節节]|Chapter\s*1|第[一1][章回節节])'
    if label_match:
        rest = head[label_match.end():]
        stop = re.search(stop_pattern, rest)
        block = rest[: stop.start()] if stop else rest[:1800]
        cleaned = clean_source_synopsis(' '.join(strip_front_matter_lines(block)))
        if cleaned:
            return {'summary': cleaned, 'source': 'explicit_label'}

    first_heading = re.search(stop_pattern, head)
    if first_heading and first_heading.start() > 60:
        prefix = head[: first_heading.start()]
        if not re.search(r'(?im)^\s*(?:序|自序|前言|后记|後記|作者序|阿达日记)\s*$', prefix):
            cleaned = clean_source_synopsis(' '.join(strip_front_matter_lines(prefix)))
            if cleaned:
                return {'summary': cleaned, 'source': 'front_blurb'}
    return {'summary': '', 'source': ''}


def build_prompt(
    title: str,
    author: str,
    relpath: str,
    categories: list[str],
    tags: list[str],
    analysis_text: str,
    strategy: str,
    text_char_count: int,
    source_char_count: int,
    source_synopsis: str,
) -> list[dict[str, str]]:
    system = (
        '你是中文小说书单策展助手。请输出严格 JSON，不要 markdown，不要解释，不要思考过程。'
        'summary 和 intro 必须无剧透：不要暴露结局、最终配对、死亡、真凶、反转、身份揭晓或最终选择。'
        '只能概括设定、人物关系、核心冲突、情绪风格和阅读体验。scores 必须是 0 到 100 的整数。'
    )
    user = {
        'task': '根据作品内容生成书架卡片、推荐标签与评分。',
        'title': title,
        'author': author,
        'relpath': relpath,
        'existing_categories': categories,
        'existing_tags': tags,
        'allowed_primary_categories': ALLOWED_PRIMARY_CATEGORIES,
        'analysis_strategy': strategy,
        'text_char_count': text_char_count,
        'source_char_count': source_char_count,
        'source_synopsis': source_synopsis,
        'summary_instruction': '如果 source_synopsis 非空，不要生成 summary/intro；只输出 primary_category、tags、scores、reason。',
        'anti_spoiler_rules': [
            '不要引用或复述结尾信息',
            '不要写“最后/最终/原来/真相是/结局是”等剧透式表述',
            '不要用“从A到B”总结关系终点，例如不要写“从互怼到心动”“从针锋相对到心意相通”',
            '不要确认最终恋爱结果，只能写暧昧、拉扯、试探、关系升温边缘',
            '不要写怀孕、车祸、失忆、绑架、第三者、身世揭露等中后段具体情节',
            '如果片段包含重大转折，只用“关系出现转折”“冲突升级”等模糊描述',
            'summary 和 intro 面向未读者，必须像书店简介，不像剧情复盘',
        ],
        'required_schema': {
            'primary_category': '从 allowed_primary_categories 中选 1 个',
            'tags': ['3到8个中文标签'],
            'scores': {
                'overall': '0-100',
                'emotion': '0-100',
                'chemistry': '0-100',
                'spice': '0-100',
                'readability': '0-100',
            },
            'reason': '20-80字，解释推荐理由',
        },
        'content': analysis_text,
    }
    if not source_synopsis:
        user['required_schema'] = {
            'summary': '120-220字的无剧透详情页简介，写清设定、人物关系、核心冲突、情绪风格和适合读者，但不要剧情复盘',
            'intro': '55-100字的无剧透书架卡片介绍，偏推荐语，不能揭露结局或重大反转',
            **user['required_schema'],
        }
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)},
    ]


def parse_json_block(raw: str) -> dict[str, object]:
    text = raw.strip()
    if text.startswith('```'):
        text = text.strip('`')
        if '\n' in text:
            text = text.split('\n', 1)[1]
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def fallback_payload(title: str, author: str, relpath: str, categories: list[str], tags: list[str]) -> dict[str, object]:
    heuristic_categories = categories or infer_categories(title, author, relpath, '')
    heuristic_tags = tags or infer_tags(title, author, relpath, '', heuristic_categories)
    primary = heuristic_categories[0] if heuristic_categories else '都市言情'
    return {
        'summary': f'{title} 目前采用规则提要，AI 评分尚未完成，适合后续补跑本地模型细化推荐。',
        'intro': f'{author} 的《{title}》，主打 {primary} 方向，可继续补跑本地 AI 评分。',
        'primary_category': primary,
        'tags': heuristic_tags[:6],
        'scores': {
            'overall': 62,
            'emotion': 60,
            'chemistry': 58,
            'spice': 55,
            'readability': 66,
        },
        'reason': '本地模型暂不可用，先保留规则评分作为兜底。',
    }


def clamp_score(value: object, fallback: int) -> int:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return max(0, min(100, numeric))


def sanitize_no_spoiler_text(value: str, limit: int) -> str:
    raw_text = normalize_spaces(value)
    clauses = re.split(r'(?<=[，。！？；])', raw_text)
    kept_clauses = [clause for clause in clauses if not any(word in clause for word in NO_SPOILER_CLAUSE_WORDS)]
    text = normalize_spaces(''.join(kept_clauses) or raw_text)
    for source, replacement in NO_SPOILER_REPLACEMENTS.items():
        text = text.replace(source, replacement)
    clauses = re.split(r'(?<=[，。！？；])', text)
    kept_clauses = [clause for clause in clauses if not any(word in clause for word in NO_SPOILER_CLAUSE_WORDS)]
    text = normalize_spaces(''.join(kept_clauses) or text)
    text = re.sub(r'从([^，。；]{1,18})到([^，。；]{1,18})', r'在\1与\2之间拉扯', text)
    text = text.replace('边缘拉扯边缘拉扯', '边缘拉扯')
    text = text.replace('拉扯边缘拉扯', '拉扯')
    text = normalize_spaces(text)
    if len(text) <= limit:
        return text.rstrip('，、, ')
    clipped = text[:limit].rstrip('，、, ')
    strong_break = max(clipped.rfind(mark) for mark in '。！？；')
    if strong_break >= 40:
        return clipped[:strong_break + 1]
    soft_break = max(clipped.rfind(mark) for mark in '，、')
    if soft_break >= 40:
        return clipped[:soft_break].rstrip('，、, ')
    return clipped


def normalize_result(
    raw: dict[str, object],
    title: str,
    author: str,
    relpath: str,
    categories: list[str],
    tags: list[str],
    preset_summary: str = '',
) -> dict[str, object]:
    if not raw:
        raw = fallback_payload(title, author, relpath, categories, tags)
    primary_category = str(raw.get('primary_category') or (categories[0] if categories else '都市言情'))
    if primary_category not in ALLOWED_PRIMARY_CATEGORIES:
        primary_category = categories[0] if categories else '都市言情'
    raw_tags = raw.get('tags')
    merged_tags = [primary_category]
    if isinstance(raw_tags, list):
        merged_tags.extend(str(item).strip() for item in raw_tags if str(item).strip())
    merged_tags.extend(tags)
    unique_tags = []
    seen = set()
    for tag in merged_tags:
        if tag and tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)
    scores = raw.get('scores') if isinstance(raw.get('scores'), dict) else {}
    overall = clamp_score(scores.get('overall'), 60)
    emotion = clamp_score(scores.get('emotion'), overall)
    chemistry = clamp_score(scores.get('chemistry'), overall)
    spice = clamp_score(scores.get('spice'), overall)
    readability = clamp_score(scores.get('readability'), overall)
    summary = sanitize_no_spoiler_text(preset_summary or str(raw.get('summary') or ''), SUMMARY_MAX_CHARS)
    intro_source = str(raw.get('intro') or '') or str(raw.get('summary') or '') or preset_summary
    intro = sanitize_no_spoiler_text(intro_source, INTRO_MAX_CHARS)
    if not summary:
        summary = fallback_payload(title, author, relpath, categories, tags)['summary']
    if not intro:
        intro = fallback_payload(title, author, relpath, categories, tags)['intro']
    return {
        'summary': summary,
        'intro': intro,
        'primary_category': primary_category,
        'tags': unique_tags[:8],
        'scores': {
            'overall': overall,
            'emotion': emotion,
            'chemistry': chemistry,
            'spice': spice,
            'readability': readability,
        },
        'reason': sanitize_no_spoiler_text(str(raw.get('reason') or '规则和 AI 联合评分完成。'), 120),
    }


def call_model(
    base_url: str,
    model: str,
    token: str,
    messages: list[dict[str, str]],
    timeout: int,
    max_tokens: int,
) -> tuple[dict[str, object], dict[str, object]]:
    if not token:
        raise RuntimeError('AI token is required by the configured endpoint.')
    payload = {
        'model': model,
        'temperature': 0.1,
        'max_tokens': max_tokens,
        'response_format': {'type': 'json_object'},
        'chat_template_kwargs': {'enable_thinking': False},
        'messages': messages,
    }
    data = http_json(api_url(base_url, '/chat/completions'), token, payload, timeout, 'POST')
    message = ((data.get('choices') or [{}])[0]).get('message') or {}
    raw_content = str(message.get('content') or '')
    return parse_json_block(raw_content), data


def score_work(
    file_path: Path,
    title: str,
    author: str,
    relpath: str,
    categories: list[str],
    tags: list[str],
    url: str,
    model: str,
    token: str,
    timeout: int,
    mode: str,
    whole_char_limit: int,
    spread_chunk_count: int,
    spread_chunk_char_limit: int,
    sample_profile: str = 'focused',
) -> dict[str, object]:
    available_models = fetch_models(url, token, timeout)
    resolved_model = resolve_model_name(model, available_models)
    text, _encoding = load_work_text(file_path, relpath)
    source_synopsis_payload = extract_source_synopsis(text)
    source_synopsis = str(source_synopsis_payload.get('summary') or '')
    strategy, analysis_text, source_char_count = build_analysis_payload(
        text,
        mode,
        whole_char_limit,
        spread_chunk_count,
        spread_chunk_char_limit,
        sample_profile,
    )
    messages = build_prompt(
        title,
        author,
        relpath,
        categories,
        tags,
        analysis_text,
        strategy,
        len(text),
        source_char_count,
        source_synopsis,
    )
    start = time.time()
    raw_result, raw_response = call_model(url, resolved_model, token, messages, timeout, 430 if source_synopsis else 780)
    elapsed = round(time.time() - start, 2)
    result = normalize_result(raw_result, title, author, relpath, categories, tags, source_synopsis)
    return {
        'ok': True,
        'result': result,
        'meta': {
            'resolved_model': resolved_model,
            'available_models': available_models,
            'strategy': strategy,
            'sample_profile': sample_profile,
            'text_char_count': len(text),
            'source_char_count': source_char_count,
            'has_source_synopsis': bool(source_synopsis),
            'summary_source': 'source_synopsis' if source_synopsis else 'ai_generated',
            'source_synopsis_source': source_synopsis_payload.get('source') or '',
            'source_synopsis_char_count': len(source_synopsis),
            'elapsed_sec': elapsed,
            'has_reasoning_content': bool(((raw_response.get('choices') or [{}])[0]).get('message', {}).get('reasoning_content')),
        },
    }


def main() -> int:
    args = parse_args()
    file_path = Path(args.file)
    if not file_path.is_absolute():
        file_path = Path.cwd() / file_path
    if not file_path.exists():
        print(json.dumps({'ok': False, 'error': f'file not found: {file_path}'}, ensure_ascii=False))
        return 1

    relpath = args.relpath or file_path.relative_to(Path.cwd()).as_posix()
    categories = safe_json_loads(args.categories, [])
    tags = safe_json_loads(args.tags, [])
    title = args.title or file_path.stem
    author = args.author or ''

    try:
        payload = score_work(
            file_path=file_path,
            title=title,
            author=author,
            relpath=relpath,
            categories=categories,
            tags=tags,
            url=args.url,
            model=args.model,
            token=args.token,
            timeout=args.timeout,
            mode=args.mode,
            whole_char_limit=args.whole_char_limit,
            spread_chunk_count=args.spread_chunk_count,
            spread_chunk_char_limit=args.spread_chunk_char_limit,
            sample_profile=args.sample_profile,
        )
    except Exception as exc:
        fallback = normalize_result(fallback_payload(title, author, relpath, categories, tags), title, author, relpath, categories, tags)
        print(json.dumps({'ok': False, 'error': str(exc), 'result': fallback}, ensure_ascii=False))
        return 0

    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
