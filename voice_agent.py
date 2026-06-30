import os
import time
import re
from flask import Flask, request, abort, render_template, jsonify, send_from_directory
from dotenv import load_dotenv
from gtts import gTTS

# ==================== 【✅ LINE Bot v3 規格套件】 ====================
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
    AudioMessage
)
from linebot.v3.webhooks import (
    MessageEvent, 
    TextMessageContent, 
    AudioMessageContent,
    ImageMessageContent
)
# ============================================================

from google import genai
from google.genai import types
from google.genai.errors import APIError

# 0. 載入環境變數
load_dotenv()

app = Flask(__name__)

# 1. 初始化 LINE v3 配置
configuration = Configuration(access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# 2. API 金鑰輪替設定（安全地毯式點名法：完美破解 Render 變數排序錯亂問題）
API_KEYS = []
for idx in range(1, 21):  # 精準點名 1 到 20 號環境變數
    key = os.getenv(f"GEMINI_API_KEY_{idx}")
    if key and key.strip():
        API_KEYS.append(key.strip())

if not API_KEYS and os.getenv("GEMINI_API_KEY"):
    API_KEYS.append(os.getenv("GEMINI_API_KEY").strip())

# 💡 加上 flush=True 讓 Render 網頁日誌在開機時，立刻吐出到底成功抓到了幾把鑰匙！
print(f"🔑 系統初始化成功：地毯式搜索完畢！已成功載入 {len(API_KEYS)} 組完全獨立的 API 金鑰進行輪替。", flush=True)

current_key_idx = 0
USER_CHAT_HISTORIES = {}
USER_PROJECT_NOTES = {} 
BASE_URL = os.getenv("BASE_URL", "https://onrender.com")

def get_current_client():
    global current_key_idx
    return genai.Client(api_key=API_KEYS[current_key_idx])

def switch_to_next_key():
    global current_key_idx
    if len(API_KEYS) > 0:
        current_key_idx = (current_key_idx + 1) % len(API_KEYS)
    print(f"🔀 【金鑰輪替】自動切換至第 {current_key_idx + 1} 把金鑰！", flush=True)
# 🧹 消失的清潔隊：負責定時清空 5 分鐘前的舊語音，維持硬碟乾淨，避免 I/O 阻塞
def clean_expired_audio_files():
    audio_dir = "static/audio"
    if not os.path.exists(audio_dir):
        return
        
    current_time = time.time()
    expiration_time = 300 # 5 分鐘
    
    try:
        for filename in os.listdir(audio_dir):
            file_path = os.path.join(audio_dir, filename)
            if os.path.isfile(file_path):
                file_creation_time = os.path.getmtime(file_path)
                if (current_time - file_creation_time) > expiration_time:
                    os.remove(file_path)
                    print(f"🗑️ [硬碟自動清理] 已強制抹除舊檔案: {filename}", flush=True)
    except Exception as e:
        print(f"⚠️ 清理舊檔案時發生異常: {e}", flush=True)

# 🧠 核心思考中樞：升級為「專案永久記憶」雙軌大腦（全面加上 flush=True）
def ask_jarvis(user_id, content_part, mime_type=None):
    global current_key_idx, USER_CHAT_HISTORIES, USER_PROJECT_NOTES
    
    if user_id not in USER_CHAT_HISTORIES: USER_CHAT_HISTORIES[user_id] = []
    if user_id not in USER_PROJECT_NOTES: USER_PROJECT_NOTES[user_id] = ""
        
    failed_keys_count = 0
    success = False
    reply_text = "⚠️ 賈維斯大腦目前嚴重超載，請幫我等待 30 秒後再試一次喔！"

    # 💡 檢查使用者是否輸入了特殊的特殊控制指令
    if not mime_type and isinstance(content_part, str):
        if content_part.startswith("記憶專案：") or content_part.startswith("記憶專案:"):
            project_detail = content_part.split("：", 1)[-1].split(":", 1)[-1].strip()
            USER_PROJECT_NOTES[user_id] = project_detail
            print(f"📝 用戶 {user_id} 成功鎖定永久專案記憶。", flush=True)
            return f"📂 【專案記憶成功】Money 已將此專案架構永久鎖定在核心記憶區！所有回答都必須圍繞此規格：\n\n📌 架構：\n{project_detail}"
        
        if content_part.strip() == "刪除專案":
            if USER_PROJECT_NOTES[user_id]:
                USER_PROJECT_NOTES[user_id] = ""
                print(f"🗑️ 用戶 {user_id} 已手動清空永久專案記憶。", flush=True)
                return "🗑️ 【專案記憶已清空】Money 已經把目前的專案架構從永久記憶區抹除囉！"
            return "❌ 報告主人，目前本來就沒有綁定任何專案喔！"

    # 📥 包裝多模態與純文字數據
    if mime_type:
        user_part = types.Part.from_bytes(data=content_part, mime_type=mime_type)
        if 'audio' in mime_type:
            prompt_part = types.Part.from_text(text="使用者傳送了音訊檔案。請聽語音內容並結合歷史回答。")
        elif 'image' in mime_type:
            prompt_part = types.Part.from_text(text="使用者傳送了照片。請仔細看圖片細節並結合歷史回答。")
        else:
            prompt_part = types.Part.from_text(text="請處理此檔案並回答使用者。")
        message_payload = [user_part, prompt_part]
    else:
        message_payload = [types.Part.from_text(text=f"這是用戶輸入的文字：'{content_part}'。請結合歷史回答他。")]

    base_instruction = (
        "你是一位精通軟體工程、架構設計的頂級 AI 工程師助理，名字叫作『Money』。\n"
        "1. 文字回覆中包含程式碼請務必使用標準 ```python ... ``` 包覆。\n"
        "2. 請使用台灣口語繁體中文與專業術語，拒絕大陸用語。"
    )
    
    if USER_PROJECT_NOTES[user_id]:
        base_instruction += f"\n\n🚨【重要專案架構，回答必須圍繞此規格】：\n{USER_PROJECT_NOTES[user_id]}"

    # 開始輪替金鑰思考（完美修正新版 SDK 安全設定與模型規格）
    while failed_keys_count < max(len(API_KEYS), 1) and not success:
        try:
            print(f"🧠 [嘗試中] 正在使用第 {current_key_idx + 1} 把金鑰思考... (目前累計失敗: {failed_keys_count})", flush=True)
            client = get_current_client()
            
            # 🔥 修正 1：全面改用免費額度最大、最穩定的 gemini-2.5-flash 模型
            chat = client.chats.create(
                model='gemini-2.5-flash',
                history=USER_CHAT_HISTORIES[user_id],
                config=types.GenerateContentConfig(
                    system_instruction=base_instruction,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.2,
                    # 🔥 修正 2：完美封裝新版 SDK 規定的官方標準安全過濾器物件
                    safety_settings=[
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                    ]
                )
            )
            
            response = chat.send_message(message=message_payload)
            
            if response.text:
                reply_text = response.text
                USER_CHAT_HISTORIES[user_id] = chat.get_history()
                success = True
                print(f"✅ 第 {current_key_idx + 1} 把金鑰運算成功！", flush=True)
                
                if len(USER_CHAT_HISTORIES[user_id]) > 20:
                    USER_CHAT_HISTORIES[user_id] = USER_CHAT_HISTORIES[user_id][-20:]
                    print(f"✂️ [日常記憶修剪] 已自動裁切日常對話，永久專案記憶依舊安全鎖定。", flush=True)
            else:
                print(f"⚠️ 金鑰 {current_key_idx + 1} 回傳了空文字，準備切換金鑰。", flush=True)
                switch_to_next_key()
                failed_keys_count += 1
                
        except APIError as e:
            # 加上 flush=True 讓我們在 Render 清楚看到 Google 到底拋出什麼拒絕錯誤碼
            print(f"⚠️ 第 {current_key_idx + 1} 把金鑰受阻 (錯誤碼: {e.code})，詳細訊息: {e.message}", flush=True)
            switch_to_next_key()
            failed_keys_count += 1
        except Exception as e:
            print(f"❌ 發生非 API 限制的系統未知錯誤: {e}", flush=True)
            reply_text = f"🤖 賈維斯核心錯誤: {e}"
            break
            
    return reply_text
# ==================== 【📣 LINE 雙重訊息發送模組】 ====================
def send_dual_reply(reply_token, text_content):
    audio_dir = "static/audio"
    if not os.path.exists(audio_dir):
        os.makedirs(audio_dir, exist_ok=True)
        
    timestamp = int(time.time())
    filename = f"reply_{timestamp}.mp3"
    filepath = os.path.join(audio_dir, filename)
    
    # 智慧隔離：移除程式碼區塊，防止語音朗讀程式碼亂碼
    clean_text_for_voice = re.sub(r'```[\s\S]*?```', '，好的，這段核心程式碼我已經發送到你的手機畫面上了，你可以直接複製參考，以下為你解釋它的運作邏輯。', text_content)
    clean_text_for_voice = clean_text_for_voice.replace("**", "").replace("*", "").replace("`", "").replace('"', '').replace("'", "")
    clean_text_for_voice = clean_text_for_voice.strip()
    
    try:
        tts = gTTS(text=clean_text_for_voice, lang='zh-tw', slow=False)
        tts.save(filepath)
        print(f"⚡ 雲端 Google 擬真語音檔生成成功！", flush=True)
    except Exception as tts_err:
        print(f"⚠️ 語音引擎調用失敗: {tts_err}", flush=True)

    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            # 確保時長精整化為 int，防止 LINE v3 拒收
            duration_ms = int((len(clean_text_for_voice) * 230) + 400)
            if duration_ms < 2000: duration_ms = 2000
            
            audio_url = f"{BASE_URL}/static/audio/{filename}"
            print(f"🔊 語音條長度已精準校配：{duration_ms} 毫秒。網址: {audio_url}", flush=True)
            
            messages = [
                TextMessage(text=text_content),
                AudioMessage(original_content_url=audio_url, duration=duration_ms)
            ]
        else:
            print("⚠️ 語音檔案生成失敗，降級發送純文字。", flush=True)
            messages = [TextMessage(text=text_content)]
            
        messaging_api.reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )
