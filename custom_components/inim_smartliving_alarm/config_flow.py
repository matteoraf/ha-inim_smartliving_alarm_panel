"""Config flow for Inim Alarm integration."""

from collections.abc import Mapping
import contextlib
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult, section
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import PANEL_ERRORS, create_panel
from .const import (
    AREA_SWITCH_USE_DEFAULT,
    ARM_MODE_INSTANT,
    ARM_MODE_PARTIAL,
    ARM_MODE_TOTAL,
    BOARD_SMARTLAN_G,
    BOARD_SMARTLAN_SI,
    CONF_AREA_SWITCH_ARM_MODE_BY_AREA,
    CONF_AREA_SWITCHES_DEFAULT_ARM_MODE,
    CONF_AREAS,
    CONF_BOARD,
    CONF_EVENT_LOG_SIZE,
    CONF_FOUND_BOARD,
    CONF_HOST,
    CONF_OUTPUTS,
    CONF_PANEL_NAME,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_PORT,
    CONF_SCENARIO_ARM_AWAY,
    CONF_SCENARIO_ARM_HOME,
    CONF_SCENARIO_ARM_NIGHT,
    CONF_SCENARIO_ARM_VACATION,
    CONF_SCENARIO_DISARM,
    CONF_SCENARIOS,
    CONF_THERMOSTATS,
    CONF_USERNAME,
    CONF_ZONES,
    CONF_ZONES_NOT_READY,
    DATA_INITIAL_PANEL_CONFIG,
    DATA_PANEL,
    DEFAULT_AREA_SWITCHES_ARM_MODE,
    DEFAULT_EVENT_LOG_SIZE,
    DEFAULT_PANEL_NAME,
    DEFAULT_PORT,
    DEFAULT_ZONES_NOT_READY,
    DOMAIN,
    SYSTEM_MAX_EVENT_LOG_SIZE,
    ZONES_NOT_READY_REFUSE,
    ZONES_NOT_READY_WARN,
)
from .discovery import DiscoveredBoard, DiscoveryError, discover
from .notifications import Texts, async_texts
from .panel_config import PanelConfigError, async_fetch_panel_configuration
from .peripherals import peripheral_placeholders
from .polling import GROUPS, SLOW_POLLING_DURATION, SLOW_POLLING_INTERVAL, GroupSpec
from .protocol.errors import (
    DecryptionError,
    InvalidPinError,
    ProtocolError,
    UnsupportedPanelError,
)
from .protocol.model import model_for
from .selection import (
    AVAILABLE,
    OPT_IN_KEYS,
    SELECTION_KEYS,
    available_thermostats,
    mapped_scenarios,
    numbers,
    output_label,
    outputs_shown,
    panel_config,
    selected,
    suggested_mappings,
    suggested_selection,
)
from .stored_config import (
    area_label,
    area_names,
    keypad_names,
    scenario_label,
    scenario_names,
    zone_names,
)
from .thermostats import with_new_keypads
from .transport.base import TransportError
from .user_code import user_code_placeholders

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
        "panel_programming",
        "unsupported_firmware",
        "unsupported_model",
    )
)


def _error_key(err: ValueError) -> str:
    key = err.key if isinstance(err, PanelConfigError) else str(err)
    return key if key in _ERROR_KEYS else "validation_error_detail"


async def _validate_for_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    data: Mapping[str, Any],
    *,
    keypad_check: "KeypadCheck | None" = None,
) -> dict[str, Any]:
    """Check an entry's new connection details, as at setup.

    A loaded entry holds the board's only connection: it is paused
    meanwhile. Raises as _validate_connection_and_fetch_initial_config.
    """
    running = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get(DATA_PANEL)
    async with running.released() if running is not None else contextlib.nullcontext():
        return await _validate_connection_and_fetch_initial_config(
            hass, data, keypad_check=keypad_check
        )


# Secrets of an entry: never sent back to a form, shown as password fields
_SECRETS = (CONF_PIN, CONF_PASSWORD)


