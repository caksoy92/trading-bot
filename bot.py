from flask import Flask, request, jsonify
from datetime import datetime
import requests

app = Flask(__name__)

islemler = []

TELEGRAM_TOKEN = "8232322100:AAFvT1ajUGzKO95AkUbvaRZAJ0quynQYNZM"
TELEGRAM_CHAT_ID = "1719868928"

def telegram_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj})

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print(f"Sinyal geldi: {data}")

    islem = {
        "zaman": datetime.now().strftime("%H:%M:%S"),
        "action": data.get("action"),
        "symbol": data.get("symbol"),
        "price":  data.get("price")
    }
    islemler.append(islem)

    mesaj = f"🔔 Sinyal!\nİşlem: {islem['action']}\nSembol: {islem['symbol']}\nFiyat: {islem['price']}\nZaman: {islem['zaman']}"
    telegram_gonder(mesaj)

    return jsonify({"status": "ok"})

@app.route('/islemler', methods=['GET'])
def goster():
    return jsonify(islemler)

if __name__ == '__main__':
    print("Bot başladı, sinyal bekleniyor...")
    app.run(host='0.0.0.0', port=8080)
