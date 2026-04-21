from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import hmac
import zipfile
from datetime import UTC, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WRITER_DIR = BASE_DIR / 'writer'
SUPPORTED_EXTENSIONS = {'.txt', '.epub'}
SKIP_FILE_STEMS = {'content', 'contents', 'index', 'catalog', 'fail', '网址'}
MIN_WORK_FILE_SIZE = 256
DEFAULT_READER_PASSWORD = os.getenv('READER_APP_PASSWORD', '1996/12/25')
DEFAULT_READER_AI_URL = os.getenv('READER_AI_URL', 'http://127.0.0.1:8000/v1')
DEFAULT_READER_AI_MODEL = os.getenv('READER_AI_MODEL', 'Qwen3.6-35B-A3B-4bit')
DEFAULT_READER_AI_TOKEN = os.getenv('READER_AI_TOKEN', '')

READER_CATEGORY_RULES = {
    '虐文': ['虐', '泪恋', '绯恋', '仇恋', '囚恋', '绝情', '背叛', '误会', '前妻', '替身', '葬心', '火葬场'],
    '骨科文': ['骨科', '兄妹', '姐弟', '兄弟', '哥哥情人', '弟弟情郎', '妹妹甜心', '我不是你的妹妹'],
    '黄文': ['欲火', '床伴', '床戏', '情欲', '偷欢', '情妇', '处女', '春药', '肉食女', '色诱', '艳情', '欲望'],
    '甜宠': ['甜心', '蜜糖', '宠妻', '宝贝', '娇妻', '甜蜜', '蜜桃', '糖果'],
    '豪门总裁': ['总裁', '少东', '总经理', '豪门', '大亨', '总监', '富豪', '贵公子'],
    '青梅竹马': ['青梅竹马', '家教情人', '隔壁', '竹马', '发小'],
    '先婚后爱': ['新婚', '先婚后爱', '婚后', '闪婚', '代嫁', '逼婚', '契约婚姻'],
    '替身追妻': ['替身', '前妻', '追妻', '求和', '旧情', '回头', '复婚'],
    '强制爱': ['强夺', '强娶', '囚', '掠夺', '驭奴', '驭夫', '撒旦', '恶魔'],
    '古言宫廷': ['王爷', '皇上', '公主', '王妃', '将军', '格格', '后宫', '王朝', '侯爷', '贝勒'],
    '江湖武侠': ['江湖', '侠', '盟主', '堡主', '剑', '门主', '庄主', '少主'],
    '穿越重生': ['穿越', '重生', '前世', '今生', '异世', '时空', '千年'],
    '校园青春': ['校园', '同学', '学长', '学妹', '老师', '初恋', '中学', '高中部'],
    '悬疑灵异': ['灵异', '鬼', '诡', '惊魂', '亡灵', '异瞳', '死神', '地狱', '侦探', '怪谈'],
    '耽美百合': ['耽美', 'BL', 'GL', '同人', '美人攻', '女王受'],
}

READER_TAG_RULES = {
    '禁忌关系': ['禁忌', '骨科', '不伦', '兄妹', '姐弟', '哥哥情人', '弟弟情郎'],
    '追妻火葬场': ['前妻', '求和', '离婚', '追妻', '复婚', '回头'],
    '契约婚姻': ['契约', '签字', '协议', '婚约', '代嫁'],
    '替身文学': ['替身', '影子', '冒牌', '假扮'],
    '青梅竹马': ['青梅竹马', '竹马', '隔壁', '发小'],
    '豪门恩怨': ['豪门', '总裁', '大亨', '千金', '财阀', '少东'],
    '黑化复仇': ['复仇', '报复', '黑化', '算计', '夺爱'],
    '年上': ['叔叔', '伯爵', '总裁', '大叔'],
    '年下': ['弟弟', '学弟', '少主', '小狼狗'],
    '先虐后甜': ['虐', '误会', '和好', '回头', '火葬场'],
    '双向拉扯': ['试探', '纠缠', '暧昧', '心动'],
    '都市言情': ['都市', '总裁', '秘书', '上司', '办公室'],
    '宫廷权谋': ['皇上', '王爷', '公主', '侯爷', '宫'],
    '江湖武侠': ['剑', '侠', '江湖', '盟主', '堡主'],
    '悬疑灵异': ['灵异', '鬼', '异瞳', '怪谈', '暗夜', '死神'],
}

READER_TAG_VOCABULARY = [
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
    '古代言情',
    '江湖武侠',
    '穿越重生',
    '校园青春',
    '悬疑灵异',
    '耽美百合',
    '都市言情',
    '禁忌关系',
    '追妻火葬场',
    '契约婚姻',
    '替身文学',
    '豪门恩怨',
    '黑化复仇',
    '年上',
    '年下',
    '年龄差',
    '先虐后甜',
    '双向拉扯',
    '宫廷权谋',
    '欢喜冤家',
    '破镜重圆',
    '久别重逢',
    '暗恋成真',
    '双向暗恋',
    '日久生情',
    '一见钟情',
    '救赎治愈',
    '相爱相杀',
    '误会纠葛',
    '身份悬殊',
    '身份错位',
    '女扮男装',
    '失忆梗',
    '身世之谜',
    '带球跑',
    '单亲家庭',
    '复仇虐恋',
    '权谋斗争',
    '宫斗宅斗',
    '职场恋情',
    '娱乐圈',
    '医生律师',
    '异国恋',
    '婚恋家庭',
    '同居日常',
    '轻松搞笑',
    '温馨治愈',
    '高干商战',
    '黑帮情仇',
    '奇幻玄幻',
    '灵异悬疑',
    '推理破案',
    '民国年代',
    '乡村田园',
    '人外奇缘',
    '师生恋',
    '腹黑男主',
    '强强',
]

