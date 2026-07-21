"""候補抽出ロジック（設計書 2_design.md 3節に対応）"""
from datetime import datetime, timezone

from dateutil import parser as dateparser


def _parse_date(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = dateparser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:  # noqa: BLE001 - 日付不明な記事は「今」扱いにして落とさない
        return datetime.now(timezone.utc)


def _normalize_scores(articles: list[dict]) -> None:
    """popularity_scoreをソースごとにmin-max正規化。
    無いソースは公開日時の新しさで代替スコアを付与する。
    結果は各記事の "_norm_score" に書き込む"""
    by_source: dict[str, list[dict]] = {}
    for article in articles:
        by_source.setdefault(article["source"], []).append(article)

    now = datetime.now(timezone.utc)
    for items in by_source.values():
        scores = [a["popularity_score"] for a in items if a.get("popularity_score") is not None]

        if scores:
            lo, hi = min(scores), max(scores)
            for a in items:
                if a.get("popularity_score") is None:
                    a["_norm_score"] = 0.0
                elif hi > lo:
                    a["_norm_score"] = (a["popularity_score"] - lo) / (hi - lo)
                else:
                    a["_norm_score"] = 1.0
        else:
            for a in items:
                published = _parse_date(a.get("published_at", ""))
                age_hours = max((now - published).total_seconds() / 3600, 0)
                a["_norm_score"] = max(0.0, 1.0 - age_hours / (24 * 7))


def build_candidates(articles: list[dict], limit: int = 50, days: int = 7) -> list[dict]:
    now = datetime.now(timezone.utc)
    recent = [a for a in articles if (now - _parse_date(a.get("published_at", ""))).days <= days]

    _normalize_scores(recent)
    recent.sort(key=lambda a: a.get("_norm_score", 0), reverse=True)
    return recent[:limit]
