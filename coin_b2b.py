import discord
from discord.ext import tasks
import aiohttp
import sqlite3
import statistics
import os
from dotenv import load_dotenv
from datetime import datetime
from openai import OpenAI

load_dotenv()

DISCORD_TOKEN = os.getenv("B2B_DISCORD_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
CHANNEL_ID = int(os.getenv("B2B_CHANNEL_ID"))

client_discord = discord.Client(intents=discord.Intents.default())
client_openai = OpenAI(api_key=OPENAI_KEY)

monitoring_state = {}

DB_PATH = os.path.expanduser("~/coin_data.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coin_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            coin TEXT,
            price REAL,
            volume REAL,
            change_rate REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            coin TEXT,
            zscore REAL,
            level TEXT,
            action TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitoring_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            coin TEXT,
            prev_state TEXT,
            new_state TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_coin_data(coin, price, volume, change_rate):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO coin_history (timestamp, coin, price, volume, change_rate)
        VALUES (?, ?, ?, ?, ?)
    """, (datetime.now().strftime("%Y-%m-%d %H:%M"), coin, price, volume, change_rate))
    conn.commit()
    conn.close()

def save_alert(coin, zscore, level, action):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO alert_history (timestamp, coin, zscore, level, action)
        VALUES (?, ?, ?, ?, ?)
    """, (datetime.now().strftime("%Y-%m-%d %H:%M"), coin, zscore, level, action))
    conn.commit()
    conn.close()

def save_monitoring_log(coin, prev_state, new_state):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO monitoring_log (timestamp, coin, prev_state, new_state)
        VALUES (?, ?, ?, ?)
    """, (datetime.now().strftime("%Y-%m-%d %H:%M"), coin, prev_state, new_state))
    conn.commit()
    conn.close()

def get_history(coin, limit=50):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        SELECT price, volume, change_rate FROM coin_history
        WHERE coin = ?
        ORDER BY id DESC LIMIT ?
    """, (coin, limit))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_hourly_avg_volume(coin):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        SELECT strftime('%H', timestamp) as hour,
               AVG(volume) as avg_volume,
               COUNT(*) as count
        FROM coin_history
        WHERE coin = ?
        GROUP BY hour
        ORDER BY avg_volume DESC
        LIMIT 3
    """, (coin,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_alert_summary(coin):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        SELECT level, COUNT(*) as count
        FROM alert_history
        WHERE coin = ?
        GROUP BY level
        ORDER BY count DESC
    """, (coin,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_recent_alerts(coin, limit=3):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        SELECT timestamp, zscore, level, action
        FROM alert_history
        WHERE coin = ?
        ORDER BY id DESC LIMIT ?
    """, (coin, limit))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_price_trend(coin):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        SELECT price FROM coin_history
        WHERE coin = ?
        ORDER BY id DESC LIMIT 10
    """, (coin,))
    rows = cursor.fetchall()
    conn.close()
    if len(rows) < 2:
        return None
    prices = [row[0] for row in rows]
    change = (prices[0] - prices[-1]) / prices[-1] * 100
    return change

def calc_zscore(values, current):
    if len(values) < 5:
        return None
    avg = statistics.mean(values)
    std = statistics.stdev(values)
    if std == 0:
        return 0
    return (current - avg) / std

def get_risk_level(zscore):
    if zscore is None:
        return "collecting", None, "데이터 수집 중"
    abs_z = abs(zscore)
    if abs_z < 1.0:
        return "normal", "✅ 정상", "정상 범위"
    elif abs_z < 2.0:
        return "watch", "⚠️ 주의", "약간 비정상"
    elif abs_z < 3.0:
        return "warning", "🔶 경고", "이상 거래 의심"
    else:
        return "critical", "🚨 위험", "강한 이상 거래"

async def get_coin_data(coin):
    url = f"https://api.upbit.com/v1/ticker?markets=KRW-{coin.upper()}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            if not data or "error" in data:
                return None
            return data[0]

async def send_risk_report(channel, coin_name, coin, zscore, level, risk_emoji, risk_label, interval_msg):
    prompt = f"""
{coin_name.upper()} 코인 리스크 분석 데이터:
현재가: {coin['trade_price']:,.0f}원
거래량 Z-score: {zscore:.2f} ({risk_label})
전일대비: {coin['signed_change_rate']*100:.2f}%

위 데이터를 기업 리스크 관리 담당자에게 보고하는 형식으로
전문적이고 간결하게 3줄로 분석해줘.
투자 권유 없이 리스크 수준만 객관적으로 서술해줘.
"""
    result = client_openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    answer = result.choices[0].message.content
    msg = f"**{coin_name.upper()} 리스크 분석 리포트** 📊\n\n"
    msg += f"현재가: {coin['trade_price']:,.0f}원\n"
    msg += f"거래량 이상도: {risk_emoji} (Z-score: {zscore:.2f})\n"
    msg += f"모니터링 상태: {interval_msg}\n\n"
    msg += f"**AI 분석:**\n{answer}\n\n"
    msg += "⚠️ 본 분석은 참고용이며 투자 판단의 책임은 본인에게 있습니다."
    await channel.send(msg)

