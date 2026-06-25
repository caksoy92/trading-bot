from flask import Flask, request, jsonify
from datetime import datetime
import requests
import os
import threading
import time

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BASLANGIC_BAKIYE = 1000.0
KALDIRAC = 5
KAR_HEDEF = 0.015
TRAILING_BASLA = 0.02    # %2 karda trailing aktifleşir
TRAILING_MESAFE = 0.01   # tavandan %1 geri çekilince kapat
ORTALAMA_ESIK = 0.02
STOP_ESIK = 0.02

bakiye = BASLANGIC_BAKIYE
pozisyonlar = {}
islem_gecmisi = []

def telegram_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj})

COINGECKO_MAP = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "RENDERUSDT": "render-token",
    "BNBUSDT": "binancecoin",
    "SUIUSDT": "sui"
}

def fiyat_al(symbol):
    try:
        temiz_symbol = symbol.replace(".P", "")
        coin_id = COINGECKO_MAP.get(temiz_symbol)
        if not coin_id:
            return None
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        r = requests.get(url, timeout=5)
        return float(r.json()[coin_id]["usd"])
    except:
        return None
TREND_ESIK = 0.70

def trend_guclu_mu(symbol):
    try:
        temiz_symbol = symbol.replace(".P", "")
        coin_id = COINGECKO_MAP.get(temiz_symbol)
        if not coin_id:
            return False
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=1"
        r = requests.get(url, timeout=10)
        fiyatlar = [p[1] for p in r.json()["prices"][-48:]]
        if len(fiyatlar) < 2:
            return False
        net_hareket = abs(fiyatlar[-1] - fiyatlar[0])
        toplam_aralik = max(fiyatlar) - min(fiyatlar)
        if toplam_aralik == 0:
            return False
        oran = net_hareket / toplam_aralik
        return oran >= TREND_ESIK
    except Exception as e:
        print(f"Trend kontrol hatası: {e}")
        return False
def pozisyon_ac(symbol, fiyat, yon, kac_alim):
    global bakiye
    islem_buyuklugu = 75.0
    if bakiye < islem_buyuklugu:
        telegram_gonder(f"⚠️ {symbol} için yeterli bakiye yok!")
        return

    adet = (islem_buyuklugu * KALDIRAC) / fiyat
    bakiye -= islem_buyuklugu

    if symbol not in pozisyonlar:
        pozisyonlar[symbol] = {
            "yon": yon,
            "alimlar": [],
            "toplam_adet": 0,
            "ortalama_fiyat": 0
        }

    poz = pozisyonlar[symbol]
    poz["alimlar"].append({"fiyat": fiyat, "adet": adet})
    poz["toplam_adet"] += adet
    toplam_maliyet = sum(a["fiyat"] * a["adet"] for a in poz["alimlar"])
    poz["ortalama_fiyat"] = toplam_maliyet / poz["toplam_adet"]

    ikon = "📈" if yon == "long" else "📉"
    mesaj = (f"{ikon} {yon.upper()} Açıldı ({kac_alim}. alım)\n"
             f"Sembol: {symbol}\n"
             f"Fiyat: {fiyat}\n"
             f"Ortalama: {poz['ortalama_fiyat']:.4f}\n"
             f"Bakiye: {bakiye:.2f} USDT")
    telegram_gonder(mesaj)

def pozisyon_kapat(symbol, fiyat, sebep):
    global bakiye
    if symbol not in pozisyonlar:
        return

    poz = pozisyonlar[symbol]
    ort_fiyat = poz["ortalama_fiyat"]
    toplam_adet = poz["toplam_adet"]
    yon = poz["yon"]

    if yon == "short":
        kar_yuzde = (ort_fiyat - fiyat) / ort_fiyat
    else:
        kar_yuzde = (fiyat - ort_fiyat) / ort_fiyat

    kar_usdt = kar_yuzde * toplam_adet * fiyat
    toplam_harcanan = sum(a["fiyat"] * a["adet"] / KALDIRAC for a in poz["alimlar"])
    bakiye += toplam_harcanan + kar_usdt

    islem_gecmisi.append({
        "symbol": symbol,
        "yon": yon,
        "giris": ort_fiyat,
        "cikis": fiyat,
        "kar_usdt": round(kar_usdt, 4),
        "sebep": sebep,
        "zaman": datetime.now().strftime("%H:%M:%S")
    })

    mesaj = (f"{'✅' if kar_usdt > 0 else '❌'} {yon.upper()} Kapandı ({sebep})\n"
             f"Sembol: {symbol}\n"
             f"Giriş: {ort_fiyat:.4f}\n"
             f"Çıkış: {fiyat:.4f}\n"
             f"Kar/Zarar: {kar_usdt:.4f} USDT\n"
             f"Bakiye: {bakiye:.2f} USDT")
    telegram_gonder(mesaj)
    del pozisyonlar[symbol]

