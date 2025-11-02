from flask import Flask, jsonify, render_template, request
import requests
import time, threading
import csv
import math
from database import create_connection, create_tables, insert_stock, get_stocks, insert_zone, get_zones, insert_strategy, get_strategies

app = Flask(__name__)

# بيانات الكاش (آخر الأسعار)
cache = {}
UPDATE_INTERVAL = 86400  # 24 ساعة (بالثواني)

# قائمة الشركات التي سيتم تحديثها تلقائيًا (رموز السوق السعودي)
symbols = ["2020.SR", "1180.SR", "1120.SR", "1010.SR"]  # مثال: سابك، الأهلي، الراجحي، الرياض

# تحميل قائمة الأسهم من symbols.csv
stock_list = []
with open('symbols.csv', 'r', encoding='utf-8') as f:
    reader = csv.reader(f)
    for row in reader:
        if len(row) >= 2:
            symbol = row[0]
            name = row[1]
            region = row[2] if len(row) > 2 else ''
            stock_list.append({"symbol": symbol, "name": name, "region": region})

def fetch_price(symbol):
    """جلب سعر السهم من Yahoo Finance كبديل"""
    try:
        # استخدام Yahoo Finance كبديل
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
                result = data['chart']['result'][0]
                meta = result['meta']
                price = meta.get('regularMarketPrice', 0)
                bid = meta.get('bid', price)  # افتراضي إذا غير متوفر
                ask = meta.get('ask', price)  # افتراضي إذا غير متوفر
                difference = ask - bid if ask and bid else 0

                # حساب المؤشرات الفنية
                indicators = calculate_indicators(result)

                # استخراج بيانات OHLC للرسم البياني
                timestamps = result.get('timestamp', [])
                opens = result.get('indicators', {}).get('quote', [{}])[0].get('open', [])
                highs = result.get('indicators', {}).get('quote', [{}])[0].get('high', [])
                lows = result.get('indicators', {}).get('quote', [{}])[0].get('low', [])
                closes = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])

                # تنظيف البيانات وإنشاء قائمة OHLC
                ohlc_data = []
                for i in range(len(timestamps)):
                    if opens[i] is not None and highs[i] is not None and lows[i] is not None and closes[i] is not None:
                        ohlc_data.append({
                            't': timestamps[i] * 1000,  # تحويل إلى مللي ثانية لـ Chart.js
                            'o': opens[i],
                            'h': highs[i],
                            'l': lows[i],
                            'c': closes[i]
                        })

                cache[symbol] = {
                    "data": {
                        "symbol": symbol,
                        "price": price,
                        "bid": bid,
                        "ask": ask,
                        "difference": difference,
                        "currency": "SAR",
                        "indicators": indicators,
                        "ohlc": ohlc_data[-30:] if len(ohlc_data) > 30 else ohlc_data  # آخر 30 يوم
                    },
                    "time": time.strftime("%Y-%m-%d %H:%M:%S")
                }

                # حفظ البيانات في قاعدة البيانات
                conn = create_connection()
                if conn:
                    stock_data = (symbol.replace('.SR', ''), symbol.replace('.SR', ''), '', price, bid, ask, difference, "SAR")
                    insert_stock(conn, stock_data)
                    conn.close()

                print(f"[✅] تم تحديث {symbol}: {price} ريال")
            else:
                print(f"[⚠️] خطأ في جلب {symbol}: بيانات غير متوفرة")
        else:
            print(f"[⚠️] خطأ في جلب {symbol}: {response.status_code}")
    except Exception as e:
        print(f"[❌] خطأ أثناء جلب {symbol}: {e}")

