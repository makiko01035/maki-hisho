"""
Phase 4R: リライトエージェント
既存記事を睡眠×医師×薬膳（補助）軸でリライトする
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(Path(__file__).parent.parent / ".env")
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

REWRITE_RULES = """
【リライトの目的】
foodmakehealth.comの記事を「睡眠×医師×薬膳（補助）」軸に最適化する。
元記事が薬膳レシピ主体であっても、必ず睡眠との接続を軸に再構成する。

【リライト7原則】
1. 睡眠への接続：薬膳・食材の話は必ず「睡眠への効果」に結びつける
2. 医師の視点を追加：「外来でよく聞く悩みです」「医師として実践しています」等
3. 構成の最適化：共感→原因→改善→薬膳補助→商品→まとめ の流れに合わせる
4. SEO強化：タイトルに「医師監修」または「医師が解説」を含める（32文字以内）
5. AI感排除7項目を全項目クリアする（下記参照）
6. CTA追加：記事末尾に「<!-- yakuzen-affiliate-cta -->」を追加
7. 受診案内：まとめに「2週間改善しなければ内科・睡眠外来へ」を含める

【AI感排除7項目】
1. あいまいな結論を排除（「可能性があります」→具体的シナリオで記述）
2. 「重要です」は記事全体で3回以内
3. 文末パターンにバリエーション（です/ます/でしょう/体言止めを交互に）
4. 3連続箇条書き禁止（間にリード文やテーブルを挟む）
5. 「これにより」を完全削除
6. 「おすすめします」→「試してみてください」「取り入れてみましょう」
7. 一人称の体験談を2箇所以上（「外来でよく聞くのですが」「私自身も〇〇を実践しています」）
"""

ARTICLE_TEMPLATE = """
① 共感（H2）
- 読者の悩みを会話体で言語化（「〜していませんか？」）
- 2〜3行で共感→「原因があります」へ橋渡し

② 原因（H2）医学的＋生活習慣
- H3で3〜4個に分けて解説
- 医学的メカニズム（ホルモン・神経・体温など）をわかりやすく

③ 改善方法（H2）すぐできる
- H3で3〜7個の具体的アクション
- 「今夜から」「明日の朝から」など即実践できるレベルで書く

④ 薬膳的アプローチ（H2）補助として
- 東洋医学の体質タイプ名と簡単な説明
- H3：おすすめ食材を表形式で（食材・働き・使い方）
- 「補助として」のトーンを維持する

⑤ おすすめ商品・食品（H2）
- H3ごとに1商品：食品系・サプリ系・寝具系を状況に応じて紹介（テキストのみ・プレースホルダー不要）
- 医師コメントを添えて自然な推薦にする

⑥ まとめ（H2）
- STEP形式で「今夜から試す3ステップ」
- 「2週間改善しなければ内科・睡眠外来へ」の案内を必ず入れる
- 関連記事への内部リンクを2〜3本
"""


def run(keyword, original_article, output_dir="articles"):
    """Phase 4Rを実行（既存記事のリライト）"""
    slug = keyword.replace(' ', '_').replace('　', '_').replace('/', '_')
    article_dir = Path(output_dir) / slug
    article_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[Phase 4R] リライトエージェント起動")
    print(f"  キーワード: {keyword}")
    print(f"  元記事文字数: {len(original_article)}字")
    print(f"  リライト中...")

    original_section = f"""【元記事（リライト元）】
{original_article[:4000]}""" if original_article else "【元記事】なし（新規に近い形でリライト）"

    response = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=4000,
        messages=[{
            'role': 'user',
            'content': f"""あなたは内科医・睡眠外来担当医であり、薬膳の専門知識を持つライターです。
以下の既存記事を、foodmakehealth.comの方針に沿ってリライトしてください。

キーワード：{keyword}

{original_section}

【リライト方針】
{REWRITE_RULES}

【記事構成（この流れに再構成する）】
{ARTICLE_TEMPLATE}

【ターゲット】
30〜50代女性（睡眠の悩み・更年期・疲れが取れないなど）

【文章ルール】
- ですます調で統一（「だ/である/〜した」調は一切禁止）
- 1文は50字以内
- 段落は3行以内
- 2000〜2500文字
- Markdown形式、最初の行は「# タイトル」（32字以内・KW含む・医師監修or医師が解説を含む）
- 記事末尾に「<!-- yakuzen-affiliate-cta -->」を1行追加

出力：リライト後の記事全文のみ（説明文不要）"""
        }]
    )

    draft = response.content[0].text.strip()

    output_path = article_dir / "phase4_draft.md"
    output_path.write_text(draft, encoding='utf-8')

    title_line = draft.split('\n')[0].replace('# ', '')
    print(f"\n[Phase 4R] 完了 ✅")
    print(f"  タイトル: {title_line}")
    print(f"  文字数: {len(draft)}字")
    print(f"  保存先: {output_path}")

    return draft, str(output_path)
