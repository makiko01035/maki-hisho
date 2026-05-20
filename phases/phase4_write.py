"""
Phase 4: 執筆エージェント
Phase 3の構成案をもとに本文を生成する（AI感排除7項目込み）
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(Path(__file__).parent.parent / ".env")
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

ARTICLE_TEMPLATE = """
① 共感（H2）
- 読者の悩みを会話体で言語化（「〜していませんか？」）
- 2〜3行で共感→「原因があります」へ橋渡し

② 原因（H2）医学的＋生活習慣
- H3で3〜4個に分けて解説
- 医学的メカニズム（ホルモン・神経・体温など）をわかりやすく
- 「なるほど」と思える専門情報を入れてE-E-A-Tを高める

③ 改善方法（H2）すぐできる
- H3で3〜7個の具体的アクション
- 箇条書き・表を積極的に使う
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

AI_REMOVE_RULES = """
【AI感排除7項目チェック】必ず全項目を確認・修正すること：

1. あいまいな結論を排除
   NG: 「効果がある場合もあります」「可能性があります」
   OK: 「〇〇の場合は△△が効果的です」（具体的シナリオで記述）

2. 「重要です」の連発禁止（記事中3回以内）
   NG: 「〜することが重要です」が4回以上
   OK: 具体的なアクションや数値に置き換える

3. 文末パターンに必ずバリエーションをつける
   NG: 全段落が「〜です。」「〜ます。」で終わる
   OK: 「です」「ます」「でしょう」「体言止め」を交互に使う

4. 3連続箇条書き禁止
   NG: 箇条書き→箇条書き→箇条書き
   OK: 箇条書きの間にリード文やテーブルを挟む

5. 「これにより」を完全削除
   NG: 「これにより効率が向上します」
   OK: 因果関係を具体的な文章で記述する

6. 「おすすめします」を置き換える
   NG: 「検討することをおすすめします」
   OK: 「試してみてください」「取り入れてみましょう」

7. 一人称の体験談を2箇所以上入れる
   NG: 第三者視点のみの説明
   OK: 「睡眠外来で患者さんからよく聞くのですが」「私自身も〇〇を実践しています」
"""


def run(keyword, design_md, output_dir="articles"):
    """Phase 4を実行して結果をファイルに保存する"""
    slug = keyword.replace(' ', '_').replace('　', '_').replace('/', '_')
    article_dir = Path(output_dir) / slug
    article_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[Phase 4] 執筆エージェント起動")
    print(f"  キーワード: {keyword}")
    print(f"  Phase 3の構成案を読み込み済み")
    print(f"  執筆中...")

    response = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=4000,
        messages=[{
            'role': 'user',
            'content': f"""あなたは内科医・睡眠外来担当医であり、薬膳の専門知識を持つライターです。
foodmakehealth.comのブログ記事を書いてください。

キーワード：{keyword}

【Phase 3で確定した記事構成】
{design_md}

【ライター設定（役割）】
- 内科医・睡眠外来担当医として、医学的根拠のある情報を提供する専門家
- 薬膳・東洋医学の知識を持ち「補助的アプローチ」として活用
- 読者の悩みに共感し、「今夜から試せる」具体策を伝える

【ターゲット（前提条件）】
- 30〜50代女性（睡眠の悩み・更年期・疲れが取れないなどを抱えている）
- 「病院に行くほどじゃないけど眠れない・だるい」と感じている
- 薬に頼らず食事・生活習慣から改善したいと思っている

【記事構成（指示）】
{ARTICLE_TEMPLATE}

【文章ルール（制約）】
- ですます調で統一（「だ/である/〜した」調は一切禁止）
- 1文は50字以内
- 段落は3行以内
- 箇条書き・表を積極的に使う
- 2000〜2500文字
- Markdown形式、最初の行は「# タイトル」（32字以内・KW含む・医師監修or医師が解説を含む）
- 記事末尾に「<!-- yakuzen-affiliate-cta -->」を1行追加

{AI_REMOVE_RULES}
"""
        }]
    )

    draft = response.content[0].text.strip()

    output_path = article_dir / "phase4_draft.md"
    output_path.write_text(draft, encoding='utf-8')

    title_line = draft.split('\n')[0].replace('# ', '')
    print(f"\n[Phase 4] 完了 ✅")
    print(f"  タイトル: {title_line}")
    print(f"  文字数: {len(draft)}字")
    print(f"  保存先: {output_path}")

    return draft, str(output_path)
