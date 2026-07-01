from flask import Flask, request, jsonify
from datetime import datetime, timedelta, timezone

TR_SAAT = timezone(timedelta(hours=3))

def tr_simdi():
    return datetime.now(TR_SAAT)
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
ORTALAMA_ESIK = 0.03
STOP_ESIK = 0.03
TRAILING_BASLA = 0.02
TRAILING_MESAFE = 0.01
TREND_ESIK = 0.85

def okx_symbol(symbol):
    temiz = symbol.replace(".P", "").replace("USDT", "")
    return f"{temiz}-USDT-SWAP"

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
        inst = okx_symbol(symbol)
        url = f"https://www.okx.com/api/v5/market/ticker?instId={inst}"
        r = requests.get(url, timeout=5)
        return float(r.json()["data"][0]["last"])
    except:
        return None

def trend_guclu_mu(symbol):
    try:
        inst = okx_symbol(symbol)
        url = f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar=5m&limit=48"
        r = requests.get(url, timeout=10)
        mumlar = r.json()["data"]
        if len(mumlar) < 2:
            return False
        kapanislar = [float(m[4]) for m in mumlar]
        net_hareket = abs(kapanislar[0] - kapanislar[-1])
        toplam_aralik = max(kapanislar) - min(kapanislar)
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
        poz = {"yon": yon, "alimlar": [], "toplam_adet": 0, "ortalama_fiyat": 0,
               "acilis_zamani": tr_simdi().strftime("%Y-%m-%d %H:%M:%S")}
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
    acilis = poz.get("acilis_zamani")
    kapanis = tr_simdi().strftime("%Y-%m-%d %H:%M:%S")
    sure_dk = None
    if acilis:
        try:
            t1 = datetime.strptime(acilis, "%Y-%m-%d %H:%M:%S")
            t2 = datetime.strptime(kapanis, "%Y-%m-%d %H:%M:%S")
            sure_dk = round((t2 - t1).total_seconds() / 60, 1)
        except:
            sure_dk = None
    gecmise_ekle({
        "symbol": symbol, "yon": yon, "giris": ort_fiyat, "cikis": fiyat,
        "kar_usdt": round(kar_usdt, 4), "sebep": sebep,
        "acilis": acilis, "zaman": kapanis, "sure_dk": sure_dk,
        "kademe": len(poz["alimlar"])
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
    elif alim_sayisi < 2 and dusus >= ORTALAMA_ESIK:
        pozisyon_ac(symbol, fiyat, yon, alim_sayisi + 1)
    elif alim_sayisi >= 2 and dusus >= STOP_ESIK:
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
        "zaman": tr_simdi().strftime("%Y-%m-%d %H:%M:%S")
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
@app.route('/panel', methods=['GET'])
def panel():
    return """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Bot Panel</title>
<style>
body{font-family:-apple-system,system-ui,sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:16px}
h1{font-size:18px;font-weight:600;margin:0 0 16px}
.ozet{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
.kutu{background:#1a1d24;border-radius:10px;padding:14px 18px;flex:1;min-width:130px}
.kutu .etiket{font-size:12px;color:#8a8f99;margin-bottom:6px}
.kutu .deger{font-size:22px;font-weight:600}
.poz{background:#1a1d24;border-radius:10px;padding:14px;margin-bottom:10px;border-left:3px solid #444}
.poz.long{border-left-color:#2ecc71}
.poz.short{border-left-color:#e74c3c}
.poz-ust{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.sym{font-size:16px;font-weight:600}
.yon{font-size:11px;padding:2px 8px;border-radius:6px;text-transform:uppercase}
.yon.long{background:#1a3d2a;color:#2ecc71}
.yon.short{background:#3d1a1a;color:#e74c3c}
.satir{display:flex;justify-content:space-between;font-size:13px;color:#a8adb5;margin:3px 0}
.kz{font-size:18px;font-weight:600}
.kz.art{color:#2ecc71}
.kz.eks{color:#e74c3c}
.trail{font-size:11px;color:#f39c12;margin-top:4px}
.bos{text-align:center;color:#8a8f99;padding:30px}
.gecmis{margin-top:24px}
.gecmis h2{font-size:15px;color:#a8adb5;margin-bottom:10px}
.g-satir{display:flex;justify-content:space-between;font-size:13px;padding:8px;background:#1a1d24;border-radius:6px;margin-bottom:5px}
.kucuk{font-size:11px;color:#8a8f99;text-align:center;margin-top:16px}
</style>
</head>
<body>
<h1>📊 Trading Bot Panel</h1>
<div class="ozet">
<div class="kutu"><div class="etiket">Toplam Değer</div><div class="deger" id="toplamDeger">-</div></div>
<div class="kutu"><div class="etiket">Realize K/Z</div><div class="deger" id="realizeKz">-</div></div>
<div class="kutu"><div class="etiket">Anlık K/Z</div><div class="deger" id="anlikKz">-</div></div>
<div class="kutu"><div class="etiket">Bakiye</div><div class="deger" id="bakiye">-</div></div>
<div class="kutu"><div class="etiket">Açık Pozisyon</div><div class="deger" id="pozSayi">-</div></div>
</div>
<div style="background:#1a1d24;border-radius:10px;padding:14px;margin-bottom:20px">
<div style="font-size:14px;color:#a8adb5;margin-bottom:12px;font-weight:600">📈 İstatistikler</div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
<div><div style="font-size:11px;color:#8a8f99">Toplam İşlem</div><div style="font-size:17px;font-weight:600" id="stToplam">-</div></div>
<div><div style="font-size:11px;color:#8a8f99">Kazanma Oranı</div><div style="font-size:17px;font-weight:600" id="stOran">-</div></div>
<div><div style="font-size:11px;color:#8a8f99">Kar Faktörü</div><div style="font-size:17px;font-weight:600" id="stFaktor">-</div></div>
<div><div style="font-size:11px;color:#8a8f99">Ort. Kazanç</div><div style="font-size:17px;font-weight:600;color:#2ecc71" id="stOrtK">-</div></div>
<div><div style="font-size:11px;color:#8a8f99">Ort. Kayıp</div><div style="font-size:17px;font-weight:600;color:#e74c3c" id="stOrtZ">-</div></div>
<div><div style="font-size:11px;color:#8a8f99">Ort. Süre</div><div style="font-size:17px;font-weight:600" id="stSure">-</div></div>
<div style="grid-column:1/-1;margin-top:12px;border-top:1px solid #2a2d34;padding-top:12px">
<div style="font-size:12px;color:#a8adb5;margin-bottom:8px">Ortalama Düşürme Analizi</div>
<div id="kademeDagilim" style="font-size:13px"></div>
</div>
</div>
</div>
<div id="pozisyonlar"></div>
<div class="gecmis"><h2>Son İşlemler</h2><div id="gecmis"></div></div>
<div class="kucuk">Her 30 saniyede otomatik güncellenir</div>
<script>
async function yukle(){
  try{
    const r = await fetch('/panel-veri');
    const d = await r.json();
    document.getElementById('bakiye').textContent = d.bakiye + ' $';
    const kz = document.getElementById('anlikKz');
    kz.textContent = (d.toplam_anlik_kz>=0?'+':'') + d.toplam_anlik_kz + ' $';
    kz.style.color = d.toplam_anlik_kz>=0 ? '#2ecc71' : '#e74c3c';
    document.getElementById('pozSayi').textContent = d.pozisyonlar.length;
    const st = d.istatistik;
    document.getElementById('stToplam').textContent = st.toplam_islem;
    document.getElementById('stOran').textContent = st.kazanma_orani + '%';
    document.getElementById('stFaktor').textContent = st.kar_faktoru!=null ? st.kar_faktoru : '-';
    document.getElementById('stOrtK').textContent = '+' + st.ort_kazanc + ' $';
    document.getElementById('stOrtZ').textContent = st.ort_kayip + ' $';
    document.getElementById('stSure').textContent = st.ort_sure!=null ? (st.ort_sure>=60 ? (st.ort_sure/60).toFixed(1)+' sa' : st.ort_sure+' dk') : '-';
    const kd = st.kademe_dagilim;
    const etiketler = {'1':'Sadece 1. alım','2':'2. alıma girdi','3':'3. alıma girdi (max)'};
    document.getElementById('kademeDagilim').innerHTML = ['1','2','3'].map(k=>{
      const d = kd[k];
      if(d.toplam===0) return `<div style="display:flex;justify-content:space-between;padding:4px 0;color:#8a8f99"><span>${etiketler[k]}</span><span>-</span></div>`;
      const oran = Math.round(d.kazanan/d.toplam*100);
      return `<div style="display:flex;justify-content:space-between;padding:4px 0">
        <span>${etiketler[k]}</span>
        <span>${d.toplam} işlem · ${d.kazanan}K/${d.kaybeden}Z · %${oran} · <span style="color:${d.net>=0?'#2ecc71':'#e74c3c'}">${d.net>=0?'+':''}${d.net}$</span></span>
      </div>`;
    }).join('');
    const rkz = document.getElementById('realizeKz');
    rkz.textContent = (d.realize_kz>=0?'+':'') + d.realize_kz + ' $';
    rkz.style.color = d.realize_kz>=0 ? '#2ecc71' : '#e74c3c';
    const td = document.getElementById('toplamDeger');
    td.textContent = d.toplam_deger + ' $';
    td.style.color = d.toplam_deger>=d.baslangic ? '#2ecc71' : '#e74c3c';
    const pc = document.getElementById('pozisyonlar');
    if(d.pozisyonlar.length===0){
      pc.innerHTML = '<div class="bos">Açık pozisyon yok</div>';
    } else {
      pc.innerHTML = d.pozisyonlar.map(p=>`
        <div class="poz ${p.yon}">
          <div class="poz-ust">
            <span class="sym">${p.symbol}</span>
            <span class="yon ${p.yon}">${p.yon}</span>
          </div>
          <div class="satir"><span>Ortalama</span><span>${p.ortalama}</span></div>
          <div class="satir"><span>Anlık fiyat</span><span>${p.anlik}</span></div>
          <div class="satir"><span>Alım sayısı</span><span>${p.alim_sayisi}/3</span></div>
          <div class="satir" style="margin-top:8px">
            <span class="kz ${p.kar_usdt>=0?'art':'eks'}">${p.kar_usdt>=0?'+':''}${p.kar_usdt} $</span>
            <span class="kz ${p.kar_usdt>=0?'art':'eks'}">${p.kar_yuzde>=0?'+':''}${p.kar_yuzde}%</span>
          </div>
          ${p.trailing?'<div class="trail">🚀 Trailing aktif</div>':''}
        </div>`).join('');
    }

    const gc = document.getElementById('gecmis');
    const son = d.gecmis.slice(-10).reverse();
    if(son.length===0){
      gc.innerHTML = '<div class="bos">Henüz işlem yok</div>';
    } else {
      gc.innerHTML = son.map(g=>{
        let sure = g.sure_dk!=null ? (g.sure_dk>=60 ? (g.sure_dk/60).toFixed(1)+' sa' : g.sure_dk+' dk') : '-';
        let zaman = g.zaman ? g.zaman.substring(5,16) : '';
        return `
        <div class="g-satir" style="flex-direction:column;align-items:stretch;gap:4px">
          <div style="display:flex;justify-content:space-between">
            <span>${g.symbol} ${g.yon}</span>
            <span style="color:${g.kar_usdt>=0?'#2ecc71':'#e74c3c'}">${g.kar_usdt>=0?'+':''}${g.kar_usdt} $ · ${g.sebep}</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:11px;color:#8a8f99">
            <span>${zaman}</span>
            <span>⏱ ${sure}</span>
          </div>
        </div>`;
      }).join('');
    }
  }catch(e){ console.error(e); }
}
yukle();
setInterval(yukle, 30000);
</script>
</body>
</html>"""
@app.route('/panel-veri', methods=['GET'])
def panel_veri():
    pozisyonlar = pozisyonlari_al()
    sonuc = []
    toplam_anlik_kz = 0
    for symbol, poz in pozisyonlar.items():
        anlik = fiyat_al(symbol)
        ort = poz["ortalama_fiyat"]
        yon = poz["yon"]
        adet = poz["toplam_adet"]
        if anlik:
            if yon == "short":
                kar_yuzde = (ort - anlik) / ort
            else:
                kar_yuzde = (anlik - ort) / ort
            kar_usdt = kar_yuzde * adet * anlik
        else:
            kar_yuzde = 0
            kar_usdt = 0
            anlik = ort
        toplam_anlik_kz += kar_usdt
        sonuc.append({
            "symbol": symbol,
            "yon": yon,
            "ortalama": round(ort, 4),
            "anlik": round(anlik, 4),
            "alim_sayisi": len(poz["alimlar"]),
            "kar_yuzde": round(kar_yuzde * 100, 2),
            "kar_usdt": round(kar_usdt, 2),
            "trailing": poz.get("trailing_aktif", False)
        })
    gecmis = gecmisi_al()
    realize_kz = sum(g.get("kar_usdt", 0) for g in gecmis)
    kazananlar = [g["kar_usdt"] for g in gecmis if g.get("kar_usdt", 0) > 0]
    kaybedenler = [g["kar_usdt"] for g in gecmis if g.get("kar_usdt", 0) < 0]
    toplam_islem = len(gecmis)
    kazanma_orani = round(len(kazananlar) / toplam_islem * 100, 1) if toplam_islem else 0
    toplam_kazanc = sum(kazananlar)
    toplam_kayip = abs(sum(kaybedenler))
    kar_faktoru = round(toplam_kazanc / toplam_kayip, 2) if toplam_kayip > 0 else None
    ort_kazanc = round(toplam_kazanc / len(kazananlar), 2) if kazananlar else 0
    ort_kayip = round(sum(kaybedenler) / len(kaybedenler), 2) if kaybedenler else 0
    sureler = [g["sure_dk"] for g in gecmis if g.get("sure_dk") is not None]
    ort_sure = round(sum(sureler) / len(sureler), 1) if sureler else None
    kademe_dagilim = {}
    for k in [1, 2, 3]:
        islemler = [g for g in gecmis if g.get("kademe") == k]
        if islemler:
            kazanan = len([g for g in islemler if g.get("kar_usdt", 0) > 0])
            kademe_dagilim[str(k)] = {
                "toplam": len(islemler),
                "kazanan": kazanan,
                "kaybeden": len(islemler) - kazanan,
                "net": round(sum(g.get("kar_usdt", 0) for g in islemler), 2)
            }
        else:
            kademe_dagilim[str(k)] = {"toplam": 0, "kazanan": 0, "kaybeden": 0, "net": 0}
    istatistik = {
        "toplam_islem": toplam_islem,
        "kazanma_orani": kazanma_orani,
        "kar_faktoru": kar_faktoru,
        "ort_kazanc": ort_kazanc,
        "ort_kayip": ort_kayip,
        "ort_sure": ort_sure,
        "kademe_dagilim": kademe_dagilim
    }
    kilitli = sum(a["fiyat"] * a["adet"] / KALDIRAC for poz in pozisyonlari_al().values() for a in poz["alimlar"])
    bakiye = bakiye_al()
    toplam_deger = bakiye + kilitli + toplam_anlik_kz
    return jsonify({
        "bakiye": round(bakiye, 2),
        "toplam_anlik_kz": round(toplam_anlik_kz, 2),
        "realize_kz": round(realize_kz, 2),
        "toplam_deger": round(toplam_deger, 2),
        "baslangic": BASLANGIC_BAKIYE,
        "istatistik": istatistik,
        "pozisyonlar": sonuc,
        "gecmis": gecmis
    })    
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