READER_TAG_ALIASES = {
    '黃文': '黄文',
    '肉文': '黄文',
    'H文': '黄文',
    '高H': '黄文',
    '情色': '黄文',
    '骨科': '骨科文',
    '兄妹恋': '骨科文',
    '兄妹戀': '骨科文',
    '虐恋': '虐文',
    '虐恋情深': '虐文',
    '虐心': '虐文',
    '虐身虐心': '虐文',
    '都市情感': '都市言情',
    '都市情缘': '都市言情',
    '现代言情': '都市言情',
    '豪门世家': '豪门恩怨',
    '豪门': '豪门恩怨',
    '强取豪夺': '强制爱',
    '强制恋爱': '强制爱',
    '禁忌之恋': '禁忌关系',
    '禁忌恋': '禁忌关系',
    '暧昧拉扯': '双向拉扯',
    '情感拉扯': '双向拉扯',
    '情感博弈': '双向拉扯',
    '甜虐交织': '先虐后甜',
    '追妻': '追妻火葬场',
    '火葬场': '追妻火葬场',
    '契约关系': '契约婚姻',
    '契约恋爱': '契约婚姻',
    '替身梗': '替身文学',
    '替身文': '替身文学',
    '复仇': '黑化复仇',
    '复仇爱情': '黑化复仇',
    '权谋': '权谋斗争',
    '宫廷权谋': '宫廷权谋',
    '古言': '古代言情',
    '古风': '古代言情',
    '古代言情': '古代言情',
    '悬疑': '悬疑灵异',
    '悬疑推理': '推理破案',
    '推理': '推理破案',
    '灵异': '灵异悬疑',
    '奇幻': '奇幻玄幻',
    '玄幻': '奇幻玄幻',
    '职场': '职场恋情',
    '职场日常': '职场恋情',
    '职场风云': '职场恋情',
    '职场精英': '职场恋情',
    '医生': '医生律师',
    '律师': '医生律师',
    '医生文': '医生律师',
    '律师文': '医生律师',
    '轻松幽默': '轻松搞笑',
    '轻松': '轻松搞笑',
    '搞笑': '轻松搞笑',
    '治愈': '温馨治愈',
    '温馨': '温馨治愈',
    '温馨治愈': '温馨治愈',
    '身份反差': '身份错位',
    '身份差距': '身份悬殊',
    '身份悬殊': '身份悬殊',
    '身世': '身世之谜',
    '身世之谜': '身世之谜',
    '失忆': '失忆梗',
    '失忆梗': '失忆梗',
    '误会': '误会纠葛',
    '误会重重': '误会纠葛',
    '误会梗': '误会纠葛',
    '单亲妈妈': '单亲家庭',
    '单亲爸爸': '单亲家庭',
    '单亲': '单亲家庭',
    '婚后恋爱': '先婚后爱',
    '闪婚': '先婚后爱',
    '先婚后恋': '先婚后爱',
    '欢喜冤家': '欢喜冤家',
    '相爱相杀': '相爱相杀',
    '破镜重圆': '破镜重圆',
    '久别重逢': '久别重逢',
    '双向奔赴': '救赎治愈',
    '救赎': '救赎治愈',
    '高干': '高干商战',
    '商战': '高干商战',
    '黑帮': '黑帮情仇',
    '黑道': '黑帮情仇',
}

CATEGORY_TO_TAGS = {
    '虐文': ['虐文'],
    '骨科文': ['骨科文', '禁忌关系'],
    '黄文': ['黄文'],
    '甜宠': ['甜宠'],
    '豪门总裁': ['豪门总裁', '豪门恩怨'],
    '青梅竹马': ['青梅竹马'],
    '先婚后爱': ['先婚后爱'],
    '替身追妻': ['替身追妻', '替身文学', '追妻火葬场'],
    '强制爱': ['强制爱'],
    '古言宫廷': ['古言宫廷', '古代言情'],
    '江湖武侠': ['江湖武侠'],
    '穿越重生': ['穿越重生'],
    '校园青春': ['校园青春'],
    '悬疑灵异': ['悬疑灵异'],
    '耽美百合': ['耽美百合'],
    '都市言情': ['都市言情'],
}

CATEGORY_TAG_SET = set(READER_CATEGORY_RULES) | {'都市言情'}
READER_TAG_SET = set(READER_TAG_VOCABULARY)

CATEGORY_PRIORITY = [
    '虐文',
    '骨科文',
    '黄文',
    '强制爱',
    '替身追妻',
    '豪门总裁',
    '先婚后爱',
    '甜宠',
    '古言宫廷',
    '江湖武侠',
    '穿越重生',
    '校园青春',
    '悬疑灵异',
    '耽美百合',
]

CATEGORY_MIN_SCORE = {
    '骨科文': 2,
    '黄文': 1,
    '豪门总裁': 1,
    '先婚后爱': 1,
    '替身追妻': 1,
    '古言宫廷': 1,
    '江湖武侠': 1,
    '穿越重生': 1,
    '校园青春': 1,
    '悬疑灵异': 1,
}

