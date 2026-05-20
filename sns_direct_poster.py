import os
import json
import datetime
import time
import random
import requests
import threading
from linebot.models import TextSendMessage

from clients import line_bot_api, anthropic_client, JST
from blog_yakuzen import search_rakuten_items
from threads_room import HOOKS, LINK_PHRASES, post_to_threads


# --- こはるまま：@kvision_m 旅行×楽天アフィ X自動投稿（1日2本）---

KVISION_GENRE_LOG_PATH = '/tmp/kvision_genre_log.json'

TRAVEL_GENRES = [
    # 通年固定
    {'name': 'シアーパーカー・UVカット羽織り', 'keyword': 'シアーパーカー UVカット 羽織り レディース 冷房対策 薄手'},
    {'name': '折りたたみ日傘', 'keyword': '日傘 折りたたみ 完全遮光 軽量 UVカット レディース'},
    {'name': 'アウトドアワゴン', 'keyword': 'キャリーワゴン アウトドア 折りたたみ 大容量 子連れ レジャー'},
    {'name': 'メッシュトート・おでかけバッグ', 'keyword': 'トートバッグ メッシュ 大容量 アウトドア 軽量 レディース'},
    {'name': 'ハンディファン・暑さ対策', 'keyword': 'ハンディファン 携帯扇風機 軽量 USB 旅行 暑さ対策'},
    {'name': 'ブラトップ・機能性インナー', 'keyword': 'ブラトップ 機能性インナー 旅行 快適 レディース'},
    {'name': '旅行ポーチ・トラベルコスメ', 'keyword': '旅行ポーチ トラベル コスメ 防水 コンパクト 化粧品'},
    # ロングシーズン追加
    {'name': 'キャンプ・BBQグッズ', 'keyword': 'キャンプ BBQ グッズ 子連れ アウトドア 便利 コンパクト'},
    {'name': 'キャンプ・ランタン・焚き火', 'keyword': 'ランタン 焚き火台 キャンプ アウトドア おしゃれ コンパクト'},
    {'name': 'スーツケース・旅行バッグ', 'keyword': 'スーツケース 軽量 旅行 キャリー 子連れ レディース'},
    {'name': '子ども旅行グッズ・新幹線の暇つぶし', 'keyword': '子ども 旅行 暇つぶし 新幹線 おもちゃ 知育 コンパクト'},
    {'name': '時短・便利キッチングッズ', 'keyword': '時短 便利 キッチン グッズ ワーママ おすすめ'},
    {'name': 'お取り寄せグルメ・手土産', 'keyword': 'お取り寄せ グルメ 手土産 旅行 ご当地 人気 ギフト'},
]

TRAVEL_HOOKS = [
    "先週の旅行で大活躍したのが",
    "子連れ旅でこれ持ってくよかった",
    "旅行前日に気づいた神アイテム",
    "ホテルで「買ってきてよかった」ってなったのが",
    "子どもがぐずりだしたときに救われたのが",
    "旅行バッグに必ず入れてるの、これ",
    "去年の旅行で後悔して今年買ったのが",
    "旅先で肌がボロボロになってから使い始めたのが",
    "子連れ旅行、荷物減らすために買ったのが",
    "夏の旅行に絶対持っていきたいのが",
    "キャンプ行くとき絶対持っていくのが",
    "おでかけバッグに毎回入れてるのが",
    "庭ピクのときに大活躍してるのが",
    "BBQ前日に「これ買っといてよかった」ってなったのが",
    "子どもと一緒に使えて買ってよかったのが",
    "ワーママ的に時間が減ったきっかけになったのが",
    "帰省のときに持っていって正解だったのが",
    "旅行のお土産じゃなくて、旅行前に買いたいのが",
]

MAKO_THREADS_MORNING = [
    "夜中に何度も目が覚める…\n\nそういう方、思った以上に多いんです。\n\n「眠れない」より「眠りが浅い」という感覚。\n\n原因の一つに、就寝後の体温調節がうまくいっていないことがあるかもしれません。\n\n入浴で体を温めてから自然に冷ます流れが、深い眠りに入りやすくなる方もいます。",
    "夜になると考えすぎてしまう、という方いませんか？\n\n頭が静まらないまま布団に入ると、なかなか眠れないことも。\n\n東洋医学的には「心」の気が乱れている状態かもしれません。\n\nゆっくり吐く呼吸を意識するだけで、少し落ち着く方もいます。",
    "疲れているのに眠れない…\n\nこれって結構つらいですよね。\n\n「疲労」と「眠気」は別物で、体は疲れていても脳が興奮していると眠れないことがあります。\n\n就寝1時間前にブルーライトを避けると、改善した方もいます。\n\n小さなことですが、試してみる価値はあるかもしれません。",
    "更年期に入ってから眠りが浅くなった、という声を聞くことがあります。\n\nエストロゲンの変動が自律神経に影響し、体温調節が乱れやすくなることが関係しているかもしれません。\n\n薬膳的には、血を補う食材（なつめ・クコの実・黒ごまなど）が助けになるという方もいます。",
    "睡眠は「量」より「質」という話があります。\n\n6時間でも深く眠れる方もいれば、8時間眠っても疲れが取れない方もいます。\n\n睡眠の質に関わる要素は体温・光・音・寝具・ストレスなど様々。\n\n一つずつ試してみるのが遠回りに見えて近道かもしれません。",
    "子育て中のママって、眠れない理由が本当に多いですよね。\n\n子どもの夜泣き・翌日の段取りへの不安・自分だけの時間への渇望…\n\n眠れない夜に「何かできることはないか」と考えてしまう気持ち、わかります。\n\nまず「眠れなくてもOK」と思えると、少し体の力が抜けることもあるかもしれません。",
    "漢方や薬膳に興味はあるけど、どこから始めればいいか分からない…\n\nそういう声、よく聞きます。\n\nスーパーで買えるなつめ・黒豆・くるみ・黒ごまは、東洋医学で「腎」を補い、睡眠と深く関わるとされています。\n\n日々の料理に少し取り入れるだけでも、変化を感じる方もいます。",
    "「疲れているのに眠れない」「眠っても疲れが取れない」\n\nこの2つはちょっと違う問題かもしれません。\n\n前者は睡眠に入れないこと、後者は睡眠の質の問題です。\n\nどちらも辛いですが、対策も少し違うことがあります。\n\nブログでも詳しく書いています。",
    "マグネシウムが睡眠に関係している、という話があります。\n\n神経の興奮を抑え、体をリラックスさせる働きがあるとされています。\n\n海藻・ナッツ・ほうれん草などに含まれています。\n\n食事から意識するのも一つかもしれません。",
    "眠る前のスマホ、なんとなく分かっていてもやめられない…\n\nブルーライトがメラトニン（眠気を作るホルモン）の分泌を抑えるという研究があります。\n\n完全にやめなくても、画面を暗くする・ナイトモードにするだけで変わる方もいます。\n\n小さな工夫から始めてみませんか？",
]

MAKO_GENRE_LOG_PATH = '/tmp/mako_genre_log.json'

