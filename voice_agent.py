import os
import time
import re
import json
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
import gspread
from google.oauth2.service_account import Credentials

USER_CHAT_HISTORIES = {}
USER_PROJECT_NOTES = {}  # 格式會變成：{ "用戶ID": { "專案A": "詳情", "專案B": "詳情" } }

# ☁️ 【真．雲端硬碟控制中心】：綁定您的 Google 試算表
GOOGLE_SPREADSHEET_ID = "1BtczApamJO4cdvAPu_1BRbyml64MrgtXlF5bupjs3gk"

try:
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file("google_creds.json", scopes=scopes)
    gc = gspread.authorize(creds)
    sht = gc.open_by_key(GOOGLE_SPREADSHEET_ID)
    wks = sht.sheet1  # 鎖定第一個分頁

    # 💡 三欄式開荒防呆：自動鋪好 user_id、project_name、project_detail 欄位！
    if not wks.get_all_values():
        wks.append_row(["user_id", "project_name", "project_detail"])
        print("📊 [Google硬碟初始化] 偵測到空試算表，已自動建立三欄式商用骨架！", flush=True)

    # 📡 地毯式載入：將雲端試算表上的所有多專案歷史資料，一秒載入雙層記憶體
    all_records = wks.get_all_records()
    for row in all_records:
        uid = str(row.get('user_id', '')).strip()
        pname = str(row.get('project_name', '')).strip()
        pdetail = str(row.get('project_detail', '')).strip()
        if uid and pname:
            if uid not in USER_PROJECT_NOTES:
                USER_PROJECT_NOTES[uid] = {}
            USER_PROJECT_NOTES[uid][pname] = pdetail
            
    print(f"☁️ [Google硬碟連線成功] 成功從雲端復原 {len(USER_PROJECT_NOTES)} 位用戶的所有多專案記憶！", flush=True)

except Exception as g_err:
    print(f"⚠️ 連接您的 Google 雲端硬碟失敗: {g_err}", flush=True)
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

