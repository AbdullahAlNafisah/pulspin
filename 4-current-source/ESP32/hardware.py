from machine import Pin, SoftI2C, PWM

# ====================================
# CONFIGURATION FOR GENERATORS
# ====================================

INA219_ADDR = 0x40
SHUNT_RESISTOR_OHMS = 0.1  # typical value 0.1Ω (R100)
GENERATOR_MAX_DUTY = 65535  # duty_u16 range

# One combined pin map for I2C + H-bridge per generator
# Index 0 -> Generator 1, index 1 -> Generator 2, etc.
GENERATOR_PINS = [
    {"scl": 1, "sda": 0, "en": 4, "in1": 2, "in2": 3},  # Generator 1
    {"scl": 6, "sda": 5, "en": 7, "in1": 9, "in2": 8},  # Generator 2
    {"scl": 11, "sda": 10, "en": 12, "in1": 14, "in2": 13},  # Generator 3
    {"scl": 16, "sda": 15, "en": 39, "in1": 20, "in2": 21},  # Generator 4
    {"scl": 34, "sda": 33, "en": 37, "in1": 35, "in2": 36},  # Generator 5
]

# ====================================
# INA219 CLASS
# ====================================


class INA219:
    REG_CONFIG = 0x00
    REG_SHUNT_VOLTAGE = 0x01
    REG_BUS_VOLTAGE = 0x02
    REG_POWER = 0x03
    REG_CURRENT = 0x04
    REG_CALIBRATION = 0x05

    def __init__(self, i2c, addr=INA219_ADDR, shunt_ohms=SHUNT_RESISTOR_OHMS):
        self.i2c = i2c
        self.addr = addr
        self.shunt = shunt_ohms

        # 32V range, ±160mV shunt, 12-bit, 128 samples, continuous mode
        config = 0x3FFF
        self._write16(self.REG_CONFIG, config)

        # Calibration: 0.1mA per bit
        self.current_lsb = 0.0001
        self.calibration = int(0.04096 / (self.current_lsb * self.shunt))
        self._write16(self.REG_CALIBRATION, self.calibration)

        self.power_lsb = self.current_lsb * 20

    def _write16(self, reg, value):
        buf = bytearray(2)
        buf[0] = (value >> 8) & 0xFF
        buf[1] = value & 0xFF
        self.i2c.writeto_mem(self.addr, reg, buf)

    def _read16(self, reg):
        data = self.i2c.readfrom_mem(self.addr, reg, 2)
        return (data[0] << 8) | data[1]

    def _read_signed_16(self, reg):
        raw = self._read16(reg)
        if raw > 32767:
            raw -= 65536
        return raw

    def bus_voltage(self):
        raw = self._read16(self.REG_BUS_VOLTAGE)
        raw >>= 3
        return raw * 0.004  # 4 mV / bit

    def current_once(self):
        # Rewrite calibration (can be lost)
        self._write16(self.REG_CALIBRATION, self.calibration)
        raw = self._read_signed_16(self.REG_CURRENT)
        return raw * self.current_lsb

    def current_avg(self, samples=4):
        total = 0.0
        for _ in range(samples):
            total += self.current_once()
        return total / samples


# ====================================
# H-BRIDGE CLASS
# ====================================


class HBridge:
    def __init__(self, en_pin, in1_pin, in2_pin, freq=20000):
        self.en_pwm = PWM(Pin(en_pin), freq=freq)
        self.in1 = Pin(in1_pin, Pin.OUT)
        self.in2 = Pin(in2_pin, Pin.OUT)
        self.disable()

    def disable(self):
        self.en_pwm.duty_u16(0)
        self.in1.value(0)
        self.in2.value(0)

    def forward(self, duty):
        self.in1.value(1)
        self.in2.value(0)
        self.en_pwm.duty_u16(duty)

    def reverse(self, duty):
        self.in1.value(0)
        self.in2.value(1)
        self.en_pwm.duty_u16(duty)


