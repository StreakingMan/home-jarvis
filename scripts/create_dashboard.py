#!/usr/bin/env python3
"""创建"贪玩兰粤"仪表板并保存 Lovelace 配置(经 HA WebSocket API)。
注意: 本脚本中的实体 ID 已假名化(公开脱敏),仅示意结构;在自己的 HA 上使用需替换为真实实体 ID。"""
import asyncio, json, os, sys
import websockets

HA_URL = os.environ["HA_URL"].rstrip("/")
TOKEN = os.environ["HA_TOKEN"]
WS_URL = HA_URL.replace("http", "ws", 1) + "/api/websocket"
URL_PATH = "tanwan-lanyue"

# ---------- 实体速记 ----------
MAX = "quan_zi_dong_mao_ce_suo_max"
PRO2 = "quan_zi_dong_mao_ce_suo_max_pro_2"
P1S = "p1s_" + os.environ.get("P1S_SERIAL", "<P1S_SERIAL>").lower()  # 序列号经 .env 注入,不入库

def tile(entity, name=None, **kw):
    c = {"type": "tile", "entity": entity}
    if name: c["name"] = name
    c.update(kw)
    return c

CAT_MD = """### {icon} {name}
**体重** {{{{ states('sensor.{slug}_last_weight_measurement') }}}} kg
**最近如厕** {{% set d = states('sensor.{slug}_last_use_date') %}}{{% if d.lower() in ['unknown','unavailable','none'] %}}暂无记录{{% else %}}{{{{ d[5:16] }}}}{{% endif %}}
**时长** {{% set t = states('sensor.{slug}_last_use_duration') %}}{{{{ t if t.lower() not in ['unknown','unavailable'] else '—' }}}} 秒
**使用** {{% set l = states('sensor.{slug}_last_litter_used') %}}{{{{ l if l.lower() not in ['unknown','unavailable'] else '—' }}}}"""

ALERT_MD = """{% set a = [] %}
{% if is_state('binary_sensor.MAX_sand_lack','on') %}{% set a = a + ['🟠 **猫厕所 MAX** — 缺猫砂 (余 ' ~ states('sensor.MAX_litter_level') ~ '%)'] %}{% endif %}
{% if states('sensor.PRO2_litter_level')|int(100) <= 20 %}{% set a = a + ['🔴 **猫厕所 MAX PRO 2** — 猫砂余量 ' ~ states('sensor.PRO2_litter_level') ~ '%'] %}{% endif %}
{% if states('sensor.MAX_odor_eliminator_n50_left_days')|int(99) <= 3 %}{% set a = a + ['🟡 **MAX 除臭剂 N50** — 剩 ' ~ states('sensor.MAX_odor_eliminator_n50_left_days') ~ ' 天'] %}{% endif %}
{% if states('sensor.PRO2_odor_eliminator_n50_left_days')|int(99) <= 3 %}{% set a = a + ['🟡 **PRO 2 除臭剂 N50** — 剩 ' ~ states('sensor.PRO2_odor_eliminator_n50_left_days') ~ ' 天'] %}{% endif %}
{% if is_state('binary_sensor.MAX_wastebin_filled','on') %}{% set a = a + ['🗑 **MAX 集便箱已满**'] %}{% endif %}
{% if is_state('binary_sensor.PRO2_wastebin_filled','on') %}{% set a = a + ['🗑 **PRO 2 集便箱已满**'] %}{% endif %}
{% if is_state('sensor.mmgg_cn_9000000004_inland_pet_food_left_level_p_2_6','Empty') %}{% set a = a + ['🍚 **宠物喂食器** — 粮桶已空'] %}{% endif %}
{% if is_state('sensor.xiaomi_cn_9000000005_pi2001_pet_food_left_level_p_2_6','Empty') %}{% set a = a + ['🍚 **宠物喂食器2** — 粮桶已空'] %}{% endif %}
{% if states('sensor.xiaomi_cn_9000000005_pi2001_desiccant_left_level_p_6_1')|int(99) <= 10 %}{% set a = a + ['💊 **喂食器2 干燥剂** — 需更换'] %}{% endif %}
{% if states('sensor.mmgg_cn_9000000004_inland_desiccant_left_time_p_11_2')|int(99) <= 1 %}{% set a = a + ['💊 **喂食器 干燥剂** — 需更换'] %}{% endif %}
{% if states('sensor.xiaomi_cn_blt_3_anonbledev01_002_filter_life_level_p_3_1076')|int(99) <= 20 %}{% set a = a + ['💧 **饮水机滤芯** — 剩 ' ~ states('sensor.xiaomi_cn_blt_3_anonbledev01_002_filter_life_level_p_3_1076') ~ '%'] %}{% endif %}
{% if states('sensor.xiaomi_cn_blt_3_anonbledev01_002_battery_level_p_5_1003')|int(99) <= 30 %}{% set a = a + ['🔋 **饮水机电池** — 剩 ' ~ states('sensor.xiaomi_cn_blt_3_anonbledev01_002_battery_level_p_5_1003') ~ '%'] %}{% endif %}
{% if states('sensor.zhimi_cn_9000000006_rma2_filter_life_level_p_4_1')|int(99) <= 10 %}{% set a = a + ['🌀 **空气净化器滤芯** — 需更换'] %}{% endif %}
{% if states('sensor.hfjh_cn_9000000001_m100_filter_life_level_p_10_1')|int(99) <= 10 %}{% set a = a + ['🌿 **猪笼草缸滤棉** — 需更换'] %}{% endif %}
{% if a %}{{ a | join('\\n\\n') }}{% else %}✅ 所有耗材充足{% endif %}""".replace("MAX_", MAX + "_").replace("PRO2_", PRO2 + "_")