NOISE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r'^(本书来自|更多txt好书|附：|您下载的文件来自|转载信息|言情兔|豆豆小说阅读网|心栖亭|派派txt|txthj|yanqingtu|txtgogo|dddbbb)',
        r'.*小说论坛.*',
        r'.*kanfou\.net.*',
        r'.*爱书楼.*',
        r'.*收集整理.*',
        r'^https?://',
        r'.*https?://.*',
        r'^\(www\.[^)]+\)$',
        r'^TXT内容由',
        r'^本文由.*提供下载',
        r'^本作品来自互联网',
        r'^内容版权归作者所有',
        r'^手机阅读器',
        r'^返回.*$',
    ]
]

INTRO_BLOCK_PATTERNS = [
    re.compile(r'(?:内容简介|简介|文案|作品简介)[：:\s]*(.+?)(?:\n\s*\n|楔子|序章|第一章|第1章)', re.S),
    re.compile(r'男主角[:：].+?内容简介[:：]?(.*?)(?:\n\s*\n|楔子|序章|第一章|第1章)', re.S),
]

CHAPTER_PATTERN = re.compile(r'^(?:楔子|序章|尾声|番外|后记|第[0-9一二三四五六七八九十百千]+[章节回部篇卷])', re.M)
HTML_TAG_PATTERN = re.compile(r'<[^>]+>')
INTRO_MARKER_PATTERN = re.compile(r'^(?:【)?(?:书籍简介|内容介绍|内容简介|作品简介|简介|文案|内容提要|故事简介)(?:】)?[：:\s]*(.*)$')
METADATA_LINE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r'^(?:【)?(?:书名|作者|出版社|小说系列|书系|时代背景|故事地点|情节分类|内容简介|书籍简介|内容介绍|文案|男主角|女主角|男\s*主\s*角|女\s*主\s*角|配角|主角|发布时间|更新日期)(?:】)?[：:\s].*$',
        r'^(?:作者|出版社|小说系列|书系|时代背景|故事地点|情节分类|发布时间|更新日期)$',
        r'^(?:序|楔子|前言|后记|尾声|番外)$',
        r'^[!！?？。．·•【】《》〈〉<>()（）\\[\\]—_=~\\-]{1,8}$',
    ]
]
AUTHOR_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r'(?:^|[\s【《])作者[】】]?[：:\s]*([A-Za-z\u4e00-\u9fff·．\.]{2,20})',
        r'《[^》]+》\s*作者[】】]?[：:\s]*([A-Za-z\u4e00-\u9fff·．\.]{2,20})',
    ]
]
GENERIC_AUTHOR_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r'系列书籍',
        r'待更新',
        r'^作者$',
        r'^未知作者$',
    ]
]


def text_quality_score(text: str) -> int:
    cjk = len(re.findall(r'[\u4e00-\u9fff]', text))
    punctuation = len(re.findall(r'[，。！？；：「」『』（）《》、：]', text))
    control = len(re.findall(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text))
    return cjk * 4 + punctuation * 2 - control * 10


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def hash_password(password: str) -> str:
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), b'web_app_hub_password', 200_000)
    return f'pbkdf2_sha256${digest.hex()}'


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith('pbkdf2_sha256$'):
        return hmac.compare_digest(hash_password(password), password_hash)
    return hmac.compare_digest(sha256_text(password), password_hash)


def safe_json_loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def decode_text(raw: bytes) -> tuple[str, str]:
    for encoding in ('utf-8-sig', 'gb18030', 'big5', 'utf-16'):
        for trim in range(0, 5):
            snippet = raw[:-trim] if trim else raw
            if not snippet:
                continue
            try:
                return snippet.decode(encoding), encoding if trim == 0 else f'{encoding}~'
            except UnicodeDecodeError:
                continue

    candidates: list[tuple[int, str, str]] = []
    for encoding in ('utf-8-sig', 'gb18030', 'big5', 'utf-16'):
        try:
            decoded = raw.decode(encoding, errors='ignore')
            candidates.append((text_quality_score(decoded), decoded, f'{encoding}?'))
        except UnicodeDecodeError:
            continue
    if candidates:
        _score, text, encoding = max(candidates, key=lambda item: item[0])
        return text, encoding
    return raw.decode('utf-8', errors='ignore'), 'utf-8?'


