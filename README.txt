ESP32 DHT11 Local Web Dashboard
================================

Requirements
- Python installed
- ESP32 board connected by USB
- Arduino sketch prints lines such as:
    Temperature: 32.50 °C
    Humidity: 32.00 %
- Serial Monitor closed while the Python app is running

1. Open PowerShell in this folder.
2. Install dependencies if needed:
   python -m pip install flask pyserial
3. Check the port in app.py. It defaults to COM13.
4. Start the app:
   python app.py
5. Open this address in your browser:
   http://127.0.0.1:5000

Keep the PowerShell window running while using the dashboard.
Stop the app with Ctrl+C in PowerShell.

This is a local laptop dashboard, not cloud-hosted. It does not expose the dashboard to the internet.