def calculate_indicators(chart_result):
    """حساب المؤشرات الفنية"""
    try:
        timestamps = chart_result.get('timestamp', [])
        closes = chart_result.get('indicators', {}).get('quote', [{}])[0].get('close', [])

        if not closes or len(closes) < 14:
            return {
                "SMA_20": None,
                "EMA_20": None,
                "RSI": None,
                "MACD": None,
                "Bollinger_Bands": None
            }

        # تنظيف البيانات
        closes = [c for c in closes if c is not None]
        if len(closes) < 14:
            return {
                "SMA_20": None,
                "EMA_20": None,
                "RSI": None,
                "MACD": None,
                "Bollinger_Bands": None
            }

        # SMA (Simple Moving Average) - 20 يوم
        sma_20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None

        # EMA (Exponential Moving Average) - 20 يوم
        ema_20 = calculate_ema(closes, 20)

        # RSI (Relative Strength Index) - 14 يوم
        rsi = calculate_rsi(closes, 14)

        # MACD
        macd = calculate_macd(closes)

        # Bollinger Bands
        bollinger = calculate_bollinger_bands(closes, 20)

        return {
            "SMA_20": round(sma_20, 2) if sma_20 else None,
            "EMA_20": round(ema_20, 2) if ema_20 else None,
            "RSI": round(rsi, 2) if rsi else None,
            "MACD": {
                "value": round(macd['macd'][-1], 2) if macd['macd'] else None,
                "signal": round(macd['signal'][-1], 2) if macd['signal'] else None,
                "histogram": round(macd['histogram'][-1], 2) if macd['histogram'] else None
            } if macd else None,
            "Bollinger_Bands": {
                "upper": round(bollinger['upper'][-1], 2) if bollinger['upper'] else None,
                "middle": round(bollinger['middle'][-1], 2) if bollinger['middle'] else None,
                "lower": round(bollinger['lower'][-1], 2) if bollinger['lower'] else None
            } if bollinger else None
        }
    except Exception as e:
        print(f"[❌] خطأ في حساب المؤشرات: {e}")
        return {
            "SMA_20": None,
            "EMA_20": None,
            "RSI": None,
            "MACD": None,
            "Bollinger_Bands": None
        }

def calculate_ema(prices, period):
    """حساب EMA"""
    if len(prices) < period:
        return None
    ema = []
    multiplier = 2 / (period + 1)
    ema.append(sum(prices[:period]) / period)  # البداية بـ SMA
    for price in prices[period:]:
        ema.append((price * multiplier) + (ema[-1] * (1 - multiplier)))
    return ema[-1]

def calculate_rsi(prices, period):
    """حساب RSI"""
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_macd(prices):
    """حساب MACD"""
    if len(prices) < 26:
        return None
    ema_12 = calculate_ema(prices, 12)
    ema_26 = calculate_ema(prices, 26)
    if ema_12 is None or ema_26 is None:
        return None
    macd_line = ema_12 - ema_26
    signal_line = calculate_ema([macd_line], 9)  # EMA من MACD
    if signal_line is None:
        return None
    histogram = macd_line - signal_line
    return {
        'macd': [macd_line],
        'signal': [signal_line],
        'histogram': [histogram]
    }

def calculate_bollinger_bands(prices, period):
    """حساب Bollinger Bands"""
    if len(prices) < period:
        return None
    sma = sum(prices[-period:]) / period
    variance = sum((price - sma) ** 2 for price in prices[-period:]) / period
    std_dev = math.sqrt(variance)
    upper = sma + (2 * std_dev)
    lower = sma - (2 * std_dev)
    return {
        'upper': [upper],
        'middle': [sma],
        'lower': [lower]
    }

# Advanced Supply and Demand Analysis Functions

