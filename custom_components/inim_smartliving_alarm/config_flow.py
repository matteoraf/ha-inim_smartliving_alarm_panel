"""Config flow for Inim Alarm integration."""

from collections.abc import Mapping
import contextlib
import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .const import (
    BOARD_SMARTLAN_G,
    BOARD_SMARTLAN_SI,
    CONF_BOARD,
    CONF_EVENT_LOG_SIZE,
    CONF_FOUND_BOARD,
    CONF_HOST,
    CONF_LIMIT_AREAS,
    CONF_LIMIT_SCENARIOS,
    CONF_LIMIT_ZONES,
    CONF_PANEL_NAME,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_POLLING_INTERVAL,
    CONF_PORT,
    CONF_READER_NAMES,
    CONF_SCENARIO_ARM_AWAY,
    CONF_SCENARIO_ARM_HOME,
    CONF_SCENARIO_ARM_NIGHT,
    CONF_SCENARIO_ARM_VACATION,
    CONF_SCENARIO_DISARM,
    CONF_USERNAME,
    DATA_API_CLIENT,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_EVENT_LOG_SIZE,
    DEFAULT_LIMIT_AREAS,
    DEFAULT_LIMIT_SCENARIOS,
    DEFAULT_LIMIT_ZONES,
    DEFAULT_PANEL_NAME,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    KEY_INIT_AREA_NAMES,
    KEY_INIT_AREAS,
    KEY_INIT_ZONE_NAMES,
    KEY_INIT_ZONES,
    MAX_LIMIT_AREAS,
    MAX_LIMIT_ZONES,
    MIN_FIRMWARE_MAJOR,
    SYSTEM_MAX_EVENT_LOG_SIZE,
)
from .discovery import DiscoveredBoard, DiscoveryError, discover
from .legacy import create_client
from .panel_config import async_fetch_panel_configuration
from .protocol.errors import DecryptionError, InvalidPinError, ProtocolError
from .transport.base import TransportError

_LOGGER = logging.getLogger(__name__)


# Error keys a failed check may report (strings.json "error"); anything else
# is shown as a generic validation error
_ERROR_KEYS = frozenset(
    (
        "auth_failed",
        "invalid_auth",
        "invalid_board_reply",
        "invalid_panel_response",
        "invalid_pin",
        "invalid_pin_format",
        "unsupported_firmware",
    )
)


def _error_key(err: ValueError) -> str:
    return str(err) if str(err) in _ERROR_KEYS else "validation_error_detail"


def _connection_schema(board: str, *, panel_name: bool) -> vol.Schema:
    """Fields of the connection form for a board type."""
    fields: dict[Any, Any] = {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
    }
    if board == BOARD_SMARTLAN_G:
        fields[vol.Required(CONF_USERNAME)] = str
        fields[vol.Required(CONF_PASSWORD)] = TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    fields[vol.Required(CONF_PIN)] = str
    if panel_name:
        fields[vol.Optional(CONF_PANEL_NAME, default=DEFAULT_PANEL_NAME)] = str
    return vol.Schema(fields)


async def _validate_connection_and_fetch_initial_config(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> dict[str, Any]:
    """Check the panel with these connection details and read its configuration.

    Raises ConnectionError if the panel cannot be reached and ValueError
    (with an error key) if it is refused: firmware below 5, a PIN that
    matches no user code, or a configuration that cannot be read. Firmware
    5.x and models without a profile (other than the 1050 and 10100) are
    accepted with a log warning.
    """
    board = data.get(CONF_BOARD, BOARD_SMARTLAN_SI)
    try:
        api = create_client(
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_PIN],
            board=board,
            username=data.get(CONF_USERNAME, ""),
            password=data.get(CONF_PASSWORD, ""),
        )
    except InvalidPinError as err:
        raise ValueError("invalid_pin_format") from err
    try:
        try:
            firmware = await api.panel.identify()
        except TransportError as err:
            raise ConnectionError(str(err)) from err
        except DecryptionError as err:
            # A SmartLAN/G reply that does not decrypt: wrong credentials
            raise ValueError("invalid_auth") from err
        except ProtocolError as err:
            # The reply decrypted (or needs no decryption) but has the wrong
            # form. On SmartLAN/G a wrong password may, rarely, decrypt to
            # something too: the message asks to check the credentials first.
            _LOGGER.error(
                "Unexpected reply from %s:%s: %s", data[CONF_HOST], data[CONF_PORT], err
            )
            raise ValueError(
                "invalid_board_reply"
                if board == BOARD_SMARTLAN_G
                else "invalid_panel_response"
            ) from err
        if firmware.major < MIN_FIRMWARE_MAJOR:
            _LOGGER.warning("Firmware %s is not supported", firmware.version)
            raise ValueError("unsupported_firmware")
        if not api.panel.user_code_found:
            raise ValueError("invalid_pin")
        return await async_fetch_panel_configuration(hass, api)
    finally:
        await api.close()  # the board may accept only one connection


