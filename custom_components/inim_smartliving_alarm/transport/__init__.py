"""Asynchronous connections to a SmartLiving panel through a SmartLAN board.

One persistent TCP connection per panel, one request at a time, commands
ahead of polls. Frames are built and decoded by the protocol package; this
package only moves bytes. No Home Assistant imports.
"""
