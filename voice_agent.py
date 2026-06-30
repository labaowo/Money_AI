import os
import time
import re
import logging
from flask import Flask, request, abort, render_template, jsonify, send_from_directory
from dotenv import load_dotenv
from gtts import gTTS
from jinja2.exceptions import TemplateNotFound

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

# 引入我們新建立的資料庫模型
from models import Session, Project, engine, Base 

# 0. 載入環境變數
load_dotenv()

# ==================== 【日誌設定】 ====================
logging.basicConfig(
    level=logging.INFO, # 設定日誌等級為 INFO
    format='%(asctime)s - %(levelname)s - %(message)s', # 日誌格式
    handlers=[
        logging.StreamHandler() # 將日誌輸出到控制台
        # 如果需要將日誌輸出到檔案，可以取消註解下面這行
        # logging.FileHandler("app.log", encoding="utf-8") 
    ]
)
logger = logging.getLogger(__name__) # 取得當前模組的日誌器

app = Flask(__name__)

# 確保資料表在應用程式啟動時被建立
# 對於 SQLite，它會建立 projects.db 檔案和裡面的 projects 表格
# 對於 PostgreSQL，它會連線到資料庫並建立表格
with app.app_context():
    Base.metadata.create_all(engine)
    logger.info("✅ 資料庫表格檢查與建立完成！")

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
    logger.info(f"🔑 系統初始化成功：地毯式搜索完畢！已成功載入 {len(API_KEYS)} 組完全獨立的 API 金鑰進行輪替。")
    
current_key_idx = 0
USER_CHAT_HISTORIES = {} 
BASE_URL = os.getenv("BASE_URL", "https://onrender.com")

def get_current_client():
    global current_key_idx
    return genai.Client(api_key=API_KEYS[current_key_idx])

def switch_to_next_key():
    global current_key_idx
    if len(API_KEYS) > 0:
        current_key_idx = (current_key_idx + 1) % len(API_KEYS)
    logger.warning(f"🔀 【金鑰輪替】自動切換至第 {current_key_idx + 1} 把金鑰！")
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
                    logger.info(f"🗑️ [硬碟自動清理] 已強制抹除舊檔案: {filename}")
    except Exception as e:
        logger.error(f"⚠️ 清理舊檔案時發生異常: {e}")