# ====================================
# GLOBAL HARDWARE STATE
# ====================================

i2c_buses = []
sensors = []  # list of INA219 or None
generators = []  # list of HBridge
generator_states = []  # [{'dir': 'stop'|'fwd'|'rev', 'duty': int}, ...]
sensor_values = []  # [{'v': float, 'i': float} or None, ...]


# ====================================
# HARDWARE SETUP
# ====================================


def setup_hardware(i2c_freq):
    """Initialise all I2C buses, sensors, and generator drivers."""
    global i2c_buses, sensors, generators, generator_states, sensor_values

    from machine import SoftI2C, Pin  # local import for MicroPython

    print("Setting up INA219 sensors for generators...")
    i2c_buses = []
    sensors = []

    for idx, pins in enumerate(GENERATOR_PINS):
        scl_pin = pins["scl"]
        sda_pin = pins["sda"]
        gen_id = idx + 1

        print("Generator {} I2C -> SCL={}, SDA={}".format(gen_id, scl_pin, sda_pin))
        i2c = SoftI2C(scl=Pin(scl_pin), sda=Pin(sda_pin), freq=i2c_freq)
        i2c_buses.append(i2c)

        devices = i2c.scan()
        print("  Found devices:", [hex(d) for d in devices])

        if INA219_ADDR not in devices:
            print(
                "  WARNING: INA219 not found at",
                hex(INA219_ADDR),
                "on generator",
                gen_id,
            )
            sensors.append(None)
        else:
            sensor = INA219(i2c, INA219_ADDR, SHUNT_RESISTOR_OHMS)
            sensors.append(sensor)

    # --- H-bridges for generators ---
    print("Setting up H-bridges for generators...")
    generators = []
    generator_states = []
    sensor_values = []

    for idx, pins in enumerate(GENERATOR_PINS):
        gen_id = idx + 1
        g = HBridge(pins["en"], pins["in1"], pins["in2"])
        generators.append(g)
        generator_states.append({"dir": "stop", "duty": 0})
        sensor_values.append({"v": 0.0, "i": 0.0})
        print(
            "Generator {} -> EN={}, IN1={}, IN2={}".format(
                gen_id, pins["en"], pins["in1"], pins["in2"]
            )
        )

    # Rebind globals
    globals()["generators"] = generators
    globals()["generator_states"] = generator_states
    globals()["sensor_values"] = sensor_values

    return sensors, generators


# ====================================
# GENERATOR CONTROL HELPERS
# ====================================


def set_generator(gen_index, direction, duty):
    """
    Control a generator by 0-based index.
    direction: 'stop', 'fwd', 'rev'; duty: 0..GENERATOR_MAX_DUTY
    """
    if gen_index < 0 or gen_index >= len(generators):
        return

    duty = max(0, min(GENERATOR_MAX_DUTY, int(duty)))
    generator_states[gen_index]["duty"] = duty

    if direction == "stop" or duty == 0:
        generators[gen_index].disable()
        generator_states[gen_index]["dir"] = "stop"
    elif direction == "fwd":
        generators[gen_index].forward(duty)
        generator_states[gen_index]["dir"] = "fwd"
    elif direction == "rev":
        generators[gen_index].reverse(duty)
        generator_states[gen_index]["dir"] = "rev"


def stop_all_generators():
    for idx in range(len(generators)):
        set_generator(idx, "stop", 0)


# ====================================
# SENSOR UPDATE
# ====================================


def update_sensors():
    """Read all sensors and store in sensor_values."""
    for idx, sensor in enumerate(sensors):
        if sensor is None:
            sensor_values[idx] = None
            continue
        try:
            v_bus = sensor.bus_voltage()
            current = sensor.current_avg(4)
            sensor_values[idx] = {"v": v_bus, "i": current}
        except Exception as e:
            print("Sensor read error on G{}:".format(idx + 1), e)
            sensor_values[idx] = None
