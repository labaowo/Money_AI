import os
import time
import re
from flask import Flask, request, abort

# ==================== 【LINE Bot v3 規格】 ====================
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
from dotenv import load_dotenv
from gtts import gTTS  # 👈 完美更換成雲端專用的 gTTS 套件晶片！
# 0. 載入環境變數
load_dotenv()

app = Flask(__name__)

# 1. 初始化 LINE v3 配置
configuration = Configuration(access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# 2. API 金鑰輪替設定
API_KEYS = []
key_index = 1
while True:
    key = os.getenv(f"GEMINI_API_KEY_{key_index}")
    if key:
        API_KEYS.append(key)
        key_index += 1
    else:
        break

if not API_KEYS and os.getenv("GEMINI_API_KEY"):
    API_KEYS.append(os.getenv("GEMINI_API_KEY"))

print(f"🔑 系統初始化成功：已載入 {len(API_KEYS)} 組 API 金鑰進行自動轮替。")
current_key_idx = 0
USER_CHAT_HISTORIES = {}
# 💡 全新晶片：專案永久記憶筆記本 (獨立於歷史紀錄之外，不說刪除就永不清洗)
USER_PROJECT_NOTES = {} 
# 🌐 補回遺失的雲端網址變數：優先讀取 Render 後台設定的 BASE_URL，讀不到則使用預設值
BASE_URL = os.getenv("BASE_URL", "https://onrender.com")

def get_current_client():
    global current_key_idx
    return genai.Client(api_key=API_KEYS[current_key_idx])

def switch_to_next_key():
    global current_key_idx
    current_key_idx = (current_key_idx + 1) % len(API_KEYS)
    print(f"🔀 【金鑰輪替】自動切換至第 {current_key_idx + 1} 把金鑰！")

# 核心思考中樞：升級為「專案永久記憶」雙軌大腦
def ask_jarvis(user_id, content_part, mime_type=None):
    global current_key_idx, USER_CHAT_HISTORIES, USER_PROJECT_NOTES
    
    # 初始化該用戶的記憶區
    if user_id not in USER_CHAT_HISTORIES: USER_CHAT_HISTORIES[user_id] = []
    if user_id not in USER_PROJECT_NOTES: USER_PROJECT_NOTES[user_id] = ""
        
    failed_keys_count = 0
    success = False
    reply_text = "⚠️ 賈維斯大腦目前嚴重超載，請幫我等待 30 秒後再試一次喔！"

    # 💡 檢查使用者是否輸入了「記憶專案」或「刪除專案」的特殊控制指令
    if not mime_type and isinstance(content_part, str):
        # 1. 記憶專案指令
        if content_part.startswith("記憶專案：") or content_part.startswith("記憶專案:"):
            project_detail = content_part.split("：", 1)[-1].split(":", 1)[-1].strip()
            USER_PROJECT_NOTES[user_id] = project_detail
            return f"📂 【專案記憶成功】Money 已將此專案架構永久鎖定在核心記憶區！除非你對我說『刪除專案』，否則我絕對不會忘記它囉！\n\n📌 當前鎖定架構：\n{project_detail}"
        
        # 2. 刪除專案指令
        if content_part.strip() == "刪除專案":
            if USER_PROJECT_NOTES[user_id]:
                USER_PROJECT_NOTES[user_id] = ""
                return "🗑️ 【專案記憶已清空】Money 已經把目前的專案架構從永久記憶區抹除囉！我們隨時可以開始新專案！"
            return "❌ 報告主人，目前本來就沒有綁定任何專案喔！"

    # 包裝多模態數據
    if mime_type:
        user_part = types.Part.from_bytes(data=content_part, mime_type=mime_type)
        if 'audio' in mime_type:
            prompt_part = types.Part.from_text(text="使用者剛剛對你傳送了一段音訊檔案。請立刻『聽』這段音訊並理解其語音內容，結合之前的對話歷史回答他。")
        elif 'image' in mime_type:
            prompt_part = types.Part.from_text(text="使用者剛剛對你傳送了一張照片或圖片。請立刻仔細『看』這張圖片的細節與內容，結合之前的對話歷史回答他。")
        else:
            prompt_part = types.Part.from_text(text="請處理此檔案並回答使用者。")
        message_payload = [user_part, prompt_part]
    else:
        message_payload = f"這是用戶剛剛對你輸入的文字：'{content_part}'。請結合之前的對話歷史回答他。"

    # 💡 動態組裝系統提示詞：如果這個用戶有綁定專案，就強行把專案釘在最上方！
    base_instruction = (
        "你是一位精通各類軟體工程、架構設計與演算法的資深頂級 AI 軟體工程師兼個人助理，名字叫作『Money』。\n"
        "你的使用者正在透過 LINE 或網頁對你進行程式諮詢。請提供『專業級』、架構清晰的代碼範例。\n"
        "【回覆規範鐵律】：\n"
        "1. 文字回覆中如果包含程式碼範例，請務必使用標準的 Markdown 語法封裝（例如 ```python ... ```）。\n"
        "2. 請使用台灣口語繁體中文與專業工程術語，拒絕大陸用語。\n"
        "3. 內容請精準直白、一針見血。回答長度控制在正常範圍內。"
    )
    
    # 如果有專案筆記，強行注入大腦最底層的核心指令
    if USER_PROJECT_NOTES[user_id]:
        base_instruction += f"\n\n🚨【重要：目前正在開發的永久專案架構如下，此內容絕對不能忘記，所有回答都必須圍繞此規格】：\n{USER_PROJECT_NOTES[user_id]}"

    while failed_keys_count < len(API_KEYS) and not success:
        try:
            print(f"🧠 [嘗試中] 正在使用第 {current_key_idx + 1} 把金鑰思考...")
            client = get_current_client()
            
            chat = client.chats.create(
                model='gemini-2.5-pro',
                history=USER_CHAT_HISTORIES[user_id],
                config=types.GenerateContentConfig(
                    system_instruction=base_instruction, # 💡 動態注入包含永久專案的指導語
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.2,
                    safety_settings=[
                        types.SafetySetting(category=1, threshold=4),
                        types.SafetySetting(category=2, threshold=4),
                        types.SafetySetting(category=3, threshold=4),
                        types.SafetySetting(category=4, threshold=4),
                        types.SafetySetting(category=5, threshold=4),
                    ]
                )
            )
            
            response = chat.send_message(message=message_payload)
            
            if response.text:
                reply_text = response.text
                USER_CHAT_HISTORIES[user_id] = chat.get_history()
                success = True
                print(f"✅ 第 {current_key_idx + 1} 把金鑰運算成功！")
                
                # 防 Token 爆炸日常記憶裁切 (保留最近 10 輪對話)
                # 💡 這裡裁切完全不會傷到上面的專案筆記，因為專案是獨立外掛在 system_instruction 裡面的！
                if len(USER_CHAT_HISTORIES[user_id]) > 20:
                    USER_CHAT_HISTORIES[user_id] = USER_CHAT_HISTORIES[user_id][-20:]
                    print(f"✂️ [日常記憶修剪] 已自動裁切日常對話，永久專案記憶依舊安全鎖定。")
                
            else:
                switch_to_next_key()
                failed_keys_count += 1
                
        except APIError as e:
            print(f"⚠️ 第 {current_key_idx + 1} 把金鑰受阻 (錯誤碼: {e.code})。")
            switch_to_next_key()
            failed_keys_count += 1
        except Exception as e:
            print(f"❌ 發生非 API 限制的錯誤: {e}")
            reply_text = f"🤖 賈維斯核心錯誤: {e}"
            break
            
    return reply_text

# 協助發送雙重訊息（文字 + 語音）：專業級聲碼分離優化版
from gtts import gTTS # 確保這行寫在函式上方

def send_dual_reply(reply_token, text_content):
    audio_dir = "static/audio"
    if not os.path.exists(audio_dir):
        os.makedirs(audio_dir, exist_ok=True)
        
    timestamp = int(time.time())
    # 💡 雲端與手機相容性最高的標準 MP3 格式
    filename = f"reply_{timestamp}.mp3"
    filepath = os.path.join(audio_dir, filename)
    
    # 智慧隔離：把文字中所有的 ```...``` 區塊（程式碼）全部移除，防止語音引擎唸一堆亂碼
    clean_text_for_voice = re.sub(r'```[\s\S]*?```', '，好的，這段核心程式碼我已經發送到你的手機畫面上了，你可以直接複製參考，以下為你解釋它的運作邏輯。', text_content)
    clean_text_for_voice = clean_text_for_voice.replace("**", "").replace("*", "").replace("`", "").replace('"', '').replace("'", "")
    clean_text_for_voice = clean_text_for_voice.strip()
    
    try:
        # 直接調用雲端最穩定的 Google 語音引擎 (指定台灣中文口音，不卡執行緒)
        tts = gTTS(text=clean_text_for_voice, lang='zh-tw', slow=False)
        tts.save(filepath)
        print(f"⚡ 雲端 Google 擬真語音檔生成成功！")
    except Exception as tts_err:
        print(f"⚠️ 語音引擎調用失敗: {tts_err}")

    # 使用 v3 MessagingApi 發送
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            # 語速時間計算：一個字分配 230 毫秒，加 400 毫秒尾音，節奏乾脆自然
            duration_ms = (len(clean_text_for_voice) * 230) + 400
            if duration_ms < 2000: duration_ms = 2000
            
            audio_url = f"{BASE_URL}/static/audio/{filename}"
            print(f"🔊 語音條長度已精準校配：{duration_ms} 毫秒。")
            
            messages = [
                TextMessage(text=text_content),  # 手機秀出漂亮原始代碼
                AudioMessage(original_content_url=audio_url, duration=duration_ms)  # 語音只播中文解說
            ]
        else:
            print("⚠️ 語音檔案生成失敗，降級發送純文字。")
            messages = [TextMessage(text=text_content)]
            
        messaging_api.reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )
