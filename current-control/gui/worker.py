import time
from queue import Queue, Empty

from PySide6.QtCore import QThreadPool, QRunnable, QObject, Signal, Slot


class WorkerSignals(QObject):
    state = Signal(list)  # list of generator dicts from /api/state
    error = Signal(str)


class Worker(QRunnable):
    """
    Worker that:
      - periodically polls all sensors via ESP32Client (get_state)
      - processes queued control commands (set_generator) in the same thread
    """

    def __init__(self, client, interval=0.15):
        super().__init__()
        self.client = client
        self.interval = interval
        self.signals = WorkerSignals()
        self._running = True

        # Queue of (gen_id, direction, duty)
        self._command_queue = Queue()

    def stop(self):
        self._running = False

    def send_command(self, gen_id, direction, duty):
        """
        Called from GUI thread to enqueue a control command.
        """
        self._command_queue.put((gen_id, direction, duty))

    def _process_commands(self, max_per_cycle=10):
        """
        Send a few queued commands to ESP32 each cycle so we don't starve polling.
        """
        from queue import Empty

        for _ in range(max_per_cycle):
            try:
                gen_id, direction, duty = self._command_queue.get_nowait()
            except Empty:
                break
            try:
                self.client.set_generator(gen_id, direction, duty)
            except Exception as e:
                self.signals.error.emit(f"Control error: {e}")

    @Slot()
    def run(self):
        while self._running:
            # 1) Poll state
            try:
                generators = self.client.get_state()
                self.signals.state.emit(generators)
            except Exception as e:
                self.signals.error.emit(str(e))

            # 2) Process some pending control commands
            self._process_commands()

            time.sleep(self.interval)
