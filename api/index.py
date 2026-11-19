from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
from supabase import create_client, Client
import os
from datetime import datetime, timedelta
import pytz
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
BOSSES_PER_CARD = 10
TZ_TAIWAN = pytz.timezone("Asia/Taipei")


def get_group_respawn_interval(chat_id, boss_name, default_interval):
    """取得群組專屬重生間隔；沒有設定時回退至全域預設值。"""
    response = (
        supabase.table("group_boss_settings")
        .select("respawn_interval")
        .eq("chat_id", chat_id)
        .eq("boss_name", boss_name)
        .limit(1)
        .execute()
    )

    if response.data:
        return int(response.data[0]["respawn_interval"])

    return int(default_interval)


def find_boss_config(configs, boss_name):
    """依正式名稱或別名尋找 BOSS 設定。boss_name 預期為大寫字串。"""
    for cfg in configs:
        official = cfg.get("boss_name")
        if official and official.upper() == boss_name:
            return cfg

        aliases = cfg.get("aliases") or cfg.get("alias")
        if isinstance(aliases, str):
            alias_list = [a.strip().upper() for a in aliases.split(",") if a.strip()]
        elif isinstance(aliases, (list, tuple)):
            alias_list = [str(a).strip().upper() for a in aliases]
        else:
            alias_list = []

        if boss_name in alias_list:
            return cfg

    return None


