import network
import time

import hardware
import http_server

# ====================================
# CONFIGURATION
# ====================================

# --- WiFi ---
WIFI_SSID = "Nafisah_wifi"
WIFI_PASSWORD = "s123123s"

# Timing
SENSOR_UPDATE_INTERVAL = 0.2  # seconds
SERVER_POLL_INTERVAL = 0.05  # seconds


# ====================================
# WIFI HELPER
# ====================================


def connect_wifi(ssid, password, timeout_s=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Connecting to WiFi:", ssid)
        wlan.connect(ssid, password)

        start = time.time()
        while not wlan.isconnected():
            if time.time() - start > timeout_s:
                print("WiFi connection timeout.")
                return None
            time.sleep(0.5)

    print("WiFi connected, IP:", wlan.ifconfig()[0])
    return wlan


# ====================================
# MAIN LOOP
# ====================================


def main():
    # WiFi
    wlan = connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    if wlan is None:
        print("Cannot continue without WiFi.")
        return

    # Hardware (sensors + generators)
    hardware.setup_hardware(i2c_freq=100000)

    # Start server
    server = http_server.start_http_server()

    last_sensor_update = 0

    print(
        "Entering main loop. Open http://{}/ in your browser.".format(
            wlan.ifconfig()[0]
        )
    )

    try:
        while True:
            now = time.ticks_ms()

            # Periodic sensor update
            if time.ticks_diff(now, int(last_sensor_update * 1000)) > int(
                SENSOR_UPDATE_INTERVAL * 1000
            ):
                hardware.update_sensors()
                last_sensor_update = now / 1000.0

            # Handle one client if present
            try:
                conn, addr = server.accept()
            except OSError:
                conn = None

            if conn:
                http_server.handle_client(conn)

            time.sleep(SERVER_POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt - stopping all generators.")
        hardware.stop_all_generators()
        time.sleep(0.5)


# Run main if this file is executed
#if __name__ == "__main__":
#    main()
