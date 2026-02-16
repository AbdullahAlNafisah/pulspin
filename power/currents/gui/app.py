import sys

import pyqtgraph as pg
from PySide6.QtCore import QThreadPool, Slot
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QSpinBox,
)

from esp32_client import ESP32Client
from worker import Worker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("ESP32 Generator Current Monitor & Control")

        self.threadpool = QThreadPool()
        self.worker = None
        self.client = None

        self.num_generators = 5  # matches firmware
        self.max_points = 200

        # For each generator: list of currents
        self.history_i = [[0.0] * self.max_points for _ in range(self.num_generators)]

        # Closed-loop control state:
        # { gen_id: {"target_a": float, "direction": "fwd"/"rev", "duty": int} }
        self.control_targets = {}

        # Per-generator current setpoint widgets (index by generator_id, 1..N)
        self.current_spins = [None] * (self.num_generators + 1)

        # ========= UI Layouts =========
        main_layout = QVBoxLayout()
        top_layout = QHBoxLayout()
        body_layout = QHBoxLayout()
        bottom_layout = QVBoxLayout()  # one row per generator

        # ---- Top: connection + streaming ----
        self.host_edit = QLineEdit("192.168.8.48")
        self.host_edit.setPlaceholderText("ESP32 IP or host")
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.on_connect_clicked)

        self.start_button = QPushButton("Start Sampling")
        self.start_button.setCheckable(True)
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.on_start_clicked)

        self.status_label = QLabel("Disconnected")

        top_layout.addWidget(QLabel("ESP32 Host:"))
        top_layout.addWidget(self.host_edit)
        top_layout.addWidget(self.connect_button)
        top_layout.addWidget(self.start_button)
        top_layout.addWidget(self.status_label)
        # ---- Body: 5 horizontal pyqtgraph plots ----
        self.plot_boxes = []  # store per-generator (label, plot) for updates
        self.plots = []
        self.curves = []

        for i in range(self.num_generators):
            gen_id = i + 1  # 1-based

            vbox = QVBoxLayout()

            # Add a label ABOVE the plot
            current_label = QLabel("Last: 0 mA")
            current_label.setStyleSheet("font-size: 12px; font-weight: bold;")
            vbox.addWidget(current_label)

            plot = pg.PlotWidget()
            plot.setBackground("w")
            plot.setTitle(f"Generator {gen_id} Current")
            plot.setLabel("left", "Current (A)")
            plot.setLabel("bottom", "Sample")
            plot.showGrid(x=True, y=True)
            plot.setYRange(-1.0, 1.0)
            plot.enableAutoRange(axis="y", enable=False)

            pen = pg.mkPen(color="k")
            x0 = list(range(self.max_points))
            curve = plot.plot(x0, self.history_i[i], pen=pen)

            self.plot_boxes.append((current_label, plot))
            self.plots.append(plot)
            self.curves.append(curve)

            vbox.addWidget(plot)
            body_layout.addLayout(vbox)

        # ---- Bottom: control panel for ALL generators at once ----
        for gen_id in range(1, self.num_generators + 1):
            row = QHBoxLayout()

            row.addWidget(QLabel(f"G{gen_id}"))

            spin = QSpinBox()
            spin.setRange(0, 1000)  # 0..1000 mA (0-1 A)
            spin.setValue(100)
            self.current_spins[gen_id] = spin

            fwd_button = QPushButton("FWD")
            rev_button = QPushButton("REV")
            stop_button = QPushButton("STOP")

            fwd_button.clicked.connect(
                lambda _, gid=gen_id: self.send_control(gid, "fwd")
            )
            rev_button.clicked.connect(
                lambda _, gid=gen_id: self.send_control(gid, "rev")
            )
            stop_button.clicked.connect(
                lambda _, gid=gen_id: self.send_control(gid, "stop")
            )

            row.addWidget(QLabel("Target (mA):"))
            row.addWidget(spin)
            row.addWidget(fwd_button)
            row.addWidget(rev_button)
            row.addWidget(stop_button)

            bottom_layout.addLayout(row)

        # ---- Assemble main widget ----
        main_widget = QWidget()
        main_layout.addLayout(top_layout)
        main_layout.addLayout(body_layout)
        main_layout.addLayout(bottom_layout)
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

    # =========================
    # Connection / Worker logic
    # =========================

    def on_connect_clicked(self):
        host = self.host_edit.text().strip()
        if not host:
            self.status_label.setText("Host is empty")
            return

        self.client = ESP32Client(host=host, port=80, timeout=2.0)
        try:
            generators = self.client.get_state()  # quick test
            self.status_label.setText(
                f"Connected, {len(generators)} generators reported"
            )
            self.start_button.setEnabled(True)
        except Exception as e:
            self.client = None
            self.status_label.setText(f"Connection failed: {e}")
            self.start_button.setEnabled(False)

    def on_start_clicked(self, checked):
        if checked:
            if self.client is None:
                self.status_label.setText("Not connected")
                self.start_button.setChecked(False)
                return

            # Start worker
            self.worker = Worker(self.client, interval=0.15)
            self.worker.signals.state.connect(self.on_state_update)
            self.worker.signals.error.connect(self.on_worker_error)
            self.threadpool.start(self.worker)
            self.start_button.setText("Stop Sampling")
            self.status_label.setText("Sampling...")
        else:
            if self.worker is not None:
                self.worker.stop()
                self.worker = None
            self.start_button.setText("Start Sampling")
            self.status_label.setText("Stopped")

    # =========================
    # State updates + plotting
    # =========================

    @Slot(list)
    def on_state_update(self, generators):
        """
        generators: list of dicts from /api/state, e.g.
          { "id": 1, "dir": "fwd", "duty": 30000, "v": 12.34, "i": 0.123 }
        """
        for g in generators:
            gen_id = g.get("id")
            if gen_id is None:
                continue

            idx = gen_id - 1
            if not (0 <= idx < self.num_generators):
                continue

            dir_state = g.get("dir", "stop")
            i_meas = g.get("i", 0.0)
            if i_meas is None:
                i_meas = 0.0

            # SIGNED current: reverse direction -> negative current
            if dir_state == "rev":
                i_display = -i_meas
            else:
                i_display = i_meas

            # Maintain rolling history for plotting
            hist = self.history_i[idx]
            hist.append(i_display)
            if len(hist) > self.max_points:
                hist.pop(0)

            # Update label above plot
            label, _plot = self.plot_boxes[idx]
            label.setText(f"Last: {i_display * 1000:.1f} mA")

            x = list(range(len(hist)))
            self.curves[idx].setData(x, hist)

            # -------------------------
            # CLOSED-LOOP CURRENT CONTROL
            # -------------------------
            ctl = self.control_targets.get(gen_id)
            if ctl is not None and ctl["direction"] != "stop":
                target_a = ctl["target_a"]
                measured_abs = abs(i_meas)  # use magnitude for control

                error = target_a - measured_abs  # A
                Kp = 20000  # tune if needed
                delta_duty = int(Kp * error)

                new_duty = ctl["duty"] + delta_duty
                new_duty = max(0, min(65535, new_duty))

                # Avoid flooding ESP32
                if abs(new_duty - ctl["duty"]) > 50:
                    ctl["duty"] = new_duty
                    self._queue_command(gen_id, ctl["direction"], new_duty)

    @Slot(str)
    def on_worker_error(self, msg):
        self.status_label.setText(f"Error: {msg}")

    # =========================
    # Control commands (GUI -> queue -> worker)
    # =========================

    def _queue_command(self, generator_id, direction, duty):
        """
        Helper to enqueue a control command safely.
        Falls back to direct call if worker is not running.
        """
        if self.worker is not None:
            self.worker.send_command(generator_id, direction, duty)
        elif self.client is not None:
            # Should normally not happen while sampling, but keep a fallback
            try:
                self.client.set_generator(generator_id, direction, duty)
            except Exception as e:
                self.status_label.setText(f"Control error: {e}")

    def send_control(self, generator_id, direction):
        if self.client is None:
            self.status_label.setText("Not connected")
            return

        # STOP: cancel control and send duty 0
        if direction == "stop":
            self.control_targets.pop(generator_id, None)
            self._queue_command(generator_id, "stop", 0)
            self.status_label.setText(f"Stopped G{generator_id}")
            return

        # FWD / REV: set or update closed-loop target
        spin = self.current_spins[generator_id]
        target_ma = spin.value()
        target_a = target_ma / 1000.0  # convert to amps

        ctl = self.control_targets.get(generator_id)
        if ctl is None:
            initial_duty = (
                int(min(max(target_a * 65535, 5000), 50000)) if target_a > 0 else 0
            )
            ctl = {
                "target_a": target_a,
                "direction": direction,
                "duty": initial_duty,
            }
            self.control_targets[generator_id] = ctl
        else:
            ctl["target_a"] = target_a
            ctl["direction"] = direction
            if ctl["duty"] <= 0:
                ctl["duty"] = int(min(max(target_a * 65535, 5000), 50000))

        self._queue_command(generator_id, direction, ctl["duty"])
        self.status_label.setText(
            f"Closed-loop: G{generator_id} -> {direction}, {target_ma} mA"
        )


# =========================
# Entry point
# =========================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1400, 600)
    window.show()
    sys.exit(app.exec())
