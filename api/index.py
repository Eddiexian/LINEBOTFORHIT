from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
from supabase import create_client, Client
import os
from datetime import datetime, timedelta
import math

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
BOSSES_PER_CARD = 3  # 每卡片顯示 3 隻 BOSS

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

def build_boss_carousel(combined_list):
    """
    生成 LINE Flex Carousel，每張卡片顯示 3 隻 BOSS
    支援左右滑動切換
    """
    if not combined_list:
        return FlexSendMessage(
            alt_text="📊 BOSS追蹤",
            contents={
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": "📊 系統內目前沒有任何BOSS設定", "align": "center"}
                    ]
                }
            }
        )
    
    # 計算總頁數
    total_cards = math.ceil(len(combined_list) / BOSSES_PER_CARD)
    bubbles = []
    
    for card_idx in range(total_cards):
        start_idx = card_idx * BOSSES_PER_CARD
        end_idx = start_idx + BOSSES_PER_CARD
        card_bosses = combined_list[start_idx:end_idx]
        
        # 構建該卡片的表格列
        table_contents = []
        
        # 標題列
        table_contents.append({
            "type": "box",
            "layout": "horizontal",
            "backgroundColor": "#1a1d20",
            "paddingAll": "8px",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": "狀態", "color": "#ffffff", "size": "xs", "weight": "bold", "flex": 1, "align": "center"},
                {"type": "text", "text": "BOSS", "color": "#ffffff", "size": "xs", "weight": "bold", "flex": 2, "align": "center"},
                {"type": "text", "text": "預計時間", "color": "#ffffff", "size": "xs", "weight": "bold", "flex": 3, "align": "center"}
            ]
        })
        
        # 數據列
        for row_idx, item in enumerate(card_bosses):
            boss_name = item["boss_name"]
            next_time = item["next_time"]
            
            # 判斷狀態與時間
            if item["status"] == "unknown":
                status_icon = "⚪"
                time_display = "尚未回報"
            else:
                now = datetime.now(next_time.tzinfo)
                countdown = next_time - now
                minutes_left = int(countdown.total_seconds() / 60)
                
                date_str = next_time.strftime("%m/%d")
                time_str = next_time.strftime("%H:%M")
                weekday_str = WEEK_DAYS[next_time.weekday()]
                
                if minutes_left > 0:
                    status_icon = "🟢"
                    time_display = f"{date_str}({weekday_str}) {time_str}"
                else:
                    status_icon = "🔴"
                    time_display = f"{date_str}({weekday_str}) {time_str}"
            
            row_bg = "#f8f9fa" if row_idx % 2 == 0 else "#ffffff"
            
            table_contents.append({
                "type": "box",
                "layout": "horizontal",
                "backgroundColor": row_bg,
                "paddingAll": "6px",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": status_icon, "size": "sm", "flex": 1, "align": "center"},
                    {"type": "text", "text": boss_name, "size": "xs", "weight": "bold", "color": "#212529", "flex": 2, "align": "center"},
                    {"type": "text", "text": time_display, "size": "xs", "color": "#495057", "flex": 3, "align": "center", "wrap": True}
                ]
            })
        
        # 建立卡片
        bubble = {
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "0px",
                "spacing": "none",
                "contents": table_contents
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "8px",
                "backgroundColor": "#e9ecef",
                "spacing": "xs",
                "contents": [
                    {"type": "text", "text": f"第 {card_idx + 1} / {total_cards} 頁", "size": "xs", "color": "#495057", "align": "center"},
                    {"type": "text", "text": "💡 左右滑動查看更多 | K BOSS名稱 回報擊殺", "size": "xs", "color": "#6c757d", "align": "center"}
                ]
            }
        }
        
        bubbles.append(bubble)
    
    # 如果只有一張卡片，直接返回 Bubble；否則用 Carousel
    if len(bubbles) == 1:
        return FlexSendMessage(alt_text="📊 BOSS追蹤", contents=bubbles[0])
    else:
        return FlexSendMessage(
            alt_text="📊 BOSS追蹤（左右滑動查看）",
            contents={
                "type": "carousel",
                "contents": bubbles
            }
        )

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

    # ────────────────────────────────────────────────────
    # 情況 A：【功能新增】清空本群所有紀錄 K CLEAR
    # ────────────────────────────────────────────────────
    if user_msg == "K CLEAR" or user_msg == "KCLEAR":
        # 僅刪除當前 chat_id (該群組) 的擊殺紀錄，不影響BOSS Config 設定
        supabase.table("boss_records").delete().eq("chat_id", chat_id).execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🗑️ 已成功清空本群組的所有BOSS重生追蹤紀錄！"))
        return

    # ────────────────────────────────────────────────────
    # 情況 B：查詢群組專屬清單 K LIST (分頁卡片輪播版本)
    # ────────────────────────────────────────────────────
    if user_msg == "K LIST" or user_msg == "KLIST":
        # 1. 先撈出全域支援的所有BOSS Config，確保沒擊殺紀錄的王也能出現在清單上供使用者點擊
        config_resp = supabase.table("boss_config").select("*").order("boss_name", desc=False).execute()
        configs = config_resp.data
        
        if not configs:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📊 系統內目前沒有任何BOSS設定，請先至網頁後台新增。"))
            return

        # 2. 撈出本群組目前的擊殺紀錄
        records_resp = supabase.table("boss_records").select("*").eq("chat_id", chat_id).execute()
        records_map = {rec["boss_name"]: rec for rec in records_resp.data}
        
        # 3. 整合兩張表的資料並進行排序邏輯
        combined_list = []
        for cfg in configs:
            name = cfg["boss_name"]
            if name in records_map:
                rec = records_map[name]
                next_time = datetime.fromisoformat(rec["next_spawn_time"].replace("Z", "+00:00"))
                # 用於排序的時間戳記
                sort_timestamp = next_time.timestamp()
                status = "tracked"
            else:
                next_time = None
                # 未擊殺的BOSS，其排序權重設為無限大（強迫排在表格最下面）
                sort_timestamp = float('inf')
                status = "unknown"
                
            combined_list.append({
                "boss_name": name,
                "next_time": next_time,
                "sort_key": sort_timestamp,
                "status": status
            })
            
        # 👑 【UIUX 優化】依據出沒時間由近到遠精準排序（最快重生的置頂）
        combined_list.sort(key=lambda x: x["sort_key"])
        
        # 生成卡片輪播
        flex_msg = build_boss_carousel(combined_list)
        line_bot_api.reply_message(event.reply_token, flex_msg)
        return

    # ────────────────────────────────────────────────────
    # 情況 C：回報擊殺 K [BOSS名稱] (保持原有精美戰報回覆)
    # ────────────────────────────────────────────────────
    if user_msg.startswith("K"):
        boss_name = user_msg.replace("K", "").strip()
        
        if not boss_name or boss_name == "LIST" or boss_name == "CLEAR" or boss_name.startswith("LIST"):
            return
            
        # 先撈取所有 BOSS 設定，用於正式名稱與別名比對
        configs_resp = supabase.table("boss_config").select("*").execute()
        configs = configs_resp.data or []

        found_cfg = None
        for cfg in configs:
            official = cfg.get("boss_name")
            if official and official.upper() == boss_name:
                found_cfg = cfg
                break
            # 支援多種別名欄位格式：'aliases' (string comma-separated) 或 list
            aliases = cfg.get("aliases") or cfg.get("alias")
            if aliases:
                if isinstance(aliases, str):
                    alias_list = [a.strip().upper() for a in aliases.split(",") if a.strip()]
                elif isinstance(aliases, (list, tuple)):
                    alias_list = [str(a).strip().upper() for a in aliases]
                else:
                    alias_list = []
                if boss_name in alias_list:
                    found_cfg = cfg
                    break

        if found_cfg:
            real_name = found_cfg.get("boss_name")
            interval = found_cfg.get("respawn_interval")

            kill_time = datetime.now() + timedelta(hours=8)
            next_spawn_time = kill_time + timedelta(minutes=interval)

            data_to_save = {
                "chat_id": chat_id,
                "boss_name": real_name,
                "kill_time": kill_time.isoformat(),
                "next_spawn_time": next_spawn_time.isoformat(),
                "updated_by": user_id,
            }
            supabase.table("boss_records").upsert(data_to_save, on_conflict="chat_id,boss_name").execute()

            reply_text = (
                f"擊殺：{real_name}\n"
                f"擊殺時間：{kill_time.strftime('%m/%d %H:%M')}\n"
                f"下一次：{next_spawn_time.strftime('%m/%d %H:%M')}\n"
                f"間隔：{interval} 分鐘"
            )
        else:
            reply_text = f"找不到王怪：{boss_name}，請至後台新增。"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