def normalize_spaces(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def count_cjk(text: str) -> int:
    return len(re.findall(r'[\u4e00-\u9fff]', text or ''))


def relpath_for(path: Path) -> str:
    resolved = path.resolve()
    writer_root = WRITER_DIR.resolve()
    try:
        return resolved.relative_to(writer_root).as_posix()
    except ValueError:
        return resolved.relative_to(BASE_DIR).as_posix()


def should_skip_path(path: Path) -> bool:
    stem = path.stem.strip().lower()
    if stem in SKIP_FILE_STEMS:
        return True
    if path.stem.startswith('说明'):
        return True
    if path.stat().st_size < MIN_WORK_FILE_SIZE:
        return True
    return False


def infer_author_and_title(path: Path) -> tuple[str, str]:
    try:
        parts = path.relative_to(WRITER_DIR).parts
    except ValueError:
        parts = path.parts
    author = parts[0] if parts else '未知作者'
    stem = path.stem.strip()
    title = stem
    if title.startswith(author):
        title = title[len(author):].strip(' ._-·')
    title = re.sub(r'^\d+[.、_\- ]+', '', title).strip()
    title = re.sub(r'\s+', ' ', title).strip()
    return author or '未知作者', title or stem or path.name


def is_generic_author(author: str) -> bool:
    compact = normalize_spaces(author)
    if not compact:
        return True
    return any(pattern.search(compact) for pattern in GENERIC_AUTHOR_PATTERNS)


def clean_author_name(author: str) -> str:
    candidate = normalize_spaces(author).strip('【】[]()（）《》<>:：,，.。!！?？')
    candidate = re.sub(r'(?:著|整理|编著|TXT下载|txt下载|全文阅读)$', '', candidate, flags=re.I).strip()
    if not candidate:
        return ''
    if candidate in {'暂缺', '暂无', '待补', '未知', '作者'}:
        return ''
    if len(candidate) > 20:
        return ''
    if count_cjk(candidate) < 2 and not re.fullmatch(r'[A-Za-z][A-Za-z .]{1,18}', candidate):
        return ''
    return candidate


def extract_author_from_text(text: str) -> str:
    head = '\n'.join(sanitize_text(text).splitlines()[:40])
    for pattern in AUTHOR_PATTERNS:
        match = pattern.search(head)
        if not match:
            continue
        author = clean_author_name(match.group(1))
        if author:
            return author
    return ''


def extract_author_from_stem(stem: str) -> str:
    normalized = normalize_spaces(stem)
    if not normalized or ' ' not in normalized:
        match = re.search(r'[）)]\s*([A-Za-z\u4e00-\u9fff·．\.]{2,12})(?:\s*(?:暂缺|暂无).*)?$', normalized)
        return clean_author_name(match.group(1)) if match else ''
    match = re.search(r'[）)]\s*([A-Za-z\u4e00-\u9fff·．\.]{2,12})(?:\s*(?:暂缺|暂无).*)?$', normalized)
    if match:
        author = clean_author_name(match.group(1))
        if author:
            return author
    parts = normalized.split()
    tail = clean_author_name(parts[-1])
    if tail and len(tail) <= 6:
        return tail
    if len(parts) == 2:
        head = clean_author_name(parts[0])
        if head and len(head) <= 6:
            return head
    return ''


def normalize_lines(text: str) -> list[str]:
    return [normalize_spaces(line) for line in sanitize_text(text).split('\n') if normalize_spaces(line)]


def is_metadata_line(line: str, title: str = '') -> bool:
    candidate = normalize_spaces(line)
    if not candidate:
        return True
    if title and candidate in {title, f'《{title}》'}:
        return True
    if CHAPTER_PATTERN.match(candidate):
        return True
    return any(pattern.match(candidate) for pattern in METADATA_LINE_PATTERNS)


def clean_intro_candidate(text: str, title: str = '') -> str:
    candidate = normalize_spaces(text)
    if not candidate:
        return ''
    candidate = INTRO_MARKER_PATTERN.sub(r'\1', candidate)
    candidate = re.sub(r'^(?:【[^】]{0,20}】|《[^》]{0,40}》)\s*', '', candidate)
    if title and candidate.startswith(title):
        candidate = candidate[len(title):].lstrip(' ：:,-_')
    candidate = candidate.lstrip('!！?？。．·•【】《》〈〉<>()（）[]—_=~ ')
    candidate = re.split(r'(?:出版社|小说系列|书系|男主角|女主角|男\s*主\s*角|女\s*主\s*角|时代背景|故事地点|情节分类|发布时间|更新日期)[：:\s]', candidate, maxsplit=1)[0]
    candidate = re.split(r'(?:楔子|序章|第一章|第1章)', candidate, maxsplit=1)[0]
    candidate = normalize_spaces(candidate)
    if not candidate:
        return ''
    if any(token in candidate for token in ('小说论坛', 'kanfou.net', '爱书楼', '收集整理')):
        return ''
    if any(pattern.match(candidate) for pattern in METADATA_LINE_PATTERNS):
        return ''
    if '作者：' in candidate[:20] or '【作者】' in candidate[:20]:
        return ''
    if len(candidate) < 16 or count_cjk(candidate) < 8:
        return ''
    return candidate[:180]


def collect_intro_from_lines(lines: list[str], title: str) -> str:
    for idx, line in enumerate(lines[:60]):
        marker_match = INTRO_MARKER_PATTERN.match(line)
        if not marker_match:
            continue
        inline = clean_intro_candidate(marker_match.group(1), title)
        if inline:
            return inline
        pieces: list[str] = []
        for follow in lines[idx + 1: idx + 14]:
            if is_metadata_line(follow, title):
                if pieces:
                    break
                continue
            piece = clean_intro_candidate(follow, title)
            if not piece:
                if pieces:
                    break
                continue
            pieces.append(piece)
            if len(' '.join(pieces)) >= 160:
                break
        candidate = clean_intro_candidate(' '.join(pieces), title)
        if candidate:
            return candidate

    pieces = []
    chapter_seen = False
    for line in lines[:40]:
        if CHAPTER_PATTERN.match(line):
            chapter_seen = True
            if pieces:
                break
            continue
        if is_metadata_line(line, title):
            if pieces:
                break
            continue
        piece = clean_intro_candidate(line, title)
        if not piece:
            if pieces:
                break
            continue
        pieces.append(piece)
        if chapter_seen or len(' '.join(pieces)) >= 140:
            break
    return clean_intro_candidate(' '.join(pieces), title)


def strip_html(text: str) -> str:
    text = re.sub(r'<\s*br\s*/?\s*>', '\n', text, flags=re.I)
    text = re.sub(r'</p\s*>', '\n\n', text, flags=re.I)
    text = HTML_TAG_PATTERN.sub(' ', text)
    return html.unescape(text)


def read_epub_text(path: Path) -> str:
    pieces: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            name for name in archive.namelist()
            if name.lower().endswith(('.xhtml', '.html', '.htm', '.xml'))
        )
        for name in names:
            lowered = name.lower()
            if any(skip in lowered for skip in ('toc', 'nav', 'cover')):
                continue
            try:
                raw = archive.read(name)
            except KeyError:
                continue
            text, _encoding = decode_text(raw)
            text = strip_html(text)
            text = sanitize_text(text)
            if len(text) >= 50:
                pieces.append(text)
    return '\n\n'.join(pieces)


