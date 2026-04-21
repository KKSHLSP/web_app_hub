from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from reader_ai import INTRO_MAX_CHARS, extract_source_synopsis, sanitize_no_spoiler_text
from reader_core import BASE_DIR, load_work_text_from_relpath, safe_json_loads
from reader_score_schema import normalize_score_metrics


DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'hub.db'
REPORTS_DIR = DATA_DIR / 'reader_synopsis_reports'


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Backfill reader summaries from source blurbs without AI calls.')
    parser.add_argument('--db', default=str(DB_PATH), help='SQLite database path')
    parser.add_argument('--status', choices=('done', 'all'), default='done', help='Rows to scan')
    parser.add_argument('--limit', type=int, default=0, help='Maximum number of rows to scan, 0 means no limit')
    parser.add_argument('--dry-run', action='store_true', help='Only report, do not update DB')
    parser.add_argument('--sample-size', type=int, default=12, help='Number of sample rows per bucket in the report')
    parser.add_argument('--report-dir', default=str(REPORTS_DIR), help='Directory for JSON reports')
    return parser.parse_args()


def db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 30000')
    return conn


def select_rows(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    sql = '''
        SELECT id, relpath, title, author, summary, intro, ai_metrics_json, ai_status
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


def append_sample(samples: list[dict[str, object]], item: dict[str, object], sample_size: int) -> None:
    if len(samples) < sample_size:
        samples.append(item)


def update_row(conn: sqlite3.Connection, row: sqlite3.Row, source_summary: str, source_kind: str) -> None:
    metrics = safe_json_loads(row['ai_metrics_json'], {})
    if not isinstance(metrics, dict):
        metrics = {}
    metrics = normalize_score_metrics(metrics)
    metrics['analysis_summary_source'] = 'source_synopsis'
    metrics['analysis_has_source_synopsis'] = True
    metrics['analysis_source_synopsis_source'] = source_kind
    metrics['analysis_source_synopsis_char_count'] = len(source_summary)
    metrics['analysis_synopsis_backfilled_at'] = now_iso()
    intro = sanitize_no_spoiler_text(source_summary, INTRO_MAX_CHARS)
    conn.execute(
        '''
        UPDATE reader_works
        SET summary = ?, intro = ?, ai_metrics_json = ?, updated_at = ?
        WHERE id = ?
        ''',
        (
            source_summary,
            intro,
            json.dumps(metrics, ensure_ascii=False),
            now_iso(),
            row['id'],
        ),
    )


def main() -> int:
    args = parse_args()
    conn = db(args.db)
    rows = select_rows(conn, args)
    scanned = 0
    has_source = 0
    no_source = 0
    already_source = 0
    replaceable = 0
    updated = 0
    failed = 0
    samples = {
        'has_source': [],
        'no_source': [],
        'updated': [],
        'failed': [],
    }

    for row in rows:
        scanned += 1
        try:
            text, _encoding = load_work_text_from_relpath(row['relpath'])
            source = extract_source_synopsis(text)
            source_summary = str(source.get('summary') or '')
            source_kind = str(source.get('source') or '')
            current_metrics = safe_json_loads(row['ai_metrics_json'], {})
            current_source = current_metrics.get('analysis_summary_source') if isinstance(current_metrics, dict) else ''
            item = {
                'id': row['id'],
                'title': row['title'],
                'author': row['author'],
                'status': row['ai_status'],
                'source': source_kind,
                'summary_chars': len(source_summary),
            }
            if source_summary:
                has_source += 1
                append_sample(samples['has_source'], {**item, 'summary': source_summary[:180]}, args.sample_size)
                if current_source == 'source_synopsis' and row['summary'] == source_summary:
                    already_source += 1
                    continue
                replaceable += 1
                append_sample(samples['updated'], {**item, 'old_summary': str(row['summary'] or '')[:120], 'new_summary': source_summary[:180]}, args.sample_size)
                if not args.dry_run:
                    update_row(conn, row, source_summary, source_kind)
                    updated += 1
            else:
                no_source += 1
                append_sample(samples['no_source'], item, args.sample_size)
        except Exception as exc:
            failed += 1
            append_sample(
                samples['failed'],
                {
                    'id': row['id'],
                    'title': row['title'],
                    'author': row['author'],
                    'relpath': row['relpath'],
                    'error': str(exc),
                },
                args.sample_size,
            )

    if not args.dry_run:
        conn.commit()

    report = {
        'created_at': now_iso(),
        'db_path': str(Path(args.db).resolve()),
        'status_scope': args.status,
        'dry_run': args.dry_run,
        'scanned': scanned,
        'has_source_synopsis': has_source,
        'no_source_synopsis': no_source,
        'already_source_synopsis': already_source,
        'replaceable_with_source_synopsis': replaceable,
        'updated': updated,
        'failed': failed,
        'samples': samples,
    }
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f'synopsis_backfill_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**{k: report[k] for k in report if k != 'samples'}, 'report_path': str(report_path)}, ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
