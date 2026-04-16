import os
import pytz
from linebot import LineBotApi, WebhookHandler
from anthropic import Anthropic

line_bot_api = LineBotApi(os.environ['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ['LINE_CHANNEL_SECRET'])
anthropic_client = Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
JST = pytz.timezone('Asia/Tokyo')
