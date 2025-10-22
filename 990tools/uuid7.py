#!/usr/bin/env python3
"""
uuid7.py - UUID v7 (time-ordered) generation utility

This module provides fast UUID v7 generation without database dependencies.
UUID v7 is time-ordered, making it ideal for primary keys and indexes.
"""

import time
import random


def generate_uuid_v7() -> str:
    """Generate a UUID v7 (time-ordered) string.

    UUID v7 format: timestamp (48 bits) + version (4 bits) + rand_a (12 bits) + variant (2 bits) + rand_b (62 bits)

    Returns:
        str: UUID v7 string in standard format (e.g., '01234567-89ab-cdef-0123-456789abcdef')
    """
    # Get current timestamp in milliseconds since Unix epoch
    timestamp_ms = int(time.time() * 1000)

    # UUID v7 format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    # timestamp_high (32 bits) | version (4 bits) | timestamp_low (16 bits) | rand_a (12 bits) | variant (2 bits) | rand_b (62 bits)

    # Extract timestamp components
    timestamp_high = (timestamp_ms >> 16) & 0xFFFFFFFFFFFF  # 48 bits
    timestamp_low = timestamp_ms & 0xFFFF  # 16 bits

    # Generate random parts
    rand_a = random.randint(0, 0xFFF)  # 12 bits
    rand_b = random.randint(0, 0x3FFFFFFFFFFFFFFF)  # 62 bits

    # Construct UUID v7
    # Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    # timestamp_high (32 bits) | version (4 bits) | timestamp_low (16 bits) | rand_a (12 bits) | variant (2 bits) | rand_b (62 bits)

    part1 = timestamp_high >> 16  # First 32 bits of timestamp
    part2 = ((timestamp_high & 0xFFFF) << 16) | (7 << 12) | timestamp_low  # timestamp_low(16) + version(4) + timestamp_high_low(12)
    part3 = (rand_a << 2) | 0x2  # rand_a(12) + variant(2)
    part4 = rand_b  # rand_b(62 bits, but we'll take 32)

    # Actually construct properly
    uuid_int = (timestamp_high << 80) | (7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b

    # Convert to hex and format as UUID string
    uuid_hex = f"{uuid_int:032x}"
    return f"{uuid_hex[:8]}-{uuid_hex[8:12]}-{uuid_hex[12:16]}-{uuid_hex[16:20]}-{uuid_hex[20:32]}"


# For backward compatibility, keep the old function name
uuid7 = generate_uuid_v7


if __name__ == "__main__":
    # Test UUID generation
    print("Testing UUID v7 generation:")
    for i in range(5):
        uuid = generate_uuid_v7()
        print(f"UUID {i+1}: {uuid}")
        # Small delay to show time ordering
        time.sleep(0.001)