def read_work_text(path: Path) -> tuple[str, str]:
    if path.suffix.lower() == '.epub':
        return read_epub_text(path), 'epub-html'
    raw = path.read_bytes()
    text, encoding = decode_text(raw)
    return text, encoding


def read_work_preview(path: Path, byte_limit: int = 65536) -> tuple[str, str]:
    if path.suffix.lower() == '.epub':
        return read_work_text(path)
    with path.open('rb') as handle:
        raw = handle.read(byte_limit)
    text, encoding = decode_text(raw)
    return text, encoding


def sanitize_text(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\u3000', '  ').replace('\x00', '')
    clean_lines: list[str] = []
    blank_count = 0
    for original_line in text.split('\n'):
        line = original_line.strip()
        compact = normalize_spaces(line)
        if compact and any(pattern.search(compact) for pattern in NOISE_PATTERNS):
            continue
        if compact and re.fullmatch(r'[﹋﹊\-_=~·•*]{4,}', compact):
            continue
        if not compact:
            blank_count += 1
            if blank_count <= 1:
                clean_lines.append('')
            continue
        blank_count = 0
        clean_lines.append(line.strip())
    cleaned = '\n'.join(clean_lines)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def split_paragraphs(text: str) -> list[str]:
    paragraphs = []
    for chunk in re.split(r'\n{2,}', sanitize_text(text)):
        normalized = normalize_spaces(chunk)
        if not normalized:
            continue
        if len(normalized) < 2:
            continue
        paragraphs.append(normalized)
    return paragraphs


def extract_intro(text: str, title: str) -> str:
    for pattern in INTRO_BLOCK_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = clean_intro_candidate(match.group(1), title)
            if candidate:
                return candidate[:180]
    candidate = collect_intro_from_lines(normalize_lines(text), title)
    if candidate:
        return candidate[:180]
    for paragraph in split_paragraphs(text)[:8]:
        candidate = clean_intro_candidate(paragraph, title)
        if candidate:
            return candidate[:180]
    return ''


def extract_excerpt(text: str, title: str) -> str:
    pieces = []
    for paragraph in split_paragraphs(text):
        if paragraph.startswith('作者') or paragraph.startswith('男主角') or paragraph.startswith('女主角'):
            continue
        if title and paragraph == title:
            continue
        if CHAPTER_PATTERN.match(paragraph):
            continue
        if len(paragraph) < 16:
            continue
        pieces.append(paragraph)
        if len(' '.join(pieces)) >= 180:
            break
    excerpt = normalize_spaces(' '.join(pieces))
    return excerpt[:220]


def count_chapters(text: str) -> int:
    matches = CHAPTER_PATTERN.findall(text)
    return len(matches) if matches else 0


def infer_category_scores(title: str, author: str, relpath: str, text: str) -> dict[str, int]:
    strong_haystack = ' '.join([title, relpath]).lower()
    soft_haystack = ' '.join([title, relpath, text[:280]]).lower()
    scores: dict[str, int] = {}
    for category, keywords in READER_CATEGORY_RULES.items():
        haystack = soft_haystack if category in {'虐文', '黄文', '校园青春', '豪门总裁'} else strong_haystack
        score = sum(2 if keyword == title else 1 for keyword in keywords if keyword.lower() in haystack)
        if score:
            scores[category] = score

    family_terms = [term for term in ('哥哥', '弟弟', '妹妹', '姐姐') if term in soft_haystack]
    explicit_bone_terms = ('兄妹', '姐弟', '兄弟', '骨科', '哥哥情人', '妹妹甜心', '弟弟情郎')
    if ((len(family_terms) >= 2) or any(term in strong_haystack for term in explicit_bone_terms)) and any(token in soft_haystack for token in ('情人', '禁忌', '恋', '爱')):
        scores['骨科文'] = scores.get('骨科文', 0) + 3
        scores['禁忌关系'] = scores.get('禁忌关系', 0) + 2
    if '总裁' in soft_haystack and any(token in soft_haystack for token in ('契约', '秘书', '情妇', '娇妻')):
        scores['豪门总裁'] = scores.get('豪门总裁', 0) + 2
    if any(token in strong_haystack for token in ('皇上', '王爷', '侯爷', '贝勒', '公主')):
        scores['古言宫廷'] = scores.get('古言宫廷', 0) + 2
    return scores


def infer_categories(title: str, author: str, relpath: str, text: str) -> list[str]:
    scores = infer_category_scores(title, author, relpath, text)
    scores = {
        category: score
        for category, score in scores.items()
        if score >= CATEGORY_MIN_SCORE.get(category, 1)
    }
    if not scores:
        if any(token in title for token in ('总裁', '秘书', '情妇', '娇妻')):
            return ['豪门总裁', '都市言情']
        return ['都市言情']
    ordered = sorted(
        scores.items(),
        key=lambda item: (-item[1], CATEGORY_PRIORITY.index(item[0]) if item[0] in CATEGORY_PRIORITY else 999, item[0]),
    )
    return [name for name, _score in ordered[:6]]


def normalize_reader_tag(tag: object) -> str:
    raw = normalize_spaces(str(tag or ''))
    if not raw:
        return ''
    raw = raw.strip(' #,，、/\\|;；:：[]【】()（）')
    if not raw:
        return ''
    if raw in READER_TAG_SET:
        return raw
    alias = READER_TAG_ALIASES.get(raw)
    if alias:
        return alias
    compact = re.sub(r'\s+', '', raw)
    if compact in READER_TAG_SET:
        return compact
    alias = READER_TAG_ALIASES.get(compact)
    if alias:
        return alias
    return ''


def normalize_reader_tags(tags: list[object], categories: list[str] | None = None, max_tags: int = 8) -> list[str]:
    normalized = []
    seen = set()

    def add(tag: object) -> None:
        canonical = normalize_reader_tag(tag)
        if canonical and canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)

    for category in categories or []:
        for tag in CATEGORY_TO_TAGS.get(str(category), [category]):
            add(tag)
    for tag in tags or []:
        add(tag)
    if not normalized:
        add('都市言情')
    return normalized[:max_tags]


