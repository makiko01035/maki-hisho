import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from linebot.models import TextSendMessage

from clients import line_bot_api, anthropic_client

NOTE_SESSION_FILE = '/tmp/note_sessions.json'

NOTE_QUALITY_AGENTS = {
    "共感": """あなたはワーママ（30〜40代・AI初心者）の立場で記事を採点してください。

【採点基準（各2点×10項目）】
1. リード文で「自分の悩みに答えてくれる」と感じられるか
2. 「忙しくて時間がない」「子育てで精一杯」という気持ちへの共感があるか
3. まきの体験談が「自分もそうだった」と感じられるか
4. 感情的な共感ポイントが2箇所以上あるか
5. 専門用語を使わず、わかりやすく書かれているか
6. 「私にもできるかも」という希望が持てるか
7. 読後に「シェアしたい」と思えるか
8. 「完璧じゃなくていい」という安心感があるか
9. 冒頭100字で記事の価値が伝わるか
10. タイトルで「これは私のことだ」と感じられるか""",

    "差別化": """あなたはnote編集者です。「まき」ならではの記事かどうかを採点してください。

【採点基準（各2点×10項目）】
1. まきの実体験エピソードが具体的に書かれているか（「夫急逝後」「3児ワンオペ」等）
2. 数字・期間・具体的な出来事が入っているか（「〇ヶ月で」「〇万円」等）
3. 「まきだからこそ書ける」オリジナルの気づきがあるか
4. AI×日常×ワーママの掛け合わせが活きているか
5. 医療職の視点が自然に入っているか
6. 「プログラミングゼロからの実体験」が感じられるか
7. 他のAI系note記事と差別化できているか
8. まきのキャラクター（親しみやすさ・誠実さ）が文章に出ているか
9. 失敗談・試行錯誤も含まれているか
10. 読者が「この人を信頼したい」と感じられるか""",

    "行動促進": """あなたはコンテンツマーケターです。読者の行動を促す記事かどうかを採点してください。

【採点基準（各2点×10項目）】
1. 「今日から試せる」具体的なアクションがあるか
2. 「まず何から始めるか」が明確か
3. 行動のハードルが低く設定されているか（「5分でできる」等）
4. まとめに次の行動への誘導があるか
5. 読後感が「やってみよう」になっているか
6. 抽象的なアドバイスより具体的な手順があるか
7. 「完璧にやらなくていい」という心理的安全性があるか
8. 読者が迷わずに動ける構成になっているか
9. SNSでシェアしたくなる「刺さる言葉」があるか
10. タイトルから本文まで一貫したメッセージがあるか""",

    "有料価値": """あなたはnote有料記事の購入者です。お金を払う価値があるかを採点してください。

【採点基準（各2点×10項目）】
1. 有料部分に具体的な手順・プロンプト・テンプレートがあるか
2. 無料部分で「続きを読みたい」と思わせられているか
3. 有料部分でしか得られない情報があるか
4. 「300円の価値がある」と感じられるか
5. 再現性のある内容（自分でも同じことができる）か
6. 具体例・実例が含まれているか
7. 有料/無料の切り分けが自然か（急に終わっていないか）
8. 購入後の満足感が期待できるか
9. 「これを読んで実践した」と言えるレベルか
10. 価格に対してボリュームが適切か""",

    "文体・読みやすさ": """あなたはベテラン編集者です。文体と読みやすさを採点してください。

【採点基準（各2点×10項目）】
1. ですます調で完全に統一されているか
2. 「これにより」「〜することが重要です」等のAI感ワードがないか
3. 段落が3〜4行以内で読みやすいか（スマホで読みやすい）
4. 文末にバリエーションがあるか（体言止め含む）
5. 箇条書きの使い方が適切か（多用しすぎていないか）
6. 1文が長すぎないか（50字以内が理想）
7. 「〜の場合もあります」等の曖昧な表現が少ないか
8. 冒頭・中間・末尾に感情的な言葉があるか
9. 読んでいてテンポが良いか
10. 全体のボリューム・構成バランスが適切か"""
}