MAKO_THREADS_AFF_GENRES = [
    {'name': '睡眠サプリ（GABA）',   'keyword': 'GABA 睡眠 サプリ'},
    {'name': 'ホットアイマスク',      'keyword': 'ホットアイマスク 蒸気 睡眠'},
    {'name': 'なつめ（薬膳）',        'keyword': 'なつめ 薬膳 乾燥'},
    {'name': 'カモミールハーブティー', 'keyword': 'カモミール ハーブティー 安眠'},
    {'name': '加味逍遙散',            'keyword': '加味逍遙散 漢方 更年期'},
    {'name': 'クコの実（薬膳）',      'keyword': 'クコの実 薬膳 乾燥'},
    {'name': '睡眠枕',                'keyword': '枕 睡眠 低反発'},
    {'name': '加味帰脾湯',            'keyword': '加味帰脾湯 漢方 不眠'},
    {'name': 'マグネシウムサプリ',    'keyword': 'マグネシウム サプリ 睡眠'},
    {'name': '竜眼肉（薬膳）',        'keyword': '竜眼肉 りゅうがんにく 薬膳'},
    {'name': 'ラベンダーアロマ',      'keyword': 'ラベンダー アロマオイル 安眠'},
    {'name': '酸棗仁湯',              'keyword': '酸棗仁湯 漢方 不眠'},
    {'name': '抑肝散',                'keyword': '抑肝散 漢方 神経 不眠'},
    {'name': '睡眠ハーブティー',      'keyword': '睡眠茶 ハーブ パッションフラワー'},
    {'name': '黒ごま・黒豆（薬膳）', 'keyword': '黒ごま 黒豆 薬膳 腎'},
]

# (1本目フック, 2本目詳細 or None)  ※ None=1本完結
MORNING_TWEET_PAIRS = [
    # ── 型1：有益Tips型 ──
    (
        "子連れ旅行の旅費、ここ1年でけっこう浮いた気がする\n\n意識してやってること3つ↓",
        "・楽天カードで決済（ポイント2〜4倍）\n・マラソン期間に予約まとめる\n・直前割を狙える旅行は積極的に使う\n\n全部やると年1〜2回分の旅行費が浮く感覚がある\nポイントって「なんとなく貯まる」より「狙って貯める」ほうが全然違う\n\nまずカードだけでも変えてみる価値ある",
    ),
    (
        "子連れ旅行のホテル、チェックイン前にこれやるだけで満足度が上がった\n\nやってること↓",
        "チェックイン前日か当日朝に電話で一言頼む\n\n「なるべく高い階の部屋をお願いできますか」\n「子連れなので隣室から離れた部屋だと助かります」\n\nダメもとで頼んでみるのが大事\n無料でやってくれることが思ったより多い",
    ),
    (
        "子連れ旅行の荷物、毎回多すぎて何とかしたいと思ってた\n\n今やってる削減ルール↓",
        "・コスメ・シャンプーはホテルのアメニティ確認してから持つか決める\n・着替えは「1日1セット＋1枚」だけ\n・現地のドラッグストアで買えるものは持たない\n\nこの3つだけ意識したら旅行バッグが明らかに軽くなった",
    ),
    (
        "旅行グッズを買うタイミング、損してる人が多い気がする\n\n楽天マラソン期間に合わせると全然違う↓",
        "楽天マラソン中は買えば買うほどポイントが上がる仕組み\n旅行グッズはまとめて買いやすいから相性がいい\n\nスーツケース・ハンディファン・日傘・旅行ポーチあたりを\nマラソン期間に集中させると実感できる\n\n次のマラソンまでに「ほしいものリスト」を作っておくのが正解",
    ),
    (
        "海外旅行、旅行保険を別に入ってる人いる？\n\n実は楽天カードに付帯されてる話↓",
        "楽天カードの海外旅行保険、カードで旅費を払えば自動付帯される\n\n傷害・疾病・遅延・紛失のカバーがある\n知らないで別途保険に入ってる人がけっこう多い\n\n補償内容は確認してから使う前提だけど\nとりあえず把握しておいて損はない",
    ),
    (
        "新幹線の子連れ料金、ここ把握してると違う\n\n年1〜2回は使えるやつ↓",
        "JRが季節限定で出す「お子さま向けきっぷ」、GW以外にも出てる\n\n夏休み・年末年始あたりは特に注意\nJRの公式サイトを月1回チェックするだけで引っかかることがある\n\n子連れは旅費がかさむから\n1回引っかかるだけで全然違う",
    ),
    (
        "旅行グッズ、買う前にやってること↓",
        "楽天ROOMで「子連れ旅行」で検索する\n\n実際に子連れ旅行してる人が買ってよかったものをまとめてるから\n情報の質が全然違う\n\nレビューは書かない人もいるけど\nROOMはわざわざ登録してるから「本当に使ったもの」が多い",
    ),
    (
        "楽天トラベルで同じホテル、時期によって値段が全然違う理由\n\n直前割の仕組みを知ってると得する↓",
        "ホテルは空室が出るとチェックイン3日前あたりから値下げすることがある\n\n楽天トラベルの「直前割」フィルターをONにすると探しやすい\n\n子連れだと直前予約は難しいことも多いけど\n予定が読める旅行なら積極的に狙ってみる価値ある",
    ),
    (
        "旅行費を安くする方法、一番確実なのがこれ\n\nやってないならまずここから↓",
        "早期予約割\n\n楽天トラベルで2〜3ヶ月前に予約すると\n「早割プラン」が出てることが多い\n\n直前より10〜30%安いことがある\n\n子連れは学校・幼稚園の行事に合わせるから\n逆算して早めに動ける旅行は早割一択",
    ),
    (
        "楽天のポイント、サービスを組み合わせるほど増える仕組みになってる\n\n旅行に関係するやつだけまとめると↓",
        "・楽天カード使用：+2倍（必須）\n・楽天トラベル予約：+1倍\n・楽天市場でお買い物：+0.5倍〜\n\n全部揃えると旅行費の還元率が体感でかなり上がる\n\n「なんとなく楽天カード持ってる」状態から\n「ポイントを設計して貯める」に変えるだけで違う",
    ),
    # ── 型2：問いかけ型 ──
    (
        "子連れ旅行で一番テンションが下がる瞬間、何ですか？\n\nうちはホテル着いた瞬間に子どもが「おなかすいた」\n毎回くる\n旅行前に軽食買っといても「これじゃない」ってなる",
        None,
    ),
    (
        "旅行前夜に何を忘れがちですか？\n\nうちはだいたい充電器かウェットシート\nどんなに準備してもどちらかが入ってない",
        None,
    ),
    (
        "旅行中の「これ持ってくればよかった」、何ですか？\n\nうちはハンディファン\n毎年後悔してるのに毎年忘れる",
        None,
    ),
    (
        "旅行の計画、何ヶ月前から立てる？\n\nうちは3ヶ月前には大体決めてる\n早く決めないとホテルが埋まるから",
        None,
    ),
    (
        "子どもがホテルに着いた瞬間に必ずやること、何ですか？\n\nうちはベッドでジャンプ\nどこのホテルでも必ずやる",
        None,
    ),
    (
        "旅行のお土産、何を基準に選んでる？\n\n職場に配るやつ選ぶの毎回悩む\n個包装で日持ちするやつを探してるとだいたい時間がかかる",
        None,
    ),
    (
        "家族旅行の予算、どうやって決めてる？\n\nうちは「宿泊費+交通費」で上限を先に決めて\n食費・お土産はそこからやりくりしてる",
        None,
    ),
    (
        "子連れキャンプでのハプニング、何かある？\n\nうちは子どもが虫捕りに夢中で\nごはんの時間になっても全然戻ってこなかった",
        None,
    ),
    (
        "旅行中、子どもが一番テンション上がるのって何ですか？\n\nうちはホテルのビュッフェ\n食べ放題というだけで毎回興奮してる",
        None,
    ),
    (
        "旅行のパッキング、一人でやる？家族みんなでやる？\n\nうちは全部一人でやってる\n頼むと絶対に余分なものを入れられる",
        None,
    ),
    # ── 型3：驚き型 ──
    (
        "楽天トラベルで予約したのに\n「ポイント付いてない？」ってなった経験ある？\n\nこれ知ってると焦らなくなる↓",
        "楽天トラベルのポイントはチェックアウト後に付与される\n\n予約した時点では反映されないから\n帰ってきてから確認するのが正解\n\n知らないと旅行中ずっと「あれ？なんで付かないんだろ」ってなる\n教えてくれる人がいないだけで、知ってれば全然焦らない",
    ),
    (
        "新幹線の料金、子どもって何歳から有料か知ってる？\n\nこれ知らないで乗ってる人、意外と多い↓",
        "6歳以上の小学生から半額で有料\n6歳未満（就学前）は無料\nただし席を取る場合は有料になる\n\n0〜5歳は膝の上に乗せれば無料だから\n長距離なら席を買うか判断するのが大事\n\nGWとかお盆は混むから特に確認しておくといい",
    ),
    (
        "ホテルのチェックイン、14時より前に着いたらどうしてる？\n\n知らないともったいないことがある↓",
        "荷物預かりをお願いするのは当然として\n「チェックインできる部屋が準備できたら教えてください」と一言頼む\n\n空いてれば13時台に入れることが思ったよりある\n\n子連れだとチェックインまでの時間潰しがしんどいから\nダメもとで聞いてみる価値ある",
    ),
    (
        "楽天マラソン、「ショップ数でポイントが上がる」仕組みって理解してる？\n\n勘違いしてた人が多かった話↓",
        "楽天マラソンは「注文した金額」じゃなくて「注文したショップ数」でポイント倍率が上がる\n\n1ショップ1000円×10ショップ＝ポイント10倍\n1ショップで1万円使っても倍率は変わらない\n\n旅行グッズを10ショップに分けて注文するのがコツ",
    ),
    (
        "楽天カードで旅行費を払うと旅行保険が付帯される\n\nこれ知らずに別途保険に入ってる人がけっこういる↓",
        "海外旅行中の傷害・疾病・遅延・携行品紛失がカバーされる\n\nただし「カードで支払った旅行費」が条件になることがある\n航空券・ホテル代を楽天カードで払っておくことが大事\n\n補償内容は事前に確認してから\n旅行前にカードの公式サイトで一度チェックしておくのをおすすめする",
    ),
    (
        "楽天トラベルの「お気に入り」機能、ただ保存するだけじゃないって知ってた？\n\n使い方で得できる話↓",
        "お気に入りに登録したホテルが値下がりすると通知が来る\n\n「このホテル行きたいな」と思ったらまずお気に入りに入れておくのがコツ\n\nさらにお気に入り経由で予約するとポイントが上乗せされる\n\n「見るだけ」で終わらせてたら損してた",
    ),
    (
        "旅行の荷物が多い理由、だいたいこれだと思う\n\n気づいてから荷物が半分になった↓",
        "コンビニとドラッグストアは旅先にほぼある\n\n日焼け止め・シャンプー・洗顔・コスメ類は現地調達でいい\n\n持っていく必要があるのは「その旅行の間しか使わないもの」だけ\n\nホテルのアメニティ確認→足りないものだけ持つ\nこれだけでバッグが明らかに軽くなる",
    ),
    (
        "楽天スーパーSALEとマラソン、どっちで旅行グッズを買うべき？\n\n実はちゃんと使い分けがある↓",
        "スーパーSALE：対象商品の価格が下がる（安くなるものが多い）\nマラソン：ショップ数でポイントが上がる（価格は変わらないことも）\n\n高いもの（スーツケース）はSALEで価格を狙う\n細々したものはマラソンでショップ数を稼ぐ\n\nこの使い分けだけでだいぶ変わる",
    ),
    (
        "旅行の予約タイミング、早すぎても遅すぎても損することがある\n\n子連れ旅行の正解↓",
        "・人気ホテルは3ヶ月前には埋まり始める（早期予約割もある）\n・マラソン期間（月1回）に合わせると+ポイント\n・直前3日以内に直前割が出ることもある\n\n子連れはスケジュールが読めるなら早期予約一択\n読めないなら直前割を狙う\n\n「なんとなく予約」をやめるだけで年間コストが変わる",
    ),
    (
        "楽天ROOMのアフィリエイト、通常より報酬が高い理由を知ってる？\n\n仕組みが面白い↓",
        "楽天ROOMは「ROOMランク」に応じて報酬にボーナスが乗る\n\nROOMをアクティブに使うほどランクが上がって報酬が増える仕組み\n\n普通に楽天アフィリエイトリンクを貼るより\nROOM経由で買ってもらったほうが報酬が高いことがある\n\n旅行グッズをROOMにまとめてるのはそのため",
    ),
]

