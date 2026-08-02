"""候補抽出ロジック（設計書 2_design.md 3節に対応）

各情報源から集めた生の記事一覧（articles）から、Claudeに渡す「候補記事」を
絞り込むための処理。ソースによって「人気度スコア」の有無・尺度がバラバラなので
（例: Qiitaはいいね数、HackerNewsはポイント数、RSS系はスコア自体が取得できない）、
そのままでは公平に比較できない。そこで、
  1. ソースごとにスコアを0〜1の範囲に正規化する（min-max正規化）
  2. スコアが無いソースは、記事の新しさ（鮮度）で代わりのスコアを付ける
という2段構えで、全ソース横断で比較可能な"_norm_score"を各記事に付与し、
それで並び替えて上位N件を候補として返す。
"""
from datetime import datetime, timezone

from dateutil import parser as dateparser


def _parse_date(value: str) -> datetime:
    """公開日時の文字列をdatetimeに変換する。パース失敗・空文字の場合は
    「今」を返すことで、日付不明な記事が新着記事として不当に有利/不利に
    ならないよう暫定的に扱う。タイムゾーン情報が無い場合はUTC扱いにする。"""
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
    # まずソースごとに記事をグループ分けする（同じソース内でのみスコアを比較するため）
    by_source: dict[str, list[dict]] = {}
    for article in articles:
        by_source.setdefault(article["source"], []).append(article)

    now = datetime.now(timezone.utc)
    for items in by_source.values():
        scores = [a["popularity_score"] for a in items if a.get("popularity_score") is not None]

        if scores:
            # このソースには人気度スコアがある（例: Qiitaのいいね数、HackerNewsのポイント）
            # → min-max正規化で0〜1の範囲にスケーリングする
            lo, hi = min(scores), max(scores)
            for a in items:
                if a.get("popularity_score") is None:
                    a["_norm_score"] = 0.0
                elif hi > lo:
                    a["_norm_score"] = (a["popularity_score"] - lo) / (hi - lo)
                else:
                    # このソース内の全記事が同じスコア（差がつかない）場合は一律満点にする
                    a["_norm_score"] = 1.0
        else:
            # このソースには人気度スコアが無い（RSS系フィード等）
            # → 代わりに「公開からの経過時間が短いほど高スコア」というロジックで代替する。
            #   1週間（24時間×7日）経過するとスコアは0に近づいていく。
            for a in items:
                published = _parse_date(a.get("published_at", ""))
                age_hours = max((now - published).total_seconds() / 3600, 0)
                a["_norm_score"] = max(0.0, 1.0 - age_hours / (24 * 7))


def build_candidates(articles: list[dict], limit: int = 50, days: int = 7) -> list[dict]:
    """全ソース分の生の記事一覧から、Claudeに渡す候補記事を絞り込む。

    1. 直近days日以内に公開された記事だけに絞る（古すぎる記事を除外）
    2. _normalize_scores()でスコアを正規化する
    3. スコアの高い順に並び替え、上位limit件だけを返す
    """
    now = datetime.now(timezone.utc)
    recent = [a for a in articles if (now - _parse_date(a.get("published_at", ""))).days <= days]

    _normalize_scores(recent)
    recent.sort(key=lambda a: a.get("_norm_score", 0), reverse=True)
    return recent[:limit]