def analyze_time_frames(ohlc_data):
    """تحليل الإطارات الزمنية المختلفة مع تحديد نوع الترند"""
    if not ohlc_data or len(ohlc_data) < 50:
        return None

    closes = [candle['c'] for candle in ohlc_data]
    highs = [candle['h'] for candle in ohlc_data]
    lows = [candle['l'] for candle in ohlc_data]

    # Monthly analysis (last 30 days)
    monthly_data = closes[-30:] if len(closes) >= 30 else closes
    monthly_high = max(highs[-30:]) if len(highs) >= 30 else max(highs)
    monthly_low = min(lows[-30:]) if len(lows) >= 30 else min(lows)

    # تحديد نوع الترند الشهري
    monthly_trend_type = identify_trend_type(monthly_data, highs[-30:] if len(highs) >= 30 else highs, lows[-30:] if len(lows) >= 30 else lows)

    # Weekly analysis (last 7 days)
    weekly_data = closes[-7:] if len(closes) >= 7 else closes
    weekly_high = max(highs[-7:]) if len(highs) >= 7 else max(highs)
    weekly_low = min(lows[-7:]) if len(lows) >= 7 else min(lows)

    # تحديد نوع الترند الأسبوعي
    weekly_trend_type = identify_trend_type(weekly_data, highs[-7:] if len(highs) >= 7 else highs, lows[-7:] if len(lows) >= 7 else lows)

    # Daily analysis (last day)
    daily_high = highs[-1] if highs else None
    daily_low = lows[-1] if lows else None

    # تحديد نوع الترند اليومي
    daily_trend_type = 'up' if len(closes) > 1 and closes[-1] > closes[-2] else 'down'

    # Entry analysis (current price context)
    current_price = closes[-1] if closes else None

    # تحديد نوع الترند الدخول
    entry_data = closes[-5:] if len(closes) >= 5 else closes
    entry_trend_type = identify_trend_type(entry_data, highs[-5:] if len(highs) >= 5 else highs, lows[-5:] if len(lows) >= 5 else lows)

    return {
        'monthly': {
            'high': monthly_high,
            'low': monthly_low,
            'trend': monthly_trend_type
        },
        'weekly': {
            'high': weekly_high,
            'low': weekly_low,
            'trend': weekly_trend_type
        },
        'daily': {
            'high': daily_high,
            'low': daily_low,
            'trend': daily_trend_type
        },
        'entry': {
            'current_price': current_price,
            'trend': entry_trend_type,
            'position': 'above_mid' if current_price and current_price > (monthly_high + monthly_low) / 2 else 'below_mid'
        }
    }

def identify_trend_type(closes, highs, lows):
    """تحديد نوع الترند (صاعد، هابط، فرعي)"""
    if len(closes) < 5:
        return 'unknown'

    # الترند الصاعد: قاعان أعلى من بعض
    higher_lows = all(lows[i] > lows[i-1] for i in range(1, len(lows)))
    # الترند الهابط: قمتان أقل من بعض
    lower_highs = all(highs[i] < highs[i-1] for i in range(1, len(highs)))

    if higher_lows and closes[-1] > closes[0]:
        return 'uptrend'
    elif lower_highs and closes[-1] < closes[0]:
        return 'downtrend'
    elif len(closes) >= 3:
        # الترند الفرعي: قاعين وقمة أو قمتين وقاع
        recent_lows = lows[-3:]
        recent_highs = highs[-3:]
        if recent_lows[1] < min(recent_lows[0], recent_lows[2]) and recent_highs[1] > max(recent_highs[0], recent_highs[2]):
            return 'internal_trend'
        else:
            return 'sideways'
    else:
        return 'sideways'

def identify_trend(ohlc_data):
    """تحديد الاتجاه العام"""
    if not ohlc_data or len(ohlc_data) < 20:
        return None

    closes = [candle['c'] for candle in ohlc_data]

    # Calculate moving averages
    sma_20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None

    current_price = closes[-1]

    # Trend identification
    if sma_20 and sma_50:
        if current_price > sma_20 > sma_50:
            main_trend = 'uptrend'
        elif current_price < sma_20 < sma_50:
            main_trend = 'downtrend'
        else:
            main_trend = 'sideways'
    else:
        main_trend = 'unknown'

    # Internal trend (short-term)
    if len(closes) >= 5:
        recent_trend = 'up' if closes[-1] > closes[-5] else 'down'
    else:
        recent_trend = 'unknown'

    return {
        'main_trend': main_trend,
        'internal_trend': recent_trend,
        'sma_20': sma_20,
        'sma_50': sma_50
    }

def calculate_curve(ohlc_data):
    """حساب المنحنى (المسافة بين العرض والطلب)"""
    if not ohlc_data or len(ohlc_data) < 10:
        return None

    highs = [candle['h'] for candle in ohlc_data]
    lows = [candle['l'] for candle in ohlc_data]

    # Calculate average range
    ranges = [high - low for high, low in zip(highs, lows)]
    avg_range = sum(ranges) / len(ranges) if ranges else 0

    # Current curve (distance between recent high and low)
    recent_high = max(highs[-10:]) if len(highs) >= 10 else max(highs)
    recent_low = min(lows[-10:]) if len(lows) >= 10 else min(lows)
    curve = recent_high - recent_low

    return {
        'curve': curve,
        'avg_range': avg_range,
        'normalized_curve': curve / avg_range if avg_range > 0 else 0
    }