KOHARU_ROOM_URL = "https://room.rakuten.co.jp/makiko01035/items"

ROOM_INTRO_TWEETS = [
    "私が実際に買ってよかったもの、ROOMにまとめてます\n子連れ旅行・アウトドア・日常使いのグッズを厳選してるので参考にしてみて↓",
    "旅行グッズ・キャンプ道具・便利グッズ、使ってよかったものを楽天ROOMにまとめてます\n購入前の参考にどうぞ↓",
    "子連れ旅行で本当に使えたものだけROOMに残してる\n301件あるのでお気に入り登録しておいてもらえると嬉しい↓",
    "ワーママ目線で厳選した旅行・生活グッズ、楽天ROOMにまとめてます\n気になるものあったら見てみて↓",
    "子ども連れのおでかけに役立つグッズ、全部ここにまとめてます\n使ってよかったものだけ厳選↓",
]


def post_kvision_room_intro():
    """週1回（木曜）夜19時：楽天ROOM誘導投稿（X）"""
    import random, time as _time
    try:
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping room intro")
            return
        text = random.choice(ROOM_INTRO_TWEETS)
        resp = client.create_tweet(text=text)
        tweet_id = resp.data['id']
        _time.sleep(3)
        client.create_tweet(
            text=KOHARU_ROOM_URL,
            in_reply_to_tweet_id=tweet_id
        )
        print("kvision room intro tweet successful")
    except Exception as e:
        print(f"post_kvision_room_intro error: {e}")


def post_koharu_threads_room_intro():
    """週1回（木曜）夜19時：楽天ROOM誘導投稿（Threads）"""
    import random, time as _time
    try:
        text = random.choice(ROOM_INTRO_TWEETS)
        post_id = _post_to_koharu_threads(text)
        if post_id:
            _time.sleep(5)
            _post_to_koharu_threads(KOHARU_ROOM_URL, reply_to_id=post_id)
        print("koharu threads room intro successful")
    except Exception as e:
        print(f"post_koharu_threads_room_intro error: {e}")


def _get_kvision_x_client():
    import tweepy
    api_key = (os.environ.get('KVISION_X_API_KEY') or '').strip()
    api_secret = (os.environ.get('KVISION_X_API_SECRET') or '').strip()
    access_token = (os.environ.get('KVISION_X_ACCESS_TOKEN') or '').strip()
    access_token_secret = (os.environ.get('KVISION_X_ACCESS_TOKEN_SECRET') or '').strip()
    if not all([api_key, api_secret, access_token, access_token_secret]):
        return None
    return tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret
    )


def _fetch_travel_suggestion(genre):
    """楽天APIで旅行グッズ1件取得 → Claude投稿文生成。(本文, url) のタプルを返す"""
    import random
    from blog_yakuzen import search_rakuten_items
    items = search_rakuten_items(genre['keyword'], hits=5)
    if not items:
        return None, None
    item = random.choice(items)
    name = item['name'][:40]
    price = item['price']
    url = item['url']
    hook = random.choice(TRAVEL_HOOKS)
    prompt = (
        f"商品名：{name}\n価格：{price}円\nジャンル：{genre['name']}\n\n"
        f"X（旧Twitter）投稿文を作ってください。\n"
        f"冒頭は必ず「{hook}」で始める。\n"
        "ルール：\n"
        "・全体3行以内（改行込み）\n"
        "・URLもハッシュタグも含めない（どちらも別途対応）\n"
        "・ですます調NG・口語・体言止めOK\n"
        "・機能ではなく『使った後の未来・変化』を伝える\n"
        "・30〜40代子連れ旅行好きワーママに刺さる言葉を使う\n\n"
        "余計な説明不要。投稿文だけ出力。"
    )
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}]
        )
        body = resp.content[0].text.strip()
    except Exception as e:
        print(f"travel suggestion generate error ({genre['name']}): {e}")
        body = f"{hook}\n{name}"
    return body, url


