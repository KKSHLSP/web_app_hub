import hashlib
import json
import secrets
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlparse

from reader_core import (
    BASE_DIR,
    build_content_payload,
    ensure_reader_schema,
    get_reader_settings,
    rank_works,
    row_to_work_dict,
    safe_json_loads,
    sync_reader_index,
)

STATIC_DIR = BASE_DIR / 'static'
DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'hub.db'
HOST = '0.0.0.0'
PORT = 8777
LOGIN_VERIFY_WINDOW_SEC = 300
RUN_SAVE_WINDOW_SEC = 3
READER_AUTH_WINDOW_SEC = 15
READER_SESSION_TTL_SEC = 12 * 60 * 60

DATA_DIR.mkdir(parents=True, exist_ok=True)
VERIFY_RATE = {}
SAVE_RATE = {}
READER_AUTH_RATE = {}
READER_TOKENS = {}
READER_INDEX_READY = False
READER_SYNC_LOCK = Lock()
READER_TOKEN_LOCK = Lock()


def now_iso():
    return datetime.now(UTC).isoformat()


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def client_ip(handler):
    forwarded = handler.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return handler.client_address[0]


def rate_limited(store, key, window_sec):
    now = time.time()
    last = store.get(key)
    if last and now - last < window_sec:
        return True, round(window_sec - (now - last), 1)
    store[key] = now
    return False, 0


def purge_reader_tokens():
    now = time.time()
    with READER_TOKEN_LOCK:
        expired = [token for token, meta in READER_TOKENS.items() if meta['expires_at'] <= now]
        for token in expired:
            READER_TOKENS.pop(token, None)


def issue_reader_token():
    purge_reader_tokens()
    token = secrets.token_urlsafe(24)
    with READER_TOKEN_LOCK:
        READER_TOKENS[token] = {'expires_at': time.time() + READER_SESSION_TTL_SEC}
    return token


def validate_reader_token(token: str) -> bool:
    if not token:
        return False
    purge_reader_tokens()
    with READER_TOKEN_LOCK:
        meta = READER_TOKENS.get(token)
        if not meta:
            return False
        meta['expires_at'] = time.time() + READER_SESSION_TTL_SEC
    return True