def pozisyon_kontrol(symbol, fiyat):
    if symbol not in pozisyonlar:
        return
    poz = pozisyonlar[symbol]
    ort = poz["ortalama_fiyat"]
    yon = poz["yon"]
    alim_sayisi = len(poz["alimlar"])

    if yon == "short":
        dusus = (fiyat - ort) / ort
        kar = (ort - fiyat) / ort
    else:
        dusus = (ort - fiyat) / ort
        kar = (fiyat - ort) / ort

    # Trailing aktif mi? (kar bir kez %2'yi geçtiyse)
    if poz.get("trailing_aktif"):
        # En yüksek karı güncelle
        if kar > poz["en_yuksek_kar"]:
            poz["en_yuksek_kar"] = kar
        # Tavandan TRAILING_MESAFE kadar geri çekildiyse kapat
        if kar <= poz["en_yuksek_kar"] - TRAILING_MESAFE:
            pozisyon_kapat(symbol, fiyat, "TRAILING TP")
            return

    # Kar TRAILING_BASLA'yı geçtiyse trailing'i başlat
    if kar >= TRAILING_BASLA and not poz.get("trailing_aktif"):
        poz["trailing_aktif"] = True
        poz["en_yuksek_kar"] = kar
        telegram_gonder(f"🚀 {symbol} trailing aktif! Kar: %{kar*100:.2f} (pump takibi)")
        return

    # Normal %1.5 kar hedefi (trailing devreye girmediyse)
    if not poz.get("trailing_aktif") and kar >= KAR_HEDEF:
        pozisyon_kapat(symbol, fiyat, "KAR HEDEFİ")
    elif alim_sayisi < 3 and dusus >= ORTALAMA_ESIK:
        pozisyon_ac(symbol, fiyat, yon, alim_sayisi + 1)
    elif alim_sayisi >= 3 and dusus >= STOP_ESIK:
        pozisyon_kapat(symbol, fiyat, "STOP")

def fiyat_takip():
    while True:
        semboller = list(pozisyonlar.keys())
        for symbol in semboller:
            fiyat = fiyat_al(symbol)
            if fiyat and symbol in pozisyonlar:
                pozisyon_kontrol(symbol, fiyat)
        time.sleep(35)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    symbol = data.get("symbol")
    fiyat = float(data.get("price", 0))
    action = data.get("action", "").lower()

    if not symbol or not fiyat:
        return jsonify({"status": "hata"})

    if action == "sell":
        if symbol not in pozisyonlar:
            if trend_guclu_mu(symbol):
                telegram_gonder(f"⏭️ {symbol} SHORT atlandı (güçlü trend)")
            else:
                pozisyon_ac(symbol, fiyat, "short", 1)

    elif action == "buy":
        if symbol not in pozisyonlar:
            if trend_guclu_mu(symbol):
                telegram_gonder(f"⏭️ {symbol} LONG atlandı (güçlü trend)")
            else:
                pozisyon_ac(symbol, fiyat, "long", 1)

    return jsonify({"status": "ok"})
@app.route('/durum', methods=['GET'])
def durum():
    return jsonify({
        "bakiye": round(bakiye, 2),
        "acik_pozisyonlar": pozisyonlar,
        "islem_gecmisi": islem_gecmisi
    })

if __name__ == '__main__':
    t = threading.Thread(target=fiyat_takip, daemon=True)
    t.start()
    print("Bot başladı!")
    app.run(host='0.0.0.0', port=8080)
