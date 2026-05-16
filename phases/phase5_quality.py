"""
Phase 5: 品質チェックエージェント
5エージェントを並列で実行して採点し、95点以上になるまでループする
"""
import os
import json
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(Path(__file__).parent.parent / ".env")
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# 5つの採点エージェント定義
AGENTS = {
    "デザイン": """あなたはWebデザイン・フォーマットの専門家です。記事を20点満点で採点してください。

【採点基準（各2点×10項目）】
1. H2構成が目次に対応し、各H2冒頭に1文結論があるか
2. テーブルのヘッダー・スタイルが統一されているか
3. 「<!-- yakuzen-affiliate-cta -->」が記事末尾にあるか
4. テキスト強調（**太字**）が適切に使われているか
5. 段落が3行以内で読みやすいか
6. 箇条書きの前後にリード文があるか
7. 見出し直後にいきなり箇条書きが来ていないか
8. 全体のフォーマットが統一されているか
9. 「この記事でわかること」的な導入が冒頭にあるか
10. まとめがSTEP形式または箇条書きで読みやすいか""",

    "SEO": """あなたはSEO専門家です。記事を20点満点で採点してください。

【採点基準（各2点×10項目）】
1. タイトルにキーワードが含まれているか・32文字以内か
2. タイトルに「医師監修」または「医師が解説」が含まれているか
3. 本文が2000文字以上あるか
4. 冒頭100字で悩みへの共感と記事の内容が明示されているか
5. H2/H3見出しにキーワードが自然に含まれているか
6. E-E-A-T要素（著者の専門知識・数値・具体的事例）が含まれているか
7. FAQまたはQ&A形式のセクションが含まれているか
8. 記事末尾に「<!-- yakuzen-affiliate-cta -->」があるか
9. 「2週間改善しなければ内科・睡眠外来へ」などの医療機関への案内があるか
10. 内部リンク候補（「関連記事」「こちらも参考に」等）が明記されているか""",

    "編集者": """あなたはベテラン編集者です。記事を20点満点で採点してください。

【採点基準（各2点×10項目）】
1. ですます調で完全に統一されているか（「だ/である」調の混入がないか）
2. 「これにより」が完全に削除されているか
3. 「〜することが重要です」が3回以下か
4. あいまいな結論（「場合もあります」「可能性があります」）がないか
5. 3連続箇条書きがないか（間にリード文やテーブルを挟んでいるか）
6. 文末パターンにバリエーションがあるか（体言止め含む）
7. 一人称の体験談・観察が2箇所以上あるか
8. 1文が50字以内に収まっているか
9. 「おすすめします」が「試してみてください」等に置き換えられているか
10. 専門用語が初出時に平易に説明されているか""",

    "医学専門家": """あなたは内科医・睡眠専門医です。記事を20点満点で採点してください。

【採点基準（各2点×10項目）】
1. 医学情報（ホルモン・神経・体温調節等）が正確か
2. 薬膳・食材の説明（効能・使い方）が正確か
3. 「今夜から試せる」具体的アクションがあるか
4. 睡眠外来・更年期外来で実際によく聞く悩みに答えているか
5. 薬に頼らないアプローチが適切に提示されているか
6. 薬膳は「補助的アプローチ」として適切なトーンか（過剰な主張がないか）
7. 根拠のない断言（「必ず良くなります」等）がないか
8. 医師としての専門コメント・実体験が含まれているか
9. 読者が安心できる信頼性のある情報があるか
10. 「2週間改善しなければ医療機関へ」などの適切な受診案内があるか""",

    "読者（30〜50代女性）": """あなたは30〜50代女性（睡眠の悩み・更年期・疲れ）の立場で記事を採点してください。

【採点基準（各2点×10項目）】
1. リード文で「自分の悩みに答えてくれる」と感じられるか
2. 「病院に行くほどじゃないけど眠れない」という気持ちに寄り添っているか
3. 専門用語が分かりやすく説明されているか
4. 「自分でも今夜から試せそう」と感じられるか
5. 「まず何から始めるか」が明確か
6. 段落が読みやすいか（スマホで読める長さ）
7. FAQが実際に自分が抱く疑問と一致しているか
8. 商品紹介（アフィリエイト）が不自然でなく自然に組み込まれているか
9. 読後に「この記事を見て良かった」と思えるか
10. 「更年期×睡眠」の悩みに具体的に答えているか"""
}