# ==================== 【📥 LINE 訊息事件接收處理通道】 ====================

# 4. 【通道一】處理「文字訊息」
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_text = event.message.text
    print(f"\n💬 收到來自用戶的 LINE 文字: '{user_text}'", flush=True)
    
    clean_expired_audio_files()
    reply_text = ask_jarvis(user_id, content_part=user_text)
    send_dual_reply(event.reply_token, reply_text)
    print(f"📤 已將【文字解答 + 口語解說語音】同步傳回手機！", flush=True)

# 5. 【通道二】處理「語音訊息」
@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    print(f"\n🎙️ 收到來自用戶的 LINE 語音！正在嘗試下載並轉譯大腦...", flush=True)
    
    clean_expired_audio_files()
    temp_audio_path = f"{message_id}.m4a"
    
    with ApiClient(configuration) as api_client:
        messaging_api_blob = MessagingApiBlob(api_client)
        message_content = messaging_api_blob.get_message_content(message_id)
        with open(temp_audio_path, 'wb') as fd:
            fd.write(message_content)
            
    try:
        with open(temp_audio_path, "rb") as f:
            audio_bytes = f.read()
            
        reply_text = ask_jarvis(user_id, content_part=audio_bytes, mime_type='audio/m4a')
        send_dual_reply(event.reply_token, reply_text)
        print(f"📤 已將【聽懂語音的解答 + 口語解說語音】同步傳回手機！", flush=True)
    except Exception as e:
        print(f"❌ 語音識別核心崩潰: {e}", flush=True)
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="🤖 抱歉主人，我的耳朵剛剛開小差了，請再說一次或打字告訴我喔！")]
                )
            )
    finally:
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)