# 🧠 核心思考中樞：絕對防禦重置版（徹底解決進入迴圈前的截斷死角）
def ask_jarvis(user_id, content_part, mime_type=None):
    global current_key_idx, USER_CHAT_HISTORIES, USER_PROJECT_NOTES
    
    # 1. 每次呼叫，強制在函數內部初始化區域變數，絕不共用狀態
    failed_keys_count = 0
    success = False
    reply_text = "⚠️ 賈維斯大腦目前嚴重超載，請幫我等待 30 秒後再試一次喔！"
    
    if user_id not in USER_CHAT_HISTORIES: USER_CHAT_HISTORIES[user_id] = []
    
    # 🛡️ 終極型態防禦：如果不在裡面，或者發現殘留舊版的純文字，強制洗成乾淨的新版字典！
    if user_id not in USER_PROJECT_NOTES or isinstance(USER_PROJECT_NOTES.get(user_id), str):
        USER_PROJECT_NOTES[user_id] = {}

    if not mime_type and isinstance(content_part, str):
        user_msg = content_part.strip()

        # 📂 【通道 A：新增/修改指定專案】
        # 指令格式：記憶專案：[專案名稱] 專案內容描述
        if user_msg.startswith("記憶專案：") or user_msg.startswith("記憶專案:"):
            raw_content = user_msg.split("：", 1)[-1].split(":", 1)[-1].strip()
            
            # 🔍 智慧提取專案名稱：支援 [ ]、【 】、「 」、( )，如果沒寫則預設為 "未命名專案"
            p_name = "未命名專案"
            p_detail = raw_content
            match = re.match(r'[\[【「\((](.*?)[\]】」\))]', raw_content)
            if match:
                p_name = match.group(1).strip()
                p_detail = raw_content.replace(match.group(0), "").strip()

            # 更新本機短期記憶體
            USER_PROJECT_NOTES[user_id][p_name] = p_detail
            
            # ⚡ 同步寫入 Google 試算表（一案一行）
            try:
                # 點名搜索：尋找同時滿足「user_id」且「第二欄專案名稱吻合」的那一行
                all_cells = wks.findall(user_id, in_column=1)
                target_row = None
                for c in all_cells:
                    if str(wks.cell(c.row, 2).value).strip() == p_name:
                        target_row = c.row
                        break
                
                if target_row:
                    wks.update_cell(target_row, 3, p_detail) # 專案存在，更新第三欄規格詳情
                    print(f"📝 用戶 {user_id} 成功覆寫專案【{p_name}】的記憶。", flush=True)
                else:
                    wks.append_row([user_id, p_name, p_detail]) # 專案不存在，追加一行新紀錄
                    print(f"📝 用戶 {user_id} 成功建立全新專案【{p_name}】並佔用新行。", flush=True)
            except Exception as g_err:
                print(f"⚠️ Google 試算表寫入失敗: {g_err}", flush=True)
                
            return f"📂 【專案記憶成功】Money 已將專案【{p_name}】鎖定至您的 Google 雲端試算表！\n\n📌 規格：\n{p_detail}"
        
        # 🗑️ 【通道 B：指定名稱刪除專案】
        # 指令格式：刪除專案：專案名稱
        if user_msg.startswith("刪除專案：") or user_msg.startswith("刪除專案:"):
            p_name = user_msg.split("：", 1)[-1].split(":", 1)[-1].strip()
            
            if not p_name:
                return "❌ 報告主人，請告訴我要刪除哪一個專案喔！例如：`刪除專案：lei profile`"

            if p_name in USER_PROJECT_NOTES[user_id]:
                # 擦除本機短期記憶
                del USER_PROJECT_NOTES[user_id][p_name]
                
                # ⚡ 同步在 Google 試算表上精準「挖掉這一行」
                try:
                    all_cells = wks.findall(user_id, in_column=1)
                    deleted_from_cloud = False
                    for c in all_cells:
                        if str(wks.cell(c.row, 2).value).strip() == p_name:
                            wks.delete_rows(c.row) # 精準物理刪除這行
                            deleted_from_cloud = True
                            print(f"🗑️ 已從雲端試算表物理抹除用戶 {user_id} 的專案【{p_name}】！", flush=True)
                            break
                except Exception as g_err:
                    print(f"⚠️ Google 試算表刪除失敗: {g_err}", flush=True)
                    
                return f"🗑️ 【專案抹除成功】屬於您的專案【{p_name}】已從雲端硬碟與記憶區徹底移除囉！"
            return f"❌ 報告主人，我的雲端大腦裡本來就沒有綁定過叫做【{p_name}】的專案喔！"
            
        # 💡 防呆分支：如果只打了「刪除專案」四個字卻沒給名字
        if user_msg == "刪除專案":
            return "❌ 報告主人，為了防止誤刪，請帶上專案名稱喔！\n格式：`刪除專案：專案名稱`"

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
    
    if USER_PROJECT_NOTES.get(user_id):
        project_strings = ""
        for name, detail in USER_PROJECT_NOTES[user_id].items():
            project_strings += f"\n📁 專案名稱：【{name}】\n📌 規格規格：\n{detail}\n" + "-"*30
        
        base_instruction += f"\n\n🚨【重要多專案規格架構，回答必須圍繞以下活著的專案】：\n{project_strings}"
    # 🎯 照妖鏡日誌：強制印出目前陣列長度與計數器，抓出為什麼不進迴圈
    print(f"📡 [準備進入思考迴圈] 用戶: {user_id}, 金鑰總數: {len(API_KEYS)}, 當前失敗計數: {failed_keys_count}, 成功狀態: {success}", flush=True)

    # 4. 核心金鑰輪替重試迴圈
    total_keys = len(API_KEYS) if len(API_KEYS) > 0 else 1
    while failed_keys_count < total_keys and not success:
        try:
            print(f"🧠 [嘗試中] 正在使用第 {current_key_idx + 1} 把金鑰思考... (目前累計失敗: {failed_keys_count}/{total_keys})", flush=True)
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
                print(f"✅ 第 {current_key_idx + 1} 把金鑰運算成功！", flush=True)
                break
            else:
                print(f"⚠️ 金鑰 {current_key_idx + 1} 回傳空文字，自動切換。", flush=True)
                switch_to_next_key()
                failed_keys_count += 1
                
        except APIError as e:
            print(f"⚠️ 第 {current_key_idx + 1} 把金鑰受阻 (錯誤碼: {e.code})，原因: {e.message}", flush=True)
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