def _panel_count(config: Mapping[str, Any], section: str, names: str) -> int:
    """How many areas or zone numbers the panel has: its label count."""
    return len((config.get(section) or {}).get(names) or [])


def _capped(settings: Mapping[str, Any], key: str, maximum: int) -> int:
    """A stored limit, at most the maximum (the maximum if none is stored)."""
    return min(settings.get(key) or maximum, maximum)


def _board_key(board: DiscoveredBoard) -> str:
    return f"{board.board}|{board.host}|{board.port}"


def _board_label(board: DiscoveredBoard) -> str:
    """How a found board is listed: address, type and what it told."""
    address = board.host if board.port is None else f"{board.host}:{board.port}"
    if board.board == BOARD_SMARTLAN_SI:
        details = f"SmartLAN/SI, MAC {board.mac}" if board.mac else "SmartLAN/SI"
        return f"{address} - {details}"
    details = ["SmartLAN/G"]
    if board.model:
        details.append(f"SmartLiving {board.model.lstrip('0')}")
    if board.firmware:
        details.append(f"firmware {board.firmware}")
    if board.panel_name:
        details.append(f'"{board.panel_name}"')
    return f"{address} - {', '.join(details)}"


class InimAlarmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Inim Alarm."""

    VERSION = 1
    MINOR_VERSION = 3

    def __init__(self) -> None:
        """Start with no data (kept per flow, between the steps)."""
        self._flow_data: dict[str, Any] = {}
        self._discovered: dict[str, DiscoveredBoard] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Search the network, or choose the board type."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["discover", BOARD_SMARTLAN_SI, BOARD_SMARTLAN_G],
        )

    async def async_step_discover(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Search the local network for boards, then pick one of them.

        Only boards on Home Assistant's own subnet answer.
        """
        if user_input is not None:
            board = self._discovered[user_input[CONF_FOUND_BOARD]]
            self._flow_data["discovered"] = {
                CONF_HOST: board.host,
                CONF_PORT: board.port or DEFAULT_PORT,
                CONF_PANEL_NAME: board.panel_name or DEFAULT_PANEL_NAME,
            }
            return await self._async_step_connection(board.board, None)

        try:
            boards = await discover()
        except DiscoveryError as err:
            _LOGGER.warning("Cannot search for boards: %s", err)
            boards = []
        configured = {
            entry.unique_id
            for entry in self._async_current_entries(include_ignore=False)
        }
        self._discovered = {
            _board_key(board): board
            for board in boards
            if f"{board.host}:{board.port}" not in configured
        }
        if not self._discovered:
            return await self.async_step_discover_none()
        return self.async_show_form(
            step_id="discover",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FOUND_BOARD): vol.In(
                        {
                            key: _board_label(board)
                            for key, board in self._discovered.items()
                        }
                    )
                }
            ),
            description_placeholders={"count": str(len(self._discovered))},
        )

    async def async_step_discover_none(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """No new board answered: search again, or enter the board by hand."""
        return self.async_show_menu(
            step_id="discover_none",
            menu_options=["discover", BOARD_SMARTLAN_SI, BOARD_SMARTLAN_G],
        )

    async def async_step_smartlan_si(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Connection details for a SmartLAN/SI board."""
        return await self._async_step_connection(BOARD_SMARTLAN_SI, user_input)

    async def async_step_smartlan_g(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Connection details for a SmartLAN/G board (experimental)."""
        return await self._async_step_connection(BOARD_SMARTLAN_G, user_input)

    async def _async_step_connection(
        self, board: str, user_input: dict[str, Any] | None
    ) -> FlowResult:
        """Check the panel with the details entered, then ask for the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {**user_input, CONF_BOARD: board}
            self._flow_data["user_input_step1"] = data
            host = data[CONF_HOST]

            await self.async_set_unique_id(f"{host}:{data[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            try:
                _LOGGER.info("Validating Inim Alarm connection for: %s", host)
                initial_panel_config = (
                    await _validate_connection_and_fetch_initial_config(self.hass, data)
                )
            except ConnectionError:
                _LOGGER.warning("Connection failed for %s", host)
                errors["base"] = "cannot_connect"
            except ValueError as vex:
                _LOGGER.warning("Validation error for %s: %s", host, vex)
                errors["base"] = _error_key(vex)
            except Exception as exc:
                _LOGGER.exception(
                    "Unexpected exception during Inim Alarm setup for %s: %s",
                    host,
                    exc,
                )
                errors["base"] = "unknown"
            else:
                self._flow_data[DATA_INITIAL_PANEL_CONFIG] = initial_panel_config
                _LOGGER.info("Inim Alarm connection validated for %s", host)
                return await self.async_step_initial_options()

        return self.async_show_form(
            step_id=board,
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(board, panel_name=True),
                user_input or self._flow_data.get("discovered", {}),
            ),
            errors=errors,
        )

    async def async_step_initial_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the second step for initial options (polling, limits, scenario mappings)."""
        errors: dict[str, str] = {}
        step1_data = self._flow_data["user_input_step1"]
        initial_config_data = self._flow_data[DATA_INITIAL_PANEL_CONFIG]

        # Prepare scenario choices for the form
        scenario_names_data = initial_config_data.get("scenarios", {})
        scenario_names_list = scenario_names_data.get("names", [])
        scenario_choices = {"none": "None (Do Not Assign)"}
        if scenario_names_list:
            for i, name in enumerate(scenario_names_list):
                scenario_choices[str(i)] = (
                    f"Scenario {i + 1}: {name if name else f'Unnamed Scenario {i + 1}'}"
                )

        if user_input is not None:
            # Combine data from step 1 and this step
            final_data_for_entry = {
                CONF_BOARD: step1_data[CONF_BOARD],
                CONF_HOST: step1_data[CONF_HOST],
                CONF_PORT: step1_data[CONF_PORT],
                CONF_PIN: step1_data[CONF_PIN],
                CONF_PANEL_NAME: step1_data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME),
                DATA_INITIAL_PANEL_CONFIG: initial_config_data,  # Store all fetched initial data
            }
            if step1_data[CONF_BOARD] == BOARD_SMARTLAN_G:
                final_data_for_entry[CONF_USERNAME] = step1_data[CONF_USERNAME]
                final_data_for_entry[CONF_PASSWORD] = step1_data[CONF_PASSWORD]

            final_options_for_entry = {
                CONF_POLLING_INTERVAL: user_input[CONF_POLLING_INTERVAL],
                CONF_LIMIT_AREAS: user_input[CONF_LIMIT_AREAS],
                CONF_LIMIT_ZONES: user_input[CONF_LIMIT_ZONES],
                CONF_LIMIT_SCENARIOS: user_input[CONF_LIMIT_SCENARIOS],
                CONF_EVENT_LOG_SIZE: min(
                    user_input[CONF_EVENT_LOG_SIZE], SYSTEM_MAX_EVENT_LOG_SIZE
                ),
                CONF_READER_NAMES: user_input.get(CONF_READER_NAMES, ""),
            }
            for key in [
                CONF_SCENARIO_ARM_HOME,
                CONF_SCENARIO_ARM_AWAY,
                CONF_SCENARIO_ARM_NIGHT,
                CONF_SCENARIO_ARM_VACATION,
                CONF_SCENARIO_DISARM,
            ]:
                val = user_input.get(key)
                final_options_for_entry[key] = (
                    int(val) if val and val != "none" else None
                )

            title = final_data_for_entry.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME)
            return self.async_create_entry(
                title=title, data=final_data_for_entry, options=final_options_for_entry
            )

        # Schema for this step, using defaults from step1 if available for limits/polling
        initial_options_schema_dict = {
            vol.Optional(
                CONF_POLLING_INTERVAL,
                default=step1_data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            # Proposed: every area and zone number of this panel model
            vol.Optional(
                CONF_LIMIT_AREAS,
                default=_panel_count(
                    initial_config_data, KEY_INIT_AREAS, KEY_INIT_AREA_NAMES
                )
                or DEFAULT_LIMIT_AREAS,
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_LIMIT_AREAS)),
            vol.Optional(
                CONF_LIMIT_ZONES,
                default=_panel_count(
                    initial_config_data, KEY_INIT_ZONES, KEY_INIT_ZONE_NAMES
                )
                or DEFAULT_LIMIT_ZONES,
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_LIMIT_ZONES)),
            vol.Optional(
                CONF_LIMIT_SCENARIOS,
                default=step1_data.get(CONF_LIMIT_SCENARIOS, DEFAULT_LIMIT_SCENARIOS),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
            vol.Optional(CONF_EVENT_LOG_SIZE, default=DEFAULT_EVENT_LOG_SIZE): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=SYSTEM_MAX_EVENT_LOG_SIZE)
            ),
            vol.Optional(CONF_READER_NAMES, default=""): str,
        }
        if len(scenario_choices) > 1:
            initial_options_schema_dict.update(
                {
                    vol.Optional(CONF_SCENARIO_ARM_HOME, default="none"): vol.In(
                        scenario_choices
                    ),
                    vol.Optional(CONF_SCENARIO_ARM_AWAY, default="none"): vol.In(
                        scenario_choices
                    ),
                    vol.Optional(CONF_SCENARIO_ARM_NIGHT, default="none"): vol.In(
                        scenario_choices
                    ),
                    vol.Optional(CONF_SCENARIO_ARM_VACATION, default="none"): vol.In(
                        scenario_choices
                    ),
                    vol.Optional(CONF_SCENARIO_DISARM, default="none"): vol.In(
                        scenario_choices
                    ),
                }
            )

        return self.async_show_form(
            step_id="initial_options",
            data_schema=vol.Schema(initial_options_schema_dict),
            errors=errors,
            description_placeholders={
                "panel_name": step1_data.get(CONF_PANEL_NAME, "Inim Panel")
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Change the connection details of an existing entry.

        The panel is contacted with the new details and its configuration is
        stored again. Options, entities and their unique IDs are kept. The
        board type stays as set up.
        """
        entry = self._get_reconfigure_entry()
        board = entry.data.get(CONF_BOARD, BOARD_SMARTLAN_SI)
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            unique_id = f"{host}:{port}"

            if any(
                other.unique_id == unique_id and other.entry_id != entry.entry_id
                for other in self._async_current_entries(include_ignore=True)
            ):
                return self.async_abort(reason="already_configured")

            # A loaded entry holds the board's only connection: pause it
            running = (
                self.hass.data.get(DOMAIN, {})
                .get(entry.entry_id, {})
                .get(DATA_API_CLIENT)
            )
            try:
                async with (
                    running.released()
                    if running is not None
                    else contextlib.nullcontext()
                ):
                    initial_panel_config = (
                        await _validate_connection_and_fetch_initial_config(
                            self.hass, {CONF_BOARD: board, **user_input}
                        )
                    )
            except ConnectionError:
                _LOGGER.warning("Connection failed for %s:%s", host, port)
                errors["base"] = "cannot_connect"
            except ValueError as vex:
                _LOGGER.warning("Validation error for %s:%s: %s", host, port, vex)
                errors["base"] = _error_key(vex)
            except Exception:
                _LOGGER.exception("Unexpected error reconfiguring %s:%s", host, port)
                errors["base"] = "unknown"
            else:
                # A changed entry is reloaded by its update listener; reload an
                # unchanged one here so that the flow always ends with one reload.
                if not self.hass.config_entries.async_update_entry(
                    entry,
                    unique_id=unique_id,
                    data={
                        **entry.data,
                        **user_input,
                        DATA_INITIAL_PANEL_CONFIG: initial_panel_config,
                    },
                ):
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(board, panel_name=False), user_input or entry.data
            ),
            errors=errors,
            description_placeholders={"panel_name": entry.title},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return InimAlarmOptionsFlowHandler(config_entry)


class InimAlarmOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle an options flow for Inim Alarm."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.initial_panel_config = config_entry.data.get(DATA_INITIAL_PANEL_CONFIG, {})
        # Combine initial data (from entry.data) with current options (from entry.options)
        # for populating form defaults correctly.
        self.current_pin = config_entry.data.get(CONF_PIN, "")  # Get current PIN
        self.current_settings = {**config_entry.data, **config_entry.options}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the main options for the Inim Alarm integration, including PIN change."""
        errors: dict[str, str] = {}

        if user_input is not None:
            updated_options = {}
            new_data = (
                self.config_entry.data.copy()
            )  # Start with existing data for potential PIN update

            # Handle PIN change
            new_pin = user_input.get(CONF_PIN)
            if new_pin and new_pin != self.current_pin:
                _LOGGER.info(
                    "Attempting to validate new PIN for %s",
                    self.config_entry.data[CONF_HOST],
                )
                try:
                    await _validate_connection_and_fetch_initial_config(
                        self.hass,
                        self.config_entry.data[CONF_HOST],
                        self.config_entry.data[CONF_PORT],
                        new_pin,
                    )
                    _LOGGER.info(
                        "New PIN validated successfully for %s",
                        self.config_entry.data[CONF_HOST],
                    )
                    new_data[CONF_PIN] = new_pin  # Update PIN in data
                except ConnectionError:
                    _LOGGER.warning(
                        "Connection failed with new PIN for %s",
                        self.config_entry.data[CONF_HOST],
                    )
                    errors[CONF_PIN] = "cannot_connect_new_pin"
                except ValueError as vex:
                    _LOGGER.warning(
                        "PIN validation error for %s: %s",
                        self.config_entry.data[CONF_HOST],
                        vex,
                    )
                    if str(vex) == "auth_failed":
                        errors[CONF_PIN] = "auth_failed_new_pin"
                    else:
                        errors[CONF_PIN] = "pin_validation_error"
                except Exception as exc:
                    _LOGGER.exception("Unexpected error validating new PIN: %s", exc)
                    errors[CONF_PIN] = "unknown_pin_error"

            if not errors.get(
                CONF_PIN
            ):  # If PIN validation passed or PIN wasn't changed
                if (
                    new_data != self.config_entry.data
                ):  # If PIN was changed and validated
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                    _LOGGER.info(
                        "Updated PIN in config entry data for %s",
                        self.config_entry.title,
                    )

                # Process other options
                updated_options[CONF_POLLING_INTERVAL] = user_input.get(
                    CONF_POLLING_INTERVAL
                )
                updated_options[CONF_LIMIT_AREAS] = user_input.get(CONF_LIMIT_AREAS)
                updated_options[CONF_LIMIT_ZONES] = user_input.get(CONF_LIMIT_ZONES)
                updated_options[CONF_LIMIT_SCENARIOS] = user_input.get(
                    CONF_LIMIT_SCENARIOS
                )
                event_log_size_input = user_input.get(
                    CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE
                )
                updated_options[CONF_EVENT_LOG_SIZE] = min(
                    event_log_size_input, SYSTEM_MAX_EVENT_LOG_SIZE
                )
                updated_options[CONF_READER_NAMES] = user_input.get(
                    CONF_READER_NAMES, ""
                )

                for key in [
                    CONF_SCENARIO_ARM_HOME,
                    CONF_SCENARIO_ARM_AWAY,
                    CONF_SCENARIO_ARM_NIGHT,
                    CONF_SCENARIO_ARM_VACATION,
                    CONF_SCENARIO_DISARM,
                ]:
                    val = user_input.get(key)
                    if val == "none":
                        updated_options[key] = None
                    elif val is not None:
                        try:
                            updated_options[key] = int(val)
                        except (ValueError, TypeError):
                            updated_options[key] = None

                _LOGGER.debug("Creating/updating options with: %s", updated_options)
                return self.async_create_entry(title="", data=updated_options)
            # If there was a PIN error, re-show the form with the error

        # Prepare scenario choices for the form
        scenario_names_data = self.initial_panel_config.get("scenarios", {})
        scenario_names_list = scenario_names_data.get("names", [])
        scenario_choices = {"none": "None (Do Not Assign)"}
        if scenario_names_list is not None:
            for i, name in enumerate(scenario_names_list):
                scenario_choices[str(i)] = (
                    f"Scenario {i + 1}: {name if name else f'Unnamed Scenario {i + 1}'}"
                )

        # Define the schema for the options form
        options_schema_dict = {
            vol.Optional(
                CONF_PIN, description={"suggested_value": self.current_pin}
            ): str,  # Show current PIN for re-entry/change
            vol.Optional(
                CONF_POLLING_INTERVAL,
                default=self.current_settings.get(CONF_POLLING_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            # A limit stored by an older version may be above today's maximum
            # (areas used to go up to 16): offer the maximum instead
            vol.Optional(
                CONF_LIMIT_AREAS,
                default=_capped(
                    self.current_settings, CONF_LIMIT_AREAS, MAX_LIMIT_AREAS
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_LIMIT_AREAS)),
            vol.Optional(
                CONF_LIMIT_ZONES,
                default=_capped(
                    self.current_settings, CONF_LIMIT_ZONES, MAX_LIMIT_ZONES
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_LIMIT_ZONES)),
            vol.Optional(
                CONF_LIMIT_SCENARIOS,
                default=self.current_settings.get(CONF_LIMIT_SCENARIOS),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
            vol.Optional(
                CONF_EVENT_LOG_SIZE,
                default=self.current_settings.get(
                    CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE
                ),
            ): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=SYSTEM_MAX_EVENT_LOG_SIZE)
            ),
            vol.Optional(
                CONF_READER_NAMES,
                default=self.current_settings.get(CONF_READER_NAMES, ""),
            ): str,
        }

        if len(scenario_choices) > 1:
            options_schema_dict.update(
                {
                    vol.Optional(
                        CONF_SCENARIO_ARM_HOME,
                        default=str(
                            self.current_settings.get(CONF_SCENARIO_ARM_HOME, "none")
                        ),
                    ): vol.In(scenario_choices),
                    vol.Optional(
                        CONF_SCENARIO_ARM_AWAY,
                        default=str(
                            self.current_settings.get(CONF_SCENARIO_ARM_AWAY, "none")
                        ),
                    ): vol.In(scenario_choices),
                    vol.Optional(
                        CONF_SCENARIO_ARM_NIGHT,
                        default=str(
                            self.current_settings.get(CONF_SCENARIO_ARM_NIGHT, "none")
                        ),
                    ): vol.In(scenario_choices),
                    vol.Optional(
                        CONF_SCENARIO_ARM_VACATION,
                        default=str(
                            self.current_settings.get(
                                CONF_SCENARIO_ARM_VACATION, "none"
                            )
                        ),
                    ): vol.In(scenario_choices),
                    vol.Optional(
                        CONF_SCENARIO_DISARM,
                        default=str(
                            self.current_settings.get(CONF_SCENARIO_DISARM, "none")
                        ),
                    ): vol.In(scenario_choices),
                }
            )
        else:
            _LOGGER.info("No scenarios available to map in options flow")

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(options_schema_dict), errors=errors
        )