def post_kvision_morning_tweet():
    """@kvision_m 朝9:00：3型ローテーション・ツリー形式（日付ベース・重複なし）"""
    import time as _time
    try:
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping")
            return
        day_of_year = datetime.datetime.now().timetuple().tm_yday
        t1, t2 = MORNING_TWEET_PAIRS[day_of_year % len(MORNING_TWEET_PAIRS)]
        resp = client.create_tweet(text=t1)
        if t2:
            _time.sleep(3)
            client.create_tweet(text=t2, in_reply_to_tweet_id=resp.data['id'])
        print(f"kvision morning tweet successful: {t1[:30]}...")
    except Exception as e:
        print(f"post_kvision_morning_tweet error: {e}")
        try:
            line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(text=f"❌ @kvision_m 朝つぶやき投稿エラー\n{str(e)[:200]}"))
        except Exception:
            pass


def post_kvision_travel_aff(slot_index):
    """@kvision_mに旅行×楽天アフィをXにスレッド形式で投稿（本文→リプライにURL）"""
    import time as _time
    all_genres = _get_all_kvision_genres()
    genre = all_genres[slot_index % len(all_genres)]
    try:
        body, url = _fetch_travel_suggestion(genre)
        if not body or not url:
            return
        score = _check_kvision_post_quality(body)
        if score < 60:
            print(f"post_kvision_travel_aff: quality low ({score}pts), skipping")
            try:
                line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(
                    text=f"⚠️ @kvision_m アフィスレッド品質低下（{score}点）でスキップ\nジャンル：{genre['name']}\n本文：{body[:100]}"
                ))
            except Exception:
                pass
            return
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping")
            return
        resp = client.create_tweet(text=body)
        tweet_id = resp.data['id']
        _time.sleep(3)
        client.create_tweet(
            text=f"↓ 商品はこちら\n{url}\n[楽天PR]",
            in_reply_to_tweet_id=tweet_id
        )
        _record_genre_used(genre['name'])
        print(f"kvision X thread post ({genre['name']}) successful [score:{score}]")
    except Exception as e:
        print(f"post_kvision_travel_aff({slot_index}) error: {e}")
        try:
            line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(text=f"❌ @kvision_m アフィスレッド投稿エラー（{genre['name']}）\n{str(e)[:200]}"))
        except Exception:
            pass


# ========== 月替わりジャンル・ジャンル管理 ==========

def _get_monthly_kvision_genres():
    """今月の特集ジャンルをkvision_monthly_genres.jsonから取得"""
    import json
    month_key = datetime.datetime.now().strftime('%m')
    try:
        with open('kvision_monthly_genres.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get(month_key, [])
    except Exception as e:
        print(f"monthly kvision genres read error: {e}")
        return []


def _get_all_kvision_genres():
    """固定ジャンル + 今月の特集ジャンルを合わせたリスト"""
    return TRAVEL_GENRES + _get_monthly_kvision_genres()


def _load_genre_log():
    try:
        with open(KVISION_GENRE_LOG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_genre_log(log):
    try:
        with open(KVISION_GENRE_LOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False)
    except Exception as e:
        print(f"genre log save error: {e}")


def _select_least_used_genre():
    """最後に使った日が最も古いジャンルを選ぶ（均等ローテーション）"""
    all_genres = _get_all_kvision_genres()
    log = _load_genre_log()
    return min(all_genres, key=lambda g: log.get(g['name'], ''))


def _record_genre_used(genre_name):
    """ジャンルの使用日時をログに記録"""
    log = _load_genre_log()
    log[genre_name] = datetime.datetime.now().isoformat()
    _save_genre_log(log)


def _check_kvision_post_quality(body):
    """投稿文を100点満点でAI採点。60点未満はNG"""
    prompt = (
        f"以下のX投稿文を100点満点で採点してください。\n\n"
        f"投稿文：\n{body}\n\n"
        f"採点基準：\n"
        f"・口語・自然体で宣伝臭くない：30点\n"
        f"・30〜40代子連れワーママに刺さる言葉：30点\n"
        f"・3行以内・簡潔：20点\n"
        f"・冒頭が自然につながっている：20点\n\n"
        f"数字だけ出力（例：75）"
    )
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=10,
            messages=[{'role': 'user', 'content': prompt}]
        )
        score_str = resp.content[0].text.strip()
        return int(''.join(filter(str.isdigit, score_str)) or '100')
    except Exception:
        return 100  # エラー時は通過させる


# まきが選んだ固定アフィ商品ストック（URL直指定・投稿文もまきのコメントベース）
FIXED_AFF_POSTS = [
    {
        'name': 'ポケットチェア',
        'body': "庭ピクのときとかも大活躍\n折りたためるポケットチェア、アウトドアも運動会も持って行けるサイズ感がちょうどいい",
        'url': 'https://a.r10.to/hXxfyW',
    },
    {
        'name': 'ネッククーラー',
        'body': "これからの季節あったら絶対便利\n熱中症予防に早めに用意しておきたいやつ。子連れのお出かけ前の準備リストに入れてる",
        'url': 'https://a.r10.to/hgY7sa',
    },
    {
        'name': 'ピクニックマット',
        'body': "庭ピクするのにもいつも使ってる\n天気のいい休みの日は庭でピクニックが定番。1枚あると出番がとにかく多い",
        'url': 'https://a.r10.to/hkV4yh',
    },
]


def _post_kvision_fixed_aff(post, client, time_module):
    """固定アフィ商品をXにスレッド形式で投稿"""
    resp = client.create_tweet(text=post['body'])
    tweet_id = resp.data['id']
    time_module.sleep(3)
    client.create_tweet(
        text=f"↓ 商品はこちら\n{post['url']}\n[楽天PR]",
        in_reply_to_tweet_id=tweet_id
    )
    print(f"kvision fixed aff post ({post['name']}) successful")


def post_kvision_travel_aff_auto():
    """最長未使用ジャンルを優先選択。3日に1回は固定アフィストックから投稿"""
    import time as _time
    day = datetime.datetime.now().day
    if FIXED_AFF_POSTS and day % 3 == 0:
        post = FIXED_AFF_POSTS[(day // 3) % len(FIXED_AFF_POSTS)]
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping")
            return
        try:
            _post_kvision_fixed_aff(post, client, _time)
        except Exception as e:
            print(f"post_kvision_fixed_aff error: {e}")
            try:
                line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(text=f"❌ @kvision_m 固定アフィ投稿エラー\n{str(e)[:200]}"))
            except Exception:
                pass
    else:
        all_genres = _get_all_kvision_genres()
        day_of_year = datetime.datetime.now().timetuple().tm_yday
        slot = day_of_year % len(all_genres)
        post_kvision_travel_aff(slot)


# ========== 楽天カード誘導ツイート ==========

CARD_TWEETS = [
    "楽天トラベルで旅行予約するとき、楽天カードで払うとポイントが3〜4倍になる話、旅行前の自分に教えてあげたかった",
    "旅行費用の積立を楽天カードに変えてから、気づいたら年間けっこうなポイントに。子どもの旅行代金に充当してる",
    "楽天プレミアムカード、持ってると空港ラウンジが無料で使える。子連れ旅行の出発前の休憩にこれが地味に最高",
    "楽天マラソン×楽天カードの組み合わせ、旅行グッズのまとめ買いするなら知っておいたほうがいいやつ",
    "海外旅行の荷物リストに「楽天カード」を追加してから、旅先での買い物ポイントが馬鹿にならない",
    "楽天カードの旅行保険、カードで予約すれば付帯されるのに使ってない人多すぎる。子連れ旅行なら確認して",
    "旅行貯金を楽天カードのポイントで賄ってる。現金じゃないから心理的ハードルが低くて続いてる",
]

RAKUTEN_CARD_AFF_URLS = [
    "https://a.r10.to/hkZjJw",
    "https://a.r10.to/h5E1Na",
    "https://a.r10.to/h5yRmz",
    "https://a.r10.to/h5v161",
    "https://a.r10.to/h55SLT",
    "https://a.r10.to/hksjy7",
    "https://a.r10.to/h5MZJX",
    "https://a.r10.to/hYrRdd",
    "https://a.r10.to/h5aOuJ",
    "https://a.r10.to/h57LDq",
]

CARD_AFF_TWEETS_WITH_URL = [
    "楽天カード、旅行好きには正直マストだと思ってる。楽天トラベルのポイント還元が段違い\n\n詳細はこちら↓",
    "子連れ旅行のコスト、楽天カードのポイントで少し軽くできてる。年会費無料でこの恩恵は大きい\n\n詳細はこちら↓",
    "楽天プレミアムカードの空港ラウンジ特典、旅行好きなら元が取れる。子連れ出発前の待機場所として最高\n\n詳細はこちら↓",
    "旅行前にとりあえず楽天カードで予約する癖をつけてから、ポイントがどんどん貯まるようになった\n\n詳細はこちら↓",
    "楽天カードの旅行保険、カードで予約するだけで付帯されるの知ってた？子連れ旅行なら絶対確認して\n\n詳細はこちら↓",
    "子ども連れの旅費って地味にかさむ。楽天カードのポイント還元で少しでも圧縮するのがマイルール\n\n詳細はこちら↓",
    "楽天マラソン前に楽天カード作っておくと、まとめ買いのポイントが倍以上変わる話\n\n詳細はこちら↓",
]


def _pick_card_url():
    """楽天カードアフィURLをランダムに1つ返す"""
    import random
    return random.choice(RAKUTEN_CARD_AFF_URLS)


def post_kvision_card_tweet():
    """週2回（水・土）昼12:30：楽天カード誘導ツイート（スレッド形式）"""
    import random, time as _time
    try:
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping card tweet")
            return
        text = random.choice(CARD_AFF_TWEETS_WITH_URL)
        resp = client.create_tweet(text=text)
        tweet_id = resp.data['id']
        _time.sleep(3)
        client.create_tweet(
            text=f"{_pick_card_url()}\n[楽天PR]",
            in_reply_to_tweet_id=tweet_id
        )
        print("kvision card tweet successful")
    except Exception as e:
        print(f"post_kvision_card_tweet error: {e}")


# ========== こはるまま Threads自動投稿 ==========

def _post_to_koharu_threads(text, reply_to_id=None):
    """こはるままのThreads APIに投稿。成功時はpost_idを返す、失敗時はNone"""
    access_token = os.environ.get('KOHARU_THREADS_ACCESS_TOKEN', '').strip()
    user_id = os.environ.get('KOHARU_THREADS_USER_ID', '').strip()
    if not access_token or not user_id:
        print("KOHARU_THREADS tokens not configured, skipping")
        return None
    try:
        params = {
            'media_type': 'TEXT',
            'text': text,
            'access_token': access_token,
        }
        if reply_to_id:
            params['reply_to_id'] = reply_to_id
        container_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads',
            params=params,
            timeout=15
        )
        if container_res.status_code != 200:
            print(f"koharu threads container error: {container_res.status_code} {container_res.text}")
            return None
        creation_id = container_res.json().get('id')
        import time as _time
        _time.sleep(5)
        publish_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads_publish',
            params={'creation_id': creation_id, 'access_token': access_token},
            timeout=15
        )
        if publish_res.status_code != 200:
            print(f"koharu threads publish error: {publish_res.status_code} {publish_res.text}")
            return None
        post_id = publish_res.json().get('id')
        print(f"koharu threads posted: {post_id}")
        return post_id
    except Exception as e:
        print(f"_post_to_koharu_threads error: {e}")
        return None


