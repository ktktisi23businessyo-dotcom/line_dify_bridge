import os
import json
import requests
from flask import Flask, request

app = Flask(__name__)

# ===== 環境変数 =====
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
DIFY_API_KEY = os.getenv("DIFY_API_KEY")
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "https://api.dify.ai")

if not LINE_TOKEN or not DIFY_API_KEY:
    app.logger.warning("Missing env vars: LINE_CHANNEL_ACCESS_TOKEN or DIFY_API_KEY")

# ===== ユーザーごとの会話ID（簡易メモリ）=====
# ※ Render再起動で消えるが、今回はOK
USER_CONVERSATIONS = {}


# ===== Dify 呼び出し（streaming / SSE）=====
def call_dify(user_text: str, user_id: str) -> str:
    url = f"{DIFY_BASE_URL}/v1/chat-messages"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
    }

    conversation_id = USER_CONVERSATIONS.get(user_id, "")

    payload = {
        "inputs": {},
        "query": user_text,
        "response_mode": "streaming",  # ★重要
        "conversation_id": conversation_id,
        "user": f"line:{user_id}",
        "files": [],
        "auto_generate_name": True,
    }

    app.logger.info(f"Dify request URL: {url}")

    r = requests.post(
        url,
        headers=headers,
        json=payload,
        stream=True,
        timeout=60,
    )

    # 400/401 などはここで捕まえる
    if r.status_code >= 400:
        app.logger.error(f"Dify error {r.status_code}: {r.text}")
        r.raise_for_status()

    answer_parts = []

    # SSE（data: {...}）を読む
    for raw_line in r.iter_lines(decode_unicode=True):
        if not raw_line:
            continue

        line = raw_line.strip()
        if not line.startswith("data:"):
            continue

        data_str = line[len("data:"):].strip()

        if data_str == "[DONE]":
            break

        try:
            obj = json.loads(data_str)
        except Exception:
            continue

        # 会話IDを保存
        cid = obj.get("conversation_id")
        if cid:
            USER_CONVERSATIONS[user_id] = cid

        # 回答本文
        if isinstance(obj.get("answer"), str):
            answer_parts.append(obj["answer"])

        # 終了イベント
        if obj.get("event") in ("message_end", "message_end_event"):
            break

    answer = "".join(answer_parts).strip()
    return answer or "（返答が取得できませんでした。もう一度送ってください。）"


# ===== LINE 返信 =====
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


# ===== ヘルスチェック =====
@app.get("/")
def health():
    return "ok", 200


# ===== Webhook =====
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


# ===== ローカル実行用（Renderでは使われない）=====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
