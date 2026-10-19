import unittest

from zwanki.ride import Buttons, parse_battery_message, parse_button_message


def varint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def button_message(held_bits=0, left=0, right=0):
    """Build a payload the way the Ride sends it: field 1 = inverted button map, field 3 = analog readings."""
    body = b"\x08" + varint(~held_bits & 0xFFFFFFFF)
    for location, value in ((0, left), (1, right), (2, 0), (3, 0)):
        zigzag = ((value << 1) ^ (value >> 31)) & 0xFFFFFFFF
        reading = b"\x08" + varint(location) + b"\x10" + varint(zigzag)
        body += b"\x1a" + varint(len(reading)) + reading
    return body


class ParseTest(unittest.TestCase):
    def test_real_message_left_big_pressed(self):
        # Captured from a Zwift Ride (firmware 1.1.0) with the left big button pushed all the way.
        payload = bytes.fromhex("08ffffffff0f1a05080010c7011a04080110001a04080210001a0408031000")
        self.assertEqual(parse_button_message(payload), (0, {"left_big": -100, "right_big": 0}))

    def test_digital_and_analog(self):
        held, analog = parse_button_message(button_message(0x1000 | 0x10, right=73))
        self.assertEqual(held, 0x1010)
        self.assertEqual(analog, {"left_big": 0, "right_big": 73})

    def test_battery(self):
        self.assertEqual(parse_battery_message(bytes.fromhex("1063")), 99)


class ButtonsTest(unittest.TestCase):
    def test_button_reported_by_both_controllers_fires_once(self):
        b = Buttons(60, 25)
        self.assertEqual(b.update("left", 0x1000, {}), {"right_up"})
        self.assertEqual(b.update("right", 0x1000, {}), set())
        b.update("left", 0, {})
        self.assertEqual(b.update("right", 0, {}), set())
        self.assertEqual(b.update("right", 0x1000, {}), {"right_up"})

    def test_big_button_hysteresis(self):
        b = Buttons(60, 25)
        self.assertEqual(b.update("right", 0, {"right_big": -70}), {"right_big"})
        self.assertEqual(b.update("right", 0, {"right_big": -40}), set())  # still held
        self.assertEqual(b.update("right", 0, {"right_big": -10}), set())  # released
        self.assertEqual(b.update("right", 0, {"right_big": -65}), {"right_big"})

    def test_pressure_is_strongest_reading(self):
        b = Buttons(60, 25)
        b.update("left", 0, {"left_big": -42, "right_big": 0})
        b.update("right", 0, {"left_big": 0, "right_big": 7})
        self.assertEqual(b.pressure(), {"left_big": 42, "right_big": 7})

    def test_forget_releases_buttons(self):
        b = Buttons(60, 25)
        b.update("left", 0x0100, {})
        b.forget("left")
        self.assertEqual(b.down, set())


if __name__ == "__main__":
    unittest.main()