def identify_zones(ohlc_data):
    """تحديد مناطق العرض والطلب"""
    if not ohlc_data or len(ohlc_data) < 20:
        return []

    highs = [candle['h'] for candle in ohlc_data]
    lows = [candle['l'] for candle in ohlc_data]
    closes = [candle['c'] for candle in ohlc_data]

    zones = []

    # Peak detection for supply zones
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            # Supply zone (resistance)
            strength = evaluate_zone_strength(ohlc_data, i, 'supply')
            zones.append({
                'type': 'supply',
                'price': highs[i],
                'index': i,
                'strength': strength,
                'pattern': 'peak'
            })

    # Valley detection for demand zones
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            # Demand zone (support)
            strength = evaluate_zone_strength(ohlc_data, i, 'demand')
            zones.append({
                'type': 'demand',
                'price': lows[i],
                'index': i,
                'strength': strength,
                'pattern': 'valley'
            })

    # Sort zones by strength
    zones.sort(key=lambda x: x['strength'], reverse=True)

    return zones[:10]  # Return top 10 zones

def evaluate_zone_strength(ohlc_data, index, zone_type):
    """تقييم قوة المنطقة"""
    if not ohlc_data or index >= len(ohlc_data):
        return 0

    # Freshness factor (how recent the zone is)
    total_candles = len(ohlc_data)
    freshness = 1 - (index / total_candles)  # More recent = higher score

    # Candle count factor (how many candles touched the zone)
    touch_count = 0
    zone_price = ohlc_data[index]['h'] if zone_type == 'supply' else ohlc_data[index]['l']

    # Check surrounding candles
    start_idx = max(0, index - 10)
    end_idx = min(len(ohlc_data), index + 10)

    for i in range(start_idx, end_idx):
        candle = ohlc_data[i]
        if zone_type == 'supply' and candle['h'] >= zone_price * 0.995:  # Within 0.5%
            touch_count += 1
        elif zone_type == 'demand' and candle['l'] <= zone_price * 1.005:  # Within 0.5%
            touch_count += 1

    candle_factor = min(touch_count / 20, 1)  # Normalize to 0-1

    # Breakout strength (how many times price broke through)
    breakout_count = 0
    for i in range(index + 1, len(ohlc_data)):
        if zone_type == 'supply' and ohlc_data[i]['c'] > zone_price:
            breakout_count += 1
        elif zone_type == 'demand' and ohlc_data[i]['c'] < zone_price:
            breakout_count += 1

    breakout_factor = min(breakout_count / 5, 1)  # Normalize to 0-1

    # Combined strength (0-100)
    strength = (freshness * 30 + candle_factor * 40 + breakout_factor * 30)

    return round(strength, 2)

def wow_trade_strategy(ohlc_data, zones, trend_analysis, time_frame_analysis):
    """تنفيذ استراتيجية WOW Trade"""
    if not ohlc_data or not zones or not trend_analysis or not time_frame_analysis:
        return {'signal': 'hold', 'confidence': 0, 'details': 'بيانات غير كافية'}

    current_price = ohlc_data[-1]['c'] if ohlc_data else None
    if not current_price:
        return {'signal': 'hold', 'confidence': 0, 'details': 'لا يوجد سعر حالي'}

    # WOW Trade conditions
    # 1. Uptrend in higher timeframes
    higher_tf_up = time_frame_analysis.get('monthly', {}).get('trend') == 'up' and \
                   time_frame_analysis.get('weekly', {}).get('trend') == 'up'

    # 2. Price above demand zone
    demand_zones = [z for z in zones if z['type'] == 'demand' and z['strength'] > 50]
    above_demand = any(current_price > z['price'] * 1.02 for z in demand_zones)  # 2% buffer

    # 3. Internal trend up
    internal_up = trend_analysis.get('internal_trend') == 'up'

    # 4. Curve analysis (tight range)
    curve_data = calculate_curve(ohlc_data)
    tight_curve = curve_data and curve_data.get('normalized_curve', 1) < 0.5

    if higher_tf_up and above_demand and internal_up and tight_curve:
        return {
            'signal': 'buy',
            'confidence': 85,
            'details': 'WOW Trade: اتجاه صاعد في الإطارات العليا، فوق منطقة طلب قوية، اتجاه داخلي صاعد، منحنى ضيق'
        }
    elif not higher_tf_up:
        return {
            'signal': 'hold',
            'confidence': 60,
            'details': 'WOW Trade: الاتجاه في الإطارات العليا غير صاعد'
        }
    else:
        return {
            'signal': 'hold',
            'confidence': 40,
            'details': 'WOW Trade: لم تتحقق جميع الشروط'
        }