def ensure_schema():
    conn = db()
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS apps (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            route TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            password_hash TEXT,
            birthday TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS schulte_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            played_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            grid_size INTEGER NOT NULL,
            total_numbers INTEGER NOT NULL,
            time_sec REAL NOT NULL,
            correct_count INTEGER NOT NULL,
            mistakes INTEGER NOT NULL,
            clicks INTEGER NOT NULL,
            avg_interval_sec REAL,
            first_chunk_sec REAL,
            last_chunk_sec REAL,
            score INTEGER NOT NULL,
            condition TEXT,
            notes TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS mbti_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tested_at TEXT NOT NULL,
            mbti_type TEXT NOT NULL,
            ei_score INTEGER NOT NULL,
            sn_score INTEGER NOT NULL,
            tf_score INTEGER NOT NULL,
            jp_score INTEGER NOT NULL,
            notes TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        '''
    )

    cols = [r['name'] for r in conn.execute('PRAGMA table_info(schulte_runs)').fetchall()]
    if 'user_id' not in cols:
        conn.execute('ALTER TABLE schulte_runs ADD COLUMN user_id INTEGER')
        conn.execute('UPDATE schulte_runs SET user_id = 1 WHERE user_id IS NULL')
    if 'condition' not in cols:
        conn.execute('ALTER TABLE schulte_runs ADD COLUMN condition TEXT')
    if 'notes' not in cols:
        conn.execute('ALTER TABLE schulte_runs ADD COLUMN notes TEXT')

    conn.execute(
        'INSERT OR IGNORE INTO apps (id, name, slug, kind, route, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        ('app-schulte', '舒爾特方格', 'schulte', 'game', '/apps/schulte', now_iso()),
    )
    conn.execute(
        'INSERT OR IGNORE INTO apps (id, name, slug, kind, route, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        ('app-mbti', 'MBTI 測試', 'mbti', 'tool', '/apps/mbti', now_iso()),
    )
    conn.execute(
        'INSERT OR IGNORE INTO apps (id, name, slug, kind, route, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        ('app-reader', '私密閱讀系統', 'reader', 'reader', '/apps/reader', now_iso()),
    )
    conn.execute(
        'INSERT OR IGNORE INTO users (id, name, password_hash, birthday, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
        (1, '測試帳號', hash_password('onelun'), None, now_iso(), now_iso()),
    )
    ensure_reader_schema(conn)
    conn.commit()
    conn.close()


def ensure_reader_index_ready(force=False):
    global READER_INDEX_READY
    if READER_INDEX_READY and not force:
        return {'added': 0, 'updated': 0, 'removed': 0, 'failed': 0}
    with READER_SYNC_LOCK:
        if READER_INDEX_READY and not force:
            return {'added': 0, 'updated': 0, 'removed': 0, 'failed': 0}
        conn = db()
        summary = sync_reader_index(conn, rescan_all=force)
        conn.commit()
        conn.close()
        READER_INDEX_READY = True
        return summary


def parse_positive_int(value, default=0):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def fetch_reader_row(conn, work_id, user_id=0):
    return conn.execute(
        '''
        SELECT
            w.*,
            COALESCE(rr.progress, 0) AS progress,
            COALESCE(rr.last_scroll, 0) AS last_scroll,
            rr.last_read_at,
            COALESCE(ro.total_opens, 0) AS total_opens
        FROM reader_works AS w
        LEFT JOIN reader_reads AS rr ON rr.work_id = w.id AND rr.user_id = ?
        LEFT JOIN (
            SELECT work_id, SUM(opened_count) AS total_opens
            FROM reader_reads
            GROUP BY work_id
        ) AS ro ON ro.work_id = w.id
        WHERE w.id = ?
        ''',
        (user_id or 0, work_id),
    ).fetchone()


def fetch_reader_works(conn, user_id=0, query=''):
    sql = '''
        SELECT
            w.*,
            COALESCE(rr.progress, 0) AS progress,
            COALESCE(rr.last_scroll, 0) AS last_scroll,
            rr.last_read_at,
            COALESCE(ro.total_opens, 0) AS total_opens
        FROM reader_works AS w
        LEFT JOIN reader_reads AS rr ON rr.work_id = w.id AND rr.user_id = ?
        LEFT JOIN (
            SELECT work_id, SUM(opened_count) AS total_opens
            FROM reader_reads
            GROUP BY work_id
        ) AS ro ON ro.work_id = w.id
    '''
    params = [user_id or 0]
    if query:
        like = f'%{query}%'
        sql += ' WHERE w.title LIKE ? OR w.author LIKE ? OR w.keyword_blob LIKE ?'
        params.extend([like, like, like])
    sql += ' ORDER BY w.title_sort ASC'
    return conn.execute(sql, params).fetchall()


def build_reader_catalog(conn, user_id, query='', selected_tags=None, sort='recommended', limit=30, related_to=0):
    selected_tags = [tag for tag in (selected_tags or []) if tag]
    anchor = None
    if related_to:
        anchor_row = fetch_reader_row(conn, related_to, user_id)
        anchor = row_to_work_dict(anchor_row) if anchor_row else None

    works = [row_to_work_dict(row) for row in fetch_reader_works(conn, user_id, query)]
    if selected_tags:
        works = [work for work in works if set(selected_tags) & set(work['tags'])]
    if related_to:
        works = [work for work in works if work['id'] != related_to]

    ranked = rank_works(works, sort, selected_tags, query, anchor)
    return ranked[:limit], anchor


def get_reader_facets(conn):
    rows = conn.execute('SELECT author, tags_json FROM reader_works').fetchall()
    authors = {row['author'] for row in rows}
    counter = Counter()
    for row in rows:
        for tag in safe_json_loads(row['tags_json'], []):
            counter[tag] += 1
    tags = [{'name': name, 'count': count} for name, count in counter.most_common(30)]
    return {
        'totalWorks': conn.execute('SELECT COUNT(*) AS total FROM reader_works').fetchone()['total'],
        'totalAuthors': len(authors),
        'tags': tags,
    }


def current_reader_settings():
    conn = db()
    settings = get_reader_settings(conn)
    conn.close()
    return settings


def merge_ai_categories(primary_category, existing_categories):
    categories = [primary_category] if primary_category else []
    for category in existing_categories or []:
        if category and category not in categories:
            categories.append(category)
    return categories[:6]


def refresh_reader_ai(conn, work_ids):
    settings = get_reader_settings(conn)
    if not settings['reader_ai_token']:
        return [{'ok': False, 'error': '尚未配置本地 AI Bearer token，請先到設定頁補上。'}]

    results = []
    script_path = BASE_DIR / 'reader_ai.py'
    for work_id in work_ids:
        row = conn.execute(
            'SELECT id, relpath, title, author, categories_json, tags_json FROM reader_works WHERE id = ?',
            (work_id,),
        ).fetchone()
        if not row:
            results.append({'id': work_id, 'ok': False, 'error': 'work not found'})
            continue

        cmd = [
            sys.executable,
            str(script_path),
            '--file',
            str(BASE_DIR / row['relpath']),
            '--title',
            row['title'],
            '--author',
            row['author'],
            '--relpath',
            row['relpath'],
            '--categories',
            row['categories_json'],
            '--tags',
            row['tags_json'],
            '--url',
            settings['reader_ai_url'],
            '--model',
            settings['reader_ai_model'],
            '--token',
            settings['reader_ai_token'],
        ]
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
        except subprocess.TimeoutExpired:
            conn.execute(
                'UPDATE reader_works SET ai_status = ?, ai_reason = ?, updated_at = ? WHERE id = ?',
                ('failed', 'AI 评分脚本超时', now_iso(), work_id),
            )
            results.append({'id': work_id, 'ok': False, 'error': 'AI 评分脚本超时'})
            continue

        raw_output = (completed.stdout or '').strip() or (completed.stderr or '').strip()
        if not raw_output:
            conn.execute(
                'UPDATE reader_works SET ai_status = ?, ai_reason = ?, updated_at = ? WHERE id = ?',
                ('failed', 'AI 评分脚本没有输出', now_iso(), work_id),
            )
            results.append({'id': work_id, 'ok': False, 'error': 'AI 评分脚本没有输出'})
            continue

        try:
            payload = json.loads(raw_output.splitlines()[-1])
        except json.JSONDecodeError:
            conn.execute(
                'UPDATE reader_works SET ai_status = ?, ai_reason = ?, updated_at = ? WHERE id = ?',
                ('failed', raw_output[:400], now_iso(), work_id),
            )
            results.append({'id': work_id, 'ok': False, 'error': raw_output[:240]})
            continue

        if not payload.get('ok'):
            conn.execute(
                'UPDATE reader_works SET ai_status = ?, ai_reason = ?, updated_at = ? WHERE id = ?',
                ('failed', str(payload.get('error') or 'AI 评分失败')[:400], now_iso(), work_id),
            )
            results.append({'id': work_id, 'ok': False, 'error': payload.get('error', 'AI 评分失败')})
            continue

        result = payload.get('result') or {}
        meta = payload.get('meta') or {}
        existing_categories = safe_json_loads(row['categories_json'], [])
        categories = merge_ai_categories(result.get('primary_category'), existing_categories)
        conn.execute(
            '''
            UPDATE reader_works
            SET
                summary = ?,
                intro = ?,
                tags_json = ?,
                categories_json = ?,
                primary_category = ?,
                ai_score = ?,
                ai_metrics_json = ?,
                ai_reason = ?,
                ai_model = ?,
                ai_status = ?,
                ai_scored_at = ?,
                updated_at = ?
            WHERE id = ?
            ''',
            (
                result.get('summary'),
                result.get('intro'),
                json.dumps(result.get('tags', []), ensure_ascii=False),
                json.dumps(categories, ensure_ascii=False),
                result.get('primary_category'),
                (result.get('scores') or {}).get('overall'),
                json.dumps(result.get('scores') or {}, ensure_ascii=False),
                result.get('reason'),
                meta.get('resolved_model') or settings['reader_ai_model'],
                'done',
                now_iso(),
                now_iso(),
                work_id,
            ),
        )
        results.append({'id': work_id, 'ok': True, 'meta': meta})
    return results


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path == '/':
            self.path = '/index.html'
        elif parsed.path == '/apps/schulte':
            self.path = '/schulte.html'
        elif parsed.path == '/apps/mbti':
            self.path = '/mbti.html'
        elif parsed.path == '/apps/reader':
            self.path = '/reader.html'
        elif parsed.path == '/settings':
            self.path = '/settings.html'
        return super().do_HEAD()

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get('Content-Length', '0'))
        return json.loads(self.rfile.read(length) or b'{}')

    def _reader_token(self):
        return self.headers.get('X-Reader-Token', '').strip()

    def _require_reader_token(self):
        if validate_reader_token(self._reader_token()):
            return True
        self._json({'error': 'reader auth required'}, 401)
        return False

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/apps':
            conn = db()
            rows = [dict(r) for r in conn.execute('SELECT * FROM apps ORDER BY created_at ASC').fetchall()]
            conn.close()
            return self._json({'apps': rows})

        if parsed.path == '/api/users':
            conn = db()
            rows = [dict(r) for r in conn.execute('SELECT id, name, birthday, created_at, updated_at, CASE WHEN password_hash IS NULL OR password_hash = "" THEN 0 ELSE 1 END AS has_password FROM users ORDER BY id ASC').fetchall()]
            conn.close()
            return self._json({'users': rows})

        if parsed.path == '/api/reader/settings':
            settings = current_reader_settings()
            return self._json({
                'settings': {
                    'ai_url': settings['reader_ai_url'],
                    'ai_model': settings['reader_ai_model'],
                    'has_ai_token': bool(settings['reader_ai_token']),
                    'has_password': bool(settings['reader_password_hash']),
                }
            })

        if parsed.path == '/api/reader/bootstrap':
            if not self._require_reader_token():
                return
            ensure_reader_index_ready()
            params = parse_qs(parsed.query)
            user_id = parse_positive_int(params.get('user_id', ['0'])[0], 0)
            q = (params.get('q', [''])[0] or '').strip()
            tags = [item.strip() for item in (params.get('tags', [''])[0] or '').split(',') if item.strip()]
            sort = (params.get('sort', ['recommended'])[0] or 'recommended').strip()
            conn = db()
            works, _anchor = build_reader_catalog(conn, user_id, q, tags, sort, limit=30)
            facets = get_reader_facets(conn)
            conn.close()
            return self._json({'works': works, 'facets': facets})

        if parsed.path == '/api/reader/works':
            if not self._require_reader_token():
                return
            ensure_reader_index_ready()
            params = parse_qs(parsed.query)
            user_id = parse_positive_int(params.get('user_id', ['0'])[0], 0)
            q = (params.get('q', [''])[0] or '').strip()
            sort = (params.get('sort', ['recommended'])[0] or 'recommended').strip()
            limit = min(max(parse_positive_int(params.get('limit', ['40'])[0], 40), 1), 120)
            related_to = parse_positive_int(params.get('related_to', ['0'])[0], 0)
            tags = [item.strip() for item in (params.get('tags', [''])[0] or '').split(',') if item.strip()]
            conn = db()
            works, anchor = build_reader_catalog(conn, user_id, q, tags, sort, limit, related_to)
            conn.close()
            return self._json({'works': works, 'anchor': anchor})

        if parsed.path == '/api/reader/work':
            if not self._require_reader_token():
                return
            ensure_reader_index_ready()
            params = parse_qs(parsed.query)
            work_id = parse_positive_int(params.get('id', ['0'])[0], 0)
            user_id = parse_positive_int(params.get('user_id', ['0'])[0], 0)
            if not work_id:
                return self._json({'error': 'id required'}, 400)
            conn = db()
            row = fetch_reader_row(conn, work_id, user_id)
            if not row:
                conn.close()
                return self._json({'error': 'work not found'}, 404)
            work = row_to_work_dict(row)
            related, _anchor = build_reader_catalog(conn, user_id, '', [], 'related', 8, related_to=work_id)
            conn.close()
            return self._json({'work': work, 'related': related})

        if parsed.path == '/api/reader/content':
            if not self._require_reader_token():
                return
            ensure_reader_index_ready()
            params = parse_qs(parsed.query)
            work_id = parse_positive_int(params.get('id', ['0'])[0], 0)
            if not work_id:
                return self._json({'error': 'id required'}, 400)
            conn = db()
            row = conn.execute('SELECT relpath FROM reader_works WHERE id = ?', (work_id,)).fetchone()
            conn.close()
            if not row:
                return self._json({'error': 'work not found'}, 404)
            return self._json({'content': build_content_payload(row['relpath'])})

        if parsed.path == '/api/schulte/runs':
            params = parse_qs(parsed.query)
            limit = min(int(params.get('limit', ['200'])[0]), 1000)
            user_id = params.get('user_id', [None])[0]
            conn = db()
            if user_id:
                rows = [dict(r) for r in conn.execute('SELECT * FROM schulte_runs WHERE user_id = ? ORDER BY played_at DESC LIMIT ?', (user_id, limit)).fetchall()]
            else:
                rows = [dict(r) for r in conn.execute('SELECT * FROM schulte_runs ORDER BY played_at DESC LIMIT ?', (limit,)).fetchall()]
            conn.close()
            return self._json({'runs': rows})

        if parsed.path == '/api/mbti/runs':
            params = parse_qs(parsed.query)
            limit = min(int(params.get('limit', ['200'])[0]), 1000)
            user_id = params.get('user_id', [None])[0]
            conn = db()
            if user_id:
                rows = [dict(r) for r in conn.execute('SELECT * FROM mbti_runs WHERE user_id = ? ORDER BY tested_at DESC LIMIT ?', (user_id, limit)).fetchall()]
            else:
                rows = [dict(r) for r in conn.execute('SELECT * FROM mbti_runs ORDER BY tested_at DESC LIMIT ?', (limit,)).fetchall()]
            conn.close()
            return self._json({'runs': rows})

        if parsed.path == '/':
            self.path = '/index.html'
        elif parsed.path == '/apps/schulte':
            self.path = '/schulte.html'
        elif parsed.path == '/apps/mbti':
            self.path = '/mbti.html'
        elif parsed.path == '/apps/reader':
            self.path = '/reader.html'
        elif parsed.path == '/settings':
            self.path = '/settings.html'
        elif parsed.path.startswith('/apps/'):
            return self._json({'error': 'not found'}, 404)
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/users':
            payload = self._read_json()
            name = (payload.get('name') or '').strip()
            password = payload.get('password') or ''
            birthday = (payload.get('birthday') or '').strip() or None
            if not name:
                return self._json({'error': 'name required'}, 400)
            conn = db()
            try:
                cur = conn.execute(
                    'INSERT INTO users (name, password_hash, birthday, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
                    (name, hash_password(password) if password else None, birthday, now_iso(), now_iso()),
                )
                conn.commit()
                user_id = cur.lastrowid
            except sqlite3.IntegrityError:
                conn.close()
                return self._json({'error': 'user exists'}, 400)
            conn.close()
            return self._json({'ok': True, 'id': user_id})

        if parsed.path == '/api/users/verify':
            payload = self._read_json()
            user_id = payload.get('user_id')
            password = payload.get('password') or ''
            if not user_id:
                return self._json({'error': 'user_id required'}, 400)
            ip = client_ip(self)
            limited, retry = rate_limited(VERIFY_RATE, f'{ip}:{user_id}', LOGIN_VERIFY_WINDOW_SEC)
            if limited:
                return self._json({'error': f'驗證過於頻繁，請 {retry} 秒後再試'}, 429)
            conn = db()
            row = conn.execute('SELECT id, password_hash FROM users WHERE id = ?', (user_id,)).fetchone()
            conn.close()
            if not row:
                return self._json({'error': 'user not found'}, 404)
            ok = (not row['password_hash']) or (hash_password(password) == row['password_hash'])
            return self._json({'ok': bool(ok)})

        if parsed.path == '/api/users/update':
            payload = self._read_json()
            user_id = payload.get('user_id')
            birthday = (payload.get('birthday') or '').strip() or None
            password = payload.get('password')
            if not user_id:
                return self._json({'error': 'user_id required'}, 400)
            conn = db()
            row = conn.execute('SELECT id FROM users WHERE id = ?', (user_id,)).fetchone()
            if not row:
                conn.close()
                return self._json({'error': 'user not found'}, 404)
            if password is not None:
                password_hash = hash_password(password) if password else None
                conn.execute('UPDATE users SET birthday = ?, password_hash = ?, updated_at = ? WHERE id = ?', (birthday, password_hash, now_iso(), user_id))
            else:
                conn.execute('UPDATE users SET birthday = ?, updated_at = ? WHERE id = ?', (birthday, now_iso(), user_id))
            conn.commit()
            conn.close()
            return self._json({'ok': True})

        if parsed.path == '/api/reader/auth':
            payload = self._read_json()
            password = payload.get('password') or ''
            ip = client_ip(self)
            limited, retry = rate_limited(READER_AUTH_RATE, f'{ip}:reader', READER_AUTH_WINDOW_SEC)
            if limited:
                return self._json({'error': f'閱讀入口驗證過於頻繁，請 {retry} 秒後再試'}, 429)
            settings = current_reader_settings()
            if hash_password(password) != settings['reader_password_hash']:
                return self._json({'ok': False, 'error': '閱讀系統密碼不正確'}, 401)
            return self._json({'ok': True, 'token': issue_reader_token(), 'ttl_sec': READER_SESSION_TTL_SEC})

        if parsed.path == '/api/reader/settings':
            payload = self._read_json()
            password = payload.get('password')
            ai_url = payload.get('ai_url')
            ai_model = payload.get('ai_model')
            ai_token = payload.get('ai_token')
            conn = db()
            ensure_reader_schema(conn)
            if password is not None and str(password).strip():
                conn.execute(
                    'UPDATE app_settings SET value = ?, updated_at = ? WHERE key = ?',
                    (hash_password(str(password).strip()), now_iso(), 'reader_password_hash'),
                )
            if ai_url is not None:
                conn.execute(
                    'UPDATE app_settings SET value = ?, updated_at = ? WHERE key = ?',
                    (str(ai_url).strip(), now_iso(), 'reader_ai_url'),
                )
            if ai_model is not None:
                conn.execute(
                    'UPDATE app_settings SET value = ?, updated_at = ? WHERE key = ?',
                    (str(ai_model).strip(), now_iso(), 'reader_ai_model'),
                )
            if ai_token is not None:
                conn.execute(
                    'UPDATE app_settings SET value = ?, updated_at = ? WHERE key = ?',
                    (str(ai_token).strip(), now_iso(), 'reader_ai_token'),
                )
            conn.commit()
            settings = get_reader_settings(conn)
            conn.close()
            return self._json({
                'ok': True,
                'settings': {
                    'ai_url': settings['reader_ai_url'],
                    'ai_model': settings['reader_ai_model'],
                    'has_ai_token': bool(settings['reader_ai_token']),
                },
            })

        if parsed.path == '/api/reader/progress':
            if not self._require_reader_token():
                return
            payload = self._read_json()
            user_id = parse_positive_int(payload.get('user_id'), 0)
            work_id = parse_positive_int(payload.get('work_id'), 0)
            progress = max(0.0, min(100.0, float(payload.get('progress', 0) or 0)))
            last_scroll = max(0.0, float(payload.get('last_scroll', 0) or 0))
            opened = bool(payload.get('opened'))
            if not user_id or not work_id:
                return self._json({'error': 'user_id and work_id required'}, 400)
            conn = db()
            conn.execute(
                '''
                INSERT INTO reader_reads (user_id, work_id, opened_count, progress, last_scroll, opened_at, last_read_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, work_id) DO UPDATE SET
                    opened_count = reader_reads.opened_count + excluded.opened_count,
                    progress = CASE WHEN excluded.progress > reader_reads.progress THEN excluded.progress ELSE reader_reads.progress END,
                    last_scroll = excluded.last_scroll,
                    last_read_at = excluded.last_read_at
                ''',
                (user_id, work_id, 1 if opened else 0, progress, last_scroll, now_iso(), now_iso()),
            )
            conn.commit()
            conn.close()
            return self._json({'ok': True})

        if parsed.path == '/api/reader/reindex':
            if not self._require_reader_token():
                return
            return self._json({'ok': True, 'summary': ensure_reader_index_ready(force=True)})

        if parsed.path == '/api/reader/ai/refresh':
            if not self._require_reader_token():
                return
            ensure_reader_index_ready()
            payload = self._read_json()
            work_id = parse_positive_int(payload.get('work_id'), 0)
            limit = min(max(parse_positive_int(payload.get('limit'), 1), 1), 20)
            conn = db()
            if work_id:
                work_ids = [work_id]
            else:
                rows = conn.execute(
                    'SELECT id FROM reader_works WHERE ai_status != ? ORDER BY heuristic_score DESC, id ASC LIMIT ?',
                    ('done', limit),
                ).fetchall()
                work_ids = [row['id'] for row in rows]
            results = refresh_reader_ai(conn, work_ids)
            conn.commit()
            conn.close()
            return self._json({'ok': True, 'results': results})

        if parsed.path == '/api/schulte/runs':
            payload = self._read_json()
            required = ['user_id', 'played_at', 'mode', 'grid_size', 'total_numbers', 'time_sec', 'correct_count', 'mistakes', 'clicks', 'score']
            for key in required:
                if key not in payload:
                    return self._json({'error': f'missing field: {key}'}, 400)
            ip = client_ip(self)
            limited, retry = rate_limited(SAVE_RATE, f"schulte:{ip}:{payload.get('user_id')}", RUN_SAVE_WINDOW_SEC)
            if limited:
                return self._json({'error': f'保存過於頻繁，請 {retry} 秒後再試'}, 429)
            conn = db()
            cur = conn.execute(
                '''
                INSERT INTO schulte_runs (
                    user_id, played_at, mode, grid_size, total_numbers, time_sec, correct_count,
                    mistakes, clicks, avg_interval_sec, first_chunk_sec, last_chunk_sec,
                    score, condition, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    payload['user_id'], payload['played_at'], payload['mode'], payload['grid_size'], payload['total_numbers'],
                    payload['time_sec'], payload['correct_count'], payload['mistakes'], payload['clicks'],
                    payload.get('avg_interval_sec'), payload.get('first_chunk_sec'), payload.get('last_chunk_sec'),
                    payload['score'], payload.get('condition'), payload.get('notes'),
                ),
            )
            conn.commit()
            run_id = cur.lastrowid
            conn.close()
            return self._json({'ok': True, 'id': run_id})

        if parsed.path == '/api/schulte/runs/clear':
            payload = self._read_json()
            user_id = payload.get('user_id')
            if not user_id:
                return self._json({'error': 'user_id required'}, 400)
            conn = db()
            conn.execute('DELETE FROM schulte_runs WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            return self._json({'ok': True})

        if parsed.path == '/api/mbti/runs':
            payload = self._read_json()
            required = ['user_id', 'tested_at', 'mbti_type', 'ei_score', 'sn_score', 'tf_score', 'jp_score']
            for key in required:
                if key not in payload:
                    return self._json({'error': f'missing field: {key}'}, 400)
            ip = client_ip(self)
            limited, retry = rate_limited(SAVE_RATE, f"mbti:{ip}:{payload.get('user_id')}", RUN_SAVE_WINDOW_SEC)
            if limited:
                return self._json({'error': f'保存過於頻繁，請 {retry} 秒後再試'}, 429)
            conn = db()
            cur = conn.execute(
                'INSERT INTO mbti_runs (user_id, tested_at, mbti_type, ei_score, sn_score, tf_score, jp_score, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (payload['user_id'], payload['tested_at'], payload['mbti_type'], payload['ei_score'], payload['sn_score'], payload['tf_score'], payload['jp_score'], payload.get('notes')),
            )
            conn.commit()
            run_id = cur.lastrowid
            conn.close()
            return self._json({'ok': True, 'id': run_id})

        if parsed.path == '/api/mbti/runs/clear':
            payload = self._read_json()
            user_id = payload.get('user_id')
            if not user_id:
                return self._json({'error': 'user_id required'}, 400)
            conn = db()
            conn.execute('DELETE FROM mbti_runs WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            return self._json({'ok': True})

        return self._json({'error': 'not found'}, 404)


if __name__ == '__main__':
    ensure_schema()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Hub listening on http://{HOST}:{PORT}')
    server.serve_forever()