def post_koharu_threads_morning():
    """こはるまま Threads朝7:30：旅あるあるテキスト投稿"""
    import random
    try:
        text = random.choice(TRAVEL_MORNING_TWEETS)
        _post_to_koharu_threads(text)
        print(f"koharu threads morning post successful: {text[:30]}...")
    except Exception as e:
        print(f"post_koharu_threads_morning error: {e}")


def post_koharu_threads_aff_auto():
    """こはるまま Threads夜20:00：旅行グッズアフィ投稿（日付ローテーション・Xとずらす）3日に1回は固定ストック"""
    import time as _time
    day = datetime.datetime.now().day
    if FIXED_AFF_POSTS and (day + 1) % 3 == 0:
        post = FIXED_AFF_POSTS[((day + 1) // 3) % len(FIXED_AFF_POSTS)]
        try:
            post_id = _post_to_koharu_threads(post['body'])
            if post_id:
                _time.sleep(5)
                _post_to_koharu_threads(f"↓ 商品はこちら\n{post['url']}\n[楽天PR]", reply_to_id=post_id)
            print(f"koharu threads fixed aff post ({post['name']}) successful")
        except Exception as e:
            print(f"koharu threads fixed aff error: {e}")
        return
    all_genres = _get_all_kvision_genres()
    slot = (day + 3) % len(all_genres)
    genre = all_genres[slot]
    try:
        body, url = _fetch_travel_suggestion(genre)
        if not body or not url:
            return
        post_id = _post_to_koharu_threads(body)
        if post_id and url:
            _time.sleep(5)
            _post_to_koharu_threads(f"↓ 商品はこちら\n{url}\n[楽天PR]", reply_to_id=post_id)
        print(f"koharu threads aff post ({genre['name']}) successful")
    except Exception as e:
        print(f"post_koharu_threads_aff_auto error: {e}")


def post_koharu_threads_card():
    """こはるまま Threads週2回（水・土）12:30：楽天カード誘導（スレッド形式）"""
    import random, time as _time
    try:
        text = random.choice(CARD_AFF_TWEETS_WITH_URL)
        post_id = _post_to_koharu_threads(text)
        if post_id:
            _time.sleep(5)
            _post_to_koharu_threads(f"{_pick_card_url()}\n[楽天PR]", reply_to_id=post_id)
        print("koharu threads card post successful")
    except Exception as e:
        print(f"post_koharu_threads_card error: {e}")


# ========== MAKO Threads自動投稿 ==========

def _post_to_mako_threads(text, reply_to_id=None):
    """MAKOのThreads APIに投稿。成功時はpost_idを返す、失敗時はNone"""
    import time as _time
    access_token = os.environ.get('MAKO_THREADS_ACCESS_TOKEN', '').strip()
    user_id = os.environ.get('MAKO_THREADS_USER_ID', '').strip()
    if not access_token or not user_id:
        print("MAKO_THREADS tokens not configured, skipping")
        return None
    try:
        params = {
            'media_type': 'TEXT',
            'text': text,
            'access_token': access_token,
        }
        if reply_to_id:
            params['reply_to_id'] = reply_to_id
        container_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads',
            params=params,
            timeout=15
        )
        if container_res.status_code != 200:
            print(f"mako threads container error: {container_res.status_code} {container_res.text}")
            return None
        creation_id = container_res.json().get('id')
        _time.sleep(5)
        publish_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads_publish',
            params={'creation_id': creation_id, 'access_token': access_token},
            timeout=15
        )
        if publish_res.status_code != 200:
            print(f"mako threads publish error: {publish_res.status_code} {publish_res.text}")
            return None
        post_id = publish_res.json().get('id')
        print(f"mako threads posted: {post_id}")
        return post_id
    except Exception as e:
        print(f"_post_to_mako_threads error: {e}")
        return None


