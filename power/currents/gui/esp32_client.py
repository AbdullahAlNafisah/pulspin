import requests


class ESP32Client:
    """
    Simple client for the ESP32 HTTP API:
      - GET /api/state  -> {"generators": [ { "id": 1, "dir": "...", "duty": ..., "v": ..., "i": ... }, ... ]}
      - GET /api/control?g=1&dir=fwd&duty=30000
    """

    def __init__(self, host="192.168.8.48", port=80, timeout=2.0):
        # host can be "192.168.1.50" or "http://192.168.1.50"
        if host.startswith("http://") or host.startswith("https://"):
            base = host
        else:
            base = f"http://{host}"

        # add port if not already present
        if port and ":" not in base.split("//", 1)[1]:
            base = f"{base}:{port}"

        self.base_url = base.rstrip("/")
        self.timeout = timeout

    def get_state(self):
        """Return list of generator dicts from /api/state."""
        url = f"{self.base_url}/api/state"
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        # firmware returns {"generators": [...]}
        return data.get("generators", [])

    def set_generator(self, generator_id, direction="stop", duty=0):
        """
        generator_id: 1..N  (1-based ID, as exposed by the ESP32 API)
        direction: 'stop', 'fwd', 'rev'
        duty: 0..65535
        """
        url = f"{self.base_url}/api/control"
        params = {"g": generator_id, "dir": direction, "duty": duty}
        r = requests.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()