def pcp_strategy(ohlc_data, zones, trend_analysis, time_frame_analysis):
    """تنفيذ استراتيجية PCP (Pullback Continuation Pattern)"""
    if not ohlc_data or not zones or not trend_analysis or not time_frame_analysis:
        return {'signal': 'hold', 'confidence': 0, 'details': 'بيانات غير كافية'}

    current_price = ohlc_data[-1]['c'] if ohlc_data else None
    if not current_price:
        return {'signal': 'hold', 'confidence': 0, 'details': 'لا يوجد سعر حالي'}

    # PCP conditions
    # 1. Downtrend in higher timeframes (for continuation)
    higher_tf_down = time_frame_analysis.get('monthly', {}).get('trend') == 'down' or \
                     time_frame_analysis.get('weekly', {}).get('trend') == 'down'

    # 2. Price testing supply zone
    supply_zones = [z for z in zones if z['type'] == 'supply' and z['strength'] > 60]
    testing_supply = any(abs(current_price - z['price']) / z['price'] < 0.01 for z in supply_zones)  # Within 1%

    # 3. Internal trend down or sideways
    internal_down = trend_analysis.get('internal_trend') in ['down', 'sideways']

    # 4. Recent pullback from higher levels
    recent_high = max([c['h'] for c in ohlc_data[-5:]]) if len(ohlc_data) >= 5 else current_price
    pullback = (recent_high - current_price) / recent_high > 0.02  # 2% pullback

    if higher_tf_down and testing_supply and internal_down and pullback:
        return {
            'signal': 'sell',
            'confidence': 80,
            'details': 'PCP: اتجاه هابط في الإطارات العليا، اختبار منطقة عرض قوية، اتجاه داخلي هابط، تصحيح من مستويات أعلى'
        }
    elif not higher_tf_down:
        return {
            'signal': 'hold',
            'confidence': 50,
            'details': 'PCP: الاتجاه في الإطارات العليا غير مناسب'
        }
    else:
        return {
            'signal': 'hold',
            'confidence': 30,
            'details': 'PCP: لم تتحقق جميع الشروط'
        }

def multi_time_frame_filter(ohlc_data, zones, trend_analysis, time_frame_analysis):
    """فلترة متعددة الإطارات الزمنية"""
    if not time_frame_analysis:
        return {'decision': 'neutral', 'confidence': 0, 'reason': 'لا توجد بيانات إطارات زمنية'}

    monthly_trend = time_frame_analysis.get('monthly', {}).get('trend')
    weekly_trend = time_frame_analysis.get('weekly', {}).get('trend')
    daily_trend = time_frame_analysis.get('daily', {}).get('trend')

    # Decision table logic
    if monthly_trend == 'up' and weekly_trend == 'up' and daily_trend == 'up':
        return {
            'decision': 'strong_buy',
            'confidence': 90,
            'reason': 'اتجاه صاعد في جميع الإطارات الزمنية'
        }
    elif monthly_trend == 'down' and weekly_trend == 'down' and daily_trend == 'down':
        return {
            'decision': 'strong_sell',
            'confidence': 90,
            'reason': 'اتجاه هابط في جميع الإطارات الزمنية'
        }
    elif monthly_trend == 'up' and weekly_trend == 'up' and daily_trend == 'down':
        return {
            'decision': 'weak_buy',
            'confidence': 60,
            'reason': 'اتجاه صاعد في الإطارات العليا، هابط في اليومي'
        }
    elif monthly_trend == 'down' and weekly_trend == 'down' and daily_trend == 'up':
        return {
            'decision': 'weak_sell',
            'confidence': 60,
            'reason': 'اتجاه هابط في الإطارات العليا، صاعد في اليومي'
        }
    else:
        return {
            'decision': 'neutral',
            'confidence': 40,
            'reason': 'اتجاهات متضاربة في الإطارات الزمنية'
        }