HEADER_MD = """# {{ now().strftime('%m月%d日') }} · {{ ['周一','周二','周三','周四','周五','周六','周日'][now().weekday()] }}
{% set h = now().hour %}{% if h < 6 %}🌙 夜深了{% elif h < 9 %}☀️ 早上好{% elif h < 12 %}🌤 上午好{% elif h < 14 %}🍱 中午好{% elif h < 18 %}⛅️ 下午好{% else %}🌆 晚上好{% endif %}，室内 **{{ states('sensor.dmaker_cn_9000000007_p28_temperature_p_9_1') }}°C / {{ states('sensor.dmaker_cn_9000000007_p28_relative_humidity_p_9_2') }}%**，PM2.5 **{{ states('sensor.zhimi_cn_9000000006_rma2_pm2_5_density_p_3_4') }}** ({{ states('sensor.zhimi_cn_9000000006_rma2_air_quality_p_3_3') }})"""

def gauge(entity, name, green=50, yellow=20):
    return {"type": "gauge", "entity": entity, "name": name, "min": 0, "max": 100,
            "needle": False, "unit": "%",
            "severity": {"green": green, "yellow": yellow, "red": 0}}

CONFIG = {
    "title": "贪玩兰粤",
    "views": [{
        "title": "总览",
        "path": "home",
        "icon": "mdi:home-heart",
        "type": "sections",
        "max_columns": 4,
        "badges": [
            {"type": "entity", "entity": "weather.forecast_wo_de_jia", "show_entity_picture": True},
            {"type": "entity", "entity": "sensor.dmaker_cn_9000000007_p28_temperature_p_9_1", "name": "室温", "icon": "mdi:thermometer", "color": "orange"},
            {"type": "entity", "entity": "sensor.dmaker_cn_9000000007_p28_relative_humidity_p_9_2", "name": "湿度", "icon": "mdi:water-percent", "color": "blue"},
            {"type": "entity", "entity": "sensor.zhimi_cn_9000000006_rma2_pm2_5_density_p_3_4", "name": "PM2.5", "icon": "mdi:leaf", "color": "green"},
            {"type": "entity", "entity": f"sensor.{P1S}_print_status", "name": "打印机", "icon": "mdi:printer-3d", "color": "purple"},
        ],
        "sections": [
            # ── 概览 ────────────────────────────
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "今天", "heading_style": "title", "icon": "mdi:calendar-today"},
                {"type": "markdown", "content": HEADER_MD, "grid_options": {"columns": "full"}},
                {"type": "weather-forecast", "entity": "weather.forecast_wo_de_jia",
                 "forecast_type": "daily", "show_current": True, "show_forecast": True,
                 "grid_options": {"columns": "full"}},
                tile("sensor.miaomiaoc_cn_blt_3_anonbledev02_t2_temperature_p_2_1", "植物墙 温度", color="green"),
                tile("sensor.miaomiaoc_cn_blt_3_anonbledev03_t2_temperature_p_2_1", "鸟笼 温度", color="amber"),
                tile("sensor.zhimi_cn_9000000006_rma2_air_quality_p_3_3", "空气质量", icon="mdi:weather-windy", color="teal"),
                tile("sensor.xiaomi_cn_9000000008_r34r00_outdoor_temp_p_12_7", "空调室外温度", color="deep-orange"),
            ]},
            # ── 灯光 ────────────────────────────
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "灯光", "heading_style": "title", "icon": "mdi:lightbulb-group",
                 "badges": [{"type": "entity", "entity": "sun.sun"}]},
                tile("light.mijia_cn_group_90000000000000001_group3_s_2_light", "餐厅灯组", features=[{"type": "light-brightness"}]),
                tile("light.mijia_cn_group_90000000000000002_group3_s_2_light", "餐厅射灯组"),
                tile("light.mijia_cn_group_90000000000000003_group3_s_2_light", "书墙灯组"),
                tile("light.leishi_cn_9000000003_eps126_s_2_light", "餐厅吊灯"),
                tile("light.yeelink_cn_9000000009_ml6_s_2_light", "格栅灯"),
                tile("light.yeelink_cn_9000000010_wy0a03_s_2_light", "厨房灯带"),
                tile("light.xiaomi_cn_9000000011_btlm2p_s_2_light", "大门筒灯"),
                tile("light.yeelink_cn_9000000012_mbulb3_s_2", "壁灯"),
                tile("light.ftd_cn_9000000013_dsplmp_s_2_light", "碗柜灯"),
                tile("light.devcea_cn_9000000014_ls2305_s_2_light", "台灯2"),
                tile("light.devcea_cn_9000000015_ls2305_s_2_light", "摇臂台灯"),
                tile("light.lemesh_cn_9000000002_wy0d02_s_2_light", "盆栽射灯"),
            ]},
            # ── 耗材提醒 ────────────────────────
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "耗材提醒", "heading_style": "title", "icon": "mdi:package-variant-closed-check"},
                {"type": "markdown", "content": ALERT_MD, "grid_options": {"columns": "full"}},
                gauge(f"sensor.{MAX}_litter_level", "MAX 猫砂", 40, 20),
                gauge(f"sensor.{PRO2}_litter_level", "PRO2 猫砂", 40, 20),
                gauge("sensor.xiaomi_cn_blt_3_anonbledev01_002_filter_life_level_p_3_1076", "饮水机滤芯", 50, 20),
                gauge("sensor.zhimi_cn_9000000006_rma2_filter_life_level_p_4_1", "空净滤芯", 50, 20),
                tile(f"sensor.{MAX}_odor_eliminator_n50_left_days", "MAX 除臭剂 N50", icon="mdi:spray", color="cyan"),
                tile(f"sensor.{PRO2}_odor_eliminator_n50_left_days", "PRO2 除臭剂 N50", icon="mdi:spray", color="cyan"),
                tile("sensor.mmgg_cn_9000000004_inland_pet_food_left_level_p_2_6", "喂食器 余粮", icon="mdi:food-drumstick", color="brown"),
                tile("sensor.xiaomi_cn_9000000005_pi2001_pet_food_left_level_p_2_6", "喂食器2 余粮", icon="mdi:food-drumstick", color="brown"),
                tile("sensor.xiaomi_cn_9000000005_pi2001_desiccant_left_level_p_6_1", "喂食器2 干燥剂", icon="mdi:water-off", color="grey"),
                tile("sensor.xiaomi_cn_blt_3_anonbledev01_002_battery_level_p_5_1003", "饮水机电池", icon="mdi:battery-30"),
                tile("sensor.xiaomi_cn_9000000008_r34r00_filter_life_level_p_21_1", "空调滤芯", icon="mdi:air-filter", color="light-blue"),
                tile("sensor.hfjh_cn_9000000001_m100_filter_life_level_p_10_1", "猪笼草缸滤棉", icon="mdi:sprout", color="green"),
            ]},
            # ── 猫咪 ────────────────────────────
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "猫咪", "heading_style": "title", "icon": "mdi:cat",
                 "badges": [{"type": "entity", "entity": "binary_sensor.e_duo_qie_yowling_detected", "name": "嚎叫检测"}]},
                {"type": "markdown", "content": CAT_MD.format(icon="🐈", name="哦多茄", slug="e_duo_qie")},
                {"type": "markdown", "content": CAT_MD.format(icon="🐈‍⬛", name="雕猫", slug="diao_mao")},
                {"type": "markdown", "content": CAT_MD.format(icon="🐱", name="妹妹", slug="mei_mei")},
                tile("number.e_duo_qie_weight", "哦多茄 登记体重", icon="mdi:scale"),
                tile("number.diao_mao_weight", "雕猫 登记体重", icon="mdi:scale"),
                tile("number.mei_mei_weight", "妹妹 登记体重", icon="mdi:scale"),
            ]},
            # ── 猫厕所 ──────────────────────────
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "猫厕所 MAX", "heading_style": "title", "icon": "mdi:litter-box",
                 "badges": [{"type": "entity", "entity": f"binary_sensor.{MAX}_toilet_occupied", "name": "占用"}]},
                tile(f"sensor.{MAX}_state", "状态", icon="mdi:robot", color="indigo"),
                tile(f"sensor.{MAX}_last_used_by", "最近使用", icon="mdi:paw", color="pink"),
                tile(f"sensor.{MAX}_litter_weight", "砂重", icon="mdi:weight-kilogram"),
                tile(f"sensor.{MAX}_times_used", "今日次数", icon="mdi:counter"),
                tile(f"binary_sensor.{MAX}_wastebin_filled", "集便箱", icon="mdi:delete"),
                tile(f"switch.{MAX}_auto_clean", "自动清理"),
                {"type": "tile", "entity": f"button.{MAX}_scoop", "name": "手动铲屎", "icon": "mdi:shovel", "color": "orange", "tap_action": {"action": "toggle"}},
                {"type": "tile", "entity": f"button.{MAX}_deodorize", "name": "除臭", "icon": "mdi:spray", "color": "cyan", "tap_action": {"action": "toggle"}},
            ]},
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "猫厕所 MAX PRO 2", "heading_style": "title", "icon": "mdi:litter-box",
                 "badges": [{"type": "entity", "entity": f"binary_sensor.{PRO2}_toilet_occupied", "name": "占用"}]},
                {"type": "picture-entity", "entity": f"camera.{PRO2}", "camera_image": f"camera.{PRO2}",
                 "camera_view": "live", "show_state": False, "show_name": False,
                 "grid_options": {"columns": "full"}},
                tile(f"sensor.{PRO2}_state", "状态", icon="mdi:robot", color="indigo"),
                tile(f"sensor.{PRO2}_last_used_by", "最近使用", icon="mdi:paw", color="pink"),
                tile(f"sensor.{PRO2}_litter_weight", "砂重", icon="mdi:weight-kilogram"),
                tile(f"sensor.{PRO2}_times_used", "今日次数", icon="mdi:counter"),
                tile(f"binary_sensor.{PRO2}_wastebin_filled", "集便箱", icon="mdi:delete"),
                tile(f"switch.{PRO2}_auto_clean", "自动清理"),
                {"type": "tile", "entity": f"button.{PRO2}_scoop", "name": "手动铲屎", "icon": "mdi:shovel", "color": "orange", "tap_action": {"action": "toggle"}},
                {"type": "tile", "entity": f"light.{PRO2}_light", "name": "照明", "icon": "mdi:lightbulb-on"},
            ]},
            # ── 3D 打印机 ───────────────────────
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "3D 打印机 · 拓竹 P1S", "heading_style": "title", "icon": "mdi:printer-3d",
                 "badges": [{"type": "entity", "entity": f"binary_sensor.{P1S}_online", "name": "在线"},
                            {"type": "entity", "entity": f"binary_sensor.{P1S}_print_error", "name": "错误"}]},
                {"type": "picture-glance", "title": "打印仓实况",
                 "camera_image": f"camera.{P1S}_camera", "camera_view": "live",
                 "entities": [f"light.{P1S}_chamber_light", f"sensor.{P1S}_print_progress", f"sensor.{P1S}_remaining_time"],
                 "grid_options": {"columns": "full"}},
                gauge(f"sensor.{P1S}_print_progress", "打印进度", 0, 0),
                {"type": "picture-entity", "entity": f"image.{P1S}_cover_image", "name": "当前模型",
                 "show_state": False, "show_name": True},
                tile(f"sensor.{P1S}_print_status", "打印状态", icon="mdi:printer-3d-nozzle", color="purple"),
                tile(f"sensor.{P1S}_current_stage", "当前阶段", icon="mdi:progress-wrench", color="indigo"),
                tile(f"sensor.{P1S}_remaining_time", "剩余时间", icon="mdi:clock-outline", color="amber"),
                tile(f"sensor.{P1S}_current_layer", "当前层", icon="mdi:layers", color="teal"),
                tile(f"sensor.{P1S}_nozzle_temperature", "喷嘴温度", icon="mdi:thermometer-high", color="red"),
                tile(f"sensor.{P1S}_bed_temperature", "热床温度", icon="mdi:radiator", color="orange"),
                tile(f"light.{P1S}_chamber_light", "打印仓灯"),
                tile(f"fan.{P1S}_chamber_fan", "打印仓风扇"),
                {"type": "markdown", "grid_options": {"columns": "full"},
                 "content": "**任务** {{ states('sensor." + P1S + "_task_name') }}\n\n**层数** {{ states('sensor." + P1S + "_current_layer') }}/{{ states('sensor." + P1S + "_total_layer_count') }} · **耗材** {{ states('sensor." + P1S + "_print_weight') }} g / {{ states('sensor." + P1S + "_print_length') }} m"},
            ]},
        ],
    }],
}