def score_by_agent(agent_name, agent_prompt, article_content):
    """1エージェントが採点する（並列実行用）"""
    try:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=800,
            messages=[{
                'role': 'user',
                'content': f"""{agent_prompt}

【採点対象の記事】
{article_content[:3000]}  ← 先頭3000文字で採点

必ずJSON形式のみで返してください（説明文不要）：
{{"score": XX, "issues": ["問題点1", "問題点2", "問題点3"]}}
scoreは0〜20の整数、issuesは具体的な修正箇所（最大3件）"""
            }]
        )

        text = response.content[0].text.strip()
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            result['agent'] = agent_name
            return result
        return {'agent': agent_name, 'score': 12, 'issues': ['採点結果の解析に失敗']}

    except Exception as e:
        return {'agent': agent_name, 'score': 12, 'issues': [f'採点エラー: {str(e)}']}


def run_parallel_scoring(article_content):
    """5エージェントを並列で実行して採点する"""
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(score_by_agent, name, prompt, article_content): name
            for name, prompt in AGENTS.items()
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"    [{result['agent']}] {result['score']}/20点")

    total = sum(r['score'] for r in results)
    all_issues = [issue for r in results for issue in r.get('issues', [])]
    # スコアが低いエージェントの問題点を優先
    results_sorted = sorted(results, key=lambda x: x['score'])
    priority_issues = [issue for r in results_sorted for issue in r.get('issues', [])]

    return total, results, priority_issues


def fix_article(article_content, issues):
    """問題点をもとに記事を修正する"""
    issues_text = "\n".join(f"- {issue}" for issue in issues[:8])

    response = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=4000,
        messages=[{
            'role': 'user',
            'content': f"""以下の記事の問題点を修正してください。

【優先的に修正すべき問題点】
{issues_text}

【元の記事】
{article_content}

修正ルール：
- 記事の構成・内容・文字数は変えない
- 指摘された問題点のみを修正する
- ですます調を維持する
- 修正後の記事全文をMarkdown形式で返す（説明文は不要）"""
        }]
    )
    return response.content[0].text.strip()


def run(keyword, draft_md, output_dir="articles"):
    """Phase 5を実行（最大3サイクルループ）"""
    slug = keyword.replace(' ', '_').replace('　', '_').replace('/', '_')
    article_dir = Path(output_dir) / slug
    article_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[Phase 5] 品質チェックエージェント起動")
    print(f"  キーワード: {keyword}")
    print(f"  目標スコア: 95/100点")

    article = draft_md
    all_cycle_results = []
    final_score = 0
    passed = False

    for cycle in range(1, 4):
        print(f"\n  サイクル {cycle}/3 — 5エージェントが並列採点中...")
        total, results, priority_issues = run_parallel_scoring(article)
        final_score = total

        cycle_result = {
            'cycle': cycle,
            'total': total,
            'agent_scores': {r['agent']: r['score'] for r in results},
            'issues': priority_issues[:8]
        }
        all_cycle_results.append(cycle_result)

        print(f"\n  合計スコア: {total}/100点", end="")

        if total >= 95:
            print(" ✅ 合格！")
            passed = True
            break
        else:
            print(f" — あと{95 - total}点")
            if cycle < 3:
                print(f"  問題点を修正中...")
                for issue in priority_issues[:5]:
                    print(f"    - {issue}")
                article = fix_article(article, priority_issues)
            else:
                print(f"  ⚠️ 3サイクル後も{total}点 → まきさんに確認を依頼します")

    # 結果をファイルに保存
    quality_result = {
        'keyword': keyword,
        'final_score': final_score,
        'cycles': len(all_cycle_results),
        'passed': passed,
        'all_cycle_results': all_cycle_results
    }

    quality_path = article_dir / "phase5_quality.json"
    quality_path.write_text(
        json.dumps(quality_result, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    final_path = article_dir / "phase5_final.md"
    final_path.write_text(article, encoding='utf-8')

    print(f"\n[Phase 5] 完了 ✅")
    print(f"  最終スコア: {final_score}/100点 ({'合格' if passed else '要確認'})")
    print(f"  採点結果: {quality_path}")
    print(f"  最終記事: {final_path}")

    return article, final_score, passed, str(quality_path)