# 🧠 核心思考中樞：絕對防禦重置版（徹底解決進入迴圈前的截斷死角）
def ask_jarvis(user_id, content_part, mime_type=None):
    global current_key_idx, USER_CHAT_HISTORIES # 移除 USER_PROJECT_NOTES
    
    # 1. 每次呼叫，強制在函數內部初始化區域變數，絕不共用狀態
    failed_keys_count = 0
    success = False
    reply_text = "⚠️ 賈維斯大腦目前嚴重超載，請幫我等待 30 秒後再試一次喔！"
    
    if user_id not in USER_CHAT_HISTORIES: USER_CHAT_HISTORIES[user_id] = []

    # 建立資料庫會話
    session = Session()
    current_project_note = "" # 用於儲存從資料庫讀取的專案筆記內容
    
    try: # 新增 try 區塊，將整個 ask_jarvis 的主要邏輯包起來
        # 嘗試從資料庫中取得該使用者的專案記憶
        project_entry = session.query(Project).filter_by(user_id=user_id).first()
        if project_entry:
            current_project_note = project_entry.description

        # 2. 檢查使用者特殊指令
        if not mime_type and isinstance(content_part, str):
            if content_part.startswith("記憶專案：") or content_part.startswith("記憶專案:"):
                project_detail = content_part.split("：", 1)[-1].split(":", 1)[-1].strip()
                
                if project_entry:
                    # 如果已有專案記憶，則更新
                    project_entry.description = project_detail
                    session.add(project_entry)
                    logger.info(f"📝 用戶 {user_id} 成功更新永久專案記憶。")
                else:
                    # 如果沒有專案記憶，則新增
                    new_project = Project(user_id=user_id, description=project_detail)
                    session.add(new_project)
                    logger.info(f"📝 用戶 {user_id} 成功鎖定永久專案記憶。")
                
                session.commit() # 提交變更到資料庫
                return f"📂 【專案記憶成功】Money 已將此專案架構永久鎖定！\n\n📌 架構：\n{project_detail}"
            
            if content_part.strip() == "刪除專案":
                if project_entry:
                    session.delete(project_entry) # 從資料庫刪除
                    session.commit() # 提交變更
                    logger.info(f"🗑️ 用戶 {user_id} 已手動清空永久專案記憶。")
                    return "🗑️ 【專案記憶已清空】目前的專案架構已從永久記憶區抹除囉！"
                return "❌ 報告主人，目前本來就沒有綁定任何專案喔！"

    # 3. 數據格式封裝
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
    # 使用從資料庫讀取的專案筆記
    if current_project_note:
        base_instruction += f"\n\n🚨【重要專案架構，回答必須圍繞此規格】：\n{current_project_note}"

    # 🎯 照妖鏡日誌：強制印出目前陣列長度與計數器，抓出為什麼不進迴圈
    print(f"📡 [準備進入思考迴圈] 用戶: {user_id}, 金鑰總數: {len(API_KEYS)}, 當前失敗計數: {failed_keys_count}, 成功狀態: {success}", flush=True)

        # 4. 核心金鑰輪替重試迴圈
        total_keys = len(API_KEYS) if len(API_KEYS) > 0 else 1
        while failed_keys_count < total_keys and not success:
            try:
                logger.info(f"🧠 [嘗試中] 正在使用第 {current_key_idx + 1} 把金鑰思考... (目前累計失敗: {failed_keys_count}/{total_keys})")
                client = get_current_client()
                
                chat = client.chats.create(
                    model='gemini-2.5-flash',
                    history=USER_CHAT_HISTORIES[user_id],
                    config=types.GenerateContentConfig(
                        system_instruction=base_instruction,
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0.2,
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
                    logger.info(f"✅ 第 {current_key_idx + 1} 把金鑰運算成功！")
                    break
                else:
                    logger.warning(f"⚠️ 金鑰 {current_key_idx + 1} 回傳空文字，自動切換。")
                    switch_to_next_key()
                    failed_keys_count += 1
                    
            except APIError as e:
                logger.warning(f"⚠️ 第 {current_key_idx + 1} 把金鑰受阻 (錯誤碼: {e.code})，原因: {e.message}")
                switch_to_next_key()
                failed_keys_count += 1
            except Exception as e:
                logger.error(f"❌ 發生非 API 限制的系統未知錯誤: {e}")
                reply_text = f"🤖 賈維斯核心錯誤: {e}"
                break
    finally:
        session.close() # 確保每次請求結束都關閉資料庫會話            
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
        logger.info(f"⚡ 雲端 Google 擬真語音檔生成成功！")
    except Exception as tts_err:
        logger.error(f"⚠️ 語音引擎調用失敗: {tts_err}")

    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            # 確保時長精整化為 int，防止 LINE v3 拒收
            duration_ms = int((len(clean_text_for_voice) * 230) + 400)
            if duration_ms < 2000: duration_ms = 2000
            
            audio_url = f"{BASE_URL}/static/audio/{filename}"
            logger.info(f"🔊 語音條長度已精準校配：{duration_ms} 毫秒。網址: {audio_url}")
            
            messages = [
                TextMessage(text=text_content),
                AudioMessage(original_content_url=audio_url, duration=duration_ms)
            ]
        else:
            logger.warning("⚠️ 語音檔案生成失敗，降級發送純文字。")
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
    logger.info(f"\n💬 收到來自用戶的 LINE 文字: '{user_text}'")
    
    clean_expired_audio_files()
    reply_text = ask_jarvis(user_id, content_part=user_text)
    send_dual_reply(event.reply_token, reply_text)
    logger.info(f"📤 已將【文字解答 + 口語解說語音】同步傳回手機！")

# 5. 【通道二】處理「語音訊息」（終極完美版：MimeType 原生解碼晶片，免安裝 FFmpeg）
@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    logger.info(f"\n🎙️ 收到來自用戶的 LINE 語音！下載二進位數據並啟用原生解碼...")
    
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
            
        # ⚡ 核心解鎖：將 mime_type 修正為標準格式 'audio/aac'！
        # 這樣一來，Gemini 就能免轉檔、100% 直接聽懂 LINE 下載下來的 m4a 檔案！
        reply_text = ask_jarvis(user_id, content_part=audio_bytes, mime_type='audio/aac')
        
        # 進行文字 + 語音同步發送
        send_dual_reply(event.reply_token, reply_text)
        logger.info(f"📤 已將【聽懂 AAC 語音的解答 + 口語解說語音】同步傳回手機！")
        
    except Exception as e:
        logger.error(f"❌ 語音識別核心崩潰: {e}")
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="🤖 抱歉主人，我的耳朵剛剛開小差了，請再說一次或打字告訴我喔！")]
                )
            )
    finally:
        # 清除暫存檔，增加錯誤處理
        if os.path.exists(temp_audio_path):
            try:
                os.remove(temp_audio_path)
                logger.info(f"🗑️ 已清除暫存音訊檔: {temp_audio_path}")
            except OSError as e:
                logger.error(f"❌ 清除暫存音訊檔 {temp_audio_path} 失敗: {e}")

