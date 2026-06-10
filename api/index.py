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

# 星期幾的中文對照表
WEEK_DAYS = ["一", "二", "三", "四", "五", "六", "日"]

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

    # 2. 安全清洗文字 (轉大寫、去前後空格)
    raw_msg = event.message.text.strip().upper()
    
    # 精準尋找最後一個 K 字母的位置，防止群組 Tag 或是使用者英文名字含有 K 導致斷詞出錯
    user_msg = raw_msg
    if "K" in raw_msg:
        k_index = raw_msg.rfind("K")
        user_msg = raw_msg[k_index:].strip()

    # ----------------------------------------------------
    # 情況 A：查詢群組專屬清單 K LIST
    # ----------------------------------------------------
    if user_msg == "K LIST" or user_msg == "KLIST":
        response = supabase.table("boss_records").select("*").eq("chat_id", chat_id).execute()
        records = response.data
        
        if not records:
            reply_text = "📊 【王怪出沒情報清單】\n──────────────────\n目前本群組沒有任何擊殺追蹤紀錄。"
        else:
            reply_text = "📊 【王怪重生即時追蹤清單】\n──────────────────\n"
            # 依據重生時間由近到遠排序
            records.sort(key=lambda x: x["next_spawn_time"])
            
            for rec in records:
                boss_name = rec["boss_name"]
                # 解析 Supabase 時間戳記並套用時區
                next_time = datetime.fromisoformat(rec["next_spawn_time"].replace("Z", "+00:00"))
                
                # 計算倒數計時
                now = datetime.now(next_time.tzinfo)
                countdown = next_time - now
                minutes_left = int(countdown.total_seconds() / 60)
                
                # 格式化日期與時間資訊
                date_str = next_time.strftime("%m/%d")
                time_str = next_time.strftime("%H:%M")
                weekday_str = WEEK_DAYS[next_time.weekday()]
                
                if minutes_left > 0:
                    # 計算剩餘小時與分鐘數
                    hours = minutes_left // 60
                    mins = minutes_left % 60
                    countdown_str = f"還有 {hours}小時{mins}分" if hours > 0 else f"還有 {mins}分鐘"
                    
                    reply_text += f"🟢 〖{boss_name}〗\n📅 時間：{date_str} ({weekday_str}) {time_str}\n⏳ 狀態：{countdown_str}\n──────────────────\n"
                else:
                    # 計算超時小時與分鐘數
                    over_minutes = -minutes_left
                    hours = over_minutes // 60
                    mins = over_minutes % 60
                    over_str = f"超時 {hours}小時{mins}分" if hours > 0 else f"超時 {mins}分鐘"
                    
                    reply_text += f"🔴 〖{boss_name}〗\n📅 時間：{date_str} ({weekday_str}) {time_str}\n⚠️ 狀態：💥 已過時！({over_str})\n──────────────────\n"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text.strip("─\n")))
        return

    # ----------------------------------------------------
    # 情況 B：回報擊殺 K [王怪名稱] (例如: K 巴風特)
    # ----------------------------------------------------
    if user_msg.startswith("K"):
        boss_name = user_msg.replace("K", "").strip()
        
        # 安全防護：如果指令只有打 K 或者後面是 LIST，直接忽略
        if not boss_name or boss_name == "LIST":
            return
            
        # 至 Supabase 查詢該王怪在網頁設定的重生間隔
        config_resp = supabase.table("boss_config").select("*").eq("boss_name", boss_name).execute()
        
        if config_resp.data:
            # 【Bug 修正】精準獲取陣列中第一筆設定的重生成格
            interval = config_resp.data[0]["respawn_interval"]
            
            # 以台灣時間（UTC+8）計算擊殺與下次重生時間
            kill_time = datetime.now() + timedelta(hours=8)
            next_spawn_time = kill_time + timedelta(minutes=interval)
            
            # 封裝資料並 Upsert 到資料庫 (chat_id + boss_name 唯一)
            data_to_save = {
                "chat_id": chat_id,
                "boss_name": boss_name,
                "kill_time": kill_time.isoformat(),
                "next_spawn_time": next_spawn_time.isoformat(),
                "updated_by": user_id
            }
            supabase.table("boss_records").upsert(data_to_save).execute()
            
            # 格式化日期與星期
            k_date = kill_time.strftime("%m/%d")
            k_week = WEEK_DAYS[kill_time.weekday()]
            n_date = next_spawn_time.strftime("%m/%d")
            n_week = WEEK_DAYS[next_spawn_time.weekday()]
            
            reply_text = (
                f"📝 【戰報：王怪擊殺成功】\n"
                f"──────────────────\n"
                f"👹 追蹤對象：〖 {boss_name} 〗\n"
                f"⚔️ 擊殺時間：{k_date} ({k_week}) {kill_time.strftime('%H:%M')}\n"
                f"⏱️ 重生間隔：{interval} 分鐘\n"
                f"──────────────────\n"
                f"🎯 預計下一次出沒時間：\n"
                f"👉 【 {n_date} ({n_week}) {next_spawn_time.strftime('%H:%M')} 】"
            )
        else:
            reply_text = f"❌ 追蹤失敗\n找不到王怪「{boss_name}」的設定配置。\n💡 請點擊網頁後台手動新增該王怪！"
            
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
