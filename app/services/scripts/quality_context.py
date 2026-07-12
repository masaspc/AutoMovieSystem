"""学習動画の台本構造と、過去動画の維持率Insightを次回生成へ渡す。"""

from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session

from app.models.insight import Insight
from app.models.publication import Publication
from app.models.topic import Topic
from app.models.video_project import VideoProject

_VISUAL_REMEDIES = {
    "dialogue": "掛け合いを短くし、質問の直後にコード・図・結果を見せる",
    "code": "コードを一度に見せず、予想→実行行の強調→出力→修正の順に段階表示する",
    "key_point": "抽象語を減らし、具体例と一つの結論に絞る",
    "quiz": "問題文と選択肢を短くし、回答後に理由と応用例を示す",
    "diagram": "要素を一つずつ追加し、音声と対応する箇所だけを強調する",
    "steps": "現在の手順を明示し、各手順の完了結果を画面で確認する",
    "chart": "比較軸と結論を先に示し、読み取る数値を一つずつ強調する",
}


def build_learning_story_context() -> str:
    """説明の羅列を避け、予想と実演を含む学習ドラマの必須構造を返す。"""
    return (
        "\n\n[学習継続のための台本構造]\n"
        "説明だけを順番に並べず、動画全体で次の学習ドラマを作ってください。\n"
        "1. 冒頭15秒以内に、視聴者が困る具体的状況と完成後にできることを示す。\n"
        "2. zundamonが初心者にありがちな誤解または予想を一つ述べる。\n"
        "3. 視聴者にも結果を予想してもらってから、コード・操作・例を実演する。\n"
        "4. 失敗例を扱う場合は捏造せず、再現可能な入力と結果を示して原因を説明する。\n"
        "5. 修正前後を比較し、成功した理由をmetanが一文で言語化する。\n"
        "6. 終盤に新しい例へ応用する確認問題を一つ入れ、答えだけでなく理由も示す。\n"
        "7. 最後に今回できるようになったこと、30秒以内で試せる課題、次回との接続を示す。\n"
        "感情は内容に対応させ、疑問・予想外の結果はsurprised、注意点はserious、"
        "成功と理解はhappy、通常説明はneutralを使ってください。"
    )


def _retention_insights_for_channel(
    session: Session, *, channel_id: str, limit: int = 50
) -> list[Insight]:
    return (
        session.query(Insight)
        .join(Publication, Insight.source_id == Publication.id)
        .join(VideoProject, Publication.video_project_id == VideoProject.id)
        .join(Topic, VideoProject.topic_id == Topic.id)
        .filter(
            Insight.source_type == "publication",
            Insight.insight_type == "retention_scene_dip",
            Insight.confidence >= 0.6,
            Topic.channel_id == channel_id,
        )
        .order_by(Insight.created_at.desc())
        .limit(limit)
        .all()
    )


def build_retention_feedback_context(session: Session, topic_id: str) -> str:
    """同じチャンネルの離脱傾向を集約し、次回台本で実行できる制約へ変換する。"""
    topic = session.get(Topic, topic_id)
    if topic is None:
        return ""
    insights = _retention_insights_for_channel(session, channel_id=topic.channel_id)
    visual_types = [str((insight.evidence or {}).get("visual_type") or "") for insight in insights]
    counts = Counter(value for value in visual_types if value)
    repeated = [(visual_type, count) for visual_type, count in counts.most_common() if count >= 2]
    if not repeated:
        return ""
    lines = []
    for visual_type, count in repeated[:3]:
        remedy = _VISUAL_REMEDIES.get(
            visual_type, "同じ見せ方を長く続けず、具体例と別形式の画面を組み合わせる"
        )
        lines.append(f"- {visual_type}: 離脱場面{count}件。次回は{remedy}。")
    return (
        "\n\n[過去動画の視聴維持率から得た改善制約]\n"
        "以下は同じチャンネルの実測データです。内容の正確性を損なわない範囲で反映し、"
        "単に派手な演出を増やさないでください。\n"
        f"{chr(10).join(lines)}"
    )


def build_quality_context(session: Session, topic_id: str) -> str:
    return build_learning_story_context() + build_retention_feedback_context(session, topic_id)