async def main():
    async with websockets.connect(WS_URL, max_size=10 * 2**20) as ws:
        assert json.loads(await ws.recv())["type"] == "auth_required"
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        assert json.loads(await ws.recv())["type"] == "auth_ok", "auth failed"
        mid = 0
        async def call(payload):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, **payload}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == mid and r["type"] == "result":
                    return r

        # 1) 已存在则跳过创建
        r = await call({"type": "lovelace/dashboards/list"})
        existing = [d for d in r["result"] if d.get("url_path") == URL_PATH]
        if not existing:
            r = await call({"type": "lovelace/dashboards/create", "url_path": URL_PATH,
                            "title": "贪玩兰粤", "icon": "mdi:cat", "mode": "storage",
                            "show_in_sidebar": True, "require_admin": False})
            print("create:", r["success"], r.get("error", ""))
            if not r["success"]:
                sys.exit(1)
        else:
            print("dashboard already exists, updating config")

        # 2) 保存视图配置
        r = await call({"type": "lovelace/config/save", "url_path": URL_PATH, "config": CONFIG})
        print("save config:", r["success"], r.get("error", ""))
        if not r["success"]:
            sys.exit(1)

        # 3) 侧边栏排到第一位(当前用户)
        r = await call({"type": "frontend/get_user_data", "key": "sidebar"})
        value = (r.get("result") or {}).get("value") or {}
        order = [p for p in (value.get("panelOrder") or []) if p != URL_PATH]
        if not order:
            r2 = await call({"type": "get_panels"})
            panels = r2["result"]
            order = [k for k, v in panels.items()
                     if v.get("title") and k not in (URL_PATH, "config", "developer-tools")]
            order.sort()
            if "lovelace" in panels:
                order = ["lovelace"] + [p for p in order if p != "lovelace"]
        new_order = [URL_PATH] + order
        r = await call({"type": "frontend/set_user_data", "key": "sidebar",
                        "value": {"panelOrder": new_order, "hiddenPanels": value.get("hiddenPanels", [])}})
        print("sidebar order:", r["success"], "->", new_order[:5], "...")

asyncio.run(main())
