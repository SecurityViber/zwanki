import struct
import unittest

from zwanki.kickr import parse_bike_data


class BikeDataTest(unittest.TestCase):
    def test_speed_cadence_power(self):
        # flags: speed present (bit 0 clear), cadence (0x04), power (0x40)
        data = struct.pack("<HHHh", 0x0044, 2850, 170, 182)
        self.assertEqual(parse_bike_data(data), {"speed_kmh": 28.5, "cadence_rpm": 85.0, "power_w": 182})

    def test_truncated_data_stops_cleanly(self):
        data = struct.pack("<HH", 0x0044, 1000) + b"\x01"
        self.assertEqual(parse_bike_data(data), {"speed_kmh": 10.0})


if __name__ == "__main__":
    unittest.main()
