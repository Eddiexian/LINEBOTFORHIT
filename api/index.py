from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
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
    source_type = event.source.type
    if source_type == "group":
        chat_id = event.source.group_id
    elif source_type == "room":
        chat_id = event.source.room_id
    else:
        chat_id = event.source.user_id
        
    user_id = event.source.user_id 

    raw_msg = event.message.text.strip().upper()
    
    user_msg = raw_msg
    if "K" in raw_msg:
        k_index = raw_msg.rfind("K")
        user_msg = raw_msg[k_index:].strip()

    # ────────────────────────────────────────────────────
    # 情況 A：查詢群組專屬清單 K LIST (升級為高級 Flex Message 表格)
    # ────────────────────────────────────────────────────
    if user_msg == "K LIST" or user_msg == "KLIST":
        response = supabase.table("boss_records").select("*").eq("chat_id", chat_id).execute()
        records = response.data
        
        if not records:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📊 目前本群組沒有任何王怪追蹤紀錄。"))
            return
            
        records.sort(key=lambda x: x["next_spawn_time"])
        
        # 建立 Flex Message 的表格行 (rows)
        table_rows = []
        
        # 表格標頭 (Header Row)
        table_rows.append({
            "type": "box", "layout": "horizontal", "backgroundColor": "#212529", "paddingTop": "8px", "paddingBottom": "8px",
            "contents": [
                {"type": "text", "text": "狀態", "color": "#ffffff", "size": "sm", "weight": "bold", "flex": 1, "align": "center"},
                {"type": "text", "text": "王怪名稱", "color": "#ffffff", "size": "sm", "weight": "bold", "flex": 2, "align": "center"},
                {"type": "text", "text": "出沒時間", "color": "#ffffff", "size": "sm", "weight": "bold", "flex": 3, "align": "center"},
                {"type": "text", "text": "倒數/超時", "color": "#ffffff", "size": "sm", "weight": "bold", "flex": 3, "align": "center"}
            ]
        })
        
        # 走訪每筆紀錄並動態畫出表格列
        for idx, rec in enumerate(records):
            boss_name = rec["boss_name"]
            next_time = datetime.fromisoformat(rec["next_spawn_time"].replace("Z", "+00:00"))
            
            now = datetime.now(next_time.tzinfo)
            countdown = next_time - now
            minutes_left = int(countdown.total_seconds() / 60)
            
            date_str = next_time.strftime("%m/%d")
            time_str = next_time.strftime("%H:%M")
            weekday_str = WEEK_DAYS[next_time.weekday()]
            
            # 斑馬紋底色
            row_bg = "#f8f9fa" if idx % 2 == 0 else "#ffffff"
            
            if minutes_left > 0:
                status_color = "#28a745" # 綠色代表重生中
                status_text = "⏳"
                hours = minutes_left // 60
                mins = minutes_left % 60
                time_diff_str = f"{hours}h{mins}m" if hours > 0 else f"{mins}m"
                diff_color = "#17a2b8"
            else:
                status_color = "#dc3545" # 紅色代表超時
                status_text = "💥"
                over_minutes = -minutes_left
                hours = over_minutes // 60
                mins = over_minutes % 60
                time_diff_str = f"已過 {hours}h{mins}m" if hours > 0 else f"已過 {mins}m"
                diff_color = "#dc3545"
            
            # 加入資料列
            table_rows.append({
                "type": "box", "layout": "horizontal", "backgroundColor": row_bg, "paddingTop": "10px", "paddingBottom": "10px", "alignItems": "center",
                "contents": [
                    {"type": "text", "text": status_text, "size": "sm", "flex": 1, "align": "center"},
                    {"type": "text", "text": boss_name, "size": "sm", "weight": "bold", "color": "#333333", "flex": 2, "align": "center"},
                    {"type": "text", "text": f"{date_str}({weekday_str}) {time_str}", "size": "xs", "color": "#555555", "flex": 3, "align": "center"},
                    {"type": "text", "text": time_diff_str, "size": "xs", "weight": "bold", "color": diff_color, "flex": 3, "align": "center"}
                ]
            })
            
        # 封裝成完整的 LINE Flex 氣泡 JSON
        flex_contents = {
            "type": "bubble",
            "size": "giga", # 使用最寬的版面寬度
            "header": {
                "type": "box", "layout": "vertical", "backgroundColor": "#007bff", "paddingTop": "15px", "paddingBottom": "15px",
                "contents": [
                    {"type": "text", "text": "👹 王怪出沒時間追蹤看板", "color": "#ffffff", "weight": "bold", "size": "md", "align": "center"}
                ]
            },
            "body": {
                "type": "box", "layout": "vertical", "paddingAll": "0px",
                "contents": table_rows
            },
            "footer": {
                "type": "box", "layout": "vertical", "backgroundColor": "#f1f3f5", "paddingAll": "8px",
                "contents": [
                    {"type": "text", "text": "💡 提示：輸入「K [王怪名]」可即時回報擊殺時間", "size": "xs", "color": "#6c757d", "align": "center"}
                ]
            }
        }
        
        # 發送高級 Flex 訊息
        line_bot_api.reply_message(
            event.reply_token, 
            FlexSendMessage(alt_text="📊 王怪追蹤時間看板", contents=flex_contents)
        )
        return

    # ────────────────────────────────────────────────────
    # 情況 B：回報擊殺 K [王怪名稱]
    # ────────────────────────────────────────────────────
    if user_msg.startswith("K"):
        boss_name = user_msg.replace("K", "").strip()
        
        if not boss_name or boss_name == "LIST":
            return
            
        config_resp = supabase.table("boss_config").select("*").eq("boss_name", boss_name).execute()
        
        if config_resp.data:
            interval = config_resp.data[0]["respawn_interval"]
            
            kill_time = datetime.now() + timedelta(hours=8)
            next_spawn_time = kill_time + timedelta(minutes=interval)
            
            data_to_save = {
                "chat_id": chat_id, "boss_name": boss_name, "kill_time": kill_time.isoformat(), "next_spawn_time": next_spawn_time.isoformat(), "updated_by": user_id
            }
            supabase.table("boss_records").upsert(data_to_save).execute()
            
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