@tasks.loop(minutes=30)
async def collect_data():
    coins = ["BTC", "ETH", "XRP", "SOL", "DOGE"]
    for coin_name in coins:
        coin = await get_coin_data(coin_name)
        if coin:
            save_coin_data(coin_name, coin["trade_price"], coin["acc_trade_volume_24h"], coin["signed_change_rate"])

@tasks.loop(minutes=10)
async def critical_monitor():
    channel = client_discord.get_channel(CHANNEL_ID)
    if not channel:
        return
    for coin_name, state in list(monitoring_state.items()):
        if state != "critical":
            continue
        coin = await get_coin_data(coin_name)
        if not coin:
            continue
        history = get_history(coin_name)
        if len(history) < 5:
            continue
        volumes = [row[1] for row in history]
        zscore = calc_zscore(volumes, coin["acc_trade_volume_24h"])
        level, risk_emoji, risk_label = get_risk_level(zscore)
        prev_state = monitoring_state[coin_name]
        monitoring_state[coin_name] = level
        if level != "critical":
            save_monitoring_log(coin_name, prev_state, level)
            await channel.send(f"**{coin_name.upper()}** 🟢 긴급 모니터링 해제 → {risk_emoji} 상태로 전환됩니다.")
            continue
        save_alert(coin_name, zscore, level, "10분 긴급 모니터링")
        await send_risk_report(channel, coin_name, coin, zscore, level, risk_emoji, risk_label, "🚨 10분마다 긴급 모니터링 중")

@tasks.loop(minutes=30)
async def warning_monitor():
    channel = client_discord.get_channel(CHANNEL_ID)
    if not channel:
        return
    for coin_name, state in list(monitoring_state.items()):
        if state != "warning":
            continue
        coin = await get_coin_data(coin_name)
        if not coin:
            continue
        history = get_history(coin_name)
        if len(history) < 5:
            continue
        volumes = [row[1] for row in history]
        zscore = calc_zscore(volumes, coin["acc_trade_volume_24h"])
        level, risk_emoji, risk_label = get_risk_level(zscore)
        prev_state = monitoring_state[coin_name]
        monitoring_state[coin_name] = level
        if level != "warning":
            save_monitoring_log(coin_name, prev_state, level)
            if level == "critical":
                await channel.send(f"**{coin_name.upper()}** 🚨 위험 수준 상승! 긴급 모니터링으로 전환합니다.")
            else:
                await channel.send(f"**{coin_name.upper()}** 🟢 경고 모니터링 해제 → {risk_emoji} 상태로 전환됩니다.")
                continue
        save_alert(coin_name, zscore, level, "30분 경고 모니터링")
        await send_risk_report(channel, coin_name, coin, zscore, level, risk_emoji, risk_label, "🔶 30분마다 경고 모니터링 중")

@tasks.loop(hours=4)
async def auto_risk_report():
    channel = client_discord.get_channel(CHANNEL_ID)
    if not channel:
        return
    coins = ["BTC", "ETH", "XRP", "SOL", "DOGE"]
    msg = "**📋 정기 리스크 리포트**\n\n"
    for coin_name in coins:
        coin = await get_coin_data(coin_name)
        if not coin:
            continue
        history = get_history(coin_name)
        if len(history) < 5:
            msg += f"**{coin_name}** 데이터 수집 중...\n"
            continue
        volumes = [row[1] for row in history]
        zscore = calc_zscore(volumes, coin["acc_trade_volume_24h"])
        level, risk_emoji, risk_label = get_risk_level(zscore)
        prev_state = monitoring_state.get(coin_name, "normal")
        if level != prev_state:
            save_monitoring_log(coin_name, prev_state, level)
        monitoring_state[coin_name] = level
        msg += f"{risk_emoji} **{coin_name}** {coin['trade_price']:,.0f}원\n"
        if zscore is not None:
            msg += f"   Z-score: {zscore:.2f} | {risk_label}\n"
        if level == "critical" and prev_state != "critical":
            save_alert(coin_name, zscore, level, "긴급 모니터링 시작")
            msg += f"   🚨 **긴급 모니터링 시작** (10분 주기)\n"
        elif level == "warning" and prev_state != "warning":
            save_alert(coin_name, zscore, level, "경고 모니터링 시작")
            msg += f"   🔶 **경고 모니터링 시작** (30분 주기)\n"
        msg += "\n"
    msg += "⚠️ 본 리포트는 참고용이며 투자 판단의 책임은 본인에게 있습니다."
    await channel.send(msg)

@client_discord.event
async def on_ready():
    print(f"B2B 봇 실행 중: {client_discord.user}")
    init_db()
    collect_data.start()
    auto_risk_report.start()
    critical_monitor.start()
    warning_monitor.start()