def split_reply_text(text, max_length=4500, max_messages=5):
    """將長文字切成 LINE 可回覆的多則訊息。"""
    chunks = []
    current = ""

    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > max_length and current:
            chunks.append(current)
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)

    if len(chunks) > max_messages:
        chunks = chunks[:max_messages]
        chunks[-1] = chunks[-1][:max_length - 30] + "\n…清單過長，僅顯示部分內容。"

    return [TextSendMessage(text=chunk) for chunk in chunks]


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
    """生成 LINE Flex Carousel"""
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

    total_cards = math.ceil(len(combined_list) / BOSSES_PER_CARD)
    bubbles = []

    for card_idx in range(total_cards):
        start_idx = card_idx * BOSSES_PER_CARD
        end_idx = start_idx + BOSSES_PER_CARD
        card_bosses = combined_list[start_idx:end_idx]

        table_contents = []

        table_contents.append({
            "type": "box",
            "layout": "horizontal",
            "backgroundColor": "#1a1d20",
            "paddingAll": "6px",
            "spacing": "xs",
            "contents": [
                {"type": "text", "text": "狀態", "color": "#ffffff", "size": "xs", "weight": "bold", "flex": 1, "align": "center"},
                {"type": "text", "text": "BOSS", "color": "#ffffff", "size": "xs", "weight": "bold", "flex": 2, "align": "center"},
                {"type": "text", "text": "預計時間", "color": "#ffffff", "size": "xs", "weight": "bold", "flex": 3, "align": "center"}
            ]
        })

        for row_idx, item in enumerate(card_bosses):
            boss_name = item["boss_name"]
            next_time = item["next_time"]

            if item["status"] == "unknown":
                status_icon = "⚪"
                time_display = "尚未回報"
            else:
                now = datetime.now(TZ_TAIWAN)
                countdown = next_time - now
                minutes_left = int(countdown.total_seconds() / 60)

                date_str = next_time.strftime("%m/%d")
                time_str = next_time.strftime("%H:%M:%S")
                weekday_str = WEEK_DAYS[next_time.weekday()]

                status_icon = "🟢" if minutes_left > 0 else "🔴"
                time_display = f"{date_str}({weekday_str}) {time_str}"

            row_bg = "#f8f9fa" if row_idx % 2 == 0 else "#ffffff"

            table_contents.append({
                "type": "box",
                "layout": "horizontal",
                "backgroundColor": row_bg,
                "paddingAll": "4px",
                "spacing": "xs",
                "contents": [
                    {"type": "text", "text": status_icon, "size": "sm", "flex": 1, "align": "center"},
                    {"type": "text", "text": boss_name, "size": "xs", "weight": "bold", "color": "#212529", "flex": 2, "align": "center"},
                    {"type": "text", "text": time_display, "size": "xs", "color": "#495057", "flex": 3, "align": "center", "wrap": True}
                ]
            })

        bubble = {
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "0px",
                "spacing": "none",
                "contents": table_contents
            }
        }

        bubbles.append(bubble)

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
    try:
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

        # HELP / K HELP：顯示可用指令
        if raw_msg == "HELP" or user_msg in ("K HELP", "KHELP"):
            help_text = (
                "📖 BOSS追蹤指令\n"
                "• K 王怪名稱：回報剛擊殺\n"
                "• K 王怪名稱 HH:MM 或 HH:MM:SS：指定擊殺時間\n"
                "• K LIST：查看BOSS重生追蹤清單\n"
                "• K CHECK BOSS RESPAWN：查看本群組每隻王的重生間隔\n"
                "• K SET 王怪名稱 分鐘：設定單隻王的群組間隔\n"
                "• K SET 王怪名稱 HALF：設定為該王全域間隔的一半\n"
                "• K SET ALL 分鐘：全部王設定為相同間隔\n"
                "• K SET ALL HALF：全部王依各自全域間隔減半\n"
                "• K CLEAR：清除本群組的擊殺追蹤紀錄"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=help_text))
            return

        # K CHECK BOSS RESPAWN：顯示本群組目前生效的所有王怪間隔
        if user_msg in ("K CHECK BOSS RESPAWN", "KCHECK BOSS RESPAWN"):
            configs_resp = (
                supabase.table("boss_config")
                .select("boss_name,respawn_interval")
                .order("boss_name", desc=False)
                .execute()
            )
            configs = configs_resp.data or []

            if not configs:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="目前沒有任何BOSS全域設定。")
                )
                return

            group_settings_resp = (
                supabase.table("group_boss_settings")
                .select("boss_name,respawn_interval")
                .eq("chat_id", chat_id)
                .execute()
            )
            group_settings = {
                row["boss_name"]: int(row["respawn_interval"])
                for row in (group_settings_resp.data or [])
            }

            lines = ["📋 本群組目前生效的BOSS重生間隔："]
            for cfg in configs:
                boss_name = cfg["boss_name"]
                global_interval = int(cfg["respawn_interval"])
                if boss_name in group_settings:
                    interval = group_settings[boss_name]
                    source = "群組自訂"
                else:
                    interval = global_interval
                    source = "全域預設"
                lines.append(f"• {boss_name}：{interval} 分鐘（{source}）")

            line_bot_api.reply_message(
                event.reply_token,
                split_reply_text("\n".join(lines))
            )
            return

        # K SET [BOSS名稱|ALL] [分鐘|HALF]
        if user_msg.startswith("K SET ") or user_msg.startswith("KSET "):
            setting_text = (
                user_msg[len("K SET "):].strip()
                if user_msg.startswith("K SET ")
                else user_msg[len("KSET "):].strip()
            )

            setting_parts = setting_text.rsplit(" ", 1)
            if len(setting_parts) != 2 or not setting_parts[0].strip():
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="格式錯誤，請使用：\nK SET 王怪名稱 分鐘或 HALF\nK SET ALL 分鐘或 HALF\n例如：K SET ALL HALF")
                )
                return

            setting_boss_name = setting_parts[0].strip()
            interval_value = setting_parts[1].strip()

            configs_resp = supabase.table("boss_config").select("*").execute()
            configs = configs_resp.data or []

            if not configs:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="系統內目前沒有任何BOSS設定。")
                )
                return

            if setting_boss_name == "ALL":
                target_configs = configs
            else:
                found_setting_cfg = find_boss_config(configs, setting_boss_name)
                if not found_setting_cfg:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text=f"找不到王怪：{setting_boss_name}\n請先至後台新增。")
                    )
                    return
                target_configs = [found_setting_cfg]

            settings_to_save = []
            for cfg in target_configs:
                real_name = cfg.get("boss_name")
                global_interval = int(cfg.get("respawn_interval"))

                if interval_value == "HALF":
                    custom_interval = max(1, global_interval // 2)
                else:
                    try:
                        custom_interval = int(interval_value)
                        if custom_interval <= 0:
                            raise ValueError
                    except ValueError:
                        line_bot_api.reply_message(
                            event.reply_token,
                            TextSendMessage(text="間隔請輸入大於 0 的整數分鐘，或輸入 HALF。")
                        )
                        return

                settings_to_save.append({
                    "chat_id": chat_id,
                    "boss_name": real_name,
                    "respawn_interval": custom_interval,
                    "updated_by": user_id,
                    "updated_at": datetime.now(TZ_TAIWAN).isoformat(),
                })

            supabase.table("group_boss_settings").upsert(
                settings_to_save,
                on_conflict="chat_id,boss_name"
            ).execute()

            if setting_boss_name == "ALL":
                reply_text = (
                    f"✅ 已設定本群組全部 {len(settings_to_save)} 隻BOSS的重生間隔\n"
                    + (
                        "模式：全域間隔的一半"
                        if interval_value == "HALF"
                        else f"模式：統一 {interval_value} 分鐘"
                    )
                )
            else:
                saved = settings_to_save[0]
                reply_text = (
                    f"✅ 已設定本群組的重生間隔\n"
                    f"王怪：{saved['boss_name']}\n"
                    f"間隔：{saved['respawn_interval']} 分鐘"
                    + ("（全域間隔的一半）" if interval_value == "HALF" else "")
                )

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        # K CLEAR
        if user_msg == "K CLEAR" or user_msg == "KCLEAR":
            supabase.table("boss_records").delete().eq("chat_id", chat_id).execute()
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="🗑️ 已成功清空本群組的所有BOSS重生追蹤紀錄！")
            )
            return

        # K LIST
        if user_msg == "K LIST" or user_msg == "KLIST":
            config_resp = supabase.table("boss_config").select("*").order("boss_name", desc=False).execute()
            configs = config_resp.data or []

            if not configs:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="📊 系統內目前沒有任何BOSS設定，請先至網頁後台新增。")
                )
                return

            records_resp = supabase.table("boss_records").select("*").eq("chat_id", chat_id).execute()
            records_map = {rec["boss_name"]: rec for rec in (records_resp.data or [])}

            combined_list = []
            for cfg in configs:
                name = cfg["boss_name"]
                if name in records_map:
                    rec = records_map[name]
                    next_time = datetime.fromisoformat(rec["next_spawn_time"].replace("Z", "+00:00"))
                    next_time = next_time.astimezone(TZ_TAIWAN)
                    sort_timestamp = next_time.timestamp()
                    status = "tracked"
                else:
                    next_time = None
                    sort_timestamp = float("inf")
                    status = "unknown"

                combined_list.append({
                    "boss_name": name,
                    "next_time": next_time,
                    "sort_key": sort_timestamp,
                    "status": status
                })

            combined_list.sort(key=lambda x: x["sort_key"])
            flex_msg = build_boss_carousel(combined_list)
            line_bot_api.reply_message(event.reply_token, flex_msg)
            return

        # K [BOSS名稱] 或 K [BOSS名稱] HH:MM:SS
        if user_msg.startswith("K"):
            remaining = user_msg[1:].strip()

            if not remaining or remaining == "LIST" or remaining == "CLEAR" or remaining.startswith("LIST"):
                return

            boss_name = remaining
            custom_time = None
            parts = remaining.split()
            if len(parts) >= 2:
                last_part = parts[-1]
                if ":" in last_part and len(last_part) in (5, 8):
                    try:
                        time_parts = last_part.split(":")
                        hour = int(time_parts[0])
                        minute = int(time_parts[1])
                        second = int(time_parts[2]) if len(time_parts) == 3 else 0

                        if 0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60:
                            custom_time = (hour, minute, second)
                            boss_name = " ".join(parts[:-1])
                    except (ValueError, IndexError):
                        pass

            configs_resp = supabase.table("boss_config").select("*").execute()
            found_cfg = find_boss_config(configs_resp.data or [], boss_name)

            if found_cfg:
                real_name = found_cfg.get("boss_name")
                default_interval = found_cfg.get("respawn_interval")
                interval = get_group_respawn_interval(chat_id, real_name, default_interval)

                if custom_time:
                    now_tw = datetime.now(TZ_TAIWAN)
                    kill_time = now_tw.replace(
                        hour=custom_time[0],
                        minute=custom_time[1],
                        second=custom_time[2],
                        microsecond=0
                    )
                else:
                    kill_time = datetime.now(TZ_TAIWAN).replace(microsecond=0)

                next_spawn_time = kill_time + timedelta(minutes=interval)
                data_to_save = {
                    "chat_id": chat_id,
                    "boss_name": real_name,
                    "kill_time": kill_time.isoformat(),
                    "next_spawn_time": next_spawn_time.isoformat(),
                    "updated_by": user_id,
                }
                supabase.table("boss_records").upsert(
                    data_to_save,
                    on_conflict="chat_id,boss_name"
                ).execute()

                reply_text = (
                    f"擊殺：{real_name}\n"
                    f"擊殺時間：{kill_time.strftime('%m/%d %H:%M:%S')}\n"
                    f"下一次：{next_spawn_time.strftime('%m/%d %H:%M:%S')}\n"
                    f"間隔：{interval} 分鐘"
                )
            else:
                reply_text = f"找不到王怪：{boss_name}，請至後台新增。"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    except Exception as e:
        print(f"Error: {str(e)}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 發生錯誤：{str(e)}"))