def infer_tags(title: str, author: str, relpath: str, text: str, categories: list[str]) -> list[str]:
    strong_haystack = ' '.join([title, relpath]).lower()
    soft_haystack = ' '.join([title, relpath, text[:280]]).lower()
    tags = list(categories)
    for tag, keywords in READER_TAG_RULES.items():
        haystack = soft_haystack if tag in {'追妻火葬场', '双向拉扯', '都市言情'} else strong_haystack
        if any(keyword.lower() in haystack for keyword in keywords):
            tags.append(tag)
    if '虐文' in categories and '甜宠' in categories:
        tags.append('先虐后甜')
    if '豪门总裁' in categories:
        tags.append('都市言情')
    if '古言宫廷' in categories:
        tags.append('宫廷权谋')
    if '江湖武侠' in categories:
        tags.append('江湖武侠')
    return normalize_reader_tags(tags, categories, max_tags=10)


def estimate_score(text: str, intro: str, categories: list[str], tags: list[str], chapter_count: int) -> float:
    score = 54.0
    if intro:
        score += 6
    if len(text) >= 8000:
        score += 4
    if len(text) >= 30000:
        score += 3
    score += min(10, len(categories) * 2)
    score += min(6, len(tags))
    score += min(8, chapter_count * 0.5)
    if '虐文' in categories:
        score += 2
    if '甜宠' in categories:
        score += 2
    if '悬疑灵异' in categories or '穿越重生' in categories:
        score += 1.5
    return round(min(score, 88.0), 1)


def build_keyword_blob(title: str, author: str, relpath: str, intro: str, excerpt: str, tags: list[str]) -> str:
    pieces = [title, author, relpath, intro, excerpt, ' '.join(tags)]
    return normalize_spaces(' '.join(piece for piece in pieces if piece))


def build_work_record(path: Path) -> dict[str, object]:
    author, title = infer_author_and_title(path)
    text, encoding = read_work_preview(path)
    clean_text = sanitize_text(text)
    text_author = extract_author_from_text(clean_text)
    if text_author:
        author = text_author
    elif is_generic_author(author):
        stem_author = extract_author_from_stem(path.stem)
        if stem_author:
            author = stem_author
    intro = extract_intro(clean_text, title)
    excerpt = extract_excerpt(clean_text, title) or intro
    categories = infer_categories(title, author, relpath_for(path), f'{intro}\n{excerpt}\n{clean_text[:4000]}')
    tags = infer_tags(title, author, relpath_for(path), f'{intro}\n{excerpt}\n{clean_text[:4000]}', categories)
    chapter_count = count_chapters(clean_text)
    char_count = max(len(clean_text.replace('\n', '')), int(path.stat().st_size / 1.7))
    heuristic_score = estimate_score(clean_text, intro, categories, tags, chapter_count)
    return {
        'work_key': sha256_text(relpath_for(path)),
        'relpath': relpath_for(path),
        'author': author,
        'title': title,
        'title_sort': title.lower(),
        'ext': path.suffix.lower(),
        'encoding': encoding,
        'file_size': path.stat().st_size,
        'file_mtime': path.stat().st_mtime,
        'chapter_count': chapter_count,
        'char_count': char_count,
        'summary': intro or excerpt[:180],
        'intro': intro or excerpt[:180],
        'excerpt': excerpt[:220],
        'tags_json': json.dumps(tags, ensure_ascii=False),
        'categories_json': json.dumps(categories, ensure_ascii=False),
        'primary_category': categories[0] if categories else '都市言情',
        'keyword_blob': build_keyword_blob(title, author, relpath_for(path), intro, excerpt, tags),
        'heuristic_score': heuristic_score,
        'created_at': utc_now_iso(),
        'updated_at': utc_now_iso(),
    }


def iter_supported_paths() -> list[Path]:
    paths: list[Path] = []
    if not WRITER_DIR.exists():
        return paths
    for pattern in ('*.txt', '*.TXT', '*.epub'):
        paths.extend(WRITER_DIR.rglob(pattern))
    filtered = [
        path for path in paths
        if not should_skip_path(path)
    ]
    unique_paths = sorted({path.resolve(): path for path in filtered}.values(), key=lambda item: item.as_posix())
    return unique_paths


