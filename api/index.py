from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from supabase import create_client, Client
import os
from datetime import datetime, timedelta

app = FastAPI(redirect_slashes=False)

# 載入環境變數
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/api")
def read_root():
    return {"status": "Database API is running"}

@app.post("/api")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # 1. 取得群組或個人 ID，達成資料隔離
    source_type = event.source.type
    if source_type == "group":
        chat_id = event.source.group_id
    elif source_type == "room":
        chat_id = event.source.room_id
    else:
        chat_id = event.source.user_id
        
    user_id = event.source.user_id 

    # 2. 清洗文字 (轉大寫、去前後空格)
    raw_msg = event.message.text.strip().upper()
    
    # 過濾群組標記，只抓取含有 K 的指令部分
    if "K" in raw_msg:
        user_msg = "K" + raw_msg.split("K", 1)[1].strip()
    else:
        user_msg = raw_msg

    # 情況 A：查詢群組專屬清單 K LIST
    if user_msg == "K LIST":
        response = supabase.table("boss_records").select("*").eq("chat_id", chat_id).execute()
        records = response.data
        
        if not records:
            reply_text = "📊 本群組目前沒有任何王怪的擊殺紀錄。"
        else:
            reply_text = "📊 本群目前王怪出沒時間：\n"
            records.sort(key=lambda x: x["next_spawn_time"])
            
            for rec in records:
                boss_name = rec["boss_name"]
                next_time = datetime.fromisoformat(rec["next_spawn_time"].replace("Z", "+00:00"))
                
                # 計算倒數 (台灣時間 UTC+8)
                now = datetime.now(next_time.tzinfo)
                countdown = next_time - now
                minutes_left = int(countdown.total_seconds() / 60)
                
                time_str = next_time.strftime("%H:%M")
                if minutes_left > 0:
                    reply_text += f"▪️ {boss_name}：預計 {time_str} ({minutes_left}分鐘後)\n"
                else:
                    reply_text += f"🔺 {boss_name}：已超時 {-minutes_left}分鐘！\n"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text.strip()))
        return

    # 情況 B：回報擊殺 K [王怪名稱]
    if user_msg.startswith("K"):
        boss_name = user_msg.replace("K", "").strip()
        
        # 至 Supabase 查詢該王怪在網頁設定的重生間隔
        config_resp = supabase.table("boss_config").select("*").eq("boss_name", boss_name).execute()
        
        if config_resp.data:
            interval = config_resp.data[0]["respawn_interval"]
            
            kill_time = datetime.now() + timedelta(hours=8)
            next_spawn_time = kill_time + timedelta(minutes=interval)
            
            data_to_save = {
                "chat_id": chat_id,
                "boss_name": boss_name,
                "kill_time": kill_time.isoformat(),
                "next_spawn_time": next_spawn_time.isoformat(),
                "updated_by": user_id
            }
            supabase.table("boss_records").upsert(data_to_save).execute()
            
            reply_text = (
                f"📝 紀錄成功！\n"
                f"👹 王怪：{boss_name}\n"
                f"⚔️ 擊殺時間：{kill_time.strftime('%H:%M')}\n"
                f"⏱️ 下次重生：{next_spawn_time.strftime('%H:%M')}"
            )
        else:
            reply_text = f"❌ 找不到「{boss_name}」的設定。請點擊網頁後台新增該王怪！"
            
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