# ==================== 【✅ LINE 訊息事件接收處理通道】 ====================
import time

# 🧹 消失的清潔隊：負責定時清空 5 分鐘前的舊語音與暫存照片檔，維持雲端硬碟乾淨
def clean_expired_audio_files():
    audio_dir = "static/audio"
    if not os.path.exists(audio_dir):
        return
        
    current_time = time.time()
    # 5 分鐘 = 300 秒
    expiration_time = 300 
    
    try:
        for filename in os.listdir(audio_dir):
            file_path = os.path.join(audio_dir, filename)
            if os.path.isfile(file_path):
                file_creation_time = os.path.getmtime(file_path)
                if (current_time - file_creation_time) > expiration_time:
                    os.remove(file_path)
                    print(f"🗑️ [硬碟自動清理] 已強制抹除 5 分鐘前的舊檔案: {filename}")
    except Exception as e:
        print(f"⚠️ 清理舊檔案時發生異常: {e}")

# 4. 【通道一】處理「文字訊息」
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_text = event.message.text
    print(f"\n💬 收到來自用戶的 LINE 文字: '{user_text}'")
    
    clean_expired_audio_files()
    reply_text = ask_jarvis(user_id, content_part=user_text)
    send_dual_reply(event.reply_token, reply_text)
    print(f"📤 已將【文字解答 + 口語解說語音】同步傳回手機！")