def ensure_reader_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reader_works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_key TEXT NOT NULL UNIQUE,
            relpath TEXT NOT NULL UNIQUE,
            author TEXT NOT NULL,
            title TEXT NOT NULL,
            title_sort TEXT NOT NULL,
            ext TEXT NOT NULL,
            encoding TEXT,
            file_size INTEGER NOT NULL,
            file_mtime REAL NOT NULL,
            chapter_count INTEGER NOT NULL DEFAULT 0,
            char_count INTEGER NOT NULL DEFAULT 0,
            summary TEXT,
            intro TEXT,
            excerpt TEXT,
            tags_json TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            primary_category TEXT,
            keyword_blob TEXT NOT NULL,
            ai_score REAL,
            ai_metrics_json TEXT,
            ai_reason TEXT,
            ai_model TEXT,
            ai_status TEXT NOT NULL DEFAULT 'pending',
            ai_scored_at TEXT,
            heuristic_score REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reader_reads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_id INTEGER NOT NULL,
            opened_count INTEGER NOT NULL DEFAULT 1,
            progress REAL NOT NULL DEFAULT 0,
            last_scroll REAL NOT NULL DEFAULT 0,
            opened_at TEXT NOT NULL,
            last_read_at TEXT NOT NULL,
            UNIQUE(user_id, work_id)
        );

        CREATE INDEX IF NOT EXISTS idx_reader_works_author ON reader_works(author);
        CREATE INDEX IF NOT EXISTS idx_reader_works_title_sort ON reader_works(title_sort);
        CREATE INDEX IF NOT EXISTS idx_reader_reads_user_work ON reader_reads(user_id, work_id);
        '''
    )
    defaults = {
        'reader_password_hash': hash_password(DEFAULT_READER_PASSWORD),
        'reader_ai_url': DEFAULT_READER_AI_URL,
        'reader_ai_model': DEFAULT_READER_AI_MODEL,
        'reader_ai_token': DEFAULT_READER_AI_TOKEN,
    }
    now = utc_now_iso()
    for key, value in defaults.items():
        conn.execute(
            'INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)',
            (key, value, now),
        )


def get_reader_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        'SELECT key, value FROM app_settings WHERE key IN (?, ?, ?, ?)',
        ('reader_password_hash', 'reader_ai_url', 'reader_ai_model', 'reader_ai_token'),
    ).fetchall()
    values = {row['key']: row['value'] for row in rows}
    return {
        'reader_password_hash': values.get('reader_password_hash', hash_password(DEFAULT_READER_PASSWORD)),
        'reader_ai_url': values.get('reader_ai_url', DEFAULT_READER_AI_URL),
        'reader_ai_model': values.get('reader_ai_model', DEFAULT_READER_AI_MODEL),
        'reader_ai_token': values.get('reader_ai_token', DEFAULT_READER_AI_TOKEN),
    }


def sync_reader_index(conn: sqlite3.Connection, rescan_all: bool = False) -> dict[str, int]:
    ensure_reader_schema(conn)
    existing_rows = conn.execute(
        'SELECT relpath, file_mtime, file_size FROM reader_works'
    ).fetchall()
    existing = {row['relpath']: row for row in existing_rows}
    seen: set[str] = set()
    added = 0
    updated = 0
    failed = 0

    for path in iter_supported_paths():
        relpath = relpath_for(path)
        stat = path.stat()
        seen.add(relpath)
        row = existing.get(relpath)
        if (not rescan_all) and row and abs(row['file_mtime'] - stat.st_mtime) < 0.001 and row['file_size'] == stat.st_size:
            continue
        try:
            payload = build_work_record(path)
        except Exception:
            failed += 1
            continue
        if row:
            updated += 1
        else:
            added += 1
        conn.execute(
            '''
            INSERT INTO reader_works (
                work_key, relpath, author, title, title_sort, ext, encoding, file_size, file_mtime,
                chapter_count, char_count, summary, intro, excerpt, tags_json, categories_json,
                primary_category, keyword_blob, heuristic_score, ai_score, ai_metrics_json,
                ai_reason, ai_model, ai_status, ai_scored_at, created_at, updated_at
            ) VALUES (
                :work_key, :relpath, :author, :title, :title_sort, :ext, :encoding, :file_size, :file_mtime,
                :chapter_count, :char_count, :summary, :intro, :excerpt, :tags_json, :categories_json,
                :primary_category, :keyword_blob, :heuristic_score, NULL, NULL, NULL, NULL, 'pending', NULL,
                :created_at, :updated_at
            )
            ON CONFLICT(relpath) DO UPDATE SET
                work_key = excluded.work_key,
                author = excluded.author,
                title = excluded.title,
                title_sort = excluded.title_sort,
                ext = excluded.ext,
                encoding = excluded.encoding,
                file_size = excluded.file_size,
                file_mtime = excluded.file_mtime,
                chapter_count = excluded.chapter_count,
                char_count = excluded.char_count,
                summary = excluded.summary,
                intro = excluded.intro,
                excerpt = excluded.excerpt,
                tags_json = excluded.tags_json,
                categories_json = excluded.categories_json,
                primary_category = excluded.primary_category,
                keyword_blob = excluded.keyword_blob,
                heuristic_score = excluded.heuristic_score,
                ai_score = NULL,
                ai_metrics_json = NULL,
                ai_reason = NULL,
                ai_model = NULL,
                ai_status = 'pending',
                ai_scored_at = NULL,
                updated_at = excluded.updated_at
            ''',
            payload,
        )

    removed = 0
    if existing:
        stale = [relpath for relpath in existing if relpath not in seen]
        if stale:
            removed = len(stale)
            conn.executemany('DELETE FROM reader_works WHERE relpath = ?', [(relpath,) for relpath in stale])
    return {'added': added, 'updated': updated, 'removed': removed, 'failed': failed}


def load_work_text_from_relpath(relpath: str) -> tuple[str, str]:
    writer_candidate = WRITER_DIR / relpath
    path = writer_candidate if writer_candidate.exists() else (BASE_DIR / relpath)
    text, encoding = read_work_text(path)
    return sanitize_text(text), encoding


def build_content_payload(relpath: str) -> dict[str, object]:
    clean_text, encoding = load_work_text_from_relpath(relpath)
    paragraphs = split_paragraphs(clean_text)
    return {
        'encoding': encoding,
        'paragraphs': paragraphs,
        'char_count': len(clean_text.replace('\n', '')),
        'chapter_count': count_chapters(clean_text),
    }


def compute_tag_fit(tags: list[str], selected_tags: list[str]) -> float:
    if not selected_tags:
        return 50.0
    if not tags:
        return 0.0
    overlap = len(set(tags) & set(selected_tags))
    return round((overlap / max(len(selected_tags), 1)) * 100, 1)


def compute_relation_score(anchor: dict[str, object] | None, work: dict[str, object], query: str) -> float:
    score = 0.0
    if query:
        lowered_query = query.lower()
        if lowered_query in str(work.get('title', '')).lower():
            score += 55
        if lowered_query in str(work.get('author', '')).lower():
            score += 25
        if lowered_query in str(work.get('keyword_blob', '')).lower():
            score += 20
    if anchor:
        anchor_tags = set(anchor.get('tags', []))
        work_tags = set(work.get('tags', []))
        score += min(40.0, len(anchor_tags & work_tags) * 12.0)
        if anchor.get('author') == work.get('author'):
            score += 25
        if anchor.get('primary_category') and anchor.get('primary_category') == work.get('primary_category'):
            score += 12
        anchor_title = str(anchor.get('title', ''))
        work_title = str(work.get('title', ''))
        shared_chars = len(set(anchor_title) & set(work_title))
        score += min(18.0, shared_chars * 1.8)
    return round(min(score, 100.0), 1)


def row_to_work_dict(row: sqlite3.Row) -> dict[str, object]:
    categories = safe_json_loads(row['categories_json'], [])
    tags = normalize_reader_tags(safe_json_loads(row['tags_json'], []), categories, max_tags=10)
    ai_metrics = safe_json_loads(row['ai_metrics_json'], {})
    base_score = row['ai_score'] if row['ai_score'] is not None else row['heuristic_score']
    total_opens = row['total_opens'] if 'total_opens' in row.keys() else 0
    popularity = round(min(100.0, float(total_opens or 0) * 9.0), 1)
    return {
        'id': row['id'],
        'author': row['author'],
        'title': row['title'],
        'relpath': row['relpath'],
        'summary': row['summary'] or row['intro'] or row['excerpt'] or '',
        'intro': row['intro'] or row['summary'] or '',
        'excerpt': row['excerpt'] or '',
        'tags': tags,
        'categories': categories,
        'primary_category': row['primary_category'] or (categories[0] if categories else '都市言情'),
        'ai_score': row['ai_score'],
        'score': round(float(base_score or 0), 1),
        'ai_metrics': ai_metrics,
        'ai_reason': row['ai_reason'] or '',
        'ai_status': row['ai_status'],
        'ai_model': row['ai_model'] or '',
        'char_count': row['char_count'],
        'chapter_count': row['chapter_count'],
        'encoding': row['encoding'],
        'ext': row['ext'],
        'total_opens': total_opens or 0,
        'popularity_score': popularity,
        'keyword_blob': row['keyword_blob'],
        'updated_at': row['updated_at'],
        'last_read_at': row['last_read_at'] if 'last_read_at' in row.keys() else None,
        'progress': row['progress'] if 'progress' in row.keys() else 0,
        'last_scroll': row['last_scroll'] if 'last_scroll' in row.keys() else 0,
    }


def score_for_listing(
    work: dict[str, object],
    selected_tags: list[str],
    query: str,
    anchor: dict[str, object] | None,
) -> dict[str, float]:
    tag_fit = compute_tag_fit(work.get('tags', []), selected_tags)
    relation = compute_relation_score(anchor, work, query)
    rating = float(work.get('score') or 0)
    popularity = float(work.get('popularity_score') or 0)
    recommendation = round(rating * 0.46 + tag_fit * 0.24 + relation * 0.18 + popularity * 0.12, 1)
    return {
        'tag_fit_score': tag_fit,
        'relation_score': relation,
        'recommend_score': recommendation,
    }


def rank_works(
    works: list[dict[str, object]],
    sort: str,
    selected_tags: list[str],
    query: str,
    anchor: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    enriched: list[dict[str, object]] = []
    for work in works:
        metrics = score_for_listing(work, selected_tags, query, anchor)
        merged = {**work, **metrics}
        enriched.append(merged)

    if sort == 'rating':
        key = lambda item: (-float(item['score']), -float(item['recommend_score']), item['title'])
    elif sort == 'tag_fit':
        key = lambda item: (-float(item['tag_fit_score']), -float(item['recommend_score']), item['title'])
    elif sort == 'related':
        key = lambda item: (-float(item['relation_score']), -float(item['recommend_score']), item['title'])
    elif sort == 'latest':
        key = lambda item: (str(item['updated_at']), str(item['title']))
        return sorted(enriched, key=key, reverse=True)
    else:
        key = lambda item: (-float(item['recommend_score']), -float(item['score']), item['title'])
    return sorted(enriched, key=key)
