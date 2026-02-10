from dotenv import load_dotenv
load_dotenv()

import os
import requests
from flask import Flask, request, abort

app = Flask(__name__)

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
DIFY_API_KEY = os.getenv("DIFY_API_KEY")
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "https://api.dify.ai")

if not LINE_TOKEN or not DIFY_API_KEY:
    # Render起動時にログで気づけるようにする
    app.logger.warning("Missing env vars: LINE_CHANNEL_ACCESS_TOKEN or DIFY_API_KEY")

def call_dify(user_text: str, user_id: str) -> str:
    url = f"{DIFY_BASE_URL}/v1/chat-messages"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": {},
        "query": user_text,
        "response_mode": "blocking",
        "user": f"line:{user_id}",
    }

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()

    # Difyのレスポンスは通常 answer に入る
    answer = data.get("answer")
    if not answer:
        # 何か変な返り方でも落ちないように保険
        return "Difyの返答が取得できませんでした。もう一度送ってください。"
    return answer

def reply_line(reply_token: str, text: str) -> None:
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()

@app.get("/")
def health():
    return "ok", 200

@app.post("/webhook")
def webhook():
    body = request.get_json(silent=True)
    if not body or "events" not in body:
        return "no events", 200

    for event in body["events"]:
        # メッセージ以外（フォロー等）は無視
        if event.get("type") != "message":
            continue
        message = event.get("message", {})
        if message.get("type") != "text":
            continue

        user_text = message.get("text", "")
        reply_token = event.get("replyToken")
        user_id = (event.get("source") or {}).get("userId", "unknown")

        if not reply_token:
            continue

        try:
            dify_answer = call_dify(user_text, user_id)
            reply_line(reply_token, dify_answer)
        except requests.HTTPError as e:
            app.logger.exception("HTTP error")
            # LINEへは短く返す（エラー詳細はログへ）
            try:
                reply_line(reply_token, "エラーが発生しました。もう一度送ってください。")
            except Exception:
                pass
            abort(200)
        except Exception:
            app.logger.exception("Unhandled error")
            try:
                reply_line(reply_token, "エラーが発生しました。もう一度送ってください。")
            except Exception:
                pass

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