# 6. 【通道三】處理「圖片/照片訊息」
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    print(f"\n📸 收到來自用戶的 LINE 圖片！正在辨識程式截圖或錯誤畫面...", flush=True)
    
    clean_expired_audio_files()
    temp_img_path = f"{message_id}.jpg"
    
    with ApiClient(configuration) as api_client:
        messaging_api_blob = MessagingApiBlob(api_client)
        message_content = messaging_api_blob.get_message_content(message_id)
        with open(temp_img_path, 'wb') as fd:
            fd.write(message_content)
            
    try:
        with open(temp_img_path, "rb") as f:
            image_bytes = f.read()
            
        reply_text = ask_jarvis(user_id, content_part=image_bytes, mime_type='image/jpeg')
        send_dual_reply(event.reply_token, reply_text)
        print(f"📤 已將【看懂照片的解答 + 口語解說語音】同步傳回手機！", flush=True)
    except Exception as e:
        print(f"❌ 影像視覺核心崩潰: {e}", flush=True)
    finally:
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)

# ==================== 【✅ LINE 官方 Webhook 入口門牌與路由】 ====================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    if "00000000000000000000000000000000" in body:
        return 'OK'
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/static/audio/<filename>", methods=['GET'])
def serve_audio(filename):
    return send_from_directory("static/audio", filename)

# ==================== 【🌐 雲端電腦網頁版端點】 ====================
# 網頁通道一：讓瀏覽器能成功打開網頁畫面
@app.route("/", methods=['GET'])
def web_index():
    try:
        return render_template("index.html")
    except Exception:
        return "🟢 Money AI 伺服器已在雲端安全上線！", 200

# 網頁通道二：處理電腦網頁發送過來的文字與照片，並用同一個大腦回應
@app.route("/web-chat", methods=['POST'])
def web_chat():
    user_text = request.form.get('text', '')
    image_file = request.files.get('image')
    web_user_id = "web_platform_user" 
    
    print(f"🌐 [網頁端請求] 收到訊息: {user_text}", flush=True)
    
    if "漏動指令：" in user_text:
        real_cmd = user_text.replace("漏動指令：", "")
        _ = ask_jarvis(web_user_id, content_part=real_cmd)
        return jsonify({"reply": "📂 【專案配置成功鎖定】Money 已將左側專案架構永久鎖定！右側對話可以毫無顧慮地開發囉！"})
        
    if user_text == "刪除專案":
        _ = ask_jarvis(web_user_id, content_part="刪除專案")
        return jsonify({"reply": "🗑️ 【專案記憶已清空】目前的專案架構已從永久記憶區抹除囉！"})

    if image_file:
        image_bytes = image_file.read()
        reply_text = ask_jarvis(web_user_id, content_part=image_bytes, mime_type='image/jpeg')
    else:
        reply_text = ask_jarvis(web_user_id, content_part=user_text)
        
    return jsonify({"reply": reply_text})

# ==================== 【🚀 系統主程式啟動大門】 ====================
# 💡 鐵律：這段啟動程式碼必須永遠死死捍衛在整個檔案的最底層、最後一行！
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