@client_discord.event
async def on_message(message):
    if message.author == client_discord.user:
        return

    if message.content.startswith("!리스크"):
        coin_name = message.content.replace("!리스크", "").strip()
        coin = await get_coin_data(coin_name)
        if not coin:
            await message.channel.send("코인을 찾을 수 없어요. 예: !리스크 BTC")
            return
        save_coin_data(coin_name, coin["trade_price"], coin["acc_trade_volume_24h"], coin["signed_change_rate"])
        history = get_history(coin_name)
        if len(history) < 5:
            await message.channel.send(f"**{coin_name.upper()}** 데이터를 수집 중이에요. 잠시 후 다시 시도해주세요.")
            return
        volumes = [row[1] for row in history]
        zscore = calc_zscore(volumes, coin["acc_trade_volume_24h"])
        level, risk_emoji, risk_label = get_risk_level(zscore)
        prev_state = monitoring_state.get(coin_name, "normal")
        if level != prev_state:
            save_monitoring_log(coin_name, prev_state, level)
        monitoring_state[coin_name] = level
        if level in ["warning", "critical"]:
            action = "10분 긴급 모니터링" if level == "critical" else "30분 경고 모니터링"
            save_alert(coin_name, zscore, level, action)
        interval_map = {
            "critical": "🚨 10분마다 긴급 모니터링 중",
            "warning": "🔶 30분마다 경고 모니터링 중",
            "watch": "⚠️ 4시간마다 정기 모니터링 중",
            "normal": "✅ 4시간마다 정기 모니터링 중"
        }
        await send_risk_report(message.channel, coin_name, coin, zscore, level, risk_emoji, risk_label, interval_map.get(level, ""))

    if message.content.startswith("!통계"):
        coin_name = message.content.replace("!통계", "").strip()
        history = get_history(coin_name)
        if len(history) < 5:
            await message.channel.send(f"**{coin_name.upper()}** 데이터가 부족해요. 잠시 후 다시 시도해주세요.")
            return
        volumes = [row[1] for row in history]
        prices = [row[0] for row in history]
        trend = get_price_trend(coin_name)
        hourly = get_hourly_avg_volume(coin_name)
        alert_summary = get_alert_summary(coin_name)
        recent_alerts = get_recent_alerts(coin_name)
        msg = f"**{coin_name.upper()} 통계 분석** 📈\n\n"
        msg += f"**가격 통계**\n"
        msg += f"평균가: {statistics.mean(prices):,.0f}원\n"
        msg += f"최고가: {max(prices):,.0f}원\n"
        msg += f"최저가: {min(prices):,.0f}원\n"
        if trend is not None:
            trend_emoji = "📈" if trend > 0 else "📉"
            msg += f"최근 추이: {trend_emoji} {trend:+.2f}%\n\n"
        msg += f"**거래량 통계**\n"
        msg += f"평균 거래량: {statistics.mean(volumes):,.1f}\n"
        msg += f"최대 거래량: {max(volumes):,.1f}\n\n"
        if hourly:
            msg += f"**거래량 많은 시간대 TOP3**\n"
            for row in hourly:
                msg += f"{row[0]}시: 평균 {row[1]:,.1f}\n"
            msg += "\n"
        if alert_summary:
            msg += f"**이상 감지 이력**\n"
            for row in alert_summary:
                msg += f"{row[0]}: {row[1]}회\n"
            msg += "\n"
        if recent_alerts:
            msg += f"**최근 이상 감지 기록**\n"
            for row in recent_alerts:
                msg += f"{row[0]} | Z-score: {row[1]:.2f} | {row[2]}\n"
        await message.channel.send(msg)

    if message.content.startswith("!히스토리"):
        coin_name = message.content.replace("!히스토리", "").strip()
        history = get_history(coin_name, limit=5)
        if not history:
            await message.channel.send(f"**{coin_name.upper()}** 히스토리 데이터가 없어요.")
            return
        msg = f"**{coin_name.upper()} 최근 히스토리** 📈\n\n"
        for i, row in enumerate(history):
            msg += f"{i+1}. 가격: {row[0]:,.0f}원 | 변동률: {row[2]*100:+.2f}%\n"
        await message.channel.send(msg)

    if message.content.startswith("!모니터링"):
        if not monitoring_state:
            await message.channel.send("현재 모니터링 중인 코인이 없어요.")
            return
        msg = "**📡 현재 모니터링 상태**\n\n"
        state_map = {
            "normal": "✅ 정상 (4시간 주기)",
            "watch": "⚠️ 주의 (4시간 주기)",
            "warning": "🔶 경고 (30분 주기)",
            "critical": "🚨 위험 (10분 주기)"
        }
        for coin_name, state in monitoring_state.items():
            msg += f"**{coin_name}**: {state_map.get(state, state)}\n"
        await message.channel.send(msg)

client_discord.run(DISCORD_TOKEN)
