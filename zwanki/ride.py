"""The Zwift Ride controllers' Bluetooth protocol, and turning their reports into button presses."""

# Zwift's controller protocol, as reverse-engineered by the community (BikeControl, qdomyos-zwift).
ZWIFT_COMPANY_ID = 0x094A
RIDE_SIDES = {0x07: "right", 0x08: "left"}  # first byte of the advertised manufacturer data
NOTIFY_CHAR = "00000002-19ca-4651-86e5-fa29dcdd09d1"
WRITE_CHAR = "00000003-19ca-4651-86e5-fa29dcdd09d1"
INDICATE_CHAR = "00000004-19ca-4651-86e5-fa29dcdd09d1"
RIDE_ON = b"RideOn"  # handshake without a key exchange, so the controller replies unencrypted
MSG_BUTTONS = 0x23
MSG_BATTERY = 0x19

# Bits of the Ride's button map. A bit is cleared while its button is held.
# Checked by hand on firmware 1.1.0, except powerup/onoff, which follow the
# same pattern but haven't been pressed yet.
BUTTON_BITS = {
    "dpad_left": 0x0001, "dpad_up": 0x0002, "dpad_right": 0x0004, "dpad_down": 0x0008,
    "a": 0x0010, "b": 0x0020, "y": 0x0040, "z": 0x0080,
    "left_up": 0x0100, "left_down": 0x0200, "powerup_left": 0x0400, "onoff_left": 0x0800,
    "right_up": 0x1000, "right_down": 0x2000, "powerup_right": 0x4000, "onoff_right": 0x8000,
}
KNOWN_BITS = sum(BUTTON_BITS.values())
# The big buttons are pressure sensitive and report -100..100 instead of a bit.
ANALOG_BUTTONS = {0: "left_big", 1: "right_big"}
BUTTON_NAMES = set(BUTTON_BITS) | set(ANALOG_BUTTONS.values())


# --- protobuf decoding (just enough for the Ride's messages) ---

def read_varint(buf, i):
    value = shift = 0
    while True:
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, i


def proto_fields(buf):
    """Yield (field number, value); length-delimited values come back as bytes."""
    i = 0
    while i < len(buf):
        key, i = read_varint(buf, i)
        field, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, i = read_varint(buf, i)
        elif wire_type == 2:
            length, i = read_varint(buf, i)
            value, i = buf[i:i + length], i + length
        else:
            return  # the Ride never sends other wire types; stop rather than misparse
        yield field, value


def zigzag(n):
    return (n >> 1) ^ -(n & 1)


def parse_button_message(payload):
    """Decode a button message into (bitmask of held buttons, {big button name: -100..100})."""
    held_bits, analog = 0, {}
    for field, value in proto_fields(payload):
        if field == 1:
            held_bits = ~value & 0xFFFFFFFF
        elif field == 3:
            reading = dict(proto_fields(value))  # 1: which button, 2: zigzag-encoded value
            name = ANALOG_BUTTONS.get(reading.get(1, 0))
            if name:
                analog[name] = zigzag(reading.get(2, 0))
    return held_bits, analog


def parse_battery_message(payload):
    return dict(proto_fields(payload)).get(2)


class Buttons:
    """Merges both controllers' state and reports buttons the moment they go down."""

    def __init__(self, press_at, release_at):
        self.press_at, self.release_at = press_at, release_at
        self.held = {}  # side -> names held on that controller
        self.levels = {}  # side -> {big button: how hard it's pressed, 0..100}
        self.down = set()

    def update(self, side, held_bits, analog):
        prev = self.held.get(side, set())
        held = {name for name, bit in BUTTON_BITS.items() if held_bits & bit}
        for name, value in analog.items():
            # Hysteresis, so a big button hovering around the threshold doesn't fire repeatedly.
            if abs(value) >= self.press_at or (name in prev and abs(value) > self.release_at):
                held.add(name)
        self.held[side] = held
        self.levels[side] = {name: min(abs(value), 100) for name, value in analog.items()}
        return self._newly_down()

    def forget(self, side):
        self.held.pop(side, None)
        self.levels.pop(side, None)
        self._newly_down()

    def pressure(self):
        """Strongest reading of each big button across both controllers."""
        out = dict.fromkeys(ANALOG_BUTTONS.values(), 0)
        for levels in self.levels.values():
            for name, value in levels.items():
                out[name] = max(out[name], value)
        return out

    def _newly_down(self):
        now = set().union(*self.held.values())
        pressed = now - self.down
        self.down = now
        return pressed
