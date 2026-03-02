import time
import threading
import dwfpy as dwf

# Pulse parameters
DIO_CH = 0                 # DIO-0
PULSE_LOW_S = 1e-3         # time low (seconds) before/after high
PULSE_HIGH_S = 50e-6       # time high (seconds)
REPETITIONS = 1            # generate pulse once

# Scope parameters
SCOPE_CH = 0               # Analog In channel 0
SAMPLE_RATE = 10e6         # 10 MS/s
BUFFER_SIZE = 20000        # samples in capture buffer
SCOPE_RANGE_V = 5.0        # volts full-scale range
TRIG_LEVEL_V = 1.5         # trigger at ~midpoint of 3.3V logic
TRIG_HYST_V = 0.05

def main():
    with dwf.AnalogDiscovery2() as device:
        # (Optional but often helpful) disable auto-config so you control when instruments configure/start
        device.auto_configure = False

        scope = device.analog_input
        pattern = device.digital_output

        # --- Scope setup ---
        scope[SCOPE_CH].setup(range=SCOPE_RANGE_V)
        # Trigger on rising edge on channel 0
        scope.setup_edge_trigger(
            mode="normal",
            channel=SCOPE_CH,
            slope="rising",
            level=TRIG_LEVEL_V,
            hysteresis=TRIG_HYST_V,
        )

        captured = {}

        # Run the single-shot acquisition in a background thread so we can fire the pulse while it's armed
        def do_capture():
            # mode='normal' means this will wait for the trigger event
            scope.single(
                sample_rate=SAMPLE_RATE,
                buffer_size=BUFFER_SIZE,
                configure=True,
                start=True,
            )
            captured["samples"] = scope[SCOPE_CH].get_data()

        t = threading.Thread(target=do_capture, daemon=True)
        t.start()

        # Give the scope a moment to arm
        time.sleep(0.05)

        # --- Digital pulse setup ---
        # setup_pulse(low, high, ..., repetition=1) emits exactly one pulse pattern
        # initial_state='low' ensures we start low then go high for PULSE_HIGH_S
        pattern[DIO_CH].setup_pulse(
            low=PULSE_LOW_S,
            high=PULSE_HIGH_S,
            repetition=REPETITIONS,
            initial_state="low",
            idle_state="low",
            configure=True,
            start=True,
        )

        # Wait for capture to finish and read data
        t.join(timeout=5.0)
        samples = captured.get("samples")
        if samples is None:
            raise RuntimeError("Scope capture did not complete (no trigger?)")

        print(f"Captured {len(samples)} samples on CH{SCOPE_CH}")
        print(f"First 10 samples: {samples[:10]}")

if __name__ == "__main__":
    main()