def _fetch_mako_sleep_suggestion(genre):
    """楽天APIで睡眠グッズ・サプリ1件取得 → Claude投稿文生成（MAKOトーン）。(本文, url) を返す"""
    import random
    from blog_yakuzen import search_rakuten_items
    items = search_rakuten_items(genre['keyword'], hits=5)
    if not items:
        return None, None
    item = random.choice(items)
    name = item['name'][:40]
    price = item['price']
    url = item['url']
    prompt = (
        f"商品名：{name}\n価格：{price}円\nジャンル：{genre['name']}\n\n"
        "Threads投稿文を作ってください（本文のみ。URLなし）。\n"
        "ルール：\n"
        "・医師として発信しているため「売る」方向NG\n"
        "・悩みへの共感から始める\n"
        "・医学・薬膳の知識を淡々と伝える\n"
        "・「〜です」「〜効果があります」などの言い切り表現は使わない\n"
        "・「〜かもしれません」「〜という方もいます」「試してみる価値はあります」などの柔らかい表現を使う\n"
        "・商品への言及は最後の1行で「気になる方はこちら」程度\n"
        "・ハッシュタグなし\n"
        "・全体150文字以内\n\n"
        "余計な説明不要。投稿文だけ出力。"
    )
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=400,
            messages=[{'role': 'user', 'content': prompt}]
        )
        body = resp.content[0].text.strip()
    except Exception as e:
        print(f"mako sleep suggestion generate error ({genre['name']}): {e}")
        body = f"眠れない夜が続いているという方もいるかもしれません。\n\n気になる方はこちら"
    return body, url


def _load_mako_genre_log():
    try:
        with open(MAKO_GENRE_LOG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_mako_genre_log(log):
    try:
        with open(MAKO_GENRE_LOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False)
    except Exception as e:
        print(f"mako genre log save error: {e}")


def _select_mako_least_used_genre():
    """最後に使った日が最も古いMAKOジャンルを選ぶ（均等ローテーション）"""
    log = _load_mako_genre_log()
    return min(MAKO_THREADS_AFF_GENRES, key=lambda g: log.get(g['name'], ''))


def _record_mako_genre_used(genre_name):
    log = _load_mako_genre_log()
    log[genre_name] = datetime.datetime.now().isoformat()
    _save_mako_genre_log(log)


def _check_mako_post_quality(body):
    """MAKO投稿文を100点満点でAI採点。60点未満はNG"""
    prompt = (
        f"以下のThreads投稿文を100点満点で採点してください。\n\n"
        f"投稿文：\n{body}\n\n"
        f"採点基準：\n"
        f"・「〜です」「〜効果があります」など言い切り表現がない：30点\n"
        f"・宣伝・売る感じがなく共感・知識ベース：30点\n"
        f"・悩みへの共感から自然に始まっている：20点\n"
        f"・150文字以内・簡潔：20点\n\n"
        f"数字だけ出力（例：75）"
    )
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=10,
            messages=[{'role': 'user', 'content': prompt}]
        )
        score_str = resp.content[0].text.strip()
        return int(''.join(filter(str.isdigit, score_str)) or '100')
    except Exception:
        return 100


def post_mako_threads_morning():
    """MAKO Threads朝8:00：睡眠共感ツイート"""
    import random
    try:
        text = random.choice(MAKO_THREADS_MORNING)
        _post_to_mako_threads(text)
        print(f"mako threads morning post successful: {text[:30]}...")
    except Exception as e:
        print(f"post_mako_threads_morning error: {e}")


def post_mako_threads_aff_auto():
    """MAKO Threads夜21:00：睡眠グッズ・サプリアフィ投稿（均等ローテーション＋AIチェック）"""
    import time as _time
    genre = _select_mako_least_used_genre()
    try:
        body, url = _fetch_mako_sleep_suggestion(genre)
        if not body or not url:
            return
        score = _check_mako_post_quality(body)
        if score < 60:
            print(f"post_mako_threads_aff_auto: quality low ({score}pts), skipping")
            try:
                line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(
                    text=f"⚠️ MAKO アフィ投稿品質低下（{score}点）でスキップ\nジャンル：{genre['name']}\n本文：{body[:100]}"
                ))
            except Exception:
                pass
            return
        post_id = _post_to_mako_threads(body)
        if post_id and url:
            _time.sleep(5)
            _post_to_mako_threads(
                f"気になる方はこちら\n{url}\n[楽天PR]",
                reply_to_id=post_id
            )
        _record_mako_genre_used(genre['name'])
        print(f"mako threads aff post ({genre['name']}) successful [score:{score}]")
    except Exception as e:
        print(f"post_mako_threads_aff_auto error: {e}")
        try:
            line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(text=f"❌ MAKO アフィ投稿エラー（{genre['name']}）\n{str(e)[:200]}"))
        except Exception:
            pass


# ============================================================
# MAKO（@MAKOhealthcare）X自動投稿
# 朝7:30：えまたまスタイル・ためになる系ツリー（日付ベースローテーション）
# 参考文献：厚労省睡眠ガイドライン・NCNP公式コラム（断言形式OK）
# ============================================================

