from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping

from reader_core import normalize_reader_tags, safe_json_loads


READER_SCORE_SCHEMA_VERSION = 'reader-score-v1'
READER_TAG_VOCABULARY_VERSION = 'reader-v1'
READER_SCORE_KEYS = ('overall', 'emotion', 'chemistry', 'spice', 'readability')
READER_REQUIRED_SCORE_KEYS = set(READER_SCORE_KEYS)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def row_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def clamp_score(value: Any, fallback: int = 0) -> int:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        numeric = fallback
    return max(0, min(100, numeric))


def normalize_score_metrics(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(metrics or {})
    payload['analysis_schema_version'] = READER_SCORE_SCHEMA_VERSION
    payload['analysis_tags_vocabulary'] = READER_TAG_VOCABULARY_VERSION
    payload['analysis_score_keys'] = list(READER_SCORE_KEYS)
    return payload


def scores_from_metrics(metrics: Mapping[str, Any], fallback: Any = 0) -> dict[str, int]:
    fallback_score = clamp_score(fallback, 0)
    overall = clamp_score(metrics.get('overall'), fallback_score)
    return {
        'overall': overall,
        'emotion': clamp_score(metrics.get('emotion'), overall),
        'chemistry': clamp_score(metrics.get('chemistry'), overall),
        'spice': clamp_score(metrics.get('spice'), overall),
        'readability': clamp_score(metrics.get('readability'), overall),
    }


def build_reader_score_record(row: Mapping[str, Any], exported_at: str | None = None) -> dict[str, Any]:
    exported_at = exported_at or utc_now_iso()
    metrics = normalize_score_metrics(safe_json_loads(row_value(row, 'ai_metrics_json'), {}))
    categories = safe_json_loads(row_value(row, 'categories_json'), [])
    tags = normalize_reader_tags(safe_json_loads(row_value(row, 'tags_json'), []), categories, max_tags=8)
    scores = scores_from_metrics(metrics, row_value(row, 'ai_score') or row_value(row, 'heuristic_score') or 0)
    primary_category = row_value(row, 'primary_category') or (categories[0] if categories else '都市言情')
    summary = row_value(row, 'summary') or row_value(row, 'intro') or row_value(row, 'excerpt') or ''
    intro = row_value(row, 'intro') or row_value(row, 'summary') or ''
    return {
        'schema_version': READER_SCORE_SCHEMA_VERSION,
        'exported_at': exported_at,
        'work': {
            'id': row_value(row, 'id'),
            'relpath': row_value(row, 'relpath') or '',
            'title': row_value(row, 'title') or '',
            'author': row_value(row, 'author') or '',
            'chapter_count': int(row_value(row, 'chapter_count') or 0),
            'char_count': int(row_value(row, 'char_count') or 0),
            'ext': row_value(row, 'ext') or '',
            'encoding': row_value(row, 'encoding') or '',
        },
        'display': {
            'summary': summary,
            'intro': intro,
            'excerpt': row_value(row, 'excerpt') or '',
        },
        'classification': {
            'primary_category': primary_category,
            'categories': categories,
            'tags': tags,
            'tag_vocabulary': READER_TAG_VOCABULARY_VERSION,
        },
        'scores': scores,
        'recommendation': {
            'reason': row_value(row, 'ai_reason') or '',
            'quality': metrics.get('analysis_quality') or '',
            'preset': metrics.get('analysis_preset') or '',
            'quality_note': metrics.get('analysis_quality_note') or '',
            'summary_source': metrics.get('analysis_summary_source') or '',
        },
        'analysis': {
            'status': row_value(row, 'ai_status') or '',
            'model': row_value(row, 'ai_model') or metrics.get('analysis_scored_by') or '',
            'scored_at': row_value(row, 'ai_scored_at') or '',
            'schema_version': metrics.get('analysis_schema_version') or READER_SCORE_SCHEMA_VERSION,
            'strategy': metrics.get('analysis_strategy') or '',
            'sample_profile': metrics.get('analysis_sample_profile') or '',
            'source_char_count': int(metrics.get('analysis_source_char_count') or 0),
            'text_char_count': int(metrics.get('analysis_text_char_count') or 0),
            'has_source_synopsis': bool(metrics.get('analysis_has_source_synopsis')),
            'source_synopsis_source': metrics.get('analysis_source_synopsis_source') or '',
            'source_synopsis_char_count': int(metrics.get('analysis_source_synopsis_char_count') or 0),
            'elapsed_sec': metrics.get('analysis_elapsed_sec'),
            'scored_by': metrics.get('analysis_scored_by') or row_value(row, 'ai_model') or '',
            'has_reasoning_content': bool(metrics.get('analysis_has_reasoning_content')),
        },
    }


def validate_reader_score_record(record: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if record.get('schema_version') != READER_SCORE_SCHEMA_VERSION:
        errors.append('schema_version mismatch')
    for section in ('work', 'display', 'classification', 'scores', 'recommendation', 'analysis'):
        if not isinstance(record.get(section), dict):
            errors.append(f'missing section: {section}')
    scores = record.get('scores') if isinstance(record.get('scores'), dict) else {}
    missing_scores = READER_REQUIRED_SCORE_KEYS - set(scores)
    if missing_scores:
        errors.append(f'missing scores: {",".join(sorted(missing_scores))}')
    for key in READER_SCORE_KEYS:
        value = scores.get(key)
        if not isinstance(value, int) or value < 0 or value > 100:
            errors.append(f'invalid score: {key}')
    classification = record.get('classification') if isinstance(record.get('classification'), dict) else {}
    if classification.get('tag_vocabulary') != READER_TAG_VOCABULARY_VERSION:
        errors.append('tag_vocabulary mismatch')
    tags = classification.get('tags')
    if not isinstance(tags, list) or not tags:
        errors.append('tags must be a non-empty list')
    work = record.get('work') if isinstance(record.get('work'), dict) else {}
    if not work.get('id') or not work.get('title') or not work.get('relpath'):
        errors.append('work id/title/relpath required')
    return errors


def dumps_record(record: Mapping[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(',', ':'))
