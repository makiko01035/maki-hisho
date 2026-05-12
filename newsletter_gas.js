// ===================================================
// まきのメルマガ自動要約スクリプト（Google Apps Script）
// 設定方法：
//   1. script.google.com を makikokimura51@gmail.com で開く
//   2. 新しいプロジェクト作成 → このコードを貼り付け
//   3. 「プロジェクトのプロパティ」→「スクリプトのプロパティ」に以下を追加：
//      ANTHROPIC_API_KEY : sk-ant-xxxxx（Renderの環境変数と同じ値）
//      NOTIFY_SECRET     : maki2025
//   4. 「トリガー」→「トリガーを追加」→ processNewsletter を 週1回（日曜 20:00〜21:00）に設定
// ===================================================

var MAKI_HISHO_URL = 'https://maki-hisho.onrender.com/newsletter-summary';

function processNewsletter() {
  var props = PropertiesService.getScriptProperties();
  var apiKey = props.getProperty('ANTHROPIC_API_KEY');
  var secret = props.getProperty('NOTIFY_SECRET');

  // 未読メールを最大30件取得
  var threads = GmailApp.search('is:unread', 0, 30);
  if (threads.length === 0) {
    console.log('未読メールなし');
    return;
  }

  var emails = [];
  var threadsToDelete = [];

  for (var i = 0; i < threads.length; i++) {
    var thread = threads[i];
    var messages = thread.getMessages();
    var msg = messages[messages.length - 1];

    var from = msg.getFrom();
    var subject = msg.getSubject();
    var body = msg.getPlainBody().substring(0, 1500);

    emails.push({
      index: emails.length + 1,
      from: from,
      subject: subject,
      body: body,
      threadId: thread.getId()
    });
    threadsToDelete.push(thread);
  }

  // Claude APIで要約・フィルタリング
  var emailsText = emails.map(function(e) {
    return '[' + e.index + '] 差出人: ' + e.from + '\n件名: ' + e.subject + '\n本文: ' + e.body;
  }).join('\n\n---\n\n');

  var prompt = 'まき（医療職・3児ワーママ・副業：eBay物販・アフィリエイト・不動産投資に関心）宛のメールマガジンを分析して要約してください。\n\n' +
    '【分類ルール】\n' +
    '- 副業・ビジネス系：要点をまとめる（ビジネスに役立つ内容を優先）\n' +
    '- 不動産紹介：新築アパート・新築RC・土地の案件があればピックアップ。なければ「案件なし」と書いてスキップ\n' +
    '- 求人：「求人」と書くだけでスキップ（要約不要）\n' +
    '- その他：1行で要点のみ\n\n' +
    '【出力形式】（各メールを番号付きで、求人は1行で）\n' +
    '①【副業】差出人名\n要点：...\n\n' +
    '②【不動産】差出人名\n🏠 物件名・利回り・価格など\n\n' +
    '③【求人】→ 削除済み\n\n' +
    '【メール一覧】\n' + emailsText;

  var claudeResponse = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01'
    },
    payload: JSON.stringify({
      model: 'claude-sonnet-4-6',
      max_tokens: 2000,
      messages: [{ role: 'user', content: prompt }]
    }),
    muteHttpExceptions: true
  });

  var result = JSON.parse(claudeResponse.getContentText());
  var summary = result.content[0].text;

  // maki-hishoにPOSTしてLINEに送信
  var emailsForSession = emails.map(function(e) {
    return {
      index: e.index,
      from: e.from,
      subject: e.subject,
      summary: '',  // Claude要約は全体まとめに含まれるので個別summaryは空
      category: ''
    };
  });

  UrlFetchApp.fetch(MAKI_HISHO_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    payload: JSON.stringify({
      secret: secret,
      summary: summary,
      emails: emailsForSession
    }),
    muteHttpExceptions: true
  });

  // メールを削除
  for (var j = 0; j < threadsToDelete.length; j++) {
    threadsToDelete[j].moveToTrash();
  }

  console.log('完了：' + emails.length + '件処理');
}