# (1本目フック, 2本目詳細 or None)  ※ None=1本完結
MAKO_MORNING_TWEET_PAIRS = [
    # ── ランク形式・段階分類型 ──
    (
        "【眠れない夜のレベル4分類】\n\nLv.1 入眠困難\n・布団に入っても30分以上眠れない\n\nLv.2 中途覚醒\n・夜中に何度も目が覚める\n\nLv.3 早朝覚醒\n・起きたい時間より2時間早く目が覚める\n\nLv.4 一番見落とされがち↓",
        "Lv.4 熟眠困難\n・十分寝たのに疲れが取れない\n・「眠った気がしない」感覚\n\nこのタイプが一番気づかれにくい\n\nLv.1〜4のどれかが週3回以上・2週間以上続くと\n不眠症として医療機関に相談できる状態",
    ),
    (
        "【睡眠の質を下げている習慣】\n\n❌ やめた方がいい\n・就寝前のスマホ（メラトニン分泌を抑制）\n・夕方以降のカフェイン（5〜7時間で半減）\n・寝る直前の飲酒\n\n✅ 効果が確認されているのはこれ↓",
        "✅ 厚労省の睡眠ガイドラインに記載があるもの\n\n・毎日同じ時間に起きる（体内時計のリセット）\n・朝に光を浴びる（メラトニンサイクルを整える）\n・就寝90分前の入浴\n\n「眠れない」より\n「眠りを妨げているものを減らす」ほうが早い",
    ),
    (
        "【お酒を飲むと眠れる、は本当か？】\n\n🥉 本当のこと\n・寝つきは確かに早くなる\n・前半の深い睡眠は増える\n\n🥈 でも後半は\n・中途覚醒が増える\n・レム睡眠が乱れる\n\n🥇 一番の問題↓",
        "🥇 耐性ができる\n\n「お酒を飲まないと眠れない」状態は\n数日で起きる\n\n不眠とアルコールは相互に悪化し\n依存へ発展するリスクがある\n\n眠れない夜の原因がお酒になっていないか\n一度振り返る価値がある",
    ),
    (
        "【睡眠負債のリアル】\n\n毎日6時間睡眠を10日続けると\n1晩徹夜したのと同じくらい\nパフォーマンスが低下する\n\nしかも本人は気づいていないことが多い\n\nそして回復するには↓",
        "睡眠負債の回復には\n1か月以上・毎日8時間以上の睡眠が必要\n\n「週末に寝だめ」では回復できない\n\n回復するとき\n起床時間を遅らせるのではなく\nより早く就寝することが推奨されている",
    ),
    (
        "【女性が眠れない理由は男性と違う】\n\n女性が生涯に睡眠問題を経験する可能性は\n男性の約2倍\n\n原因は3つある\n\n①女性ホルモンの変動\n②月経周期の影響\n③更年期・閉経の影響\n\n一番意識されていないのが↓",
        "②月経周期の影響\n\n約40%の女性が月経関連の睡眠変化を経験する\n\n月経前の高温期（プロゲステロン高値）に\n・夜間の覚醒が増える\n・深い睡眠が減る\n・日中の眠気が強くなる\n\n「この時期だけ眠れない」は\nホルモンが原因のことが多い",
    ),
    (
        "【更年期に眠れなくなる理由 3つ】\n\n40代以降から眠りが変わったという方に\n知ってほしいこと\n\n①エストロゲンの低下\n②ホットフラッシュ（体温調節の乱れ）\n③深い睡眠の減少\n\n一番見落とされがちな原因↓",
        "③深い睡眠の減少\n\n年齢とともにノンレム睡眠（深い睡眠）の割合は減る\n更年期のエストロゲン低下でさらに促進される\n\n試せること\n・週150分の有酸素運動\n・大豆イソフラボン（エストロゲン様作用）\n・規則正しい起床時間\n\nホルモン補充療法も不眠改善に有効とされている",
    ),
    (
        "【就寝前のスマホが睡眠を壊す仕組み】\n\n寝る前4時間のタブレット使用を調べた研究で\n\n・メラトニンの分泌が抑制\n・寝つきに時間がかかる\n・体内時計が1.5時間後ろにずれる\n\nという結果が出ている\n\n対策は↓",
        "まず試してほしい順に\n\n✅ 就寝3時間前から部屋を暗くする\n✅ 暖色系（オレンジ）照明に切り替える\n✅ ナイトモード・ブルーライトカットを使う\n\n理想は夜間のデバイス使用を控えること\n\n「スマホをやめる」が難しいなら\nせめて「画面を暗くする」だけでも変わる",
    ),
    (
        "【たばこが睡眠を壊している話】\n\n喫煙者は非喫煙者と比べて\n\n・寝つくまでの時間が長い\n・深い睡眠（徐波睡眠）が少ない\n・総睡眠時間が短い\n\nさらにニコチンの血中半減期は約2時間\n\nだから↓",
        "就寝2時間以上前の喫煙でも\n睡眠に悪影響を与える可能性がある\n\n禁煙すると一時的に不眠が悪化することがある\n（禁煙後1週間がピーク・3〜4週で改善）\n\n眠れないからたばこを吸う、は\n長期的に逆効果になる",
    ),
    (
        "【いびきで気づく睡眠のリスク】\n\nいびきをかく\n息苦しさで目が覚める\n昼間の強い眠気\n\nこれ全部、睡眠時無呼吸症候群の可能性がある\n\n放置すると↓",
        "重症の睡眠時無呼吸を放置すると\n心筋梗塞・脳梗塞・糖尿病・高血圧のリスクが増加する\n\n治療抵抗性の高血圧患者の64%に\n睡眠時無呼吸が認められた報告もある\n\nいびきがひどい・日中の眠気が強い場合は\n一度専門医に相談する価値がある",
    ),
    (
        "【眠れない夜に知ってほしい体温の話】\n\n人は深部体温が下がるときに眠りにつく\n\n皮膚から熱を放散する→深部体温が下がる\n→この流れが早いほど寝つきが早い\n\nじゃあどうするかというと↓",
        "就寝90分前に入浴する\n\n体を温めることで一度深部体温を上げる\n→その後自然に下がる流れができる\n\n手足が温かくなって眠くなるのは\nこの仕組みが起きているサイン\n\n室温は13〜29℃・湿度40〜60%が推奨されている",
    ),
    # ── Tips型・驚き型 ──
    (
        "【コーヒー、何時まで飲んでる？】\n\nカフェインの半減期は5〜7時間\n\n午後3時のコーヒー\n→夜10時にまだ半分体に残っている\n\n眠れない原因がカフェインの人は\n思ったより多い\n\n対策↓",
        "カフェインをカットするのが難しいなら\n\n・午後2時以降は飲まない\n・デカフェに切り替える\n・緑茶・紅茶もカフェインがある\n\nコーヒー好きな人ほど\n「カフェインは大丈夫」と思いがちだが\n耐性がついているだけで影響は出ている",
    ),
    (
        "【起きた瞬間にできる最高の睡眠対策】\n\n夜の眠りを整えるのは\n実は「朝」に何をするかで決まる\n\nその日の夜に自然な眠気がくるためには\n朝の光が必要\n\n理由は↓",
        "朝に光を浴びると\nメラトニンの分泌がリセットされる\n\nメラトニンは光を浴びてから\n14〜16時間後に分泌が始まる\n\n朝7時に光を浴びれば→夜21〜23時に自然な眠気がくる\n\n起床後すぐにカーテンを開ける\nたったこれだけが夜の睡眠を変える",
    ),
    (
        "【8時間眠らないといけない、は本当か？】\n\n推奨睡眠時間は成人で7〜9時間\nこれは「範囲」であって全員同じではない\n\n「自分は6時間でも大丈夫」は\n要注意な理由↓",
        "睡眠負債は本人が気づきにくい\n\n6時間睡眠が続いていても\n「慣れた」と感じるだけで\nパフォーマンスは低下し続けている\n\n本当に必要な睡眠時間は\n「十分に休んだと感じる量」で決まる\n\n日中に強い眠気がある場合は\n睡眠時間が足りていないサイン",
    ),
    (
        "【子育て中に眠れないのは当たり前？】\n\n夜中に何度も起きる\n寝たと思ったら泣き声\n自分の時間は深夜しかない\n\nこれで睡眠不足になるのは当然だけど\n知っておいてほしいことがある↓",
        "睡眠不足は「意志力」「感情コントロール」に直結する\n\n眠れない日が続くと\n・些細なことでイライラしやすくなる\n・判断力が落ちる\n・子どもへの対応が雑になる\n\nこれは意志の問題ではなく脳の問題\n\nまず昼寝20分でも睡眠負債を返すことが\n子育ての質を守ることにつながる",
    ),
    (
        "【昼寝、やり方を間違えると逆効果】\n\n昼寝の効果\n・集中力・作業効率の回復\n・睡眠負債の部分的な返済\n\nでも間違えると夜眠れなくなる\n\n正しい昼寝の条件↓",
        "✅ 正しい昼寝\n\n・時間は20分まで\n・午後3時までに終わらせる\n・横にならずに椅子でうとうす程度でもOK\n\n❌ やってはいけない昼寝\n\n・30分以上（深い睡眠に入ると起きにくくなる）\n・夕方以降（夜の睡眠を妨げる）\n\n昼寝は「補う」ものであって「代わり」にはならない",
    ),
    (
        "【枕で眠れない夜が変わる話】\n\n寝具内部の温度は33℃前後が理想とされている\n（厚労省の睡眠指針より）\n\n高すぎても低すぎても中途覚醒が増える\n\n選ぶときに見るべきポイント↓",
        "✅ 寝具選びのポイント\n\n・通気性（熱がこもらないか）\n・体圧分散（肩・腰・首への負担が分散されるか）\n・高さ（枕は首の自然なカーブを保てるか）\n\n体温調節がうまくできる寝具は\n入眠と中途覚醒の両方に効く\n\n季節ごとに素材・厚さを変えるとさらに効果が出る",
    ),
    (
        "【ストレスが眠りを邪魔する仕組み】\n\n嫌なことを考えながら布団に入ると\n眠れないのは当然\n\nこれは「意志が弱い」のではなく\n体が「戦闘モード」になっているから\n\n脳が興奮状態のときに起きていること↓",
        "ストレスがあるとコルチゾール（ストレスホルモン）が出る\n\nコルチゾールは覚醒を促すホルモン\nこれが夜間に高いままだと眠れない\n\n対策\n・就寝前30分は「考え事をしない時間」を作る\n・呼吸を意識する（吐く息を長くする）\n・寝る前に翌日のToDoを書き出す",
    ),
    (
        "【夕食が睡眠を決める話】\n\n食べるタイミングと何を食べるかで\n眠りの質が変わる\n\n夜遅い夕食が眠りを妨げる理由↓",
        "消化活動は体温を上げる\n\n就寝直前の食事は\n深部体温が下がりにくくなるため\n寝つきが悪くなる原因になる\n\n理想は就寝2〜3時間前に食事を終える\n\nトリプトファン（メラトニンの原料）を含む食材\n・豆腐・納豆・牛乳・バナナ・ナッツ\n\n夕食に取り入れる価値がある",
    ),
    (
        "【運動すると眠れるようになる理由】\n\n「疲れたら眠れる」は本当だが\nタイミングを間違えると逆効果\n\n運動と睡眠の正しい関係↓",
        "有酸素運動（ウォーキング・水泳など）\n週150分が推奨されている\n（更年期の睡眠改善にも有効とされている）\n\nただし就寝直前の激しい運動は\n交感神経を刺激して眠りにくくなる\n\nおすすめ\n・朝〜夕方の有酸素運動\n・就寝前のストレッチ（副交感神経を優位に）",
    ),
    (
        "【妊娠中に眠れなくなる理由】\n\n妊婦の約8割が睡眠の悩みを経験する\n\n「妊娠して眠くなった」が\n「妊娠後期になったら眠れない」に変わる理由↓",
        "妊娠後期に眠れにくくなる主な原因\n\n・子宮が大きくなり膀胱を圧迫→夜間頻尿\n・胎動で目が覚める\n・むずむず脚症候群（鉄欠乏が関係）\n・睡眠時無呼吸のリスク増加\n\n「眠れないのは仕方ない」と思いがちだが\n対処できることもある",
    ),
    (
        "【「薬に頼りたくない」不眠症の方へ】\n\n不眠症の治療は睡眠薬だけじゃない\n\n現在、最も効果が確認されているのは\n「認知行動療法（CBT-I）」という非薬物療法\n\nどんな治療かというと↓",
        "認知行動療法（CBT-I）では\n\n・実際に眠れる時間だけ寝床にいる\n・毎日同じ時間に起きる\n・「眠れない」という思い込みを変える\n\nこれを専門家とともに4〜8回かけて行う\n\n睡眠薬と比べて\n「薬をやめた後も効果が続く」のが特徴",
    ),
    (
        "【体内時計がずれると眠れなくなる】\n\n「夜中の2時まで眠れない」\n「休日は昼まで寝てしまう」\n\nこれは体内時計が後ろにずれているサイン\n\nリセットする最強の方法↓",
        "体内時計は「光」でリセットされる\n\n起床後すぐに明るい光を浴びる\n→その14〜16時間後に自然な眠気がくる\n\n【重要】\n毎日「同じ時刻に起きる」こと\n休日も2時間以上ずれると「社会的時差ぼけ」が起きる\n\nまず起床時間だけ固定するのが\n体内時計リセットの入り口",
    ),
    (
        "【月経前に眠れなくなる理由】\n\n「この時期だけ眠れない」\n「月経前は必ず眠りが浅い」\n\nこれはホルモンのせいで意志の問題ではない\n\n約40%の女性が経験していること\n\n仕組みを知ると↓",
        "月経前の高温期（黄体期）に\nプロゲステロン（黄体ホルモン）が高くなる\n\nこれが深部体温を下げにくくし\n眠りの質を落とす\n\n・夜間の覚醒が増える\n・深い睡眠が減る\n・日中の眠気が強くなる\n\nこの時期だけ睡眠の質が落ちるのは\nほぼ全員に起きていること",
    ),
    # ── 問いかけ型（1本完結） ──
    (
        "眠れない夜に何をしていますか？\n\nスマホを見る・テレビをつける・横になって考え事…\n\nこれ全部、眠れなくなる方向に働いている\n\n眠れないときに「やってはいけないこと」を知っているだけで変わる",
        None,
    ),
    (
        "最近、何時間眠れていますか？\n\n「6時間あれば大丈夫」と思っているなら\n一度見直してほしいことがある\n\n毎日6時間睡眠を10日続けると\n1晩徹夜と同じパフォーマンスになる\nしかも本人は気づいていないことが多い",
        None,
    ),
    (
        "「お酒があれば眠れる」という方へ\n\nそれは解決ではなく悪化のサイン\n\nアルコールは前半の眠りを深くするが\n後半に中途覚醒が増え\n数日で耐性がつき飲む量が増える\n\n眠れない夜の原因がお酒になっていないか振り返ってほしい",
        None,
    ),
    (
        "40代以降で「以前より眠れなくなった」と感じる方\n\nそれ、更年期のせいかもしれない\n\n女性ホルモン（エストロゲン）の低下で\n不眠が増えることは医学的に確認されている\n\n「年のせい」と放置しないでほしい",
        None,
    ),
    (
        "自分のいびきがひどいと言われたことがある方へ\n\nそのいびきは睡眠時無呼吸症候群のサインかもしれない\n\n放置すると心筋梗塞・脳梗塞リスクが上がる\n\n日中の眠気が強い・朝すっきりしない\nこの2つが重なるなら一度専門医に相談してほしい",
        None,
    ),
    (
        "眠れない原因、自分で分かっていますか？\n\n不眠症の4タイプ\n\n・入眠困難（寝つけない）\n・中途覚醒（夜中に目が覚める）\n・早朝覚醒（早く目が覚める）\n・熟眠困難（眠った気がしない）\n\nどれに当てはまるかで対策が変わる",
        None,
    ),
    (
        "「睡眠薬は怖い」と思っている方へ\n\n適切に使えば安全で効果的な治療薬\n\nただし自己判断で急にやめると\n反跳性不眠が起きることがある\n\n「一生やめられない」は古い世代の薬の話\n現在の薬と混同しないでほしい",
        None,
    ),
    (
        "「疲れているのに眠れない」\n\nこれって意外と多い悩みで\n「疲労」と「眠気」は別物\n\n体は疲れていても\n脳が興奮・ストレス状態だと眠れない\n\n眠れない夜が続くなら\n「疲れているから眠れるはず」という思い込みを手放すところから",
        None,
    ),
]


