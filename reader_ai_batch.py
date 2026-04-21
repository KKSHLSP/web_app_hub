from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from reader_ai import (
    DEFAULT_SPREAD_CHUNK_CHAR_LIMIT,
    DEFAULT_SPREAD_CHUNK_COUNT,
    DEFAULT_WHOLE_CHAR_LIMIT,
    score_work,
)
from reader_core import BASE_DIR, get_reader_settings, safe_json_loads

DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'hub.db'
RUNS_DIR = DATA_DIR / 'reader_ai_runs'
LATEST_RECORDS_DIR = DATA_DIR / 'reader_ai_records'
SKIP_TITLES = {'介绍', '内容简介', '简介', '说明', '新建文本文档', '网址', '目录', '封面'}
SKIP_TITLE_KEYWORDS = ('作品集', '作品列表')


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Batch score reader works with the local AI endpoint.')
    parser.add_argument('--db', default=str(DB_PATH), help='SQLite database path')
    parser.add_argument('--run-dir', default='', help='Directory for this batch run')
    parser.add_argument('--limit', type=int, default=0, help='Maximum number of works to process, 0 means no limit')
    parser.add_argument('--start-id', type=int, default=0, help='Only process works with id >= this value')
    parser.add_argument('--retry-failed', action='store_true', help='Retry failed items too')
    parser.add_argument('--include-done', action='store_true', help='Also process works already marked done')
    parser.add_argument('--mode', choices=('auto', 'whole', 'spread'), default='auto', help='Input strategy')
    parser.add_argument('--whole-char-limit', type=int, default=DEFAULT_WHOLE_CHAR_LIMIT, help='Whole-text strategy limit')
    parser.add_argument('--spread-chunk-count', type=int, default=DEFAULT_SPREAD_CHUNK_COUNT, help='Spread strategy chunk count')
    parser.add_argument('--spread-chunk-char-limit', type=int, default=DEFAULT_SPREAD_CHUNK_CHAR_LIMIT, help='Spread strategy chunk size')
    parser.add_argument('--sample-profile', choices=('focused', 'segmented', 'weighted'), default='focused', help='Spread sampling profile')
    parser.add_argument('--timeout', type=int, default=120, help='Network timeout in seconds')
    parser.add_argument('--retry-count', type=int, default=2, help='Retry count for a single work after the first attempt fails')
    parser.add_argument('--retry-backoff-sec', type=float, default=2.5, help='Delay between retries')
    parser.add_argument('--max-consecutive-failures', type=int, default=8, help='Stop the batch if too many works fail in a row')
    parser.add_argument('--quality-tier', choices=('low', 'standard', 'high'), default='standard', help='Quality marker stored with AI metrics')
    parser.add_argument('--quality-preset', default='', help='Short preset name stored with AI metrics')
    parser.add_argument('--quality-note', default='', help='Human-readable quality note stored with AI metrics')
    parser.add_argument('--only-quality-tier', choices=('low', 'standard', 'high'), default='', help='Only reprocess rows marked with this quality tier')
    parser.add_argument('--sleep-sec', type=float, default=0.0, help='Optional delay between works')
    parser.add_argument('--ids', default='', help='Comma-separated explicit work ids')
    return parser.parse_args()


def db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def merge_ai_categories(primary_category, existing_categories):
    categories = [primary_category] if primary_category else []
    for category in existing_categories or []:
        if category and category not in categories:
            categories.append(category)
    return categories[:6]


