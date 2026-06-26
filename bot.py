from flask import Flask, request, jsonify
from datetime import datetime
import requests
import os
import threading
import time
import json
import psycopg2

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DATABASE_URL = os.environ.get("DATABASE_URL")
HEDEF_URLLER = os.environ.get("HEDEF_URLLER", "")

BASLANGIC_BAKIYE = 1000.0
KALDIRAC = 5
KAR_HEDEF = 0.015
ORTALAMA_ESIK = 0.02
STOP_ESIK = 0.02
TRAILING_BASLA = 0.02
TRAILING_MESAFE = 0.01
TREND_ESIK = 0.70

COINGECKO_MAP = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "RENDERUSDT": "render-token",
    "BNBUSDT": "binancecoin",
    "SUIUSDT": "sui"
}

def db_baglan():
    return psycopg2.connect(DATABASE_URL)

def db_kur():
    conn = db_baglan()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS ayarlar (anahtar TEXT PRIMARY KEY, deger TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS pozisyonlar (symbol TEXT PRIMARY KEY, veri TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS islem_gecmisi (id SERIAL PRIMARY KEY, veri TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS sinyaller (id SERIAL PRIMARY KEY, veri TEXT)")
    c.execute("SELECT deger FROM ayarlar WHERE anahtar='bakiye'")
    if not c.fetchone():
        c.execute("INSERT INTO ayarlar (anahtar, deger) VALUES ('bakiye', %s)", (str(BASLANGIC_BAKIYE),))
    conn.commit()
    conn.close()

def bakiye_al():
    conn = db_baglan()
    c = conn.cursor()
    c.execute("SELECT deger FROM ayarlar WHERE anahtar='bakiye'")
    r = c.fetchone()
    conn.close()
    return float(r[0]) if r else BASLANGIC_BAKIYE

def bakiye_yaz(deger):
    conn = db_baglan()
    c = conn.cursor()
    c.execute("UPDATE ayarlar SET deger=%s WHERE anahtar='bakiye'", (str(deger),))
    conn.commit()
    conn.close()

def pozisyonlari_al():
    conn = db_baglan()
    c = conn.cursor()
    c.execute("SELECT symbol, veri FROM pozisyonlar")
    sonuc = {row[0]: json.loads(row[1]) for row in c.fetchall()}
    conn.close()
    return sonuc

def pozisyon_yaz(symbol, veri):
    conn = db_baglan()
    c = conn.cursor()
    c.execute("INSERT INTO pozisyonlar (symbol, veri) VALUES (%s, %s) ON CONFLICT (symbol) DO UPDATE SET veri=%s", (symbol, json.dumps(veri), json.dumps(veri)))
    conn.commit()
    conn.close()

def pozisyon_sil(symbol):
    conn = db_baglan()
    c = conn.cursor()
    c.execute("DELETE FROM pozisyonlar WHERE symbol=%s", (symbol,))
    conn.commit()
    conn.close()

def gecmise_ekle(veri):
    conn = db_baglan()
    c = conn.cursor()
    c.execute("INSERT INTO islem_gecmisi (veri) VALUES (%s)", (json.dumps(veri),))
    conn.commit()
    conn.close()

def gecmisi_al():
    conn = db_baglan()
    c = conn.cursor()
    c.execute("SELECT veri FROM islem_gecmisi ORDER BY id")
    sonuc = [json.loads(row[0]) for row in c.fetchall()]
    conn.close()
    return sonuc

def sinyal_kaydet(veri):
    conn = db_baglan()
    c = conn.cursor()
    c.execute("INSERT INTO sinyaller (veri) VALUES (%s)", (json.dumps(veri),))
    conn.commit()
    conn.close()

def sinyalleri_al():
    conn = db_baglan()
    c = conn.cursor()
    c.execute("SELECT veri FROM sinyaller ORDER BY id DESC LIMIT 20")
    sonuc = [json.loads(row[0]) for row in c.fetchall()]
    conn.close()
    return sonuc

def telegram_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj})

def sinyali_ilet(data):
    if not HEDEF_URLLER.strip():
        return
    for url in HEDEF_URLLER.split(","):
        url = url.strip()
        if not url:
            continue
        try:
            requests.post(url, json=data, timeout=5)
        except Exception as e:
            print(f"İletim hatası ({url}): {e}")

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
        return (net_hareket / toplam_aralik) >= TREND_ESIK
    except Exception as e:
        print(f"Trend kontrol hatası: {e}")
        return False

def pozisyon_ac(symbol, fiyat, yon, kac_alim):
    bakiye = bakiye_al()
    islem_buyuklugu = 75.0
    if bakiye < islem_buyuklugu:
        telegram_gonder(f"⚠️ {symbol} için yeterli bakiye yok!")
        return
    adet = (islem_buyuklugu * KALDIRAC) / fiyat
    bakiye -= islem_buyuklugu
    bakiye_yaz(bakiye)
    pozisyonlar = pozisyonlari_al()
    if symbol not in pozisyonlar:
        poz = {"yon": yon, "alimlar": [], "toplam_adet": 0, "ortalama_fiyat": 0}
    else:
        poz = pozisyonlar[symbol]
    poz["alimlar"].append({"fiyat": fiyat, "adet": adet})
    poz["toplam_adet"] += adet
    toplam_maliyet = sum(a["fiyat"] * a["adet"] for a in poz["alimlar"])
    poz["ortalama_fiyat"] = toplam_maliyet / poz["toplam_adet"]
    pozisyon_yaz(symbol, poz)
    ikon = "📈" if yon == "long" else "📉"
    mesaj = (f"{ikon} {yon.upper()} Açıldı ({kac_alim}. alım)\n"
             f"Sembol: {symbol}\n"
             f"Fiyat: {fiyat}\n"
             f"Ortalama: {poz['ortalama_fiyat']:.4f}\n"
             f"Bakiye: {bakiye:.2f} USDT")
    telegram_gonder(mesaj)

def pozisyon_kapat(symbol, fiyat, sebep):
    pozisyonlar = pozisyonlari_al()
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
    bakiye = bakiye_al()
    bakiye += toplam_harcanan + kar_usdt
    bakiye_yaz(bakiye)
    gecmise_ekle({
        "symbol": symbol, "yon": yon, "giris": ort_fiyat, "cikis": fiyat,
        "kar_usdt": round(kar_usdt, 4), "sebep": sebep,
        "zaman": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    pozisyon_sil(symbol)
    mesaj = (f"{'✅' if kar_usdt > 0 else '❌'} {yon.upper()} Kapandı ({sebep})\n"
             f"Sembol: {symbol}\n"
             f"Giriş: {ort_fiyat:.4f}\n"
             f"Çıkış: {fiyat:.4f}\n"
             f"Kar/Zarar: {kar_usdt:.4f} USDT\n"
             f"Bakiye: {bakiye:.2f} USDT")
    telegram_gonder(mesaj)

def pozisyon_kontrol(symbol, fiyat):
    pozisyonlar = pozisyonlari_al()
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
    if poz.get("trailing_aktif"):
        if kar > poz["en_yuksek_kar"]:
            poz["en_yuksek_kar"] = kar
            pozisyon_yaz(symbol, poz)
        if kar <= poz["en_yuksek_kar"] - TRAILING_MESAFE:
            pozisyon_kapat(symbol, fiyat, "TRAILING TP")
            return
    if kar >= TRAILING_BASLA and not poz.get("trailing_aktif"):
        poz["trailing_aktif"] = True
        poz["en_yuksek_kar"] = kar
        pozisyon_yaz(symbol, poz)
        telegram_gonder(f"🚀 {symbol} trailing aktif! Kar: %{kar*100:.2f} (pump takibi)")
        return
    if not poz.get("trailing_aktif") and kar >= KAR_HEDEF:
        pozisyon_kapat(symbol, fiyat, "KAR HEDEFİ")
    elif alim_sayisi < 3 and dusus >= ORTALAMA_ESIK:
        pozisyon_ac(symbol, fiyat, yon, alim_sayisi + 1)
    elif alim_sayisi >= 3 and dusus >= STOP_ESIK:
        pozisyon_kapat(symbol, fiyat, "STOP")

def fiyat_takip():
    while True:
        try:
            pozisyonlar = pozisyonlari_al()
            for symbol in list(pozisyonlar.keys()):
                fiyat = fiyat_al(symbol)
                if fiyat:
                    pozisyon_kontrol(symbol, fiyat)
        except Exception as e:
            print(f"Fiyat takip hatası: {e}")
        time.sleep(30)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    symbol = data.get("symbol")
    fiyat = float(data.get("price", 0))
    action = data.get("action", "").lower()
    if not symbol or not fiyat:
        return jsonify({"status": "hata"})
    sinyal_kaydet({
        "symbol": symbol, "action": action, "price": fiyat,
        "zaman": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    sinyali_ilet(data)
    pozisyonlar = pozisyonlari_al()
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
        "bakiye": round(bakiye_al(), 2),
        "acik_pozisyonlar": pozisyonlari_al(),
        "islem_gecmisi": gecmisi_al(),
        "son_sinyaller": sinyalleri_al()
    })

if __name__ == '__main__':
    db_kur()
    t = threading.Thread(target=fiyat_takip, daemon=True)
    t.start()
    print("Bot başladı (veritabanı aktif)!")
    app.run(host='0.0.0.0', port=8080)
