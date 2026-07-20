from app import create_app

app = create_app()

if __name__ == "__main__":
    # Port 5000 is frequently claimed by macOS's own AirPlay Receiver service,
    # which answers any request there with a misleading "403 Forbidden" --
    # 5050 sidesteps that entirely without needing a system settings change.
    app.run(debug=True, host="127.0.0.1", port=5050)
