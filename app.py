import os
import time
import threading
import sqlite3
import re
import serial
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, Response, request
import urllib.request
import urllib.parse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

SERIAL_PORT = os.environ.get("SERIAL_PORT", "COM13")
BAUD_RATE = int(os.environ.get("BAUD_RATE", 115200))

DB_FILE = "sensor_data.db"

latest = {
    "temperature": None,
    "humidity": None,
    "connected": False,
    "last_updated": None,
    "message": "Waiting for ESP32 readings...",

    "readings_count": 0,
    "start_time": time.time()
}
lock = threading.Lock()

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                temperature REAL,
                humidity REAL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON readings(timestamp)')
        
        # Initialize readings_count
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM readings")
        count = cur.fetchone()[0]
        latest["readings_count"] = count

init_db()

def save_reading(temp, hum):
    with sqlite3.connect(DB_FILE) as conn:
        ts = datetime.now().isoformat()
        conn.execute('INSERT INTO readings (timestamp, temperature, humidity) VALUES (?, ?, ?)', (ts, temp, hum))
        with lock:
            latest["readings_count"] += 1

def serial_reader():
    while True:
        try:
            with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2) as ser:
                with lock:
                    latest["message"] = f"Port {SERIAL_PORT} open, waiting for data..."
                time.sleep(2)
                temp_buffer = None
                hum_buffer = None
                timeout_count = 0
                while True:
                    raw = ser.readline().decode("utf-8", errors="ignore").strip()
                    if not raw:
                        timeout_count += 1
                        if timeout_count > 3:
                            with lock:
                                latest["connected"] = False
                                latest["message"] = "No data received (timeout)"
                        continue
                        
                    timeout_count = 0
                    print(f"[Serial Rx] {raw}", flush=True)
                        
                    temp_match = re.search(r"Temperature:\s*(-?\d+(?:\.\d+)?)", raw, re.I)
                    hum_match = re.search(r"Humidity:\s*(-?\d+(?:\.\d+)?)", raw, re.I)
                    
                    if temp_match:
                        temp_buffer = float(temp_match.group(1))
                    if hum_match:
                        hum_buffer = float(hum_match.group(1))
                        
                    if temp_buffer is not None and hum_buffer is not None:
                        t = temp_buffer
                        h = hum_buffer
                        temp_buffer = None
                        hum_buffer = None
                        
                        if -50 <= t <= 100 and 0 <= h <= 100:
                            save_reading(t, h)
                            with lock:
                                latest["connected"] = True
                                latest["temperature"] = t
                                latest["humidity"] = h
                                latest["last_updated"] = datetime.now().isoformat()
                                latest["message"] = "Receiving sensor data"
                        else:
                            print(f"[Warning] Invalid sensor values: T={t}, H={h}", flush=True)
        except serial.SerialException as exc:
            with lock:
                latest["connected"] = False
                latest["message"] = f"Cannot open {SERIAL_PORT}. Check USB connection."
            time.sleep(3)
        except Exception as exc:
            with lock:
                latest["connected"] = False
                latest["message"] = "Serial error: " + str(exc)
            time.sleep(3)



def predict_temperature():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now() - timedelta(minutes=60)).isoformat()
        rows = conn.execute("SELECT timestamp, temperature FROM readings WHERE timestamp >= ? ORDER BY timestamp ASC", (cutoff,)).fetchall()
    
    if len(rows) < 10:
        return {"status": "insufficient_data", "message": "Collecting data for prediction (need >10 readings in last hour)"}
    
    try:
        t0 = datetime.fromisoformat(rows[0]['timestamp']).timestamp()
        
        xs = []
        ys = []
        for r in rows:
            ts = datetime.fromisoformat(r['timestamp']).timestamp()
            xs.append((ts - t0) / 60.0)
            ys.append(r['temperature'])
            
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        
        if den == 0:
            return {"status": "error", "message": "No variance in time to predict"}
            
        m = num / den
        c = mean_y - m * mean_x
        
        last_x = xs[-1]
        
        p5 = m * (last_x + 5) + c
        p15 = m * (last_x + 15) + c
        p30 = m * (last_x + 30) + c
        
        mae = sum(abs(y - (m * x + c)) for x, y in zip(xs, ys)) / n
        
        # calculate trend direction
        trend = "Stable"
        if m > 0.05:
            trend = "Rising"
        elif m < -0.05:
            trend = "Falling"
            
        duration = int(last_x) # minutes of data
        
        return {
            "status": "ok",
            "method": "Linear Regression (60m window)",
            "duration_minutes": duration,
            "trend": trend,
            "predictions": {
                "5m": round(p5, 2),
                "15m": round(p15, 2),
                "30m": round(p30, 2)
            },
            "metrics": {
                "mae": round(mae, 2)
            },
            "samples": n,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.route("/")
def index():
    return render_template("index.html")

@app.after_request
def add_header(r):
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r

@app.route("/api/current")
def api_current():
    with lock:
        uptime = int(time.time() - latest["start_time"])
        data = {**latest, "uptime_seconds": uptime}
    return jsonify(data)

@app.route("/api/predictions")
def api_predictions():
    return jsonify(predict_temperature())

@app.route("/api/history")
def api_history():
    hours = int(request.args.get("hours", 1))
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT timestamp, temperature, humidity FROM readings WHERE timestamp >= ? ORDER BY timestamp ASC", (cutoff,)).fetchall()
    
    if len(rows) > 300:
        step = len(rows) // 300
        rows = rows[::step]
        
    history = [dict(r) for r in rows]
    
    stats = {}
    if history:
        temps = [r['temperature'] for r in history]
        hums = [r['humidity'] for r in history]
        stats = {
            "temp_min": min(temps), "temp_max": max(temps), "temp_avg": round(sum(temps)/len(temps), 1),
            "hum_min": min(hums), "hum_max": max(hums), "hum_avg": round(sum(hums)/len(hums), 1)
        }
        
    return jsonify({"history": history, "stats": stats})

@app.route("/api/download_csv")
def download_csv():
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT timestamp, temperature, humidity FROM readings ORDER BY timestamp ASC").fetchall()
    
    def generate():
        yield "Timestamp,Temperature(C),Humidity(%)\n"
        for r in rows:
            yield f"{r[0]},{r[1]},{r[2]}\n"
            
    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": "attachment; filename=sensor_data.csv"})

if __name__ == "__main__":
    reader = threading.Thread(target=serial_reader, daemon=True)
    reader.start()

    
    print(f"Starting dashboard. ESP32 serial port: {SERIAL_PORT} at {BAUD_RATE} baud")
    print("Open http://127.0.0.1:5000 in your browser.")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
