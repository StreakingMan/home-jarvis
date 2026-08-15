#!/usr/bin/env python3
"""压缩"贪玩兰粤"仪表板为 4 列单屏布局。
注意: 本脚本中的实体 ID 已假名化(公开脱敏),仅示意结构;在自己的 HA 上使用需替换为真实实体 ID。"""
import asyncio, json, os, sys
import websockets

HA_URL = os.environ["HA_URL"].rstrip("/")
TOKEN = os.environ["HA_TOKEN"]
WS_URL = HA_URL.replace("http", "ws", 1) + "/api/websocket"
URL_PATH = "tanwan-lanyue"

MAX = "quan_zi_dong_mao_ce_suo_max"
PRO2 = "quan_zi_dong_mao_ce_suo_max_pro_2"
P1S = "p1s_" + os.environ.get("P1S_SERIAL", "<P1S_SERIAL>").lower()  # 序列号经 .env 注入,不入库

def t(entity, name=None, half=True, **kw):
    c = {"type": "tile", "entity": entity, "vertical": False}
    if name: c["name"] = name
    if half: c["grid_options"] = {"columns": 6, "rows": 1}
    else: c["grid_options"] = {"columns": "full", "rows": 1}
    c.update(kw)
    return c

HEADER_MD = """## {{ now().strftime('%m月%d日') }} {{ ['周一','周二','周三','周四','周五','周六','周日'][now().weekday()] }} · {% set h = now().hour %}{% if h < 6 %}🌙{% elif h < 12 %}☀️ 早上好{% elif h < 18 %}⛅️ 下午好{% else %}🌆 晚上好{% endif %}
室内 **{{ states('sensor.dmaker_cn_9000000007_p28_temperature_p_9_1') }}°C / {{ states('sensor.dmaker_cn_9000000007_p28_relative_humidity_p_9_2') }}%** · PM2.5 **{{ states('sensor.zhimi_cn_9000000006_rma2_pm2_5_density_p_3_4') }}** · {{ states('sensor.zhimi_cn_9000000006_rma2_air_quality_p_3_3') }}"""

ALERT_MD = """{% set a = [] %}
{% if is_state('binary_sensor.MAX_sand_lack','on') %}{% set a = a + ['🟠 MAX 缺猫砂 (' ~ states('sensor.MAX_litter_level') ~ '%)'] %}{% endif %}
{% if states('sensor.PRO2_litter_level')|int(100) <= 20 %}{% set a = a + ['🔴 PRO2 猫砂 ' ~ states('sensor.PRO2_litter_level') ~ '%'] %}{% endif %}
{% if states('sensor.MAX_odor_eliminator_n50_left_days')|int(99) <= 3 %}{% set a = a + ['🟡 MAX 除臭剂 N50 剩 ' ~ states('sensor.MAX_odor_eliminator_n50_left_days') ~ ' 天'] %}{% endif %}
{% if states('sensor.PRO2_odor_eliminator_n50_left_days')|int(99) <= 3 %}{% set a = a + ['🟡 PRO2 除臭剂 N50 剩 ' ~ states('sensor.PRO2_odor_eliminator_n50_left_days') ~ ' 天'] %}{% endif %}
{% if is_state('binary_sensor.MAX_wastebin_filled','on') %}{% set a = a + ['🗑 MAX 集便箱满'] %}{% endif %}
{% if is_state('binary_sensor.PRO2_wastebin_filled','on') %}{% set a = a + ['🗑 PRO2 集便箱满'] %}{% endif %}
{% if is_state('sensor.mmgg_cn_9000000004_inland_pet_food_left_level_p_2_6','Empty') %}{% set a = a + ['🍚 喂食器粮桶已空'] %}{% endif %}
{% if is_state('sensor.xiaomi_cn_9000000005_pi2001_pet_food_left_level_p_2_6','Empty') %}{% set a = a + ['🍚 喂食器2 粮桶已空'] %}{% endif %}
{% if states('sensor.xiaomi_cn_9000000005_pi2001_desiccant_left_level_p_6_1')|int(99) <= 10 %}{% set a = a + ['💊 喂食器2 干燥剂需更换'] %}{% endif %}
{% if states('sensor.mmgg_cn_9000000004_inland_desiccant_left_time_p_11_2')|int(99) <= 1 %}{% set a = a + ['💊 喂食器干燥剂需更换'] %}{% endif %}
{% if states('sensor.xiaomi_cn_blt_3_anonbledev01_002_filter_life_level_p_3_1076')|int(99) <= 20 %}{% set a = a + ['💧 饮水机滤芯 ' ~ states('sensor.xiaomi_cn_blt_3_anonbledev01_002_filter_life_level_p_3_1076') ~ '%'] %}{% endif %}
{% if states('sensor.xiaomi_cn_blt_3_anonbledev01_002_battery_level_p_5_1003')|int(99) <= 30 %}{% set a = a + ['🔋 饮水机电池 ' ~ states('sensor.xiaomi_cn_blt_3_anonbledev01_002_battery_level_p_5_1003') ~ '%'] %}{% endif %}
{% if states('sensor.zhimi_cn_9000000006_rma2_filter_life_level_p_4_1')|int(99) <= 10 %}{% set a = a + ['🌀 空净滤芯需更换'] %}{% endif %}
{% if states('sensor.hfjh_cn_9000000001_m100_filter_life_level_p_10_1')|int(99) <= 10 %}{% set a = a + ['🌿 猪笼草缸滤棉需更换'] %}{% endif %}
{% if a %}{{ a | join('\\n\\n') }}{% else %}✅ 所有耗材充足{% endif %}""".replace("MAX_", MAX + "_").replace("PRO2_", PRO2 + "_")

