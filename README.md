\# Money - 專業級多模態 AI 程式設計助理 (LINE Bot)



這是一個專門為軟體工程師、學生與開發者打造的專業級 LINE AI 助理。後台裝載 Google \*\*Gemini 2.5 Flash\*\* 旗艦多模態大腦，具備聽覺、視覺與高精確度程式撰寫能力。



\## 🚀 核心商用功能

\- \*\*多組 API 金鑰自動輪替機制\*\*：內建 Rate Limit 防護線，第一把金鑰受阻（429 錯誤）時，0.5 秒內自動背景輪替，保證服務永不斷線。

\- \*\*多模態雙眼 Debug 通道\*\*：支援 LINE 圖片事件，使用者可直接拍下電腦螢幕的 Error Log 或程式碼截圖，AI 將自動對焦並給出修復方案。

\- \*\*專業級「聲碼分離」黑科技\*\*：手機文字訊息呈現完美的 Markdown 程式碼區塊；語音條則經過 Regex 過濾器清洗，只朗讀流暢、舒適的台灣口語邏輯解說（黃金語速 200）。

\- \*\*企業級記憶控制與自動清理\*\*：保留最近 10 輪親密工程對話防 Token 爆炸，並在每次收到訊息時，背景自動清空 5 分鐘前的舊音訊暫存檔。



\## 🛠️ 開發與部署環境

\- Language: Python 3.10+

\- Framework: Flask, LINE Bot SDK v3, Google GenAI SDK, pyttsx3

\- Deployment Target: Render / Local Webhook via ngrok



