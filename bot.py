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