def _get_mako_x_client():
    import tweepy
    api_key = (os.environ.get('MAKO_X_API_KEY') or '').strip()
    api_secret = (os.environ.get('MAKO_X_API_SECRET') or '').strip()
    access_token = (os.environ.get('MAKO_X_ACCESS_TOKEN') or '').strip()
    access_token_secret = (os.environ.get('MAKO_X_ACCESS_TOKEN_SECRET') or '').strip()
    if not all([api_key, api_secret, access_token, access_token_secret]):
        return None
    return tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret
    )


def post_mako_x_morning_tweet():
    """@MAKOhealthcare 朝7:30：えまたまスタイル・ためになる系ツリー（日付ベースローテーション）"""
    import time as _time
    try:
        client = _get_mako_x_client()
        if not client:
            print("MAKO X API keys not configured, skipping")
            return
        day_of_year = datetime.datetime.now().timetuple().tm_yday
        t1, t2 = MAKO_MORNING_TWEET_PAIRS[day_of_year % len(MAKO_MORNING_TWEET_PAIRS)]
        resp = client.create_tweet(text=t1)
        if t2:
            _time.sleep(3)
            client.create_tweet(text=t2, in_reply_to_tweet_id=resp.data['id'])
        print(f"mako x morning tweet successful: {t1[:30]}...")
    except Exception as e:
        print(f"post_mako_x_morning_tweet error: {e}")
        try:
            line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(text=f"❌ @MAKOhealthcare 朝投稿エラー\n{str(e)[:200]}"))
        except Exception:
            pass
