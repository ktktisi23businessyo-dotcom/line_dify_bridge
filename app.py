import os
import requests
from flask import Flask, request

app = Flask(__name__)

# ===== 環境変数 =====
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
DIFY_API_KEY = os.getenv("DIFY_API_KEY")
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "https://api.dify.ai")

if not LINE_TOKEN or not DIFY_API_KEY:
    app.logger.warning("Missing env vars: LINE_CHANNEL_ACCESS_TOKEN or DIFY_API_KEY")


def call_dify(user_text: str, user_id: str) -> str:
    """
    Difyへ問い合わせて answer を返す（blockingモード）
    400/401などのときは本文をログに出して原因特定できるようにする
    """
    url = f"{DIFY_BASE_URL}/v1/chat-messages"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": {},
        "query": user_text,
        "response_mode": "blocking",  # ★まずは最も単純なblockingで安定させる
        "user": f"line:{user_id}",
        "files": [],
        "auto_generate_name": True,
    }

    app.logger.info(f"Dify request URL: {url}")

    r = requests.post(url, headers=headers, json=payload, timeout=60)

    # ★失敗時は必ず本文をログに出す（ここが原因特定の決め手）
    if r.status_code >= 400:
        app.logger.error(f"Dify error {r.status_code} headers: {dict(r.headers)}")
        app.logger.error(f"Dify error {r.status_code} body: {r.text}")
        r.raise_for_status()

    data = r.json()
    answer = data.get("answer")
    return answer or "（Difyの返答が空でした）もう一度送ってください。"


def reply_line(reply_token: str, text: str) -> None:
    """
    LINEへ返信する
    """
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
    if r.status_code >= 400:
        app.logger.error(f"LINE reply error {r.status_code} body: {r.text}")
        r.raise_for_status()


@app.get("/")
def health():
    return "ok", 200


@app.post("/webhook")
def webhook():
    body = request.get_json(silent=True)
    if not body or "events" not in body:
        return "OK", 200

    for event in body["events"]:
        if event.get("type") != "message":
            continue

        message = event.get("message", {})
        if message.get("type") != "text":
            continue

        reply_token = event.get("replyToken")
        user_text = message.get("text", "")
        user_id = (event.get("source") or {}).get("userId", "unknown")

        if not reply_token:
            continue

        try:
            dify_answer = call_dify(user_text, user_id)
            reply_line(reply_token, dify_answer)

        except Exception:
            app.logger.exception("Error during webhook handling")
            try:
                reply_line(reply_token, "エラーが発生しました。もう一度送ってください。")
            except Exception:
                pass
            return "OK", 200

    return "OK", 200


if __name__ == "__main__":
    # ローカル確認用（Render本番は gunicorn app:app で起動される）
    app.run(host="0.0.0.0", port=8000, debug=True)