# 6. 【通道三】處理「圖片/照片訊息」
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    logger.info(f"\n📸 收到來自用戶的 LINE 圖片！正在辨識程式截圖或錯誤畫面...")
    
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
        logger.info(f"📤 已將【看懂照片的解答 + 口語解說語音】同步傳回手機！")
    except Exception as e:
        logger.error(f"❌ 影像視覺核心崩潰: {e}")
    finally:
        # 清除暫存檔，增加錯誤處理
        if os.path.exists(temp_img_path):
            try:
                os.remove(temp_img_path)
                logger.info(f"🗑️ 已清除暫存圖片檔: {temp_img_path}")
            except OSError as e:
                logger.error(f"❌ 清除暫存圖片檔 {temp_img_path} 失敗: {e}")

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
        logger.warning("⚠️ LINE Webhook Signature 驗證失敗。")
        abort(400)
    except Exception as e:
        logger.error(f"❌ LINE Webhook 處理時發生未知錯誤: {e}")
        abort(500)
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
    except TemplateNotFound:
        logger.warning("⚠️ 網頁模板 index.html 未找到，提供預設訊息。")
        return "🟢 Money AI 伺服器已在雲端安全上線！", 200
    except Exception as e:
        logger.error(f"❌ 渲染網頁時發生未知錯誤: {e}")
        return "🔴 Money AI 伺服器啟動失敗，請檢查日誌。", 500

# ==================== 【🌐 雲端電腦網頁版端點（防呆抓漏絕對修正版）】 ====================
@app.route("/web-chat", methods=['POST'])
def web_chat():
    # 修正：地毯式防呆搜索！不管前端欄位叫 text、msg、message 還是內容，通通都抓！
    user_text = request.form.get('text', request.form.get('msg', request.form.get('message', ''))).strip()
    image_file = request.files.get('image')
    web_user_id = "web_platform_user" # 網頁版共用一個 user_id，如果需要獨立，需實作登入機制
    
    # 照妖鏡 2 號：在路由最上方強制列印前端到底送了什麼過來！
    logger.info(f"🌐 [網頁端傳輸攔截] 收到文字欄位: '{user_text}', 是否有帶圖片: {image_file is not None}")
    
    # 如果真的什麼都沒有傳過來，強制塞入警示文字發給大腦，不讓它當機
    if not user_text and not image_file:
        user_text = "嗨，Money！我剛剛點擊了網頁，請跟我打個招呼並自我介紹。"

    # 這裡的專案記憶和刪除專案指令也需要透過 ask_jarvis 處理，ask_jarvis 會操作資料庫
    # 這裡不需要額外處理，直接讓 ask_jarvis 判斷並操作資料庫
    # if "記憶專案：" in user_text or "記憶專案:" in user_text: pass
    # if user_text == "刪除專案": pass

    # 正確導向大腦核心
    if image_file:
        try:
            image_bytes = image_file.read()
            reply_text = ask_jarvis(web_user_id, content_part=image_bytes, mime_type='image/jpeg')
        except Exception as img_err:
            logger.error(f"❌ 網頁讀取圖片位元組失敗: {img_err}")
            reply_text = ask_jarvis(web_user_id, content_part=user_text) # 圖片失敗，嘗試只用文字回覆
    else:
        reply_text = ask_jarvis(web_user_id, content_part=user_text)
        
    logger.info(f"📡 [網頁端準備回傳] AI 解答長度: {len(reply_text)} 字。")
    return jsonify({"reply": reply_text})

# ==================== 【🚀 系統主程式啟動大門】 ====================
# 鐵律：這段啟動程式碼必須永遠死死捍衛在整個檔案的最底層、最後一行！
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
