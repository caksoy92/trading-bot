from flask import Flask, request, jsonify
from datetime import datetime
import requests
import os

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BASLANGIC_BAKIYE = 10.0
KALDIRAC = 5
KAR_HEDEF = 0.015
ORTALAMA_ESIK = 0.02
STOP_ESIK = 0.02

bakiye = BASLANGIC_BAKIYE
pozisyonlar = {}
islem_gecmisi = []

def telegram_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj})

def pozisyon_ac(symbol, fiyat, yon, kac_alim):
    global bakiye
    islem_buyuklugu = BASLANGIC_BAKIYE / 3
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

    if kar >= KAR_HEDEF:
        pozisyon_kapat(symbol, fiyat, "KAR HEDEFİ")
    elif alim_sayisi < 3 and dusus >= ORTALAMA_ESIK:
        pozisyon_ac(symbol, fiyat, yon, alim_sayisi + 1)
    elif alim_sayisi >= 3 and dusus >= STOP_ESIK:
        pozisyon_kapat(symbol, fiyat, "STOP")

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
            pozisyon_ac(symbol, fiyat, "short", 1)
        else:
            pozisyon_kontrol(symbol, fiyat)

    elif action == "buy":
        if symbol not in pozisyonlar:
            pozisyon_ac(symbol, fiyat, "long", 1)
        else:
            pozisyon_kontrol(symbol, fiyat)

    return jsonify({"status": "ok"})

@app.route('/durum', methods=['GET'])
def durum():
    return jsonify({
        "bakiye": round(bakiye, 2),
        "acik_pozisyonlar": pozisyonlar,
        "islem_gecmisi": islem_gecmisi
    })

if __name__ == '__main__':
    print("Bot başladı!")
    app.run(host='0.0.0.0', port=8080)
