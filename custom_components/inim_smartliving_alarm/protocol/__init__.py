"""Encoding and decoding of the Inim SmartLiving panel protocol.

Pure functions and data classes, without I/O and without Home Assistant
imports: requests are built as bytes, replies are decoded from bytes. The
transport (SmartLAN/SI or SmartLAN/G) and the API on top are separate.

Sizes depend on the panel model (number of areas, terminals and physical
slots) and are always taken from a PanelModel, never assumed.
"""