def _secret_field() -> TextSelector:
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _connection_schema(
    board: str, *, panel_name: bool, keep_secrets: bool = False
) -> vol.Schema:
    """Fields of the connection form for a board type.

    With keep_secrets (Reconfigure) the PIN and the board password may be
    left empty to keep the stored ones.
    """
    secret = vol.Optional if keep_secrets else vol.Required
    fields: dict[Any, Any] = {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
    }
    if board == BOARD_SMARTLAN_G:
        fields[vol.Required(CONF_USERNAME)] = str
        fields[secret(CONF_PASSWORD)] = _secret_field()
    fields[secret(CONF_PIN)] = _secret_field()
    if panel_name:
        fields[vol.Optional(CONF_PANEL_NAME, default=DEFAULT_PANEL_NAME)] = str
    return vol.Schema(fields)


async def _validate_connection_and_fetch_initial_config(
    hass: HomeAssistant,
    data: Mapping[str, Any],
    *,
    keypad_check: "KeypadCheck | None" = None,
) -> dict[str, Any]:
    """Check the panel with these connection details and read its configuration.

    Raises ConnectionError if the panel cannot be reached and ValueError
    (with an error key) if it is refused: firmware older than 5.1, a model
    without its own layout, a PIN that matches no user code, a panel in
    programming mode, or a configuration that cannot be read.

    Given a KeypadCheck, the enabled keypads are then checked for a
    thermostat (see KeypadCheck).
    """
    board = data.get(CONF_BOARD, BOARD_SMARTLAN_SI)
    try:
        panel = create_panel(
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
            firmware = await panel.identify()
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
        try:
            model_for(firmware)
        except UnsupportedPanelError as err:
            _LOGGER.warning("Not set up: %s", err)
            raise ValueError(f"unsupported_{err.reason}") from err
        if not panel.user_code_found:
            raise ValueError("invalid_pin")
        config = await async_fetch_panel_configuration(hass, panel)
        if keypad_check is not None:
            keypads = [number - 1 for number in available_thermostats(config)]
            try:
                found = await panel.probe_thermostats(keypads)
            except PANEL_ERRORS as err:
                _LOGGER.info("The keypads were not checked for a thermostat: %s", err)
            else:
                keypad_check.keypads = sorted(keypad + 1 for keypad in found)
        return config
    finally:
        await panel.close()  # the board may accept only one connection


@dataclass
class KeypadCheck:
    """The keypads (1-based) whose room temperature read answered at setup.

    None when they could not be checked: the entry checks them at its first
    start instead.
    """

    keypads: list[int] | None = None


# Per kind of item: the panel's labels and the word for an unnamed one
_ITEM_FORMS = {
    CONF_AREAS: (area_names, "Area"),
    CONF_ZONES: (zone_names, "Zone"),
    CONF_SCENARIOS: (scenario_names, "Scenario"),
}


def _item_label(config: Mapping[str, Any], key: str, number: int) -> str:
    """How an item is listed: its number and the panel's label."""
    if key == CONF_OUTPUTS:
        return output_label(config, number)
    if key == CONF_THERMOSTATS:
        names = keypad_names(config)
        if number <= len(names) and names[number - 1]:
            return f"{number} - {names[number - 1]}"
        return f"Keypad {number}"
    names_of, kind = _ITEM_FORMS[key]
    names = names_of(config)
    if number <= len(names) and names[number - 1]:
        return f"{number} - {names[number - 1]}"
    return f"{kind} {number}"


def _items_schema(
    config: Mapping[str, Any], chosen: Mapping[str, list[int]]
) -> vol.Schema:
    """The areas, zones, scenarios, outputs and thermostats to tick.

    One collapsible section of checkboxes per kind, so that what is left
    out stays in view; the outputs' section only when there are outputs
    the user code can switch, the thermostats' only when keypads are
    enabled.
    """
    fields: dict[Any, Any] = {}
    for key in SELECTION_KEYS:
        options = [
            SelectOptionDict(value=str(number), label=_item_label(config, key, number))
            for number in AVAILABLE[key](config)
        ]
        if key in OPT_IN_KEYS and not options:
            continue
        checkboxes = SelectSelector(
            SelectSelectorConfig(
                options=options, multiple=True, mode=SelectSelectorMode.LIST
            )
        )
        suggested = [str(number) for number in chosen[key]]
        # Outputs and thermostats came later: a form sent without their
        # section ticks none
        marker = vol.Optional if key in OPT_IN_KEYS else vol.Required
        fields[marker(key)] = section(
            vol.Schema(
                {
                    vol.Optional(
                        key, description={"suggested_value": suggested}
                    ): checkboxes
                }
            ),
            {"collapsed": False},
        )
    return vol.Schema(fields)


def _chosen_items(user_input: Mapping[str, Any]) -> dict[str, list[int]]:
    """The ticked items of the form (one section per kind), as 1-based numbers."""
    return {
        key: numbers((user_input.get(key) or {}).get(key) or [])
        for key in SELECTION_KEYS
    }


def _scenario_choices(
    config: Mapping[str, Any], scenarios: list[int], texts: Texts
) -> dict[str, str]:
    """The scenarios a mode can be mapped to: the imported ones (0-based keys)."""
    names = scenario_names(config)
    choices = {"none": texts("choice_none")}
    for number in scenarios:
        name = names[number - 1] if number <= len(names) else ""
        choices[str(number - 1)] = texts(
            "choice_scenario",
            number=number,
            name=name or texts("choice_unnamed_scenario", number=number),
        )
    return choices


def _mapping_default(
    settings: Mapping[str, Any], key: str, choices: Mapping[str, str]
) -> str:
    """The form value of a stored scenario mapping.

    An unmapped scenario is stored as None and shown as "none"; so is a
    stored scenario that is not among the choices, so that the form never
    offers a value it would refuse.
    """
    value = settings.get(key)
    choice = "none" if value is None else str(value)
    return choice if choice in choices else "none"


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

    VERSION = 2
    MINOR_VERSION = 2

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
        """Connection details for a SmartLAN/G board."""
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
                keypad_check = KeypadCheck()
                initial_panel_config = (
                    await _validate_connection_and_fetch_initial_config(
                        self.hass, data, keypad_check=keypad_check
                    )
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
                self._flow_data[CONF_THERMOSTATS] = keypad_check.keypads
                _LOGGER.info("Inim Alarm connection validated for %s", host)
                return await self.async_step_items()

        return self.async_show_form(
            step_id=board,
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(board, panel_name=True),
                user_input or self._flow_data.get("discovered", {}),
            ),
            errors=errors,
        )

    async def async_step_items(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose the areas, zones and scenarios to import.

        Proposed: what the panel shows in use (see suggested_selection).
        """
        config = self._flow_data[DATA_INITIAL_PANEL_CONFIG]
        checked = self._flow_data.get(CONF_THERMOSTATS)
        if user_input is not None:
            chosen = _chosen_items(user_input)
            if checked is None and not chosen[CONF_THERMOSTATS]:
                # Not checked and none ticked: checked at the first start
                del chosen[CONF_THERMOSTATS]
            self._flow_data["selection"] = chosen
            return await self.async_step_initial_options()
        suggested = suggested_selection(config)
        if checked is not None:
            suggested[CONF_THERMOSTATS] = checked
        return self.async_show_form(
            step_id="items",
            data_schema=_items_schema(config, suggested),
            description_placeholders={
                "panel_name": self._flow_data["user_input_step1"].get(
                    CONF_PANEL_NAME, DEFAULT_PANEL_NAME
                ),
                **peripheral_placeholders(config),
            },
        )

    async def async_step_initial_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the last step: events and scenario mappings."""
        errors: dict[str, str] = {}
        step1_data = self._flow_data["user_input_step1"]
        initial_config_data = self._flow_data[DATA_INITIAL_PANEL_CONFIG]
        selection = self._flow_data["selection"]

        # Modes can be mapped to the imported scenarios only
        scenario_choices = _scenario_choices(
            initial_config_data, selection[CONF_SCENARIOS], await async_texts(self.hass)
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
                **selection,
                CONF_EVENT_LOG_SIZE: min(
                    user_input[CONF_EVENT_LOG_SIZE], SYSTEM_MAX_EVENT_LOG_SIZE
                ),
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

        # Schema for this step
        initial_options_schema_dict = {
            vol.Optional(CONF_EVENT_LOG_SIZE, default=DEFAULT_EVENT_LOG_SIZE): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=SYSTEM_MAX_EVENT_LOG_SIZE)
            ),
        }
        if len(scenario_choices) > 1:
            # Arm Away and Disarm proposed from what the scenarios do
            proposed = {
                key: "none" if index is None else str(index)
                for key, index in suggested_mappings(
                    initial_config_data, selection
                ).items()
            }
            initial_options_schema_dict.update(
                {
                    vol.Optional(CONF_SCENARIO_ARM_HOME, default="none"): vol.In(
                        scenario_choices
                    ),
                    vol.Optional(
                        CONF_SCENARIO_ARM_AWAY, default=proposed[CONF_SCENARIO_ARM_AWAY]
                    ): vol.In(scenario_choices),
                    vol.Optional(CONF_SCENARIO_ARM_NIGHT, default="none"): vol.In(
                        scenario_choices
                    ),
                    vol.Optional(CONF_SCENARIO_ARM_VACATION, default="none"): vol.In(
                        scenario_choices
                    ),
                    vol.Optional(
                        CONF_SCENARIO_DISARM, default=proposed[CONF_SCENARIO_DISARM]
                    ): vol.In(scenario_choices),
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
            # An empty PIN or board password keeps the stored one
            details = {
                **user_input,
                **{
                    key: entry.data[key]
                    for key in _SECRETS
                    if not user_input.get(key) and key in entry.data
                },
            }
            host = details[CONF_HOST]
            port = details[CONF_PORT]
            unique_id = f"{host}:{port}"

            if any(
                other.unique_id == unique_id and other.entry_id != entry.entry_id
                for other in self._async_current_entries(include_ignore=True)
            ):
                return self.async_abort(reason="already_configured")

            keypad_check = KeypadCheck()
            try:
                initial_panel_config = await _validate_for_entry(
                    self.hass,
                    entry,
                    {CONF_BOARD: board, **details},
                    keypad_check=keypad_check,
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
                # Keypads enabled since are added to the thermostats
                thermostats = with_new_keypads(
                    entry.options,
                    panel_config(entry.data),
                    keypad_check.keypads or (),
                )
                options = (
                    entry.options
                    if thermostats is None
                    else {**entry.options, CONF_THERMOSTATS: thermostats}
                )
                # A changed entry is reloaded by its update listener; reload an
                # unchanged one here so that the flow always ends with one reload.
                if not self.hass.config_entries.async_update_entry(
                    entry,
                    unique_id=unique_id,
                    data={
                        **entry.data,
                        **details,
                        DATA_INITIAL_PANEL_CONFIG: initial_panel_config,
                    },
                    options=options,
                ):
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(board, panel_name=False, keep_secrets=True),
                {
                    key: value
                    for key, value in (user_input or entry.data).items()
                    if key not in _SECRETS
                },
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
        self.current_settings = {**config_entry.data, **config_entry.options}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose what to change."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["items", "area_switches", "general", "polling"],
        )

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """How often each polling group is read, and (SmartLAN/SI) slow
        polling mode.

        Only values set otherwise than their default are stored, so that a
        later default reaches the entries that kept it.
        """
        options = self.config_entry.options
        # Each field: its option, step and unit
        fields: list[tuple[GroupSpec, float, str]] = [
            (spec, 0.5, "s") for spec in GROUPS.values()
        ]
        if self.config_entry.data.get(CONF_BOARD, BOARD_SMARTLAN_SI) == (
            BOARD_SMARTLAN_SI
        ):
            fields += [
                (SLOW_POLLING_INTERVAL, 1, "s"),
                (SLOW_POLLING_DURATION, 1, "min"),
            ]
        if user_input is not None:
            new_options = {
                key: value
                for key, value in options.items()
                if key not in {spec.option for spec, _, _ in fields}
            }
            for spec, _, _ in fields:
                value = float(user_input.get(spec.option, spec.default))
                if value != spec.default:
                    new_options[spec.option] = value
            return self.async_create_entry(title="", data=new_options)
        return self.async_show_form(
            step_id="polling",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        spec.option, default=options.get(spec.option, spec.default)
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=spec.minimum,
                            max=spec.maximum,
                            step=step,
                            unit_of_measurement=unit,
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                    for spec, step, unit in fields
                }
            ),
        )

    async def async_step_area_switches(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """The mode each imported area's switch arms in.

        One select per area, named by its number and label; "Use the
        default" follows the general "Area switches default arm mode" option. Only
        areas set otherwise are stored, and only imported ones.
        """
        config = self.initial_panel_config
        options = self.config_entry.options
        fields = {
            f"{number} - {area_label(config, number)}": number
            for number in selected(options, config, CONF_AREAS)
        }
        if user_input is not None:
            modes = {
                str(number): user_input[field]
                for field, number in fields.items()
                if user_input.get(field, AREA_SWITCH_USE_DEFAULT)
                != AREA_SWITCH_USE_DEFAULT
            }
            return self.async_create_entry(
                title="", data={**options, CONF_AREA_SWITCH_ARM_MODE_BY_AREA: modes}
            )

        stored = options.get(CONF_AREA_SWITCH_ARM_MODE_BY_AREA) or {}
        choices = [
            AREA_SWITCH_USE_DEFAULT,
            ARM_MODE_TOTAL,
            ARM_MODE_PARTIAL,
            ARM_MODE_INSTANT,
        ]
        selector = SelectSelector(
            SelectSelectorConfig(
                options=choices,
                translation_key=CONF_AREA_SWITCH_ARM_MODE_BY_AREA,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
        return self.async_show_form(
            step_id="area_switches",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        field,
                        default=stored.get(str(number))
                        if stored.get(str(number)) in choices
                        else AREA_SWITCH_USE_DEFAULT,
                    ): selector
                    for field, number in fields.items()
                }
            ),
        )

    async def async_step_items(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Change the areas, zones and scenarios to import.

        A scenario mapped to an alarm panel mode cannot be left out: the
        mapping is changed first, in the general settings.
        """
        config = self.initial_panel_config
        errors: dict[str, str] = {}
        placeholders = peripheral_placeholders(config)
        options = self.config_entry.options
        if user_input is not None:
            chosen = _chosen_items(user_input)
            missing = [
                f"{number} - {scenario_label(config, number)}"
                for number in sorted(set(mapped_scenarios(options).values()))
                if number not in chosen[CONF_SCENARIOS]
            ]
            if not missing:
                return self.async_create_entry(title="", data={**options, **chosen})
            errors["base"] = "mapped_scenario_not_imported"
            placeholders["scenarios"] = ", ".join(missing)
        current = (
            _chosen_items(user_input)
            if user_input is not None
            else {
                key: outputs_shown(options, config)
                if key == CONF_OUTPUTS
                else selected(options, config, key)
                for key in SELECTION_KEYS
            }
        )
        return self.async_show_form(
            step_id="items",
            data_schema=_items_schema(config, current),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the general options, including the PIN.

        An empty PIN field keeps the current PIN. A new one is checked with
        the panel as at setup; the PIN and the options are then saved
        together, so that the entry reloads once.
        """
        errors: dict[str, str] = {}
        entry = self.config_entry

        if user_input is not None:
            data = dict(entry.data)
            new_pin = user_input.get(CONF_PIN) or ""
            if new_pin and new_pin != entry.data.get(CONF_PIN):
                try:
                    new_config = await _validate_for_entry(
                        self.hass, entry, {**entry.data, CONF_PIN: new_pin}
                    )
                except ConnectionError:
                    _LOGGER.warning(
                        "Cannot reach %s to check the new PIN", entry.data[CONF_HOST]
                    )
                    errors[CONF_PIN] = "cannot_connect"
                except ValueError as err:
                    _LOGGER.warning("New PIN refused for %s: %s", entry.title, err)
                    errors[CONF_PIN] = _error_key(err)
                except Exception:
                    _LOGGER.exception("Unexpected error checking the new PIN")
                    errors[CONF_PIN] = "unknown"
                else:
                    data[CONF_PIN] = new_pin
                    # read with the new PIN: its user code's output mask
                    data[DATA_INITIAL_PANEL_CONFIG] = new_config

            if not errors:
                updated_options: dict[str, Any] = {}
                event_log_size_input = user_input.get(
                    CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE
                )
                updated_options[CONF_EVENT_LOG_SIZE] = min(
                    event_log_size_input, SYSTEM_MAX_EVENT_LOG_SIZE
                )
                updated_options[CONF_AREA_SWITCHES_DEFAULT_ARM_MODE] = user_input.get(
                    CONF_AREA_SWITCHES_DEFAULT_ARM_MODE, DEFAULT_AREA_SWITCHES_ARM_MODE
                )
                updated_options[CONF_ZONES_NOT_READY] = user_input.get(
                    CONF_ZONES_NOT_READY, DEFAULT_ZONES_NOT_READY
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

                # The imported items are kept as they are
                options = {**entry.options, **updated_options}
                if data != entry.data:
                    # One update, one reload; finishing the flow then
                    # changes nothing more
                    self.hass.config_entries.async_update_entry(
                        entry, data=data, options=options
                    )
                    _LOGGER.info("PIN changed for %s", entry.title)
                return self.async_create_entry(title="", data=options)

        # Modes can be mapped to the imported scenarios only
        scenario_choices = _scenario_choices(
            self.initial_panel_config,
            selected(
                self.config_entry.options, self.initial_panel_config, CONF_SCENARIOS
            ),
            await async_texts(self.hass),
        )

        # Shown again after a refused PIN: keep what was chosen, not the PIN
        values = {
            **self.current_settings,
            **{
                key: value
                for key, value in (user_input or {}).items()
                if key not in _SECRETS
            },
        }
        options_schema_dict = {
            vol.Optional(CONF_PIN): _secret_field(),
            vol.Optional(
                CONF_EVENT_LOG_SIZE,
                default=values.get(CONF_EVENT_LOG_SIZE, DEFAULT_EVENT_LOG_SIZE),
            ): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=SYSTEM_MAX_EVENT_LOG_SIZE)
            ),
            vol.Optional(
                CONF_AREA_SWITCHES_DEFAULT_ARM_MODE,
                default=values.get(
                    CONF_AREA_SWITCHES_DEFAULT_ARM_MODE, DEFAULT_AREA_SWITCHES_ARM_MODE
                ),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[ARM_MODE_TOTAL, ARM_MODE_PARTIAL, ARM_MODE_INSTANT],
                    translation_key=CONF_AREA_SWITCHES_DEFAULT_ARM_MODE,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }

        options_schema_dict[
            vol.Optional(
                CONF_ZONES_NOT_READY,
                default=values.get(CONF_ZONES_NOT_READY, DEFAULT_ZONES_NOT_READY),
            )
        ] = SelectSelector(
            SelectSelectorConfig(
                options=[ZONES_NOT_READY_REFUSE, ZONES_NOT_READY_WARN],
                translation_key=CONF_ZONES_NOT_READY,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )

        if len(scenario_choices) > 1:
            options_schema_dict.update(
                {
                    vol.Optional(
                        key,
                        default=_mapping_default(values, key, scenario_choices),
                    ): vol.In(scenario_choices)
                    for key in (
                        CONF_SCENARIO_ARM_HOME,
                        CONF_SCENARIO_ARM_AWAY,
                        CONF_SCENARIO_ARM_NIGHT,
                        CONF_SCENARIO_ARM_VACATION,
                        CONF_SCENARIO_DISARM,
                    )
                }
            )
        else:
            _LOGGER.info("No scenarios available to map in options flow")

        running = self.hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get(DATA_PANEL)
        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(options_schema_dict),
            errors=errors,
            description_placeholders=user_code_placeholders(
                running,
                self.initial_panel_config,
            ),
        )
