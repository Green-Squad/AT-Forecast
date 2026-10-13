import datetime as dt
from decimal import Decimal
import json
import unittest
from source_snapshot import canonical, encode, quote
from turso_http import decode


class PreservationTests(unittest.TestCase):
    def test_hrana_blob_with_optional_base64_padding(self):
        self.assertEqual(decode({"type": "blob", "base64": "AP8="}), b"\x00\xff")
        self.assertEqual(decode({"type": "blob", "base64": "AP8"}), b"\x00\xff")
        self.assertEqual(decode({"type": "blob", "base64": ""}), b"")

    def test_decimal_precision_scale_and_large_ids(self):
        row = [Decimal("9007199254740993.123456789000"), 18446744073709551615]
        self.assertEqual(json.loads(canonical([encode(v) for v in row])),
                         [{"$decimal": "9007199254740993.123456789000"}, 18446744073709551615])

    def test_null_boolean_unicode_hash_and_binary(self):
        row = [None, False, True, "Pokémon 銃 e\u0301 \x00", "$2y$preserve-without-rehash", b"\x00\xff"]
        value = json.loads(canonical([encode(v) for v in row]))
        self.assertEqual(value[:5], row[:5])
        self.assertEqual(value[5], {"$binary": "AP8="})

    def test_timezone_and_naive_timestamp_are_distinct(self):
        naive = dt.datetime(2026, 11, 1, 1, 30, 0, 123456)
        aware = naive.replace(tzinfo=dt.timezone(dt.timedelta(hours=-4)))
        self.assertEqual(encode(naive), {"$datetime": "2026-11-01T01:30:00.123456", "aware": False})
        self.assertEqual(encode(aware), {"$datetime": "2026-11-01T01:30:00.123456-04:00", "aware": True})
        self.assertEqual(encode(dt.timedelta(hours=-1)), {"$duration_microseconds": -3600000000})

    def test_identifier_escaping(self):
        self.assertEqual(quote('a"b', 'postgres'), '"a""b"')
        self.assertEqual(quote('a`b', 'mysql'), '`a``b`')

    def test_unknown_values_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Unsupported source value type"):
            encode(object())


if __name__ == "__main__":
    unittest.main()
