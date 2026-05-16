"""
Phase 3: 設計エージェント
検索上位10記事のH2/H3構造を分析して最適な記事構成を設計する
"""
import os
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv(Path(__file__).parent.parent / ".env")
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def fetch_headings(url):
    """1つのURLからH2/H3見出しを抽出する"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')

        headings = []
        for tag in soup.find_all(['h2', 'h3']):
            text = tag.get_text(strip=True)
            if text and len(text) > 3:
                headings.append({'level': tag.name, 'text': text})
        return {'url': url, 'headings': headings, 'error': None}
    except Exception as e:
        return {'url': url, 'headings': [], 'error': str(e)}


def analyze_common_patterns(all_headings):
    """全サイトのH2を分析して出現率を計算する"""
    from collections import Counter
    import re

    # H2テキストをカテゴリに分類するための簡易マッピング
    category_keywords = {
        'とは': ['とは', '概要', 'について'],
        '原因': ['原因', '理由', 'なぜ', 'メカニズム'],
        '改善': ['改善', '対策', '方法', '解決', 'やり方', 'コツ'],
        '食材': ['食材', '食べ物', '食品', '飲み物', '薬膳'],
        '症状': ['症状', 'サイン', '特徴'],
        'まとめ': ['まとめ', 'まとめると', 'ポイント'],
        'おすすめ': ['おすすめ', '厳選', 'ランキング'],
        'FAQ': ['よくある', 'Q&A', '疑問', 'FAQ'],
    }

    site_count = len([s for s in all_headings if s['headings']])
    category_counts = Counter()

    for site in all_headings:
        found_categories = set()
        for h in site['headings']:
            if h['level'] == 'h2':
                text = h['text']
                for cat, keywords in category_keywords.items():
                    if any(kw in text for kw in keywords):
                        found_categories.add(cat)
        for cat in found_categories:
            category_counts[cat] += 1

    # 出現率計算
    pattern_analysis = []
    for cat, count in category_counts.most_common():
        rate = (count / site_count * 100) if site_count > 0 else 0
        pattern_analysis.append({
            'category': cat,
            'count': count,
            'rate': rate,
            'is_common': rate >= 50,
        })

    return pattern_analysis


def generate_design(keyword, all_headings, pattern_analysis):
    """Claude APIを使って最適な記事構成を設計する"""
    # 見出し構造をテキスト化
    headings_text = ""
    for site in all_headings:
        if site['headings']:
            headings_text += f"\n【{site['url'][:60]}...】\n"
            for h in site['headings']:
                indent = "  " if h['level'] == 'h3' else ""
                headings_text += f"{indent}{h['level'].upper()}: {h['text']}\n"

    # パターン分析をテキスト化
    pattern_text = ""
    common = [p for p in pattern_analysis if p['is_common']]
    unique = [p for p in pattern_analysis if not p['is_common']]
    pattern_text += f"共通パターン（50%以上）: {', '.join(p['category'] for p in common)}\n"
    pattern_text += f"差別化チャンス（50%未満）: {', '.join(p['category'] for p in unique)}\n"

    response = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=2000,
        messages=[{
            'role': 'user',
            'content': f"""あなたはSEOコンテンツ設計の専門家です。

キーワード：{keyword}
ブログ：foodmakehealth.com（睡眠×医師×薬膳）
著者：内科医・睡眠外来担当医
ターゲット：30〜50代女性（睡眠の悩み・更年期・疲れが取れない）

【上位記事の見出し構造】
{headings_text}

【パターン分析】
{pattern_text}

上記を踏まえて、以下のルールで記事構成を設計してください：

**設計ルール**
- 共通パターン（出現率50%以上）は踏襲（検索意図の核心）：60〜70%
- 差別化パート（医師×薬膳の独自知見）：30〜40%
- 必ず「共感→原因→改善→薬膳補助→まとめ」の流れを守る

**出力形式（必ずこの形式で）**
# 記事構成案：{keyword}

## 分析サマリー
- 共通パターン（踏襲する）：
- 差別化パート（独自に書く）：

## 確定H2構成
### H2-1: [見出しテキスト]
- 種別: 共通 or 独自
- 内容: このH2で書くこと
- 独自要素: 医師・薬膳の知見をどう活かすか

### H2-2: [見出しテキスト]
...（6〜7個のH2を設計）

## 独自パート設計
（内科医としての実体験・薬膳知識をどのH2でどう使うか具体的に）
"""
        }]
    )
    return response.content[0].text.strip()


def run(keyword, urls, output_dir="articles"):
    """Phase 3を実行して結果をファイルに保存する"""
    slug = re.slugify(keyword) if hasattr(__builtins__, 're') else keyword.replace(' ', '_').replace('　', '_')
    slug = keyword.replace(' ', '_').replace('　', '_').replace('/', '_')
    article_dir = Path(output_dir) / slug
    article_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[Phase 3] 設計エージェント起動")
    print(f"  キーワード: {keyword}")
    print(f"  対象URL数: {len(urls)}件")

    # H2スクレイピング
    all_headings = []
    for i, url in enumerate(urls, 1):
        print(f"  スクレイピング中... ({i}/{len(urls)}) {url[:50]}...")
        result = fetch_headings(url)
        all_headings.append(result)
        if result['error']:
            print(f"    ⚠️ エラー: {result['error']}")
        else:
            print(f"    ✅ H2/H3を{len(result['headings'])}件取得")

    # パターン分析
    pattern_analysis = analyze_common_patterns(all_headings)

    # 構成設計
    print(f"  記事構成を設計中...")
    design = generate_design(keyword, all_headings, pattern_analysis)

    # ファイル保存
    output_path = article_dir / "phase3_design.md"
    output_path.write_text(design, encoding='utf-8')

    print(f"\n[Phase 3] 完了 ✅")
    print(f"  保存先: {output_path}")
    print(f"\n{'='*50}")
    print(design)
    print('='*50)

    return design, str(output_path)
