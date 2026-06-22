from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

islemler = []

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
    print(f"Kaydedildi: {islem}")
    
    return jsonify({"status": "ok"})

@app.route('/islemler', methods=['GET'])
def goster():
    return jsonify(islemler)

if __name__ == '__main__':
    print("Bot başladı, sinyal bekleniyor...")
    app.run(host='0.0.0.0', port=8080)