def cat_row(icon, name, slug):
    return ("| " + icon + " **" + name + "** "
            "| {{ states('sensor." + slug + "_last_weight_measurement') }} kg "
            "| {% set d = states('sensor." + slug + "_last_use_date') %}{{ d[5:16] if d.lower() not in ['unknown','unavailable','none'] else '—' }} "
            "| {% set t = states('sensor." + slug + "_last_use_duration') %}{{ t ~ 's' if t.lower() not in ['unknown','unavailable'] else '—' }} |")

CATS_MD = "\n".join([
    "|  | 体重 | 最近如厕 | 时长 |",
    "|---|---|---|---|",
    cat_row("🐈", "哦多茄", "e_duo_qie"),
    cat_row("🐈‍⬛", "雕猫", "diao_mao"),
    cat_row("🐱", "妹妹", "mei_mei"),
])

P1S_MD = ("**{{ states('sensor." + P1S + "_task_name') }}** · "
          "层 {{ states('sensor." + P1S + "_current_layer') }}/{{ states('sensor." + P1S + "_total_layer_count') }} · "
          "{{ states('sensor." + P1S + "_print_weight') }} g")

CONFIG = {
    "title": "贪玩兰粤",
    "views": [{
        "title": "总览", "path": "home", "icon": "mdi:home-heart",
        "type": "sections", "max_columns": 4, "dense_section_placement": True,
        "badges": [
            {"type": "entity", "entity": "weather.forecast_wo_de_jia", "show_entity_picture": True},
            {"type": "entity", "entity": "sensor.miaomiaoc_cn_blt_3_anonbledev02_t2_temperature_p_2_1", "name": "植物墙", "icon": "mdi:sprout", "color": "green"},
            {"type": "entity", "entity": "sensor.miaomiaoc_cn_blt_3_anonbledev03_t2_temperature_p_2_1", "name": "鸟笼", "icon": "mdi:bird", "color": "amber"},
            {"type": "entity", "entity": "sensor.xiaomi_cn_9000000008_r34r00_outdoor_temp_p_12_7", "name": "室外", "icon": "mdi:thermometer", "color": "deep-orange"},
        ],
        "sections": [
            # ── 列1: 今天 + 耗材 ──
            {"type": "grid", "cards": [
                {"type": "markdown", "content": HEADER_MD, "grid_options": {"columns": "full"}},
                {"type": "heading", "heading": "耗材提醒", "heading_style": "subtitle", "icon": "mdi:package-variant-closed-check"},
                {"type": "markdown", "content": ALERT_MD, "grid_options": {"columns": "full"}},
            ]},
            # ── 列2: 灯光 ──
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "灯光", "heading_style": "title", "icon": "mdi:lightbulb-group"},
                t("light.mijia_cn_group_90000000000000001_group3_s_2_light", "餐厅灯组"),
                t("light.mijia_cn_group_90000000000000002_group3_s_2_light", "餐厅射灯"),
                t("light.mijia_cn_group_90000000000000003_group3_s_2_light", "书墙灯组"),
                t("light.leishi_cn_9000000003_eps126_s_2_light", "餐厅吊灯"),
                t("light.yeelink_cn_9000000009_ml6_s_2_light", "格栅灯"),
                t("light.yeelink_cn_9000000010_wy0a03_s_2_light", "厨房灯带"),
                t("light.xiaomi_cn_9000000011_btlm2p_s_2_light", "大门筒灯"),
                t("light.yeelink_cn_9000000012_mbulb3_s_2", "壁灯"),
                t("light.ftd_cn_9000000013_dsplmp_s_2_light", "碗柜灯"),
                t("light.devcea_cn_9000000014_ls2305_s_2_light", "台灯2"),
                t("light.devcea_cn_9000000015_ls2305_s_2_light", "摇臂台灯"),
                t("light.lemesh_cn_9000000002_wy0d02_s_2_light", "盆栽射灯"),
            ]},
            # ── 列3: 猫咪 + 猫厕所 ──
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "猫咪", "heading_style": "title", "icon": "mdi:cat"},
                {"type": "markdown", "content": CATS_MD, "grid_options": {"columns": "full"}},
                {"type": "heading", "heading": "猫厕所 MAX", "heading_style": "subtitle", "icon": "mdi:litter-box",
                 "badges": [{"type": "entity", "entity": f"binary_sensor.{MAX}_toilet_occupied", "name": "占用"}]},
                t(f"sensor.{MAX}_state", "状态", icon="mdi:robot", color="indigo"),
                t(f"sensor.{MAX}_last_used_by", "最近", icon="mdi:paw", color="pink"),
                t(f"button.{MAX}_scoop", "铲屎", icon="mdi:shovel", color="orange", tap_action={"action": "toggle"}),
                t(f"button.{MAX}_deodorize", "除臭", icon="mdi:spray", color="cyan", tap_action={"action": "toggle"}),
                {"type": "heading", "heading": "猫厕所 MAX PRO 2", "heading_style": "subtitle", "icon": "mdi:litter-box",
                 "badges": [{"type": "entity", "entity": f"binary_sensor.{PRO2}_toilet_occupied", "name": "占用"}]},
                t(f"sensor.{PRO2}_state", "状态", icon="mdi:robot", color="indigo"),
                t(f"sensor.{PRO2}_last_used_by", "最近", icon="mdi:paw", color="pink"),
                t(f"button.{PRO2}_scoop", "铲屎", icon="mdi:shovel", color="orange", tap_action={"action": "toggle"}),
                t(f"light.{PRO2}_light", "照明", icon="mdi:lightbulb-on"),
                {"type": "picture-entity", "entity": f"camera.{PRO2}", "camera_image": f"camera.{PRO2}",
                 "camera_view": "live", "show_state": False, "show_name": False,
                 "grid_options": {"columns": "full", "rows": 4}},
            ]},
            # ── 列4: P1S ──
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "拓竹 P1S", "heading_style": "title", "icon": "mdi:printer-3d",
                 "badges": [{"type": "entity", "entity": f"binary_sensor.{P1S}_online", "name": "在线"},
                            {"type": "entity", "entity": f"sensor.{P1S}_print_progress", "name": "进度"}]},
                {"type": "picture-glance", "title": "",
                 "camera_image": f"camera.{P1S}_camera", "camera_view": "live",
                 "entities": [f"light.{P1S}_chamber_light", f"fan.{P1S}_chamber_fan"],
                 "grid_options": {"columns": "full", "rows": 4}},
                t(f"sensor.{P1S}_print_status", "状态", icon="mdi:printer-3d-nozzle", color="purple"),
                t(f"sensor.{P1S}_remaining_time", "剩余", icon="mdi:clock-outline", color="amber"),
                t(f"sensor.{P1S}_nozzle_temperature", "喷嘴", icon="mdi:thermometer-high", color="red"),
                t(f"sensor.{P1S}_bed_temperature", "热床", icon="mdi:radiator", color="orange"),
                {"type": "markdown", "content": P1S_MD, "grid_options": {"columns": "full"}},
            ]},
        ],
    }],
}

async def main():
    async with websockets.connect(WS_URL, max_size=10 * 2**20) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        assert json.loads(await ws.recv())["type"] == "auth_ok", "auth failed"
        await ws.send(json.dumps({"id": 1, "type": "lovelace/config/save",
                                  "url_path": URL_PATH, "config": CONFIG}))
        while True:
            r = json.loads(await ws.recv())
            if r.get("id") == 1 and r["type"] == "result":
                print("save:", r["success"], r.get("error", ""))
                sys.exit(0 if r["success"] else 1)

asyncio.run(main())
