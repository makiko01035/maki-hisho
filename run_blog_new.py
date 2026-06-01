"""月・木 8:00 JST 自動実行：新規ブログ記事を生成してWordPressに投稿する"""
import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

NOTIFY_URL = "https://maki-hisho.onrender.com/add-task"
NOTIFY_SECRET = os.environ.get('NOTIFY_SECRET', 'maki2025')


def notify_line(message, task=""):
    try:
        requests.post(
            NOTIFY_URL,
            json={"secret": NOTIFY_SECRET, "message": message, "task": task or message[:30]},
            timeout=15,
        )
    except Exception:
        pass


def main():
    from phases import phase4_write, phase5_quality, phase6_publish

    kw_file = Path(__file__).parent / 'keywords_new.txt'
    lines = [l.strip() for l in kw_file.read_text(encoding='utf-8').splitlines() if l.strip()]
    if not lines:
        notify_line("⚠️ ブログ自動投稿（新規）：keywords_new.txtが空です")
        print("keywords_new.txtが空のため終了")
        return

    keyword = lines[0]
    print(f"キーワード: {keyword}")

    # 使用済みキーワードを先頭から削除
    remaining = '\n'.join(lines[1:]) + '\n'
    kw_file.write_text(remaining, encoding='utf-8')

    try:
        design = f"# テーマ: {keyword}\n\n共感→原因→改善→薬膳補助→まとめ の構成で執筆してください。"
        draft, _ = phase4_write.run(keyword, design)
        final, score, passed, _ = phase5_quality.run(keyword, draft)

        if passed or score >= 80:
            phase6_publish.run(keyword, final)
        else:
            msg = f"❌ 自動投稿スキップ（品質{score}点）\nキーワード：「{keyword}」\n手動で確認が必要です"
            print(msg)
            notify_line(msg)

    except Exception as e:
        msg = f"❌ 自動投稿エラー（新規）\n{str(e)[:200]}"
        print(f"エラー: {e}", file=sys.stderr)
        notify_line(msg)
        sys.exit(1)


if __name__ == '__main__':
    main()