# 5. 【通道二】處理「語音訊息」（終極完美版：MimeType 原生解碼晶片，免安裝 FFmpeg）
@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    print(f"\n🎙️ 收到來自用戶的 LINE 語音！下載二進位數據並啟用原生解碼...", flush=True)
    
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
        print(f"📤 已將【聽懂 AAC 語音的解答 + 口語解說語音】同步傳回手機！", flush=True)
        
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
        # 清除暫存檔
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

# 智能專案面板接口：讓網頁前端開機時自動撈取目前儲存的所有專案列表
@app.route("/get-projects", methods=['GET'])
def get_projects():
    web_user_id = "web_platform_user"
    user_projects = USER_PROJECT_NOTES.get(web_user_id, {})
    return jsonify({"projects": user_projects})

@app.route("/web-chat", methods=['POST'])
def web_chat():
    user_text = request.form.get('text', request.form.get('msg', request.form.get('message', ''))).strip()
    image_file = request.files.get('image')
    audio_file = request.files.get('audio')  # 🎙️ 網頁版雙向語音通道
    web_user_id = "web_platform_user" 
    
    print(f"🌐 [網頁端傳輸攔截] 文字: '{user_text}', 有無圖片: {image_file is not None}, 有無語音: {audio_file is not None}", flush=True)
    
    # 處理網頁端錄音上傳
    if audio_file:
        try:
            audio_bytes = audio_file.read()
            # 透過原生解碼，直接把網頁錄製的音訊位元組送給 Jarvis 處理
            reply_text = ask_jarvis(web_user_id, content_part=audio_bytes, mime_type='audio/aac')
        except Exception as aud_err:
            print(f"❌ 網頁讀取語音位元組失敗: {aud_err}", flush=True)
            reply_text = "🤖 賈維斯沒能聽清您的網頁語音，請再試一次喔！"
    
    # 處理圖片或純文字
    elif image_file:
        try:
            image_bytes = image_file.read()
            reply_text = ask_jarvis(web_user_id, content_part=image_bytes, mime_type='image/jpeg')
        except Exception as img_err:
            print(f"❌ 網頁讀取圖片位元組失敗: {img_err}", flush=True)
            reply_text = ask_jarvis(web_user_id, content_part=user_text)
    else:
        if not user_text:
            user_text = "嗨，Money！我剛剛點擊了網頁，請跟我打個招呼並自我介紹。"
        reply_text = ask_jarvis(web_user_id, content_part=user_text)
        
    print(f"📡 [網頁端準備回傳] AI 解答長度: {len(reply_text)} 字。", flush=True)
    
    # 🎙️ 雙向語音核心：後端自動將 AI 回覆的文字轉成語音檔，一併傳回給網頁播放！
    audio_url = ""
    if not user_text.startswith("刪除專案") and not user_text.startswith("記憶專案"):
        try:
            audio_dir = "static/audio"
            os.makedirs(audio_dir, exist_ok=True)
            timestamp = int(time.time())
            filename = f"web_reply_{timestamp}.mp3"
            filepath = os.path.join(audio_dir, filename)
            
            # 過濾程式碼，避免語音朗讀亂碼
            clean_text = re.sub(r'```[\s\S]*?```', '，好的，這段核心程式碼我已經發送到你的螢幕畫面上了。', reply_text)
            clean_text = clean_text.replace("**", "").replace("*", "").replace("`", "").strip()
            
            if clean_text:
                tts = gTTS(text=clean_text, lang='zh-tw', slow=False)
                tts.save(filepath)
                audio_url = f"/static/audio/{filename}"
        except Exception as tts_err:
            print(f"⚠️ 網頁版語音轉譯失敗: {tts_err}", flush=True)

    return jsonify({"reply": reply_text, "audio_url": audio_url})

# ==================== 【🚀 系統主程式啟動大門】 ====================
# 💡 鐵律：這段啟動程式碼必須永遠死死捍衛在整個檔案的最底層、最後一行！
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
