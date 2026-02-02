import socket
import ujson

import hardware

HTTP_PORT = 80

# ====================================
# HTML PAGE (Generator UI)
# ====================================

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ESP32 Generator Control</title>
<style>
body { font-family: Arial, sans-serif; margin: 20px; }
h1 { font-size: 1.4em; }
table { border-collapse: collapse; }
th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: center; }
button { padding: 4px 8px; margin: 2px; }
input[type=number] { width: 90px; }
.status { margin-top: 10px; font-size: 0.9em; color: #555; }
</style>
</head>
<body>
<h1>ESP32 Generator Control &amp; Current Monitor</h1>

<table>
<thead>
<tr>
  <th>Generator</th>
  <th>Direction</th>
  <th>Duty (0-65535)</th>
  <th>Commands</th>
  <th>Bus Voltage (V)</th>
  <th>Current (A)</th>
</tr>
</thead>
<tbody>
%s
</tbody>
</table>

<div class="status" id="status">Connecting...</div>

<script>
const NUM_GENERATORS = %d;

function rowHtml(id) {
  return `
<tr>
  <td>G${id}</td>
  <td id="dir${id}">-</td>
  <td><input type="number" id="duty${id}" value="40000" min="0" max="65535"></td>
  <td>
    <button onclick="sendControl(${id}, 'fwd')">FWD</button>
    <button onclick="sendControl(${id}, 'rev')">REV</button>
    <button onclick="sendControl(${id}, 'stop')">STOP</button>
  </td>
  <td id="v${id}">-</td>
  <td id="i${id}">-</td>
</tr>`;
}

document.addEventListener('DOMContentLoaded', () => {
  const rows = [];
  for (let id = 1; id <= NUM_GENERATORS; id++) {
    rows.push(rowHtml(id));
  }
  document.querySelector('tbody').innerHTML = rows.join('');
  refreshState();
  setInterval(refreshState, 500);
});

function sendControl(g, dir) {
  const duty = document.getElementById('duty' + g).value;
  fetch(`/api/control?g=${g}&dir=${dir}&duty=${duty}`)
    .then(r => r.json())
    .then(data => {
      document.getElementById('status').textContent = data.message || 'OK';
      refreshState();
    })
    .catch(err => {
      document.getElementById('status').textContent = 'Error sending control';
    });
}

function refreshState() {
  fetch('/api/state')
    .then(r => r.json())
    .then(data => {
      if (!data || !data.generators) return;
      data.generators.forEach(g => {
        const id = g.id;
        const dirEl = document.getElementById('dir' + id);
        const vEl = document.getElementById('v' + id);
        const iEl = document.getElementById('i' + id);

        if (!dirEl || !vEl || !iEl) return;

        dirEl.textContent = g.dir;
        vEl.textContent = (g.v !== null) ? g.v.toFixed(2) : 'N/A';
        iEl.textContent = (g.i !== null) ? g.i.toFixed(3) : 'N/A';
      });
      document.getElementById('status').textContent = 'Updated at ' + new Date().toLocaleTimeString();
    })
    .catch(err => {
      document.getElementById('status').textContent = 'Error reading state';
    });
}
</script>
</body>
</html>
"""


def build_html():
    """Builds the HTML page with correct number of generators."""
    return HTML_PAGE % ("", len(hardware.generators))


# ====================================
# HTTP HELPERS
# ====================================


def parse_query(path):
    """Return (route, params_dict) from a request path like /api/control?g=1&duty=100."""
    if "?" in path:
        route, qs = path.split("?", 1)
    else:
        route, qs = path, ""
    params = {}
    if qs:
        for pair in qs.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k] = v
    return route, params


def handle_client(conn):
    try:
        req = conn.recv(1024)
        if not req:
            return
        # First line: "GET /path HTTP/1.1"
        line = req.split(b"\r\n", 1)[0]
        parts = line.split()
        if len(parts) < 2:
            return
        method = parts[0].decode()
        path = parts[1].decode()

        route, params = parse_query(path)

        if route == "/":
            html = build_html()
            response = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + html
            conn.send(response)

        elif route == "/api/state":
            data = {"generators": []}
            for idx in range(len(hardware.generators)):
                state = hardware.generator_states[idx]
                sv = hardware.sensor_values[idx]
                gen_id = idx + 1
                if sv is None:
                    v = None
                    i = None
                else:
                    v = sv["v"]
                    i = sv["i"]
                data["generators"].append(
                    {
                        "id": gen_id,  # 1-based ID
                        "dir": state["dir"],
                        "duty": state["duty"],
                        "v": v,
                        "i": i,
                    }
                )
            body = ujson.dumps(data)
            response = (
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n" + body
            )
            conn.send(response)

        elif route == "/api/control":
            try:
                gen_id = int(params.get("g", "1"))  # 1-based from client
                direction = params.get("dir", "stop")
                duty = int(params.get("duty", "0"))
            except ValueError:
                body = ujson.dumps({"ok": False, "message": "Invalid parameters"})
                response = (
                    "HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n\r\n"
                    + body
                )
                conn.send(response)
            else:
                gen_index = gen_id - 1  # convert to 0-based index
                if gen_index < 0 or gen_index >= len(hardware.generators):
                    body = ujson.dumps(
                        {"ok": False, "message": "Generator ID out of range"}
                    )
                    response = (
                        "HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n\r\n"
                        + body
                    )
                    conn.send(response)
                else:
                    hardware.set_generator(gen_index, direction, duty)
                    body = ujson.dumps(
                        {
                            "ok": True,
                            "message": "Generator {} set to {} duty {}".format(
                                gen_id, direction, duty
                            ),
                        }
                    )
                    response = (
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
                        + body
                    )
                    conn.send(response)
        else:
            resp = "HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\nNot found"
            conn.send(resp)
    finally:
        conn.close()


def start_http_server():
    addr = socket.getaddrinfo("0.0.0.0", HTTP_PORT)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(2)
    s.settimeout(0.05)  # non-blocking accept with short timeout
    print("HTTP server listening on port", HTTP_PORT)
    return s
