"""Inim Smartliving API."""

import binascii
from datetime import datetime, timedelta, timezone
import logging
import socket
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class InimAlarmConstants:
    """Stores constants related to the Inim alarm protocol."""

    MAX_RESPONSE_LEN_BYTES = 251  # Default length of a response in bytes
    DEFAULT_TIMEOUT = 5  # Default socket timeout in seconds
    PIN_LENGTH_BYTES = 6  # Each PIN is represented with 6 bytes
    PIN_FILLER_HEX = "ff"  # Unset digits will be represented by "ff"
    DEFAULT_SYSTEM_MAX_ZONES = 50
    DEFAULT_SYSTEM_MAX_AREAS = 10
    DEFAULT_SYSTEM_MAX_SCENARIOS = 30

    # Max events to fetch in a single poll to avoid overload - change at your own risk
    MAX_EVENTS_PER_FETCH = 50

    # For static commands, "cmd_full" is 7-byte cmd + 1-byte its checksum = 8 bytes / 16 hex chars.
    # For dynamic base commands, "cmd_prefix"/"cmd_suffix" are parts of the 7-byte command,
    # and their checksum will be calculated on the fly.
    COMMAND_SPECS = {
        "GET_SYSTEM_INFO": {
            "cmd_full": "0000004000000c4c",
            "resp_len": 14,
        },
        "GET_PIN_CODES_BATCH1": {
            "cmd_full": "00000161f400fa50",
            "resp_len": 251,
        },
        "GET_PIN_CODES_BATCH2": {
            "cmd_full": "00000162ee003283",
            "resp_len": 51,
        },
        # Areas
        "GET_AREA_NAMES": {
            "cmd_full": "000101000000a0a2",
            "resp_len": 161,
        },
        "GET_AREAS_STATUS": {
            "cmd_full": "0000002000001030",
            "resp_len": 17,
        },
        "ARM_DISARM_AREAS_CMD_INFO": {
            "cmd_full": "0100002006000e35",
            "resp_len": 1,
        },
        # Scenarios
        "GET_SCENARIO_NAMES_1": {
            "cmd_full": "000101145000fa60",
            "resp_len": 251,
        },
        "GET_SCENARIO_NAMES_2": {
            "cmd_full": "000101154a00e647",
            "resp_len": 231,
        },
        "GET_SCENARIO_ACTIVATIONS": {
            "cmd_full": "00000173d800f03c",
            "resp_len": 241,
        },
        "GET_ACTIVE_SCENARIO": {
            "cmd_full": "0000001ffb7f019a",
            "resp_len": 2,
        },
        "CHECK_SCENARIO_ACTIVATION_ALLOWED_INFO": {  # Dynamic command base
            "cmd_prefix": "0000001ff9",  # Scenario byte (from 80) inserted here
            "cmd_suffix": "0d",  # Fixed part after scenario byte
            "resp_len": 14,
        },
        "ACTIVATE_SCENARIO_CMD_INFO": {
            "cmd_full": "010000200a000732",
            "resp_len": 1,
        },
        # Keyboards
        "GET_KEYBOARD_NAMES": {"cmd_full": "0001010b4000a0ed", "resp_len": 161},
        # Zones Names (Individual commands)
        "GET_ZONE_NAMES_1": {
            "cmd_full": "00010100a000fa9c",
            "resp_len": 251,
        },
        "GET_ZONE_NAMES_2": {
            "cmd_full": "000101019a00fa97",
            "resp_len": 251,
        },
        "GET_ZONE_NAMES_3": {
            "cmd_full": "000101029400fa92",
            "resp_len": 251,
        },
        "GET_ZONE_NAMES_4": {
            "cmd_full": "000101038e00fa8d",
            "resp_len": 251,
        },
        "GET_ZONE_NAMES_5": {
            "cmd_full": "000101048800fa88",
            "resp_len": 251,
        },
        "GET_ZONE_NAMES_6": {
            "cmd_full": "000101058200fa83",
            "resp_len": 251,
        },
        "GET_ZONE_NAMES_7": {
            "cmd_full": "000101067c0064e8",
            "resp_len": 101,
        },
        # Zones Config (Individual commands)
        "GET_ZONES_CONFIG_1": {
            "cmd_full": "000001545000fa9f",
            "resp_len": 251,
        },
        "GET_ZONES_CONFIG_2": {
            "cmd_full": "000001554a00fa9a",
            "resp_len": 251,
        },
        "GET_ZONES_CONFIG_3": {
            "cmd_full": "000001564400fa95",
            "resp_len": 251,
        },
        "GET_ZONES_CONFIG_4": {
            "cmd_full": "000001573e00962c",
            "resp_len": 151,
        },
        "GET_ZONES_CONFIG_5": {
            "cmd_full": "00000157d400fa26",
            "resp_len": 251,
        },
        "GET_ZONES_CONFIG_6": {
            "cmd_full": "00000158ce00fa21",
            "resp_len": 251,
        },
        "GET_ZONES_CONFIG_7": {
            "cmd_full": "00000159c8006486",
            "resp_len": 101,
        },
        "GET_ZONES_STATUS": {
            "cmd_full": "0000002001001a3b",
            "resp_len": 27,
        },
        "GET_ZONES_STATUS_RELATED_1": {
            "cmd_full": "0000002002001a3c",
            "resp_len": 27,
        },
        "GET_ZONES_STATUS_RELATED_2": {
            "cmd_full": "0000002003001c3f",
            "resp_len": 29,
        },
        # Events (Compact)
        "GET_NEXT_EVENT_POINTER_CMD_INFO": {
            "cmd_full": "0000001ffe000421",
            "resp_len": 6,
        },
        "GET_COMPACT_EVENT_CMD_INFO": {
            "cmd_base_prefix": "000101",
            "cmd_base_suffix": "0009",
            "resp_len": 10,
        },
    }

    # Area status/action constants
    AREA_STATUS_DISARMED = "4"
    AREA_STATUS_ARMED = "1"
    AREA_ACTION_DISARM = "4"
    AREA_ACTION_ARM = "1"
    AREA_ACTION_KEEP_STATUS = "0"
    MAX_AREAS_PAYLOAD = 16  # Payload structure supports up to 8 bytes)

    # Mappings for Zone Configuration Parsing
    ACTIVATION_TYPE_MAP = {
        0x00: "Immediate",
        0x01: "Delayed",
        0x04: "24 hours",
        # Other types as per "order in the menu of the programming software" may exist
    }
    BALANCING_TYPE_MAP = {
        0x00: "Normally Open",
        0x01: "Normally Closed",
        0x03: "Double Balancing",
        # Other types as per "order in the menu of the programming software" may exist
    }
    SENSOR_TYPE_P2_MAP = {  # For Byte 7 in Part 2 of Zone Config
        0x00: "Generic zone",
        0x80: "Shutter type",
        # Other types may exist
    }
    EVENT_ACTION_CODES_COMPACT: dict[int, str] = {
        0x9E: "Scenario Activated",
        0x80: "Area/Zone Alarm",
        0x00: "Area/Zone Alarm End",
        0x8A: "Area(s) Armed",
        0x8C: "Area(s) Disarmed",
        0x8D: "Area(s) Reset",
        0x0A: "End of Area(s) Armed",
        0x0C: "End of Area(s) Disarmed",
        0xBA: "Installer Code Inserted",
        0xC2: "Programming Started",
        0x42: "Programming Ended",
        0x9B: "Failed Call",
        0x1B: "End of Failed Call",
        0x95: "Valid Code",
        0x96: "Valid Key",
    }


