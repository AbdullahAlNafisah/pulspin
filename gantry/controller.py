import time
import serial

# Tiny GRBL cheat-sheet:
# $X          = unlock
# G21         = millimeters
# G92 X0 Y0   = set "here" as (0,0)  (use after you manually move to origin)
# G91         = relative moves (move by +dx, +dy)
# G1 X.. Y.. F.. = move with feedrate (mm/min)


class GRBL:
    def __init__(self, port, baud=115200, bed_x=450, bed_y=500):
        self.port = port

        self.bed_x, self.bed_y = bed_x, bed_y
        self.x, self.y = 0.0, 0.0

        self.s = serial.Serial(port, baud, timeout=2)
        time.sleep(2)  # GRBL resets when serial opens
        self.s.write(b"\r\n\r\n")  # wake
        time.sleep(0.2)
        self.s.reset_input_buffer()

        self.cmd("$X")  # unlock
        self.cmd("G21")  # mm

    def cmd(self, gcode):
        self.s.write((gcode.strip() + "\n").encode("ascii"))
        while True:
            r = self.s.readline().decode("ascii", errors="ignore").strip()
            if r == "ok" or r.startswith("error"):
                return r
            else:
                return r

    def origin_here(self):
        # After you manually place the head at physical origin:
        self.cmd("G92 X0 Y0")
        self.x, self.y = 0.0, 0.0

    def move(self, dx=0.0, dy=0.0, F=1500):
        # clamp to the bed (0..bed_x, 0..bed_y)
        nx, ny = self.x + dx, self.y + dy
        if nx < 0:
            dx = -self.x
        if ny < 0:
            dy = -self.y
        if nx > self.bed_x:
            dx = self.bed_x - self.x
        if ny > self.bed_y:
            dy = self.bed_y - self.y

        self.cmd("G91")  # relative mode
        self.cmd(f"G1 X{dx:.3f} Y{dy:.3f} F{F}")

        self.x += dx
        self.y += dy
        return self.x, self.y

    def close(self):
        self.s.close()
