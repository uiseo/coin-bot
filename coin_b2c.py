import discord
from discord.ext import tasks
import aiohttp
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DISCORD_TOKEN = os.getenv("B2C_DISCORD_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
CHANNEL_ID = int(os.getenv("B2C_CHANNEL_ID"))

client_discord = discord.Client(intents=discord.Intents.default())
client_openai = OpenAI(api_key=OPENAI_KEY)

async def get_coin_data(coin):
    url = f"https://api.upbit.com/v1/ticker?markets=KRW-{coin.upper()}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            if not data or "error" in data:
                return None
            return data[0]

async def get_fear_greed():
    url = "https://api.alternative.me/fng/"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json(content_type=None)
            return data["data"][0]

def detect_whale(coin_data):
    alerts = []
    avg_trade = coin_data["acc_trade_price_24h"] / 24
    current_trade = coin_data["acc_trade_price"]
    ratio = current_trade / avg_trade if avg_trade > 0 else 0
    if ratio > 2.0:
        alerts.append({"type": "매수", "ratio": ratio, "amount": coin_data["acc_trade_price"]})
    if coin_data["signed_change_rate"] < -0.02 and ratio > 1.5:
        alerts.append({"type": "매도", "ratio": ratio, "amount": coin_data["acc_trade_price"]})
    return alerts

@tasks.loop(hours=4)
async def auto_market():
    channel = client_discord.get_channel(CHANNEL_ID)
    if not channel:
        return
    coins = ["BTC", "ETH", "XRP", "SOL", "DOGE"]
    msg = "**📊 자동 시장 현황 업데이트**\n\n"
    for coin_name in coins:
        coin = await get_coin_data(coin_name)
        if coin:
            change = coin["signed_change_rate"] * 100
            emoji = "📈" if change > 0 else "📉"
            msg += f"{emoji} **{coin_name}** {coin['trade_price']:,.0f}원 ({change:+.2f}%)\n"
    fear_data = await get_fear_greed()
    msg += f"\n😨 공포/탐욕 지수: **{fear_data['value']}점** ({fear_data['value_classification']})"
    await channel.send(msg)

@client_discord.event
async def on_ready():
    print(f"B2C 봇 실행 중: {client_discord.user}")
    auto_market.start()

@client_discord.event
async def on_message(message):
    if message.author == client_discord.user:
        return

    if message.content.startswith("!코인"):
        coin_name = message.content.replace("!코인", "").strip()
        coin = await get_coin_data(coin_name)
        if not coin:
            await message.channel.send("코인을 찾을 수 없어요. 예: !코인 BTC")
            return
        prompt = f"""
코인 데이터:
현재가: {coin['trade_price']}원
고가: {coin['high_price']}원
저가: {coin['low_price']}원
전일대비: {coin['change']}

위 데이터를 초보 투자자도 이해할 수 있게 분석해줘.
다음 형식으로 답해줘:
1. 현재 상황: (한 줄 요약)
2. 근거: (어떤 데이터 때문에 이런 판단인지)
3. 주의사항: (투자 시 조심할 점)
말투는 친근하고 쉽게 해줘.
"""
        result = client_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        answer = result.choices[0].message.content
        await message.channel.send(f"**{coin_name.upper()} 분석** 📊\n현재가: {coin['trade_price']:,.0f}원\n\n{answer}")

    if message.content.startswith("!공포"):
        data = await get_fear_greed()
        value = int(data["value"])
        classification = data["value_classification"]
        if value <= 25:
            emoji, comment, advice = "😱", "극단적 공포 상태예요. 많은 사람들이 겁을 먹고 있어요.", "과도한 공포로 저평가된 구간일 수 있어요. 신중하게 판단해보세요!"
        elif value <= 45:
            emoji, comment, advice = "😟", "공포 상태예요. 시장 심리가 불안한 상황이에요.", "불안한 시장이지만 무작정 팔기보다 상황을 지켜보세요!"
        elif value <= 55:
            emoji, comment, advice = "😐", "중립 상태예요. 시장이 균형을 유지하고 있어요.", "큰 방향성 없이 관망하기 좋은 시기예요!"
        elif value <= 75:
            emoji, comment, advice = "😊", "탐욕 상태예요. 시장이 과열되기 시작했어요.", "모두가 살 때 조심해야 해요. 과도한 투자는 주의하세요!"
        else:
            emoji, comment, advice = "🤑", "극단적 탐욕 상태예요. 시장이 많이 과열됐어요.", "버블 위험이 있을 수 있어요. 지금은 신중한 투자가 필요해요!"
        await message.channel.send(
            f"**오늘의 공포/탐욕 지수** {emoji}\n\n"
            f"지수: **{value}점** ({classification})\n\n"
            f"📌 {comment}\n"
            f"💡 {advice}"
        )

    if message.content.startswith("!고래"):
        coin_name = message.content.replace("!고래", "").strip()
        coin = await get_coin_data(coin_name)
        if not coin:
            await message.channel.send("코인을 찾을 수 없어요. 예: !고래 BTC")
            return
        alerts = detect_whale(coin)
        if alerts:
            msg = f"**🐳 {coin_name.upper()} 고래 움직임 감지!**\n\n"
            for alert in alerts:
                msg += f"{'📈' if alert['type'] == '매수' else '📉'} 대량 {alert['type']} 감지!\n"
                msg += f"평소 대비 **{alert['ratio']:.1f}배** 거래 중\n"
                msg += f"거래대금: **{alert['amount']:,.0f}원**\n\n"
            msg += "⚠️ 고래 움직임이 포착됐어요. 변동성이 커질 수 있으니 신중하게 판단하세요!\n"
            msg += "💡 이건 투자 신호가 아닌 참고 정보예요. 투자 결정은 본인이 하세요!"
            whale_prompt = f"""
{coin_name.upper()} 코인에서 평소 대비 {alerts[0]['ratio']:.1f}배 대량 거래가 감지됐어요.
현재가: {coin['trade_price']:,.0f}원
전일대비: {coin['change']}

고래 움직임의 가능한 이유와 개인 투자자가 주의해야 할 점을
쉽고 친근하게 2줄로 설명해줘.
근거 없는 예측은 하지 말고, 가능성만 언급해줘.
"""
            whale_analysis = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": whale_prompt}]
            )
            msg += f"\n🤖 AI 분석:\n{whale_analysis.choices[0].message.content}"
            await message.channel.send(msg)
        else:
            await message.channel.send(f"**{coin_name.upper()}** 현재 고래 움직임 없음 ✅\n평소와 비슷한 거래량이에요.")

client_discord.run(DISCORD_TOKEN)
