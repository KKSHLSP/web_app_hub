import hashlib
import json
import sqlite3
import time
from datetime import datetime, UTC
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / 'static'
DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'hub.db'
HOST = '0.0.0.0'
PORT = 8777
LOGIN_VERIFY_WINDOW_SEC = 300
RUN_SAVE_WINDOW_SEC = 3

DATA_DIR.mkdir(parents=True, exist_ok=True)
VERIFY_RATE = {}
SAVE_RATE = {}


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

    cols = [r['name'] for r in conn.execute("PRAGMA table_info(schulte_runs)").fetchall()]
    if 'user_id' not in cols:
        conn.execute("ALTER TABLE schulte_runs ADD COLUMN user_id INTEGER")
        conn.execute("UPDATE schulte_runs SET user_id = 1 WHERE user_id IS NULL")
    if 'condition' not in cols:
        conn.execute("ALTER TABLE schulte_runs ADD COLUMN condition TEXT")
    if 'notes' not in cols:
        conn.execute("ALTER TABLE schulte_runs ADD COLUMN notes TEXT")

    conn.execute(
        "INSERT OR IGNORE INTO apps (id, name, slug, kind, route, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ('app-schulte', '舒爾特方格', 'schulte', 'game', '/apps/schulte', now_iso())
    )
    conn.execute(
        "INSERT OR IGNORE INTO apps (id, name, slug, kind, route, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ('app-mbti', 'MBTI 測試', 'mbti', 'tool', '/apps/mbti', now_iso())
    )
    conn.execute(
        "INSERT OR IGNORE INTO users (id, name, password_hash, birthday, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (1, '測試帳號', hash_password('onelun'), None, now_iso(), now_iso())
    )
    conn.commit()
    conn.close()


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
                    (name, hash_password(password) if password else None, birthday, now_iso(), now_iso())
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
                    payload['score'], payload.get('condition'), payload.get('notes')
                )
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
                (payload['user_id'], payload['tested_at'], payload['mbti_type'], payload['ei_score'], payload['sn_score'], payload['tf_score'], payload['jp_score'], payload.get('notes'))
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