def auto_update_prices():
    """تحديث تلقائي كل 24 ساعة"""
    while True:
        print("\n🔄 جاري تحديث أسعار السوق السعودي...")
        for symbol in symbols:
            fetch_price(symbol)
        print("✅ تم التحديث الكامل. في انتظار 24 ساعة أخرى.\n")
        time.sleep(UPDATE_INTERVAL)

@app.route("/")
def home():
    """صفحة البداية - واجهة المستخدم"""
    # تحديث البيانات لجميع الأسهم
    for stock in stock_list:
        symbol = f"{stock['symbol']}.SR"
        if symbol not in cache:
            fetch_price(symbol)

    # جلب البيانات من قاعدة البيانات إذا لم تكن في الكاش
    conn = create_connection()
    if conn:
        db_stocks = get_stocks(conn)
        conn.close()
        # تحديث الكاش من قاعدة البيانات
        for db_stock in db_stocks:
            symbol = f"{db_stock['symbol']}.SR"
            if symbol not in cache:
                cache[symbol] = {
                    "data": {
                        "symbol": symbol,
                        "price": db_stock['price'],
                        "bid": db_stock['bid'],
                        "ask": db_stock['ask'],
                        "difference": db_stock['difference'],
                        "currency": db_stock['currency'],
                        "indicators": None,
                        "ohlc": []
                    },
                    "time": db_stock['last_updated']
                }

    stocks = []
    for stock in stock_list:
        symbol = f"{stock['symbol']}.SR"
        data = cache.get(symbol, {}).get("data", {})

        # Calculate zone averages and count
        avg_demand_price = 0
        avg_supply_price = 0
        zone_count = 0
        ohlc_data = data.get("ohlc", [])
        if ohlc_data:
            zones = identify_zones(ohlc_data)
            zone_count = len(zones)
            demand_prices = [z['price'] for z in zones if z['type'] == 'demand']
            supply_prices = [z['price'] for z in zones if z['type'] == 'supply']
            if demand_prices:
                avg_demand_price = sum(demand_prices) / len(demand_prices)
            if supply_prices:
                avg_supply_price = sum(supply_prices) / len(supply_prices)

        stocks.append({
            "symbol": stock['symbol'],
            "name": stock['name'],
            "region": stock.get('region', ''),
            "price": data.get("price", 0),
            "bid": data.get("bid", 0),
            "ask": data.get("ask", 0),
            "difference": data.get("difference", 0),
            "avg_demand_price": round(avg_demand_price, 2) if avg_demand_price else 0,
            "avg_supply_price": round(avg_supply_price, 2) if avg_supply_price else 0,
            "zone_count": zone_count
        })

    # فلترة حسب البحث
    search = request.args.get('search', '').strip().lower()
    if search:
        filtered_stocks = []
        for stock in stocks:
            if search in stock['symbol'].lower() or search in stock['name'].lower():
                filtered_stocks.append(stock)
        stocks = filtered_stocks

    # فلترة حسب النطاق السعري
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)
    if min_price is not None or max_price is not None:
        filtered_stocks = []
        for stock in stocks:
            price = stock['price']
            if (min_price is None or price >= min_price) and (max_price is None or price <= max_price):
                filtered_stocks.append(stock)
        stocks = filtered_stocks

    return render_template("index.html", stocks=stocks, search=search)

