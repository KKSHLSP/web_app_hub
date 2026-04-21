from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from reader_core import BASE_DIR, normalize_reader_tags, safe_json_loads
from reader_score_schema import normalize_score_metrics


DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'hub.db'
REPORTS_DIR = DATA_DIR / 'reader_tag_reports'


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Normalize reader tags into the fixed tag vocabulary.')
    parser.add_argument('--db', default=str(DB_PATH), help='SQLite database path')
    parser.add_argument('--status', choices=('done', 'all'), default='all', help='Rows to normalize')
    parser.add_argument('--limit', type=int, default=0, help='Maximum number of rows to scan, 0 means no limit')
    parser.add_argument('--dry-run', action='store_true', help='Only report, do not update DB')
    parser.add_argument('--sample-size', type=int, default=20, help='Number of changed rows to sample')
    parser.add_argument('--report-dir', default=str(REPORTS_DIR), help='Directory for JSON reports')
    return parser.parse_args()


def db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 30000')
    return conn


def distinct_tag_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT value) AS count FROM reader_works, json_each(tags_json) WHERE ai_status != 'running'"
    ).fetchone()
    return int(row['count'] or 0)


def select_rows(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    sql = '''
        SELECT id, title, author, tags_json, categories_json, ai_metrics_json, ai_status
        FROM reader_works
        WHERE ai_status != 'running'
    '''
    params: list[object] = []
    if args.status == 'done':
        sql += " AND ai_status = 'done'"
    sql += ' ORDER BY id ASC'
    if args.limit > 0:
        sql += ' LIMIT ?'
        params.append(args.limit)
    return conn.execute(sql, params).fetchall()


def update_row(conn: sqlite3.Connection, row: sqlite3.Row, normalized_tags: list[str]) -> None:
    metrics = safe_json_loads(row['ai_metrics_json'], {})
    if not isinstance(metrics, dict):
        metrics = {}
    metrics = normalize_score_metrics(metrics)
    metrics['analysis_tags_normalized_at'] = now_iso()
    metrics['analysis_tags_vocabulary'] = 'reader-v1'
    conn.execute(
        '''
        UPDATE reader_works
        SET tags_json = ?, ai_metrics_json = ?, updated_at = ?
        WHERE id = ?
        ''',
        (
            json.dumps(normalized_tags, ensure_ascii=False),
            json.dumps(metrics, ensure_ascii=False),
            now_iso(),
            row['id'],
        ),
    )


def main() -> int:
    args = parse_args()
    conn = db(args.db)
    before_distinct = distinct_tag_count(conn)
    rows = select_rows(conn, args)
    scanned = 0
    changed = 0
    unchanged = 0
    emptied = 0
    simulated_distinct_tags = set()
    samples = []
    for row in rows:
        scanned += 1
        categories = safe_json_loads(row['categories_json'], [])
        old_tags = safe_json_loads(row['tags_json'], [])
        new_tags = normalize_reader_tags(old_tags, categories, max_tags=8)
        simulated_distinct_tags.update(new_tags)
        if new_tags == old_tags:
            unchanged += 1
            continue
        changed += 1
        if not new_tags:
            emptied += 1
        if len(samples) < args.sample_size:
            samples.append(
                {
                    'id': row['id'],
                    'title': row['title'],
                    'author': row['author'],
                    'old_tags': old_tags,
                    'new_tags': new_tags,
                }
            )
        if not args.dry_run:
            update_row(conn, row, new_tags)

    if not args.dry_run:
        conn.commit()
    after_distinct = len(simulated_distinct_tags) if args.dry_run else distinct_tag_count(conn)
    report = {
        'created_at': now_iso(),
        'db_path': str(Path(args.db).resolve()),
        'status_scope': args.status,
        'dry_run': args.dry_run,
        'vocabulary': 'reader-v1',
        'distinct_tags_before': before_distinct,
        'distinct_tags_after': after_distinct,
        'scanned': scanned,
        'changed': changed,
        'unchanged': unchanged,
        'emptied': emptied,
        'samples': samples,
    }
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f'tag_normalize_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**{k: report[k] for k in report if k != 'samples'}, 'report_path': str(report_path)}, ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
