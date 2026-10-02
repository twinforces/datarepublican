#!/usr/bin/env python3
"""Deferred XML Address TSV append."""

import os
import tempfile
import unittest

from models.address import Address
from xml_address_tsv import (
    address_tsv_columns,
    append_xml_addresses,
    defer_xml_addresses,
)


class XmlAddressTsvTests(unittest.TestCase):
    def test_defer_flag(self):
        os.environ.pop("XML_DEFER_ADDRESSES", None)
        self.assertFalse(defer_xml_addresses())
        os.environ["XML_DEFER_ADDRESSES"] = "1"
        self.assertTrue(defer_xml_addresses())
        os.environ.pop("XML_DEFER_ADDRESSES", None)

    def test_append_writes_header_once_and_preps_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "xml_addresses.tsv")
            os.environ["XML_ADDRESS_TSV"] = path
            try:
                a = Address(ein="123456789", name="A", address_line1="1 MAIN ST", city="X", state="NY", zip_code="10001")
                b = Address(ein="123456789", name="B", address_line1="2 MAIN ST", city="X", state="NY", zip_code="10001")
                self.assertEqual(append_xml_addresses([a, b]), 2)
                self.assertIsNotNone(a.address_id)
                self.assertIsNotNone(a.canonical_address)
                c = Address(ein="987654321", name="C", city="Y", state="CA", zip_code="90001")
                self.assertEqual(append_xml_addresses([c]), 1)
                with open(path, encoding="utf-8") as handle:
                    lines = handle.readlines()
                self.assertEqual(len(lines), 4)
                header = lines[0].rstrip("\n").split("\t")
                self.assertEqual(header, address_tsv_columns())
                self.assertTrue(header[0] == "address_id")
            finally:
                os.environ.pop("XML_ADDRESS_TSV", None)


if __name__ == "__main__":
    unittest.main()