# 5. 【通道二】處理「語音訊息」
@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    print(f"\n🎙️ 收到來自用戶的 LINE 語音！正在嘗試下載並轉譯大腦...")
    
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
        print(f"📤 已將【聽懂語音的解答 + 口語解說語音】同步傳回手機！")
        
    except Exception as e:
        print(f"❌ 語音識別核心崩潰: {e}")
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
    print(f"\n📸 收到來自用戶的 LINE 圖片！正在辨識程式截圖或錯誤畫面...")
    
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
        print(f"📤 已將【看懂照片的解答 + 口語解說語音】同步傳回手機！")
        
    except Exception as e:
        print(f"❌ 影像視覺核心崩潰: {e}")
    finally:
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)
# ==================== 【✅ LINE 官方 Webhook 入口門牌】 ====================
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
    from flask import send_from_directory
    return send_from_directory("static/audio", filename)

# ==================== 【🌐 雲端電腦網頁版端點】 ====================
from flask import render_template, jsonify

# 網頁通道一：讓瀏覽器能成功打開網頁畫面
@app.route("/", methods=['GET'])
def web_index():
    return render_template("index.html")

# 網頁通道二：處理電腦網頁發送過來的文字與照片，並用同一個大腦回應
@app.route("/web-chat", methods=['POST'])
def web_chat():
    user_text = request.form.get('text', '')
    image_file = request.files.get('image')
    web_user_id = "web_platform_user" 
    
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
    app.run(host='0.0.0.0', port=5000, threaded=True)