def select_rows(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    explicit_ids = [int(item) for item in args.ids.split(',') if item.strip().isdigit()]
    if explicit_ids:
        placeholders = ','.join('?' for _ in explicit_ids)
        sql = f'''
            SELECT id, relpath, title, author, categories_json, tags_json, char_count, ai_status, ai_score
            FROM reader_works
            WHERE id IN ({placeholders})
            ORDER BY id ASC
        '''
        return conn.execute(sql, explicit_ids).fetchall()

    sql = '''
        SELECT id, relpath, title, author, categories_json, tags_json, char_count, ai_status, ai_score
        FROM reader_works
        WHERE id >= ?
    '''
    params: list[object] = [args.start_id]
    if SKIP_TITLES:
        placeholders = ','.join('?' for _ in SKIP_TITLES)
        sql += f' AND title NOT IN ({placeholders})'
        params.extend(sorted(SKIP_TITLES))
    for keyword in SKIP_TITLE_KEYWORDS:
        sql += ' AND title NOT LIKE ?'
        params.append(f'%{keyword}%')
    if args.only_quality_tier:
        sql += " AND json_extract(ai_metrics_json, '$.analysis_quality') = ?"
        params.append(args.only_quality_tier)
    elif not args.include_done:
        if args.retry_failed:
            sql += " AND (ai_status != 'done' OR ai_score IS NULL)"
        else:
            sql += " AND (ai_status = 'pending' OR ai_status IS NULL OR ai_score IS NULL)"
    sql += '''
        ORDER BY
            CASE WHEN char_count <= ? THEN 0 ELSE 1 END,
            char_count ASC,
            id ASC
    '''
    params.append(args.whole_char_limit)
    if args.limit > 0:
        sql += ' LIMIT ?'
        params.append(args.limit)
    return conn.execute(sql, params).fetchall()


def build_metrics(payload: dict[str, object], args: argparse.Namespace) -> dict[str, object]:
    result = payload.get('result') or {}
    meta = payload.get('meta') or {}
    scores = dict(result.get('scores') or {})
    scores.update(
        {
            'analysis_quality': args.quality_tier,
            'analysis_preset': args.quality_preset or args.quality_tier,
            'analysis_quality_note': args.quality_note,
            'analysis_strategy': meta.get('strategy'),
            'analysis_sample_profile': meta.get('sample_profile'),
            'analysis_source_char_count': meta.get('source_char_count'),
            'analysis_text_char_count': meta.get('text_char_count'),
            'analysis_summary_source': meta.get('summary_source') or '',
            'analysis_has_source_synopsis': bool(meta.get('has_source_synopsis')),
            'analysis_source_synopsis_source': meta.get('source_synopsis_source') or '',
            'analysis_source_synopsis_char_count': meta.get('source_synopsis_char_count') or 0,
            'analysis_elapsed_sec': meta.get('elapsed_sec'),
            'analysis_scored_by': meta.get('resolved_model') or '',
            'analysis_has_reasoning_content': bool(meta.get('has_reasoning_content')),
        }
    )
    return scores


def update_row(conn: sqlite3.Connection, row: sqlite3.Row, payload: dict[str, object], args: argparse.Namespace) -> None:
    result = payload.get('result') or {}
    meta = payload.get('meta') or {}
    existing_categories = safe_json_loads(row['categories_json'], [])
    categories = merge_ai_categories(result.get('primary_category'), existing_categories)
    metrics = build_metrics(payload, args)
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
            json.dumps(metrics, ensure_ascii=False),
            result.get('reason'),
            meta.get('resolved_model') or '',
            'done',
            now_iso(),
            now_iso(),
            row['id'],
        ),
    )


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + '\n')


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> int:
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_RECORDS_DIR.mkdir(parents=True, exist_ok=True)

    run_dir = Path(args.run_dir).expanduser() if args.run_dir else RUNS_DIR / f'run_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    records_dir = run_dir / 'records'
    results_path = run_dir / 'results.jsonl'
    errors_path = run_dir / 'errors.jsonl'
    state_path = run_dir / 'state.json'
    run_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    conn = db(args.db)
    settings = get_reader_settings(conn)
    rows = select_rows(conn, args)
    config_payload = {
        'started_at': now_iso(),
        'db_path': str(Path(args.db).resolve()),
        'row_count': len(rows),
        'settings': {
            'ai_url': settings['reader_ai_url'],
            'ai_model': settings['reader_ai_model'],
            'has_ai_token': bool(settings['reader_ai_token']),
        },
        'args': vars(args),
    }
    write_json(run_dir / 'config.json', config_payload)
    write_json(
        state_path,
        {
            'updated_at': now_iso(),
            'run_dir': str(run_dir),
            'total_rows': len(rows),
            'processed': 0,
            'success_count': 0,
            'failure_count': 0,
            'consecutive_failures': 0,
            'status': 'starting',
        },
    )

    success_count = 0
    failure_count = 0
    consecutive_failures = 0
    for index, row in enumerate(rows, 1):
        started_at = now_iso()
        try:
            conn.execute(
                'UPDATE reader_works SET ai_status = ?, updated_at = ? WHERE id = ?',
                ('running', now_iso(), row['id']),
            )
            conn.commit()
            last_error = ''
            payload = None
            for attempt in range(args.retry_count + 1):
                if attempt > 0:
                    time.sleep(args.retry_backoff_sec)
                try:
                    payload = score_work(
                        file_path=BASE_DIR / row['relpath'],
                        title=row['title'],
                        author=row['author'],
                        relpath=row['relpath'],
                        categories=safe_json_loads(row['categories_json'], []),
                        tags=safe_json_loads(row['tags_json'], []),
                        url=settings['reader_ai_url'],
                        model=settings['reader_ai_model'],
                        token=settings['reader_ai_token'],
                        timeout=args.timeout,
                        mode=args.mode,
                        whole_char_limit=args.whole_char_limit,
                        spread_chunk_count=args.spread_chunk_count,
                        spread_chunk_char_limit=args.spread_chunk_char_limit,
                        sample_profile=args.sample_profile,
                    )
                    break
                except Exception as exc:
                    last_error = str(exc)
                    if attempt >= args.retry_count:
                        raise
                    print(
                        f'[{index}/{len(rows)}] RETRY #{row["id"]} {row["title"]} '
                        f'attempt {attempt + 2}/{args.retry_count + 1}: {last_error}',
                        flush=True,
                    )
            if payload is None:
                raise RuntimeError(last_error or 'AI scoring returned no payload')
            payload_meta = payload.setdefault('meta', {})
            payload_meta['analysis_quality'] = args.quality_tier
            payload_meta['analysis_preset'] = args.quality_preset or args.quality_tier
            payload_meta['analysis_quality_note'] = args.quality_note
            update_row(conn, row, payload, args)
            conn.commit()
            success_count += 1
            consecutive_failures = 0
            record = {
                'work_id': row['id'],
                'title': row['title'],
                'author': row['author'],
                'relpath': row['relpath'],
                'char_count': row['char_count'],
                'started_at': started_at,
                'finished_at': now_iso(),
                'attempts': attempt + 1,
                **payload,
            }
            write_json(records_dir / f'{row["id"]}.json', record)
            write_json(LATEST_RECORDS_DIR / f'{row["id"]}.json', record)
            append_jsonl(results_path, record)
            print(
                f'[{index}/{len(rows)}] DONE #{row["id"]} {row["title"]} '
                f'via {(payload.get("meta") or {}).get("strategy")} '
                f'in {(payload.get("meta") or {}).get("elapsed_sec")}s',
                flush=True,
            )
        except Exception as exc:
            conn.execute(
                'UPDATE reader_works SET ai_status = ?, ai_reason = ?, updated_at = ? WHERE id = ?',
                ('failed', str(exc)[:400], now_iso(), row['id']),
            )
            conn.commit()
            failure_count += 1
            consecutive_failures += 1
            error_record = {
                'work_id': row['id'],
                'title': row['title'],
                'author': row['author'],
                'relpath': row['relpath'],
                'char_count': row['char_count'],
                'started_at': started_at,
                'finished_at': now_iso(),
                'ok': False,
                'error': str(exc),
            }
            write_json(records_dir / f'{row["id"]}.json', error_record)
            write_json(LATEST_RECORDS_DIR / f'{row["id"]}.json', error_record)
            append_jsonl(errors_path, error_record)
            print(f'[{index}/{len(rows)}] FAIL #{row["id"]} {row["title"]}: {exc}', flush=True)

        state_payload = {
            'updated_at': now_iso(),
            'run_dir': str(run_dir),
            'total_rows': len(rows),
            'processed': index,
            'success_count': success_count,
            'failure_count': failure_count,
            'consecutive_failures': consecutive_failures,
            'last_work_id': row['id'],
            'last_title': row['title'],
            'status': 'running',
        }
        write_json(state_path, state_payload)
        if args.max_consecutive_failures > 0 and consecutive_failures >= args.max_consecutive_failures:
            write_json(
                state_path,
                {
                    'updated_at': now_iso(),
                    'run_dir': str(run_dir),
                    'total_rows': len(rows),
                    'processed': index,
                    'success_count': success_count,
                    'failure_count': failure_count,
                    'consecutive_failures': consecutive_failures,
                    'last_work_id': row['id'],
                    'last_title': row['title'],
                    'status': 'aborted',
                },
            )
            write_json(
                run_dir / 'summary.json',
                {
                    'started_at': config_payload['started_at'],
                    'finished_at': now_iso(),
                    'total_rows': len(rows),
                    'success_count': success_count,
                    'failure_count': failure_count,
                    'results_path': str(results_path),
                    'errors_path': str(errors_path),
                    'status': 'aborted',
                    'abort_reason': f'Hit {consecutive_failures} consecutive failures.',
                },
            )
            conn.close()
            return 2
        if args.sleep_sec > 0:
            time.sleep(args.sleep_sec)

    write_json(
        state_path,
        {
            'updated_at': now_iso(),
            'run_dir': str(run_dir),
            'total_rows': len(rows),
            'processed': len(rows),
            'success_count': success_count,
            'failure_count': failure_count,
            'consecutive_failures': consecutive_failures,
            'status': 'completed',
        },
    )
    write_json(
        run_dir / 'summary.json',
        {
            'started_at': config_payload['started_at'],
            'finished_at': now_iso(),
            'total_rows': len(rows),
            'success_count': success_count,
            'failure_count': failure_count,
            'results_path': str(results_path),
            'errors_path': str(errors_path),
            'status': 'completed',
        },
    )
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