@app.route("/stock/<symbol>")
def stock_detail(symbol):
    """صفحة تفاصيل السهم"""
    # التحقق من وجود السهم في القائمة، وإضافته إذا لم يكن موجودًا
    stock_exists = any(s["symbol"] == symbol for s in stock_list)
    if not stock_exists:
        stock_list.append({"symbol": symbol, "name": symbol, "region": ""})

    full_symbol = f"{symbol}.SR"
    data = cache.get(full_symbol)
    if not data:
        fetch_price(full_symbol)
        data = cache.get(full_symbol)
    if data:
        stock_info = data["data"]
        stock_info["name"] = next((s["name"] for s in stock_list if s["symbol"] == symbol), "غير معروف")
        stock_info["region"] = next((s["region"] for s in stock_list if s["symbol"] == symbol), "")

        # Calculate average demand and supply prices
        ohlc_data = stock_info.get("ohlc", [])
        if ohlc_data:
            zones = identify_zones(ohlc_data)
            demand_prices = [z['price'] for z in zones if z['type'] == 'demand']
            supply_prices = [z['price'] for z in zones if z['type'] == 'supply']
            avg_demand_price = sum(demand_prices) / len(demand_prices) if demand_prices else 0
            avg_supply_price = sum(supply_prices) / len(supply_prices) if supply_prices else 0
            stock_info["avg_demand_price"] = round(avg_demand_price, 2)
            stock_info["avg_supply_price"] = round(avg_supply_price, 2)
        else:
            stock_info["avg_demand_price"] = 0
            stock_info["avg_supply_price"] = 0

        # تحضير قائمة الأسهم للعرض
        stocks = []
        for stock in stock_list:
            symbol_full = f"{stock['symbol']}.SR"
            data_stock = cache.get(symbol_full, {}).get("data", {})
            # Calculate zone averages
            avg_demand_price = 0
            avg_supply_price = 0
            ohlc_data = data_stock.get("ohlc", [])
            if ohlc_data:
                zones = identify_zones(ohlc_data)
                demand_prices = [z['price'] for z in zones if z['type'] == 'demand']
                supply_prices = [z['price'] for z in zones if z['type'] == 'supply']
                if demand_prices:
                    avg_demand_price = sum(demand_prices) / len(demand_prices)
                if supply_prices:
                    avg_supply_price = sum(supply_prices) / len(supply_prices)
            stocks.append({
                "symbol": stock['symbol'],
                "name": stock['name'],
                "region": stock.get('region', ''),
                "price": data_stock.get("price", 0),
                "bid": data_stock.get("bid", 0),
                "ask": data_stock.get("ask", 0),
                "difference": data_stock.get("difference", 0),
                "avg_demand_price": round(avg_demand_price, 2) if avg_demand_price else 0,
                "avg_supply_price": round(avg_supply_price, 2) if avg_supply_price else 0
            })

        return render_template("stock_detail.html", stock=stock_info, stocks=stocks)
    return render_template("error.html", message="فشل في جلب بيانات السهم"), 404