def load_note_sessions():
    try:
        with open(NOTE_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_note_sessions(data):
    try:
        with open(NOTE_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"note_sessions save error: {e}")


def send_long_message(user_id, text, chunk_size=4000):
    """長いテキストを分割してLINEにpush送信"""
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    for i, chunk in enumerate(chunks):
        prefix = f"【{i+1}/{len(chunks)}】\n" if len(chunks) > 1 else ""
        line_bot_api.push_message(user_id, TextSendMessage(text=prefix + chunk))


def _score_note_by_agent(agent_name, agent_prompt, article_content):
    """1エージェントがnote記事を採点する"""
    try:
        response = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=600,
            messages=[{
                'role': 'user',
                'content': f"""{agent_prompt}

【採点対象の記事】
{article_content}

必ずJSON形式のみで返してください：
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


def _fix_note_article(article_content, issues, note_type):
    """問題点をもとにnote記事を修正する"""
    issues_text = "\n".join(f"- {issue}" for issue in issues[:8])
    type_label = "有料" if note_type == "paid" else "無料"
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=8000,
        messages=[{
            'role': 'user',
            'content': f"""以下のnote{type_label}記事の問題点を修正してください。

【優先的に修正すべき問題点】
{issues_text}

【元の記事】
{article_content}

修正ルール：
- 記事の構成・文字数は変えない
- ですます調を維持する
- まきの実体験・具体的エピソードを活かす
- 修正後の記事全文をMarkdown形式で返す（説明文は不要）"""
        }]
    )
    return response.content[0].text.strip()


def run_note_quality_check(article_content, note_type, max_cycles=10):
    """note記事の品質チェック（最大10サイクル・目標95点）"""
    agents = dict(NOTE_QUALITY_AGENTS)
    if note_type != "paid":
        agents.pop("有料価値", None)

    max_score = len(agents) * 20
    target = round(max_score * 0.95)

    article = article_content
    final_score = 0
    passed = False

    for cycle in range(1, max_cycles + 1):
        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(_score_note_by_agent, name, prompt, article): name
                for name, prompt in agents.items()
            }
            for future in as_completed(futures):
                results.append(future.result())

        total = sum(r['score'] for r in results)
        final_score = total
        print(f"  [note品質チェック] サイクル{cycle}: {total}/{max_score}点")

        if total >= target:
            passed = True
            break
        elif cycle < max_cycles:
            results_sorted = sorted(results, key=lambda x: x['score'])
            priority_issues = [issue for r in results_sorted for issue in r.get('issues', [])]
            article = _fix_note_article(article, priority_issues, note_type)

    return article, final_score, passed


def generate_note_draft_async(user_id, note_type, target=None, worry=None, experience=None):
    """note下書きをClaude APIで生成・品質チェック後にLINEに分割送信"""
    try:
        type_label = "有料" if note_type == "paid" else "無料"
        if note_type == "paid":
            type_instruction = (
                "有料記事として書いてください。\n"
                "- 無料部分：導入・共感・この記事でわかること（全体の1/3程度）\n"
                "- 「ここから有料記事です（300円）」という区切りを入れる\n"
                "- 有料部分：再現性のある具体的な手順・プロンプト・実例を含める\n"
                "- 目標文字数：3,000〜4,000文字"
            )
        else:
            type_instruction = (
                "無料記事として書いてください。\n"
                "- 読みごたえがあり、SNSでシェアされるような内容\n"
                "- 体験談ベースで共感を呼ぶ構成\n"
                "- 目標文字数：1,500〜2,000文字"
            )

        design_info = ""
        if target:
            design_info += f"\n【届けたい読者】{target}"
        if worry:
            design_info += f"\n【読者の悩み】{worry}"
        if experience:
            design_info += f"\n【まきの体験エピソード】{experience}"

        response = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=4000,
            system=(
                "あなたはAI×日常生活×ワーママ実体験を発信する「まき」として書きます。\n"
                "プロフィール：医療職・3児ワンオペ・夫急逝後にプログラミングゼロからAIで毎日を仕組み化した\n"
                "読者：「AIって何に使うの？」と思っているワーママ・AI初心者・日常を楽にしたい人\n"
                "文体：ですます調・親しみやすい・体験談ベース・専門用語を使わない\n"
                "重要：読者の悩みへの共感から入り、まきの実体験を通じて「私にもできる」と感じてもらう構成にする\n"
                + type_instruction
            ),
            messages=[{
                'role': 'user',
                'content': f'以下の設計情報に基づいてnote{type_label}記事の下書きを書いてください。タイトルも含めて。\n{design_info}'
            }]
        )

        draft = response.content[0].text
        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"✍️ note{type_label}記事を執筆中です。品質チェック（最大10回）を実行します…少々お待ちください🙏"
        ))

        final_article, score, passed = run_note_quality_check(draft, note_type)

        agents_count = 5 if note_type == "paid" else 4
        max_score = agents_count * 20
        status = "✅ 合格" if passed else "⚠️ 要確認"
        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"📝 note{type_label}記事の下書きができました！\n品質スコア：{score}/{max_score}点 {status}\n↓をそのままnoteにコピペしてください👇"
        ))
        send_long_message(user_id, final_article)
        if note_type == "paid":
            line_bot_api.push_message(user_id, TextSendMessage(
                text="✅ コピペ後、noteで「有料ライン」を「ここから有料記事です」の行に設定してから公開してください🎉"
            ))

    except Exception as e:
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ 下書き生成エラー: {str(e)[:200]}"))
