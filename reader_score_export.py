from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from reader_core import BASE_DIR, safe_json_loads
from reader_score_schema import (
    READER_SCORE_SCHEMA_VERSION,
    build_reader_score_record,
    dumps_record,
    normalize_score_metrics,
    utc_now_iso,
    validate_reader_score_record,
)


DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'hub.db'
EXPORTS_DIR = DATA_DIR / 'reader_score_exports'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Export or validate reader AI scores using the stable reader-score-v1 JSON schema.')
    parser.add_argument('--db', default=str(DB_PATH), help='SQLite database path')
    parser.add_argument('--output', default='', help='Output path. Defaults to data/reader_score_exports/*.jsonl')
    parser.add_argument('--format', choices=('jsonl', 'json'), default='jsonl', help='Export format')
    parser.add_argument('--status', choices=('done', 'all'), default='done', help='Rows to export')
    parser.add_argument('--limit', type=int, default=0, help='Maximum number of rows, 0 means no limit')
    parser.add_argument('--validate-only', action='store_true', help='Validate records without writing an export file')
    parser.add_argument('--backfill-schema', action='store_true', help='Write schema/version metadata back into ai_metrics_json')
    return parser.parse_args()


def db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 30000')
    return conn


def select_rows(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    sql = '''
        SELECT
            id, relpath, title, author, ext, encoding, chapter_count, char_count,
            summary, intro, excerpt, tags_json, categories_json, primary_category,
            ai_score, heuristic_score, ai_metrics_json, ai_reason, ai_model, ai_status,
            ai_scored_at, updated_at
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


def backfill_schema_metadata(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    metrics = safe_json_loads(row['ai_metrics_json'], {})
    if not isinstance(metrics, dict):
        metrics = {}
    normalized = normalize_score_metrics(metrics)
    if normalized == metrics:
        return False
    conn.execute(
        'UPDATE reader_works SET ai_metrics_json = ?, updated_at = ? WHERE id = ?',
        (json.dumps(normalized, ensure_ascii=False), utc_now_iso(), row['id']),
    )
    return True


def default_output_path(fmt: str) -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = 'json' if fmt == 'json' else 'jsonl'
    return EXPORTS_DIR / f'reader_scores_{READER_SCORE_SCHEMA_VERSION}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.{suffix}'


def main() -> int:
    args = parse_args()
    conn = db(args.db)
    rows = select_rows(conn, args)
    exported_at = utc_now_iso()
    records = []
    error_samples = []
    invalid_count = 0
    schema_updates = 0

    for row in rows:
        if args.backfill_schema:
            schema_updates += int(backfill_schema_metadata(conn, row))
            row = conn.execute(
                '''
                SELECT
                    id, relpath, title, author, ext, encoding, chapter_count, char_count,
                    summary, intro, excerpt, tags_json, categories_json, primary_category,
                    ai_score, heuristic_score, ai_metrics_json, ai_reason, ai_model, ai_status,
                    ai_scored_at, updated_at
                FROM reader_works
                WHERE id = ?
                ''',
                (row['id'],),
            ).fetchone()
        record = build_reader_score_record(row, exported_at=exported_at)
        errors = validate_reader_score_record(record)
        if errors:
            invalid_count += 1
            if len(error_samples) < 20:
                error_samples.append({'id': row['id'], 'title': row['title'], 'errors': errors})
        records.append(record)

    if args.backfill_schema:
        conn.commit()

    output_path = ''
    if not args.validate_only:
        path = Path(args.output).expanduser() if args.output else default_output_path(args.format)
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.format == 'json':
            path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            with path.open('w', encoding='utf-8') as handle:
                for record in records:
                    handle.write(dumps_record(record) + '\n')
        output_path = str(path)

    summary = {
        'schema_version': READER_SCORE_SCHEMA_VERSION,
        'db_path': str(Path(args.db).resolve()),
        'status_scope': args.status,
        'validate_only': args.validate_only,
        'backfill_schema': args.backfill_schema,
        'row_count': len(rows),
        'invalid_count': invalid_count,
        'schema_updates': schema_updates,
        'output_path': output_path,
        'error_samples': error_samples,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    conn.close()
    return 1 if invalid_count else 0


if __name__ == '__main__':
    raise SystemExit(main())