@app.route("/analysis/<symbol>")
def stock_analysis(symbol):
    """صفحة تحليل السهم مع الإشارات التجارية"""
    # التحقق من وجود السهم في القائمة، وإضافته إذا لم يكن موجودًا
    stock_exists = any(s["symbol"] == symbol for s in stock_list)
    if not stock_exists:
        stock_list.append({"symbol": symbol, "name": symbol, "region": ""})

    full_symbol = f"{symbol}.SR"
    data = cache.get(full_symbol)
    if not data:
        fetch_price(full_symbol)
        data = cache.get(full_symbol)
    if data:
        stock_info = data["data"]
        stock_info["name"] = next((s["name"] for s in stock_list if s["symbol"] == symbol), "غير معروف")
        stock_info["region"] = next((s["region"] for s in stock_list if s["symbol"] == symbol), "")

        # حساب الإشارات التجارية الأساسية
        signals = {}
        if stock_info.get("indicators"):
            indicators = stock_info["indicators"]

            # إشارة SMA
            if indicators.get("SMA_20") and stock_info["price"] > indicators["SMA_20"]:
                signals["SMA"] = {"signal": "شراء", "reason": "الاتجاه صاعد - السعر أعلى من المتوسط البسيط"}
            elif indicators.get("SMA_20") and stock_info["price"] < indicators["SMA_20"]:
                signals["SMA"] = {"signal": "بيع", "reason": "الاتجاه هابط - السعر أقل من المتوسط البسيط"}
            else:
                signals["SMA"] = {"signal": "محايد", "reason": "لا توجد بيانات كافية"}

            # إشارة RSI
            if indicators.get("RSI"):
                if indicators["RSI"] > 70:
                    signals["RSI"] = {"signal": "تحذير", "reason": "تشبع شراء - احتمال هبوط"}
                elif indicators["RSI"] < 30:
                    signals["RSI"] = {"signal": "فرصة", "reason": "تشبع بيع - احتمال صعود"}
                else:
                    signals["RSI"] = {"signal": "محايد", "reason": "القيمة في النطاق الطبيعي"}
            else:
                signals["RSI"] = {"signal": "محايد", "reason": "لا توجد بيانات"}

            # إشارة MACD (بسيطة بناءً على القيم الحالية)
            if indicators.get("MACD") and indicators["MACD"].get("value") and indicators["MACD"].get("signal"):
                macd_val = indicators["MACD"]["value"]
                signal_val = indicators["MACD"]["signal"]
                histogram = indicators["MACD"].get("histogram", 0)
                if macd_val > signal_val:
                    signals["MACD"] = {"signal": "شراء", "reason": f"MACD أعلى من الإشارة - اتجاه صاعد. قيمة MACD: {macd_val}, إشارة: {signal_val}, هيستوغرام: {histogram}"}
                elif macd_val < signal_val:
                    signals["MACD"] = {"signal": "بيع", "reason": f"MACD أقل من الإشارة - اتجاه هابط. قيمة MACD: {macd_val}, إشارة: {signal_val}, هيستوغرام: {histogram}"}
                else:
                    signals["MACD"] = {"signal": "محايد", "reason": f"MACD قريب من الإشارة. قيمة MACD: {macd_val}, إشارة: {signal_val}, هيستوغرام: {histogram}"}
            else:
                signals["MACD"] = {"signal": "محايد", "reason": "لا توجد بيانات"}

        stock_info["signals"] = signals

        # تحليل متقدم للعرض والطلب
        ohlc_data = stock_info.get("ohlc", [])
        if ohlc_data:
            # تحليل المناطق
            zones = identify_zones(ohlc_data)
            stock_info["zones"] = zones

            # تحليل الاتجاه
            trend_analysis = identify_trend(ohlc_data)
            stock_info["trend_analysis"] = trend_analysis

            # تحليل الإطارات الزمنية
            time_frame_analysis = analyze_time_frames(ohlc_data)
            stock_info["time_frame_analysis"] = time_frame_analysis

            # حساب المنحنى
            curve_data = calculate_curve(ohlc_data)
            stock_info["curve_analysis"] = curve_data

            # الإشارات المتقدمة
            advanced_signals = {}

            # استراتيجية WOW Trade
            wow_signal = wow_trade_strategy(ohlc_data, zones, trend_analysis, time_frame_analysis)
            advanced_signals["WOW_Trade"] = wow_signal

            # استراتيجية PCP
            pcp_signal = pcp_strategy(ohlc_data, zones, trend_analysis, time_frame_analysis)
            advanced_signals["PCP"] = pcp_signal

            # فلترة متعددة الإطارات
            mtf_filter = multi_time_frame_filter(ohlc_data, zones, trend_analysis, time_frame_analysis)
            advanced_signals["MTF_Filter"] = mtf_filter

            stock_info["advanced_signals"] = advanced_signals
        else:
            stock_info["zones"] = []
            stock_info["trend_analysis"] = None
            stock_info["time_frame_analysis"] = None
            stock_info["curve_analysis"] = None
            stock_info["advanced_signals"] = {}

        # تحضير قائمة الأسهم للعرض
        stocks = []
        for stock in stock_list:
            symbol_full = f"{stock['symbol']}.SR"
            data_stock = cache.get(symbol_full, {}).get("data", {})
            stocks.append({
                "symbol": stock['symbol'],
                "name": stock['name'],
                "region": stock.get('region', ''),
                "price": data_stock.get("price", 0),
                "bid": data_stock.get("bid", 0),
                "ask": data_stock.get("ask", 0),
                "difference": data_stock.get("difference", 0)
            })

        return render_template("stock_analysis.html", stock=stock_info, stocks=stocks)
    return render_template("error.html", message="فشل في جلب بيانات السهم"), 404

@app.route("/api/price/<symbol>")
def get_price(symbol):
    """عرض السعر الحالي مع العرض والطلب"""
    fetch_price(symbol)
    data = cache.get(symbol)
    return jsonify(data)

@app.route("/api/all")
def get_all():
    """عرض جميع الأسعار المخزّنة"""
    return jsonify(cache)

if __name__ == "__main__":
    # تشغيل التحديث التلقائي في خيط مستقل
    t = threading.Thread(target=auto_update_prices, daemon=True)
    t.start()

    app.run(debug=True)