class InimAlarmAPI:
    """Main API class."""

    def __init__(
        self, host, port, pin_code_str, system_max_zones=None, system_max_areas=None
    ) -> None:
        """Initializes the API.

        Args:
            host (str): The IP address or hostname of the alarm system (SmartLan/SI).
            port (int): The TCP port.
            pin_code_str (str): The user PIN code as a string (e.g., "1234").
            system_max_zones (int, optional): The number of zone names expected
                                                   from the get_zones command.
                                                   Defaults to InimAlarmConstants.DEFAULT_SYSTEM_MAX_ZONES.
            system_max_areas (int, optional): The (initial) maximum number of areas the system supports.
                                              Defaults to InimAlarmConstants.DEFAULT_SYSTEM_MAX_AREAS.

        """
        self.host = host
        self.port = port
        self._raw_pin = pin_code_str
        self.pin_hex = self.format_pin_code(pin_code_str)
        self.sock = None
        self._is_connected = False
        self._api_lock = threading.Lock()  # Initialize the lock for this instance

        # Use provided system_max_areas or default from constants
        self.system_max_areas = (
            system_max_areas
            if system_max_areas is not None
            else InimAlarmConstants.DEFAULT_SYSTEM_MAX_AREAS
        )

        # Use provided system_max_zones or default from constants
        self.system_max_zones = (
            system_max_zones
            if system_max_zones is not None
            else InimAlarmConstants.DEFAULT_SYSTEM_MAX_ZONES
        )

    @staticmethod
    def calculate_checksum(hex_data_string):
        """Calculates CheckSum8 Modulo 256.

        Args:
            hex_data_string (str): A string of HEX characters (e.g., "000101000000a0").
                                   This should be the data *before* the checksum byte.

        Returns:
            str: The checksum as a 2-character HEX string.

        """
        if not hex_data_string:  # Should not happen for valid commands
            return "00"

        total_sum = 0
        for i in range(0, len(hex_data_string), 2):
            byte_hex = hex_data_string[i : i + 2]
            total_sum += int(byte_hex, 16)
        checksum_val = total_sum % 256
        return format(checksum_val, "02x")

    @staticmethod
    def format_pin_code(pin_str):
        """Formats a PIN code string into a 6-byte HEX representation.
        Unset digits are represented by "ff".
        Example: "1234" -> "01020304ffff"

        Args:
            pin_str (str): The PIN code (e.g., "1234").

        Returns:
            str: The 6-byte HEX string for the PIN (12 hex characters).

        """
        pin_bytes_hex = ""
        for char_digit in pin_str:  # Assuming PIN '1' becomes hex '01'
            pin_bytes_hex += f"0{char_digit}"

        # Pad with "ff" to reach 6 bytes (12 hex characters)
        padding_needed_chars = (InimAlarmConstants.PIN_LENGTH_BYTES * 2) - len(
            pin_bytes_hex
        )
        if padding_needed_chars > 0:
            # Each "ff" is one byte, so divide by 2 for number of "ff" pairs
            pin_bytes_hex += InimAlarmConstants.PIN_FILLER_HEX * (
                padding_needed_chars // 2
            )

        return pin_bytes_hex[: InimAlarmConstants.PIN_LENGTH_BYTES * 2]

    def connect(self):
        """Establishes a TCP connection to the alarm system."""
        if self._is_connected:
            logger.info("Already connected.")
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(InimAlarmConstants.DEFAULT_TIMEOUT)
            self.sock.connect((self.host, self.port))
            self._is_connected = True
            logger.info(f"Connected to {self.host}:{self.port}")
            return True
        except socket.error as e:
            logger.error(f"Connection failed: {e}")
            self.sock = None
            self._is_connected = False
            return False

    def disconnect(self):
        """Closes the TCP connection."""
        if self.sock:
            try:
                self.sock.close()
                logger.info("Disconnected.")
            except socket.error as e:
                logger.error(f"Error during disconnect: {e}")
            finally:
                self.sock = None
                self._is_connected = False
        else:
            logger.info("Not connected, no need to disconnect.")

    def _send_raw_command(self, command_hex_with_payload):
        """
        Sends a fully formed hex command (including base command's checksum and any payload)
        after converting to binary.
        """
        if not self._is_connected or not self.sock:
            logger.error("Not connected. Cannot send command.")
            raise ConnectionError("Not connected to alarm panel.")

        try:
            binary_cmd = binascii.unhexlify(
                command_hex_with_payload
            )  # Convert hex to binary
            logger.debug(f"Sending HEX: {command_hex_with_payload}")
            # logger.debug(f"Sending BIN: {binary_cmd}") # Optional: for very low-level debug
            self.sock.sendall(binary_cmd)
            return True
        except socket.timeout:
            logger.error("Socket timeout during send.")
            # self.disconnect() # Consider implications of auto-disconnect on timeout
            raise TimeoutError("Socket timeout during send.")
        except (socket.error, binascii.Error) as e:
            logger.error(f"Error sending command: {e}")
            # self.disconnect()
            raise ConnectionError(f"Socket error during send: {e}")

    def _read_raw_response(self, buffer_size=InimAlarmConstants.MAX_RESPONSE_LEN_BYTES):
        """
        Reads a raw binary response from the socket and converts to hex.
        """
        if not self._is_connected or not self.sock:
            logger.error("Not connected. Cannot read response.")
            raise ConnectionError("Not connected to alarm panel.")
        try:
            raw_response = self.sock.recv(buffer_size)
            if not raw_response:
                logger.warning("Received empty response from panel.")
                # self.disconnect() # Connection might be closed by panel
                raise ConnectionError(
                    "Received empty response, panel may have disconnected."
                )

            response_hex = binascii.hexlify(
                raw_response
            ).decode()  # Convert binary to hex
            logger.debug(f"Received HEX: {response_hex}")
            return response_hex
        except socket.timeout:
            logger.error("Socket timeout during read.")
            raise TimeoutError("Socket timeout during read.")
        except socket.error as e:
            logger.error(f"Error reading response: {e}")
            # self.disconnect()
            raise ConnectionError(f"Socket error during read: {e}")

    def _validate_and_parse_response(self, response_hex):
        """
        Validates checksum of a typical panel response (data + its checksum)
        and returns the data part.
        Assumes the last byte of the response_hex is the checksum for the preceding data.
        """
        if len(response_hex) < 2:  # Needs at least 1 byte for checksum
            logger.error("Response too short to contain checksum: %s", response_hex)
            raise ValueError("Response too short.")

        data_hex = response_hex[:-2]  # All but the last byte (checksum)
        checksum_received_hex = response_hex[-2:]  # Last byte is checksum

        # If data_hex is empty (e.g. response was just a checksum with no preceding data from panel),
        # then checksum_calculated_hex would be for empty data.
        # This scenario is typically not what this function is for; this function is for
        # responses where the panel sends data AND a checksum for that data.
        if not data_hex and len(response_hex) == 2:
            # This means the response was just a single byte.
            # This function's purpose is to validate a data packet's checksum.
            # If the response is *only* a checksum (like for arm/disarm ack),
            # this validation step isn't applicable in the same way.
            # The calling context for such responses should handle it differently.
            # However, we can return it as is, assuming the caller knows.
            logger.debug(
                "Response is a single byte: %s. Assuming it's a direct status/checksum",
                response_hex,
            )
            return ""  # No data part if response was only a checksum. Or return the checksum itself?

        checksum_calculated_hex = self.calculate_checksum(data_hex)

        if checksum_received_hex.lower() != checksum_calculated_hex.lower():
            msg = (
                f"Response checksum mismatch! "
                f"Received: {checksum_received_hex}, Calculated: {checksum_calculated_hex} "
                f"for data: {data_hex}"
            )
            logger.error(msg)
            raise ValueError(msg)

        return data_hex

    def _send_command_core(
        self,
        eight_byte_cmd_with_checksum,
        payload_hex=None,
        expect_specific_response_len=None,
    ):
        """
        Sends an 8-byte command (which already includes its own checksum)
        and an optional payload. Reads and validates the response.
        This is for commands that are primarily a request and expect a data response.
        """
        full_command_to_send_hex = eight_byte_cmd_with_checksum
        if payload_hex:  # This case is rare for this function; usually payload commands use _send_raw_command directly
            full_command_to_send_hex += payload_hex

        try:
            self._send_raw_command(full_command_to_send_hex)

            buffer = (
                expect_specific_response_len
                if expect_specific_response_len is not None
                else InimAlarmConstants.MAX_RESPONSE_LEN_BYTES
            )
            response_hex_full = self._read_raw_response(buffer_size=buffer)

            return self._validate_and_parse_response(
                response_hex_full
            )  # Validates and strips response checksum

        except (ConnectionError, TimeoutError, ValueError) as e:
            # Log the base command part (first 7 bytes / 14 hex of the 8-byte command) for easier debugging
            logger.error(
                f"Failed to send/receive for base cmd {eight_byte_cmd_with_checksum[:14]}: {e}"
            )
            return None

    # --- Initialize ---
    def get_system_info(self):
        """Requests system type and firmware version."""
        spec = InimAlarmConstants.COMMAND_SPECS["GET_SYSTEM_INFO"]
        response_data_hex = self._send_command_core(
            spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
        )
        if response_data_hex:
            try:
                # Response is ASCII encoded HEX
                ascii_info = binascii.unhexlify(response_data_hex).decode(
                    "ascii", errors="ignore"
                )
                # Example: "6.07 01050 !" -> version "6.07", type "1050"
                parts = ascii_info.strip().split(" ")
                version = parts[0] if len(parts) > 0 else "Unknown"
                system_type = parts[1] if len(parts) > 1 else "Unknown"
                return {
                    "raw_hex": response_data_hex,
                    "ascii": ascii_info,
                    "version": version,
                    "type": system_type,
                }
            except Exception as e:
                logger.error(f"Error parsing system info: {e}")
                return {"raw_hex": response_data_hex, "error": str(e)}
        return None

    def get_areas(self):  # Names
        """Requests Area Names."""
        spec = InimAlarmConstants.COMMAND_SPECS["GET_AREA_NAMES"]
        response_data_hex = self._send_command_core(
            spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
        )
        if response_data_hex:
            try:
                # Response is an ASCII encoded HEX.
                # Each area is 16 bytes, total of 10 Areas for this system.
                area_names_raw = binascii.unhexlify(response_data_hex)
                names = []
                bytes_per_name = 16  # Each area name is 16 bytes
                for i in range(0, len(area_names_raw), bytes_per_name):
                    name_bytes = area_names_raw[i : i + bytes_per_name]
                    names.append(name_bytes.decode("ascii", errors="ignore").strip())

                return {"raw_hex": response_data_hex, "names": names}
            except Exception as e:
                logger.error(f"Error parsing area names: {e}")
                return {"raw_hex": response_data_hex, "error": str(e)}
        return None

    def get_zones(self):  # Names
        """Requests Zone Names. This involves a sequence of 7 commands."""
        zone_name_cmd_keys = [
            "GET_ZONE_NAMES_1",
            "GET_ZONE_NAMES_2",
            "GET_ZONE_NAMES_3",
            "GET_ZONE_NAMES_4",
            "GET_ZONE_NAMES_5",
            "GET_ZONE_NAMES_6",
            "GET_ZONE_NAMES_7",
        ]
        all_zone_data_hex = ""
        for cmd_key in zone_name_cmd_keys:
            spec = InimAlarmConstants.COMMAND_SPECS[cmd_key]
            response_data_hex_part = self._send_command_core(
                spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
            )
            if not response_data_hex_part:
                logger.error(f"Failed to get part of zone names for {cmd_key}")
                return None
            all_zone_data_hex += response_data_hex_part

        if all_zone_data_hex:
            try:
                # Response is an ASCII encoded HEX.
                # Each zone name takes 16 bytes.
                # The number of zone names to parse is now self.system_max_zones_to_fetch
                bytes_per_name = 16  # Each zone name is 16 bytes
                # The data for zone names is at the beginning of the concatenated response.
                # First 800 bytes (excluding checksums) are zones, for 50 zones.
                # So, we take data for self.system_max_zones_to_fetch * bytes_per_name
                zone_names_hex_data = all_zone_data_hex[
                    : self.system_max_zones * bytes_per_name * 2
                ]

                zone_names_bytes = binascii.unhexlify(zone_names_hex_data)
                names = []
                for i in range(0, len(zone_names_bytes), bytes_per_name):
                    name_bytes = zone_names_bytes[i : i + bytes_per_name]
                    names.append(name_bytes.decode("ascii", errors="ignore").strip())

                # The remaining part of all_zone_data_hex contains expansions names and free bytes.
                # TODO: Parse expansion names from remaining data if needed.
                return {"raw_hex_full": all_zone_data_hex, "zone_names": names}
            except Exception as e:
                logger.error(f"Error parsing zone names: {e}")
                return {"raw_hex_full": all_zone_data_hex, "error": str(e)}
        return None

    def get_zones_config(self):
        """
        Requests and parses detailed Zone Configuration from the alarm panel.

        This method sends a sequence of seven commands to retrieve comprehensive
        zone configuration data. The data is received in two main parts:
        - Part 1 (from 4 commands): Contains 9-byte configuration structures for 100 potential zones.
        - Part 2 (from 3 commands): Contains 12-byte configuration patterns for 50 potential items,
          which are assumed to correspond to the first 50 zones from Part 1.

        The method parses these data structures for up to `self.system_max_zones`
        (configurable at API initialization) and returns a unified dictionary of
        decoded data for each zone. Unknown/undecoded byte segments are not included
        in the parsed output per zone.
        """
        config_cmd_keys_part1 = [f"GET_ZONES_CONFIG_{i + 1}" for i in range(4)]
        config_cmd_keys_part2 = [f"GET_ZONES_CONFIG_{i + 1}" for i in range(4, 7)]

        all_config_data_hex_part1 = ""
        for cmd_key in config_cmd_keys_part1:
            spec = InimAlarmConstants.COMMAND_SPECS.get(cmd_key)
            if not spec:
                logger.error(
                    f"Command spec for {cmd_key} not found in InimAlarmConstants."
                )
                return None
            response_part = self._send_command_core(
                spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
            )
            if not response_part:
                logger.error(
                    f"Failed to retrieve configuration data (Part 1) for command {cmd_key}."
                )
                return None
            all_config_data_hex_part1 += response_part

        all_config_data_hex_part2 = ""
        for cmd_key in config_cmd_keys_part2:
            spec = InimAlarmConstants.COMMAND_SPECS.get(cmd_key)
            if not spec:
                logger.error(
                    f"Command spec for {cmd_key} not found in InimAlarmConstants."
                )
                return None
            response_part = self._send_command_core(
                spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
            )
            if not response_part:
                logger.error(
                    f"Failed to retrieve configuration data (Part 2) for command {cmd_key}."
                )
                return None
            all_config_data_hex_part2 += response_part

        zones_config_detailed = []

        for i in range(self.system_max_zones):
            zone_idx_0_based = i
            # Initialize a single dictionary for all parsed data for this zone
            zone_config_parsed = {"zone_index": zone_idx_0_based}
            # Optionally, store raw hex segments for debugging this specific zone's data
            # zone_config_parsed["_raw_config_part1_hex"] = "Data Missing or Not Applicable"
            # zone_config_parsed["_raw_config_part2_hex"] = "Data Missing or Not Applicable"

            # --- Parse Part 1 (9-byte structure) if zone_idx_0_based < 100 ---
            if (
                zone_idx_0_based < 100
            ):  # Data packet for Part 1 contains 100 zone entries
                bytes_per_zone_p1 = 9
                offset_p1 = zone_idx_0_based * bytes_per_zone_p1 * 2
                if offset_p1 + (bytes_per_zone_p1 * 2) <= len(
                    all_config_data_hex_part1
                ):
                    zone_data_p1_hex = all_config_data_hex_part1[
                        offset_p1 : offset_p1 + (bytes_per_zone_p1 * 2)
                    ]
                    # zone_config_parsed["_raw_config_part1_hex"] = zone_data_p1_hex # Optional raw hex for this zone

                    # Byte 1 & 2: Area Assignment
                    try:
                        b1_val = int(zone_data_p1_hex[0:2], 16)
                        b2_val = int(zone_data_p1_hex[2:4], 16)
                        area_mask = b1_val | (b2_val << 8)
                        assigned_areas = [
                            area_idx + 1
                            for area_idx in range(min(16, self.system_max_areas))
                            if (area_mask >> area_idx) & 1
                        ]
                        zone_config_parsed["assigned_areas"] = assigned_areas
                    except ValueError:
                        zone_config_parsed["assigned_areas"] = "Parse Error"  # Or []

                    # Byte 3: Activation Type
                    try:
                        act_val = int(zone_data_p1_hex[4:6], 16)
                        zone_config_parsed["activation_type_val"] = act_val
                        zone_config_parsed["activation_type_desc"] = (
                            InimAlarmConstants.ACTIVATION_TYPE_MAP.get(
                                act_val, f"Unknown (0x{act_val:02X})"
                            )
                        )
                    except ValueError:
                        zone_config_parsed["activation_type_desc"] = "Parse Error"

                    # Bytes 4-6 are undecoded, so they are skipped as per request.

                    # Byte 7: Duration/Sensibility (ms)
                    try:
                        zone_config_parsed["duration_ms"] = int(
                            zone_data_p1_hex[12:14], 16
                        )
                    except ValueError:
                        zone_config_parsed["duration_ms"] = "Parse Error"

                    # Byte 8: The time
                    time_val_hex = zone_data_p1_hex[14:16]
                    try:
                        time_val_int = int(time_val_hex, 16)
                        time_desc = f"Raw value 0x{time_val_int:02X}"
                        if 0 <= time_val_int <= 0x3B:  # 0 to 59 decimal means seconds
                            time_desc = f"{time_val_int} seconds"
                        elif time_val_int >= 0x80:
                            minutes_val = (
                                time_val_int
                                - 0x80
                                + (
                                    1
                                    if time_val_int > 0x80
                                    else (0 if time_val_int == 0x80 else 0)
                                )
                            )
                            if time_val_int == 0x80:
                                minutes_val = 0
                            time_desc = f"{minutes_val} minutes"
                        # zone_config_parsed["time_value_hex"] = time_val_hex # Not returning raw sub-values
                        zone_config_parsed["time"] = time_desc
                    except ValueError:
                        zone_config_parsed["time"] = "Parse Error"

                    # Byte 9: Number of pulses
                    try:
                        zone_config_parsed["pulses"] = int(zone_data_p1_hex[16:18], 16)
                    except ValueError:
                        zone_config_parsed["pulses"] = "Parse Error"
                # else: zone_config_parsed["_raw_config_part1_hex"] = "Data Missing in Packet" # If storing raw hex

            # --- Parse Part 2 (12-byte structure) if zone_idx_0_based < 50 ---
            if zone_idx_0_based < 50:  # Data packet for Part 2 contains 50 entries
                bytes_per_item_p2 = 12
                offset_p2 = zone_idx_0_based * bytes_per_item_p2 * 2
                if offset_p2 + (bytes_per_item_p2 * 2) <= len(
                    all_config_data_hex_part2
                ):
                    item_data_p2_hex = all_config_data_hex_part2[
                        offset_p2 : offset_p2 + (bytes_per_item_p2 * 2)
                    ]
                    # zone_config_parsed["_raw_config_part2_hex"] = item_data_p2_hex # Optional raw hex for this zone

                    # Bytes 1-5 are undecoded, skipped.

                    # Byte 6: Balancing type
                    try:
                        bal_val = int(item_data_p2_hex[10:12], 16)
                        zone_config_parsed["balancing_type_val"] = bal_val
                        zone_config_parsed["balancing_type_desc"] = (
                            InimAlarmConstants.BALANCING_TYPE_MAP.get(
                                bal_val, f"Unknown (0x{bal_val:02X})"
                            )
                        )
                    except ValueError:
                        zone_config_parsed["balancing_type_desc"] = "Parse Error"

                    # Byte 7: Sensor type (P2)
                    try:
                        s_type_val = int(item_data_p2_hex[12:14], 16)
                        zone_config_parsed["sensor_type_val"] = s_type_val
                        zone_config_parsed["sensor_type_desc"] = (
                            InimAlarmConstants.SENSOR_TYPE_P2_MAP.get(
                                s_type_val, f"Unknown (0x{s_type_val:02X})"
                            )
                        )
                    except ValueError:
                        zone_config_parsed["sensor_type_desc"] = "Parse Error"

                    # Bytes 8-12 are undecoded, skipped.
                # else: zone_config_parsed["_raw_config_part2_hex"] = "Data Missing in Packet" # If storing raw hex

            zones_config_detailed.append(
                zone_config_parsed
            )  # Add this zone's unified config

        return {
            # Raw hex for the entire blocks can be useful for debugging the source data
            "raw_hex_part1_full_response": all_config_data_hex_part1,
            "raw_hex_part2_full_response": all_config_data_hex_part2,
            "zones_config_detailed": zones_config_detailed,  # List of unified parsed configurations
        }

    def get_scenarios(self):  # Names
        """Requests Scenario Names. Two commands involved."""
        scenario_name_cmd_keys = ["GET_SCENARIO_NAMES_1", "GET_SCENARIO_NAMES_2"]
        all_scenario_data_hex = ""
        for cmd_key in scenario_name_cmd_keys:
            spec = InimAlarmConstants.COMMAND_SPECS[cmd_key]
            response_data_hex_part = self._send_command_core(
                spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
            )
            if not response_data_hex_part:
                logger.error(f"Failed to get part of scenario names for {cmd_key}")
                return None
            all_scenario_data_hex += response_data_hex_part

        if all_scenario_data_hex:
            try:
                # Response is ASCII encoded HEX.
                # 30 scenarios, each name 16 bytes.
                scenario_names_bytes = binascii.unhexlify(all_scenario_data_hex)
                names = []
                bytes_per_name = 16
                num_scenarios = 30
                for i in range(
                    0,
                    min(num_scenarios * bytes_per_name, len(scenario_names_bytes)),
                    bytes_per_name,
                ):
                    name_bytes = scenario_names_bytes[i : i + bytes_per_name]
                    names.append(name_bytes.decode("ascii", errors="ignore").strip())
                return {"raw_hex": all_scenario_data_hex, "names": names}
            except Exception as e:
                logger.error(f"Error parsing scenario names: {e}")
                return {"raw_hex": all_scenario_data_hex, "error": str(e)}
        return None

    def get_scenario_activations(self):
        """
        Requests Scenario Activations, detailing what areas are armed/disarmed by each scenario.
        Returns a list of activation details for all 30 supported scenarios.
        """
        spec = InimAlarmConstants.COMMAND_SPECS.get("GET_SCENARIO_ACTIVATIONS")
        if not spec:
            logger.error("Command spec for GET_SCENARIO_ACTIVATIONS not found.")
            return None

        # Send the command and get the response data (checksum stripped by _send_command_core)
        # Expected response length is 241 bytes (240 data + 1 chk).
        response_data_hex = self._send_command_core(
            spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
        )

        if not response_data_hex:
            logger.error("No response or error when fetching scenario activations.")
            return None

        # Expected 240 data bytes (480 hex characters)
        if len(response_data_hex) != 240 * 2:
            logger.error(
                f"get_scenario_activations: Expected 240 data bytes (480 hex chars), "
                f"got {len(response_data_hex) // 2} bytes."
            )
            return None  # Or return partial data if applicable / preferred

        all_scenario_details = []
        bytes_per_scenario_activation = 8

        action_map = {
            InimAlarmConstants.AREA_ACTION_ARM: "arm",  # '1'
            InimAlarmConstants.AREA_ACTION_DISARM: "disarm",  # '4'
            InimAlarmConstants.AREA_ACTION_KEEP_STATUS: "keep",  # '0'
        }

        for i in range(InimAlarmConstants.DEFAULT_SYSTEM_MAX_SCENARIOS):
            offset = i * bytes_per_scenario_activation * 2  # *2 for hex characters
            scenario_activation_hex = response_data_hex[
                offset : offset + (bytes_per_scenario_activation * 2)
            ]

            if len(scenario_activation_hex) != bytes_per_scenario_activation * 2:
                logger.warning(
                    f"Scenario {i}: Not enough data to parse activation. Got: {scenario_activation_hex}",
                )
                all_scenario_details.append(
                    {
                        "scenario_index": i,
                        "raw_hex": scenario_activation_hex,
                        "error": "Insufficient data for this scenario block",
                    }
                )
                continue

            # First 5 bytes (10 hex chars) define area actions
            area_action_hex_part = scenario_activation_hex[:10]
            # Last 3 bytes (6 hex chars) are unknown
            unknown_part_hex = scenario_activation_hex[10:]

            parsed_area_actions = {}
            # Iterate through the 5 bytes representing area actions
            for byte_idx in range(5):  # Covers up to 10 areas
                byte_val_hex = area_action_hex_part[byte_idx * 2 : byte_idx * 2 + 2]

                # Protocol: "Within each byte, the areas are represented inverted."
                # Byte 1 (idx 0): Area 2 (MSN) - Area 1 (LSN)
                # ...
                # Byte 5 (idx 4): Area 10 (MSN) - Area 9 (LSN)
                area_num_in_msn_slot = (
                    byte_idx + 1
                ) * 2  # Higher area number in the pair
                area_num_in_lsn_slot = (
                    byte_idx + 1
                ) * 2 - 1  # Lower area number in the pair

                action_char_for_msn_slot = byte_val_hex[0]  # Left nibble
                action_char_for_lsn_slot = byte_val_hex[1]  # Right nibble

                # Process LSN slot (e.g., Area 1, 3, 5, 7, 9)
                if area_num_in_lsn_slot <= self.system_max_areas:
                    parsed_area_actions[area_num_in_lsn_slot] = action_map.get(
                        action_char_for_lsn_slot, "unknown"
                    )

                # Process MSN slot (e.g., Area 2, 4, 6, 8, 10)
                if area_num_in_msn_slot <= self.system_max_areas:
                    parsed_area_actions[area_num_in_msn_slot] = action_map.get(
                        action_char_for_msn_slot, "unknown"
                    )

            all_scenario_details.append(
                {
                    "scenario_index": i,  # 0-indexed
                    "raw_activation_hex": scenario_activation_hex,
                    "area_actions": parsed_area_actions,
                    "unknown_trailing_bytes_hex": unknown_part_hex,
                }
            )

        return all_scenario_details

    def get_keyboard_names(self) -> dict[str, Any] | None:
        """Requests Keyboard Names."""
        spec = InimAlarmConstants.COMMAND_SPECS["GET_KEYBOARD_NAMES"]
        response_data_hex = self._send_command_core(
            spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
        )
        if response_data_hex:
            try:
                # Response is ASCII encoded HEX, each device name is 16 bytes.
                names_raw = binascii.unhexlify(response_data_hex)
                names = [
                    names_raw[i : i + 16].decode("ascii", errors="ignore").strip()
                    for i in range(0, len(names_raw), 16)
                ]
                return {"raw_hex": response_data_hex, "names": names}
            except Exception as e:
                logger.error(f"Error parsing keyboard names: {e}")
                return {"raw_hex": response_data_hex, "error": str(e)}
        return None

    def check_scenario_activation_allowed(self, scenario_number_0_indexed):
        """
        Checks if a specific scenario can be activated.
        Args:
            scenario_number_0_indexed (int): The 0-indexed scenario number (e.g., 0 for the first scenario).
        Returns:
            bool: True if the scenario activation is allowed, False otherwise or if an error occurs.
        """
        spec_info = InimAlarmConstants.COMMAND_SPECS.get(
            "CHECK_SCENARIO_ACTIVATION_ALLOWED_INFO"
        )
        if not spec_info:
            logger.error(
                "Command spec for CHECK_SCENARIO_ACTIVATION_ALLOWED_INFO not found."
            )
            return False

        # Validate scenario number
        if not (
            0
            <= scenario_number_0_indexed
            < InimAlarmConstants.DEFAULT_SYSTEM_MAX_SCENARIOS
        ):
            logger.error(
                f"Invalid scenario number: {scenario_number_0_indexed}. Must be 0-{InimAlarmConstants.DEFAULT_SYSTEM_MAX_SCENARIOS - 1}."
            )
            return False

        # Construct the scenario byte for the command: starts from 0x80 for scenario 0.
        scenario_command_byte_val = 0x80 + scenario_number_0_indexed
        scenario_command_byte_hex = format(scenario_command_byte_val, "02x")

        # Construct the 7-byte command part before its checksum
        seven_byte_cmd_hex = (
            spec_info["cmd_prefix"]
            + scenario_command_byte_hex
            + spec_info["cmd_suffix"]
        )

        # Calculate the checksum for this 7-byte command
        cmd_checksum = self.calculate_checksum(seven_byte_cmd_hex)
        eight_byte_cmd_with_checksum = seven_byte_cmd_hex + cmd_checksum

        # Send the command and get the response data (checksum stripped)
        # Expected response length is 14 bytes (13 data + 1 chk)
        response_data_hex = self._send_command_core(
            eight_byte_cmd_with_checksum,
            expect_specific_response_len=spec_info["resp_len"],
        )

        if response_data_hex is None:
            logger.error(
                f"No response or error for check_scenario_activation_allowed for scenario {scenario_number_0_indexed}."
            )
            return False  # Error in communication or validation

        # A positive response (all zones clear, scenario can be activated) is all zeros for the data part.
        # The response_data_hex from _send_command_core is 13 bytes (26 hex chars).
        expected_positive_response_data = "00" * 13  # 13 bytes of zeros

        if response_data_hex == expected_positive_response_data:
            logger.info(f"Scenario {scenario_number_0_indexed} activation is allowed.")
            return True
        else:
            logger.info(
                f"Scenario {scenario_number_0_indexed} activation is NOT allowed. Response data: {response_data_hex}"
            )
            return False

    # --- Status ---
    def get_areas_status(self):
        """Requests Areas Status."""
        spec = InimAlarmConstants.COMMAND_SPECS["GET_AREAS_STATUS"]
        response_data_hex = self._send_command_core(
            spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
        )  # Response 17 bytes
        if response_data_hex:
            status_info = {
                "raw_hex": response_data_hex,
                "area_statuses": {},
                "triggered_areas": [],
            }
            # First 5 bytes represent area status. Each hex digit is an area, inverted within byte.
            area_status_hex = response_data_hex[:10]
            statuses = {}
            for byte_idx in range(5):  # Covers up to 10 areas
                byte_val_hex = area_status_hex[byte_idx * 2 : byte_idx * 2 + 2]
                # Byte N: Area (N*2) - Area (N*2-1)
                area_in_msn = (byte_idx + 1) * 2  # e.g., byte_idx 0 -> Area 2
                area_in_lsn = (byte_idx + 1) * 2 - 1  # e.g., byte_idx 0 -> Area 1

                status_char_msn = byte_val_hex[
                    0
                ]  # Left nibble, represents higher area number in pair
                status_char_lsn = byte_val_hex[
                    1
                ]  # Right nibble, represents lower area number in pair

                if area_in_lsn <= self.system_max_areas:
                    statuses[area_in_lsn] = (
                        "armed"
                        if status_char_lsn == InimAlarmConstants.AREA_STATUS_ARMED
                        else "disarmed"
                        if status_char_lsn == InimAlarmConstants.AREA_STATUS_DISARMED
                        else "unknown"
                    )
                if area_in_msn <= self.system_max_areas:
                    statuses[area_in_msn] = (
                        "armed"
                        if status_char_msn == InimAlarmConstants.AREA_STATUS_ARMED
                        else "disarmed"
                        if status_char_msn == InimAlarmConstants.AREA_STATUS_DISARMED
                        else "unknown"
                    )
            status_info["area_statuses"] = statuses

            # 11th byte (index 10 of data, hex chars 20-21) tells if an area has been triggered.
            if len(response_data_hex) >= 22:
                triggered_byte_hex = response_data_hex[
                    20:22
                ]  # This is the 11th byte of the data part
                triggered_byte_val = int(triggered_byte_hex, 16)
                triggered_areas = []
                if triggered_byte_val != 0:  # 00 means no areas have alarms in memory
                    # Value is power of 2 of the area number (counting from zero)
                    for i in range(
                        self.system_max_areas
                    ):  # Check up to system max areas (e.g. 8 or 10)
                        if (triggered_byte_val >> i) & 1:  # Area N is 2^(N-1)
                            triggered_areas.append(i + 1)  # Area number is 1-indexed
                status_info["triggered_areas"] = triggered_areas
                # TODO: Decode triggered status for areas > 8
            return status_info
        return None

    def get_active_scenario(self):
        """Requests the currently active scenario."""
        spec = InimAlarmConstants.COMMAND_SPECS["GET_ACTIVE_SCENARIO"]
        response_data_hex = self._send_command_core(
            spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
        )  # Response 2 bytes (1 data byte + 1 chk)
        if response_data_hex:
            # First byte tells active scenario number (counting from 00).
            active_scenario_num = int(response_data_hex, 16)
            return {
                "raw_hex": response_data_hex,
                "active_scenario_number": active_scenario_num,
            }
        return None

    def get_zones_status(self):
        """
        Requests Zone Status from the alarm panel.
        The response contains status data potentially for up to 100 zones.
        This method populates statuses up to self.system_max_zone_names_to_fetch,
        and the returned zone_statuses dictionary will be ordered by zone number.
        """
        spec = InimAlarmConstants.COMMAND_SPECS["GET_ZONES_STATUS"]
        response_data_hex = self._send_command_core(
            spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
        )

        if not response_data_hex:
            return None

        if len(response_data_hex) != 26 * 2:  # 26 data bytes = 52 hex characters
            logger.error(
                f"get_zones_status: Expected 26 data bytes (52 hex chars), got {len(response_data_hex) // 2} bytes."
            )
            return {
                "raw_hex_data": response_data_hex,
                "error": "Unexpected response data length",
                "zone_statuses": {},
                "unknown_byte_after_status_data": "",
            }

        zone_status_block_hex = response_data_hex[
            : 25 * 2
        ]  # First 25 bytes (50 hex digits) for zone statuses
        unknown_byte_hex = response_data_hex[25 * 2 :]  # The 26th byte of data

        # Intermediate dictionary to store parsed statuses (insertion order might be jumbled)
        parsed_zone_statuses_temp = {}

        # Parse the 50 hex digits for zone statuses
        # Each digit represents the status of 2 zones.
        # Order: Digit 1 -> Zones 3&4, Digit 2 -> Zones 1&2, etc.
        for digit_index in range(50):  # 50 hex digits from D0 to D49
            hex_digit_char = zone_status_block_hex[digit_index]

            m = digit_index // 2

            zone1_num = -1
            zone2_num = -1

            if (
                digit_index % 2 == 0
            ):  # Even index: D0, D2, D4... (Corresponds to "Digit 1", "Digit 3", ...)
                zone1_num = (m * 4) + 3  # "First zone" in the pair for this digit
                zone2_num = (m * 4) + 4  # "Second zone" in the pair for this digit
            else:  # Odd index: D1, D3, D5... (Corresponds to PDF's "Digit 2", "Digit 4", ...)
                zone1_num = (m * 4) + 1  # "First zone" in the pair for this digit
                zone2_num = (m * 4) + 2  # "Second zone" in the pair for this digit

            status_zone1_str = "unknown"
            status_zone2_str = "unknown"

            if hex_digit_char == "5":  # Both zones clear
                status_zone1_str = "clear"
                status_zone2_str = "clear"
            elif hex_digit_char == "6":  # First zone alarmed, second clear
                status_zone1_str = "alarmed"
                status_zone2_str = "clear"
            elif hex_digit_char == "9":  # First zone clear, second alarmed
                status_zone1_str = "clear"
                status_zone2_str = "alarmed"
            elif hex_digit_char == "a":  # Both zones alarmed
                status_zone1_str = "alarmed"
                status_zone2_str = "alarmed"
            else:
                logger.warning(
                    f"get_zones_status: Unknown status digit '{hex_digit_char}' "
                    f"at digit_index {digit_index} for potential zone pair ({zone1_num}, {zone2_num})."
                )

            # Store status in the temporary dictionary if zone numbers are valid
            if 1 <= zone1_num <= 100:  # Data packet covers up to 100 zones
                parsed_zone_statuses_temp[zone1_num] = status_zone1_str

            if 1 <= zone2_num <= 100:  # Data packet covers up to 100 zones
                parsed_zone_statuses_temp[zone2_num] = status_zone2_str

        # Create the final ordered dictionary for zones up to system_max_zones
        ordered_zone_statuses = {}
        for i in range(1, self.system_max_zones + 1):
            if i in parsed_zone_statuses_temp:
                ordered_zone_statuses[i] = parsed_zone_statuses_temp[i]
            # else:
            # If a zone within the range [1, system_max_zones] was not found
            # in the parsed data (e.g. if data only covered fewer zones than expected, though unlikely for this command),
            # it simply won't be included. Or, you could add it with a default "unknown" status if desired.
            # For now, only include what was successfully parsed and falls within the limit.

        return {
            "raw_hex_data": response_data_hex,
            "zone_statuses": ordered_zone_statuses,  # Return the numerically ordered dictionary
            "unknown_byte_after_status_data": unknown_byte_hex,
        }

    # --- Events ---
    def _get_raw_next_event_pointer(self) -> str | None:
        """
        Fetches the "next event index/number" pointer from the panel.
        This pointer is for extended events (X_val) and needs conversion for compact events.
        Returns:
            str | None: The 2-byte little-endian hex string (e.g., "01d4" for X_val=0x01D4) or None on failure.
        """
        c = InimAlarmConstants
        spec = c.COMMAND_SPECS.get("GET_NEXT_EVENT_POINTER_CMD_INFO")
        if not spec:
            logger.error("Command spec for GET_NEXT_EVENT_POINTER_CMD_INFO not found.")
            return None

        response_data_hex = self._send_command_core(
            spec["cmd_full"], expect_specific_response_len=spec["resp_len"]
        )

        if (
            not response_data_hex or len(response_data_hex) < 4
        ):  # Expect at least 2 data bytes (4 hex chars)
            logger.error(
                "Failed to get next event pointer or response too short. Got: %s",
                response_data_hex,
            )
            return None

        byte1_hex = response_data_hex[0:2]
        byte2_hex = response_data_hex[2:4]
        extended_event_pointer_hex_val_str = byte2_hex + byte1_hex
        logger.debug(
            "Raw next event pointer response data: %s, Extracted Hex Value: %s",
            response_data_hex,
            extended_event_pointer_hex_val_str,
        )
        return extended_event_pointer_hex_val_str

    def _fetch_one_compact_event(
        self, index_byte4_hex: str, index_byte5_hex: str
    ) -> dict[str, Any] | None:
        """Fetches and parses a single compact event based on its 2-byte index parts for the command."""
        c = InimAlarmConstants
        spec = c.COMMAND_SPECS.get("GET_COMPACT_EVENT_CMD_INFO")
        if not spec:
            logger.error("Command spec for GET_COMPACT_EVENT_CMD_INFO not found.")
            return None

        seven_byte_cmd_part_hex = (
            spec["cmd_base_prefix"]
            + index_byte4_hex
            + index_byte5_hex
            + spec["cmd_base_suffix"]
        )
        cmd_checksum = self.calculate_checksum(seven_byte_cmd_part_hex)
        full_command_hex = seven_byte_cmd_part_hex + cmd_checksum

        response_data_hex = self._send_command_core(
            full_command_hex, expect_specific_response_len=spec["resp_len"]
        )

        if not response_data_hex or len(response_data_hex) != 9 * 2:
            logger.error(
                "Failed to fetch compact event or unexpected response length for index %s%s. Got: %s",
                index_byte4_hex,
                index_byte5_hex,
                response_data_hex,
            )
            return None

        try:
            event_data: dict[str, Any] = {"raw_hex_data": response_data_hex}
            ts_hex_le = response_data_hex[0:8]
            try:
                ts_bytes = binascii.unhexlify(ts_hex_le)
                num_seconds = int.from_bytes(ts_bytes, "little")
                gmt_plus_1 = timezone(timedelta(hours=1))
                epoch_dt = datetime(2000, 1, 1, 0, 0, 0, tzinfo=gmt_plus_1)
                event_dt = epoch_dt + timedelta(seconds=num_seconds)
                event_data["timestamp_iso"] = event_dt.isoformat()
            except Exception as e:
                logger.warning(
                    "Could not parse timestamp for event %s%s: %s",
                    index_byte4_hex,
                    index_byte5_hex,
                    e,
                )
                event_data["timestamp_iso"] = "Parse Error"

            details_hex = response_data_hex[8:18]
            d1_val, d2_val, d3_val, d4_val, action_code = (
                int(details_hex[0:2], 16),
                int(details_hex[2:4], 16),
                int(details_hex[4:6], 16),
                int(details_hex[6:8], 16),
                int(details_hex[8:10], 16),
            )
            event_data["action_code"] = action_code
            event_data["action_description"] = c.EVENT_ACTION_CODES_COMPACT.get(
                action_code, f"Unknown Action (0x{action_code:02X})"
            )

            if action_code in [0x8A, 0x8C, 0x8D, 0x0A, 0x0C, 0x95, 0x96]:
                area_mask_val = d1_val | (d2_val << 8)
                event_data["affected_areas"] = [
                    area_idx + 1
                    for area_idx in range(self.system_max_areas)
                    if (area_mask_val >> area_idx) & 1
                ]
                event_data["device_id_hex"] = format(d4_val, "02x")
                event_data["authorized_id_hex"] = format(d3_val, "02x")
            elif action_code == 0x9E:
                event_data["scenario_number_0_indexed"] = d3_val
            elif action_code in [0x80, 0x00]:
                event_data["area_number_1_indexed"] = d1_val
                event_data["zone_number_0_indexed"] = d3_val
                event_data["zone_number_1_indexed_for_display"] = d3_val + 1

            event_data["details_byte1_area_lsb_or_direct_area"] = format(d1_val, "02x")
            event_data["details_byte2_area_msb"] = format(d2_val, "02x")
            event_data["details_byte3_scenario_or_zone"] = format(d3_val, "02x")
            event_data["details_byte4_device_id"] = format(d4_val, "02x")
            event_data["details_byte5_action_code"] = format(action_code, "02x")

            return event_data
        except ValueError as ve:
            logger.error(
                "ValueError parsing compact event data for index %s%s: %s. Data: %s",
                index_byte4_hex,
                index_byte5_hex,
                ve,
                response_data_hex,
            )
            return {
                "raw_hex_data": response_data_hex,
                "error": f"Value parse error: {ve}",
            }
        except Exception as e:
            logger.error(
                "Unexpected error parsing compact event for index %s%s: %s",
                index_byte4_hex,
                index_byte5_hex,
                e,
            )
            return {
                "raw_hex_data": response_data_hex,
                "error": f"General parse error: {e}",
            }

    def get_compact_events(
        self,
        count: int | None = None,
        last_processed_compact_event_index_val: int | None = None,
    ) -> dict[str, Any]:
        """
        Retrieves a sequence of compact events from the alarm panel.

        Requires EITHER 'count' OR 'last_processed_compact_event_index_val'.
        If 'count' is provided, it takes precedence for determining number of events to fetch.
        """
        c = InimAlarmConstants
        events_retrieved: list[dict[str, Any]] = []

        y_current_most_recent_compact_val: int | None = None
        raw_extended_pointer_hex = self._get_raw_next_event_pointer()
        if not raw_extended_pointer_hex:
            logger.error(
                "Failed to get raw extended event pointer. Cannot determine current event head."
            )
            return {"events": [], "latest_event_index_val": None}
        try:
            x_val_extended_next = int(raw_extended_pointer_hex, 16)
            x_val_extended_most_recent = x_val_extended_next - 1
            y_current_most_recent_compact_val = (
                (x_val_extended_most_recent * 9) + 0x9204
            ) & 0xFFFF
            logger.info(
                "Panel's current most recent compact event index (Y_val): 0x%04x",
                y_current_most_recent_compact_val,
            )
        except ValueError:
            logger.error(
                "Error converting extended event pointer to int. Cannot determine current event head."
            )
            return {"events": [], "latest_event_index_val": None}

        actual_count_to_iterate: int
        start_fetching_from_index_val: int = y_current_most_recent_compact_val

        if count is not None:
            if not (0 < count <= c.MAX_EVENTS_PER_FETCH):
                logger.warning(
                    "Requested event count %s is out of range (1-%s). Clamping.",
                    count,
                    c.MAX_EVENTS_PER_FETCH,
                )
                actual_count_to_iterate = min(max(1, count), c.MAX_EVENTS_PER_FETCH)
            else:
                actual_count_to_iterate = count
            logger.info(
                "Fetching %s most recent compact events as per 'count' parameter.",
                actual_count_to_iterate,
            )
        elif last_processed_compact_event_index_val is not None:
            if (
                y_current_most_recent_compact_val
                <= last_processed_compact_event_index_val
            ):
                logger.info(
                    "No new compact events. Current head (0x%04x) not newer than last processed (0x%04x).",
                    y_current_most_recent_compact_val,
                    last_processed_compact_event_index_val,
                )
                return {
                    "events": [],
                    "latest_event_index_val": y_current_most_recent_compact_val,
                }
            actual_count_to_iterate = c.MAX_EVENTS_PER_FETCH
            logger.info(
                "Fetching new events since 0x%04x. Current head is 0x%04x. Will fetch up to %s events.",
                last_processed_compact_event_index_val,
                y_current_most_recent_compact_val,
                actual_count_to_iterate,
            )
        else:
            logger.error(
                "Invalid call: 'count' or 'last_processed_compact_event_index_val' must be provided to get_compact_events."
            )
            return {
                "events": [],
                "latest_event_index_val": y_current_most_recent_compact_val,
            }

        current_loop_index_val = start_fetching_from_index_val
        for i in range(actual_count_to_iterate):
            if current_loop_index_val < 0:
                logger.warning(
                    "Event index for query became negative (0x%04x). Stopping.",
                    current_loop_index_val,
                )
                break

            if count is None and last_processed_compact_event_index_val is not None:
                if current_loop_index_val <= last_processed_compact_event_index_val:
                    logger.debug(
                        "Incremental fetch: Reached or passed last known event index (current_loop: 0x%04x, last_processed: 0x%04x). Stopping.",
                        current_loop_index_val,
                        last_processed_compact_event_index_val,
                    )
                    break

            index_b4_hex = format((current_loop_index_val >> 8) & 0xFF, "02x")
            index_b5_hex = format(current_loop_index_val & 0xFF, "02x")

            logger.debug(
                "Fetching compact event with index bytes: %s %s (Iteration %s, Full Index Val: 0x%04x)",
                index_b4_hex,
                index_b5_hex,
                i,
                current_loop_index_val,
            )
            event = self._fetch_one_compact_event(index_b4_hex, index_b5_hex)

            if event:
                event["query_index_hex"] = index_b4_hex + index_b5_hex
                event["query_index_val"] = current_loop_index_val
                events_retrieved.append(event)
                if "error" in event:
                    logger.warning(
                        "Error in parsed event at index %s%s: %s. Continuing...",
                        index_b4_hex,
                        index_b5_hex,
                        event["error"],
                    )
            else:
                logger.info(
                    "Failed to retrieve event at index %s%s or end of log. Stopping.",
                    index_b4_hex,
                    index_b5_hex,
                )
                break

            current_loop_index_val -= 9
            if i < actual_count_to_iterate - 1:
                time.sleep(0.05)

        logger.info("Retrieved %s compact events in this batch.", len(events_retrieved))
        return {
            "events": events_retrieved,
            "latest_event_index_val": y_current_most_recent_compact_val,
        }

    # --- Actions ---
    def _construct_area_action_payload(self, areas_to_arm=None, areas_to_disarm=None):
        """
        Constructs the 8-byte HEX payload for arming/disarming areas.
        Args:
            areas_to_arm (list[int], optional): List of area numbers (1-indexed) to arm.
            areas_to_disarm (list[int], optional): List of area numbers (1-indexed) to disarm.
        Returns:
            str: 8-byte (16 hex chars) area action payload.
        """
        areas_to_arm = areas_to_arm or []
        areas_to_disarm = areas_to_disarm or []

        # Payload is 8 bytes, representing up to 16 areas.
        # Each hex digit represents an area's requested status.
        # Within each byte, areas are represented inverted (e.g. Byte 1 -> Area2-Area1).
        payload_nibbles = [InimAlarmConstants.AREA_ACTION_KEEP_STATUS] * (
            InimAlarmConstants.MAX_AREAS_PAYLOAD
        )

        for area_num_1_indexed in range(1, InimAlarmConstants.MAX_AREAS_PAYLOAD + 1):
            action = (
                InimAlarmConstants.AREA_ACTION_KEEP_STATUS
            )  # Default: Keep current status
            if area_num_1_indexed in areas_to_arm:
                action = InimAlarmConstants.AREA_ACTION_ARM  # 1 -> Arm Area
            elif area_num_1_indexed in areas_to_disarm:
                action = InimAlarmConstants.AREA_ACTION_DISARM  # 4 -> Disarm Area

            byte_index_for_area = (area_num_1_indexed - 1) // 2
            is_msn_in_byte_representation = (
                area_num_1_indexed % 2 == 0
            )  # Area 2,4,6... are MSN

            # Determine position in the 16-nibble string based on inverted representation
            if (
                is_msn_in_byte_representation
            ):  # Area is the one on the left in "Area2-Area1" -> MSN of output byte
                nibble_string_index = byte_index_for_area * 2
            else:  # Area is the one on the right in "Area2-Area1" -> LSN of output byte
                nibble_string_index = byte_index_for_area * 2 + 1

            if nibble_string_index < len(payload_nibbles):
                payload_nibbles[nibble_string_index] = action

        # The payload for areas should be exactly 8 bytes (16 hex chars)
        # If fewer than MAX_AREAS_PAYLOAD are used, remaining should be '0' (Keep status)
        final_payload_string = "".join(payload_nibbles)
        return final_payload_string[
            : InimAlarmConstants.MAX_AREAS_PAYLOAD * 2 // 2 * 2
        ]  # Ensure 8 bytes

    def arm_disarm_areas(self, areas_to_arm=None, areas_to_disarm=None):
        """
        Arms or disarms specified areas.
        Command is 8 bytes (base command + its checksum) + 14 bytes payload.
        Payload is PIN (6 bytes) + areas arm status (8 bytes).
        """
        spec_info = InimAlarmConstants.COMMAND_SPECS["ARM_DISARM_AREAS_CMD_INFO"]
        # spec_info["cmd_full"] is the 8-byte base command part ("0100002006000e35")

        area_action_payload_hex = self._construct_area_action_payload(
            areas_to_arm, areas_to_disarm
        )  # 8 bytes]
        full_payload_hex = (
            self.pin_hex + area_action_payload_hex
        )  # 6 bytes PIN + 8 bytes areas = 14 bytes

        command_to_send_hex = spec_info["cmd_full"] + full_payload_hex

        try:
            self._send_raw_command(command_to_send_hex)

            # System confirms by responding with the checksum of the payload (PIN + areas).
            # Response is 1 byte (2 hex chars).
            response_hex_full = self._read_raw_response(
                buffer_size=spec_info["resp_len"]
            )  # Expect 1 byte

            if len(response_hex_full) == 2:  # 1 byte = 2 hex chars
                expected_payload_checksum = self.calculate_checksum(full_payload_hex)
                if response_hex_full.lower() == expected_payload_checksum.lower():
                    logger.info(
                        f"Arm/Disarm successful. Armed: {areas_to_arm}, Disarmed: {areas_to_disarm}"
                    )
                    return True
                else:
                    logger.error(
                        f"Arm/Disarm failed: Incorrect response checksum. Expected {expected_payload_checksum}, Got {response_hex_full} for payload {full_payload_hex}"
                    )
                    return False
            else:
                logger.error(
                    f"Arm/Disarm failed: Unexpected response length. Got {response_hex_full}"
                )
                return False
        except (ConnectionError, TimeoutError, ValueError) as e:
            logger.error(f"Arm/Disarm areas operation failed: {e}")
            return False

    def activate_scenario(self, scenario_number):  # 0-indexed for payload
        """
        Activates a scenario.
        Command is 8 bytes + 8 bytes payload (actually 7 in example).
        Payload: PIN (6 bytes) + scenario number (1 byte).
        """
        spec_info = InimAlarmConstants.COMMAND_SPECS["ACTIVATE_SCENARIO_CMD_INFO"]
        # spec_info["cmd_full"] is "010000200a000732"

        # Scenario number is 0-indexed for payload
        if not (
            0 <= scenario_number < 30
        ):  # Assuming max 30 scenarios based on name requests
            logger.error(
                f"Invalid scenario number: {scenario_number}. Must be 0-29 (example uses 00 for scenario 1)."
            )
            return False

        scenario_byte_hex = format(scenario_number, "02x")
        # Payload: PIN (6 bytes) + scenario number (1 byte) = 7 bytes
        full_payload_hex = self.pin_hex + scenario_byte_hex

        command_to_send_hex = spec_info["cmd_full"] + full_payload_hex

        try:
            self._send_raw_command(command_to_send_hex)

            # PDF does not explicitly state success response for activate_scenario.
            # Assuming similar to arm/disarm: response is checksum of payload.
            response_hex_full = self._read_raw_response(
                buffer_size=spec_info["resp_len"]
            )  # Expect 1 byte

            if len(response_hex_full) == 2:  # Assuming 1 byte response
                expected_payload_checksum = self.calculate_checksum(full_payload_hex)
                if response_hex_full.lower() == expected_payload_checksum.lower():
                    logger.info(
                        f"Activate scenario {scenario_number} likely successful."
                    )
                    return True
                else:
                    logger.warning(
                        f"Activate scenario {scenario_number}: Response {response_hex_full} did not match expected payload checksum {expected_payload_checksum}. Operation status uncertain."
                    )
                    return False  # More conservative: if not matching expected, treat as fail or uncertain.
            else:
                logger.warning(
                    f"Activate scenario {scenario_number}: Unexpected response length {len(response_hex_full)}. Status uncertain."
                )
                return False
        except (ConnectionError, TimeoutError, ValueError) as e:
            logger.error(f"Activate scenario operation failed: {e}")
            return False

    # The following methods are supposed to be used by the integration invoking the API
    # as they handle connection and disconnection

    def get_initial_panel_configuration(self):
        """Connects, retrieves a comprehensive set of initial configuration data, then disconnects."""
        with self._api_lock:
            logger.debug("Lock acquired for get_initial_panel_configuration")
            if not self.connect():
                logger.error("Failed to connect for initial config.")
                return None
            cfg: dict[str, Any] = {"errors": []}
            try:
                # Grouped methods for fetching
                methods_to_call = [
                    ("system_info", self.get_system_info),
                    ("areas", self.get_areas),
                    ("zones", self.get_zones),
                    ("zones_config", self.get_zones_config),
                    ("scenarios", self.get_scenarios),
                    ("scenario_activations", self.get_scenario_activations),
                    (
                        "keyboard_names",
                        self.get_keyboard_names,
                    ),
                ]
                for key, method in methods_to_call:
                    logger.debug("Fetching %s...", key)
                    cfg[key] = method()
                    if cfg[key] is None:
                        cfg["errors"].append(f"Failed {key}.")
                    time.sleep(0.1)  # Small delay between commands
            except Exception as e:
                logger.error(f"Exception during initial config fetch: {e}")
                cfg["errors"].append(f"Overall exc: {str(e)}")
            finally:
                self.disconnect()

            if cfg["errors"]:
                logger.warning(
                    f"Initial config retrieval completed with errors: {cfg['errors']}"
                )
            else:
                logger.info(
                    "Initial panel configuration retrieval completed successfully."
                )
            return cfg

    def get_live_status_snapshot(self):
        """
        Connects, fetches current area status, zone status, and active scenario, then disconnects.
        Returns a dictionary with status information or an error indication.
        """

        with self._api_lock:  # Acquire the lock for the duration of this operation
            logger.debug("Lock acquired for get_live_status_snapshot")

            if not self.connect():
                logger.error("Failed to connect to panel for live status snapshot.")
                return {
                    "error": "Connection failure",
                    "areas_status": None,
                    "zones_status": None,
                    "active_scenario": None,
                }

            live_status = {
                "areas_status": None,
                "zones_status": None,
                "active_scenario": None,
                "errors": [],
            }

            try:
                logger.debug("Fetching live areas status...")
                live_status["areas_status"] = self.get_areas_status()
                if live_status["areas_status"] is None:
                    live_status["errors"].append("Failed to get areas status.")
                time.sleep(0.1)  # Consistent small delay

                logger.debug("Fetching live zones status...")
                live_status["zones_status"] = self.get_zones_status()
                if live_status["zones_status"] is None:
                    live_status["errors"].append("Failed to get zones status.")
                time.sleep(0.1)

                logger.debug("Fetching live active scenario...")
                live_status["active_scenario"] = self.get_active_scenario()
                if live_status["active_scenario"] is None:
                    live_status["errors"].append("Failed to get active scenario.")

            except Exception as e:
                logger.error(f"Exception during get_live_status_snapshot: {e}")
                live_status["errors"].append(f"Exception: {str(e)}")
            finally:
                self.disconnect()

            return live_status

    def execute_arm_disarm_areas(self, areas_to_arm=None, areas_to_disarm=None):
        """Connects, performs arm/disarm operation, then disconnects. Returns success status."""

        with self._api_lock:  # Acquire the lock for the duration of this operation
            logger.debug("Lock acquired for execute_arm_disarm_areas")

            if not self.connect():
                logger.error("Failed to connect to panel for arm/disarm operation.")
                return False

            success = False
            try:
                success = self.arm_disarm_areas(
                    areas_to_arm=areas_to_arm, areas_to_disarm=areas_to_disarm
                )
            except Exception as e:
                logger.error(f"Exception during execute_arm_disarm_areas: {e}")
                success = False  # Ensure success is false on exception
            finally:
                self.disconnect()
            return success

    def execute_activate_scenario(self, scenario_number_0_indexed):
        """
        Connects, checks if scenario activation is allowed, activates the scenario
        if allowed, then disconnects.
        Returns:
            bool: True if the scenario was successfully checked as allowed AND activated.
                  False if connection failed, check failed, activation was not allowed, or activation failed.
        """

        with self._api_lock:  # Acquire the lock for the duration of this operation
            logger.debug("Lock acquired for execute_activate_scenario")

            # Validate scenario number input first
            if not (
                0
                <= scenario_number_0_indexed
                < InimAlarmConstants.DEFAULT_SYSTEM_MAX_SCENARIOS
            ):
                logger.error(
                    f"execute_activate_scenario: Invalid scenario number {scenario_number_0_indexed}. Must be 0-{InimAlarmConstants.DEFAULT_SYSTEM_MAX_SCENARIOS - 1}."
                )
                return False

            if not self.connect():
                logger.error(
                    f"execute_activate_scenario: Failed to connect to panel for activating scenario {scenario_number_0_indexed}."
                )
                return False

            overall_success = False
            try:
                # Step 1: Call the low-level check method
                logger.info(
                    f"execute_activate_scenario: Checking activation allowance for scenario {scenario_number_0_indexed}..."
                )
                # This is the low-level method that expects an active connection
                is_allowed = self.check_scenario_activation_allowed(
                    scenario_number_0_indexed=scenario_number_0_indexed
                )

                if is_allowed is None:
                    logger.error(
                        f"execute_activate_scenario: Error occurred while checking activation allowance for scenario {scenario_number_0_indexed}."
                    )
                    # No further action, overall_success remains False
                elif not is_allowed:
                    logger.warning(
                        f"execute_activate_scenario: Activation of scenario {scenario_number_0_indexed} is NOT allowed by panel (e.g., a zone may be alarmed)."
                    )
                    # No further action, overall_success remains False
                else:
                    # Step 2: If allowed, proceed to call the low-level activate method
                    logger.info(
                        f"execute_activate_scenario: Scenario {scenario_number_0_indexed} activation is allowed by panel. Proceeding to activate..."
                    )
                    # This is the low-level method that expects an active connection
                    activation_command_successful = self.activate_scenario(
                        scenario_number=scenario_number_0_indexed
                    )

                    if activation_command_successful:
                        logger.info(
                            f"execute_activate_scenario: Scenario {scenario_number_0_indexed} activation command acknowledged successfully by panel."
                        )
                        overall_success = True
                    else:
                        logger.error(
                            f"execute_activate_scenario: Failed to activate scenario {scenario_number_0_indexed} (panel did not acknowledge command successfully) even after check passed."
                        )
                        # overall_success remains False

            except Exception as e:
                logger.error(
                    f"execute_activate_scenario: Exception during execution for scenario {scenario_number_0_indexed}: {e}"
                )
                overall_success = False  # Ensure success is false on any exception
            finally:
                self.disconnect()  # Always disconnect

            return overall_success

    def execute_check_scenario_activation_allowed(self, scenario_number_0_indexed):
        """Connects, checks scenario activation allowance, then disconnects. Returns allowance status."""

        with self._api_lock:  # Acquire the lock for the duration of this operation
            logger.debug("Lock acquired for execute_check_scenario_activation_allowed")

            if not self.connect():
                logger.error(
                    "Failed to connect to panel for checking scenario activation."
                )
                return None  # Indicate connection failure distinctly from False (not allowed)

            allowed = None
            try:
                allowed = self.check_scenario_activation_allowed(
                    scenario_number_0_indexed=scenario_number_0_indexed
                )
            except Exception as e:
                logger.error(
                    f"Exception during execute_check_scenario_activation_allowed: {e}"
                )
                allowed = None  # Indicate error
            finally:
                self.disconnect()
            return allowed

    def execute_get_compact_events(
        self,
        count: int | None = None,
        last_processed_compact_event_index_val: int | None = None,
    ) -> dict[str, Any]:
        """
        Connects, fetches compact events based on the last processed index or a specific count,
        then disconnects. Public wrapper.
        """
        with self._api_lock:
            logger.debug("Lock acquired for execute_get_compact_events")
            if not self.connect():
                logger.error("Failed to connect to panel for fetching compact events.")
                return {"events": [], "latest_event_index_val": None}

            result: dict[str, Any] = {"events": [], "latest_event_index_val": None}
            try:
                result = self.get_compact_events(
                    count=count,
                    last_processed_compact_event_index_val=last_processed_compact_event_index_val,
                )
            except Exception as e:
                logger.error(
                    "Exception during execute_get_compact_events: %s", e, exc_info=True
                )  # Added exc_info
                result = {"events": [], "latest_event_index_val": None}
            finally:
                self.disconnect()
            return result
