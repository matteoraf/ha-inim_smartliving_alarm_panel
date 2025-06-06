"""Config flow for Inim Alarm integration."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PIN, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_EVENT_LOG_SIZE,  # MODIFIED: Import new const
    CONF_LIMIT_AREAS,
    CONF_LIMIT_SCENARIOS,
    CONF_LIMIT_ZONES,
    CONF_PANEL_NAME,
    CONF_POLLING_INTERVAL,
    CONF_READER_NAMES,
    CONF_SCENARIO_ARM_AWAY,
    CONF_SCENARIO_ARM_HOME,
    CONF_SCENARIO_ARM_NIGHT,
    CONF_SCENARIO_ARM_VACATION,
    CONF_SCENARIO_DISARM,
    DATA_INITIAL_PANEL_CONFIG,
    DEFAULT_EVENT_LOG_SIZE,
    DEFAULT_LIMIT_AREAS,
    DEFAULT_LIMIT_SCENARIOS,
    DEFAULT_LIMIT_ZONES,
    DEFAULT_PANEL_NAME,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    SYSTEM_MAX_EVENT_LOG_SIZE,
)
from .inim_api import InimAlarmAPI

_LOGGER = logging.getLogger(__name__)


async def _validate_connection_and_fetch_initial_config(
    hass: HomeAssistant, host: str, port: int, pin: str
) -> dict[str, Any]:
    """Validate connection details by fetching initial panel configuration."""

    # For validation, API uses its internal defaults for max areas/zones if not specified.
    api = InimAlarmAPI(host=host, port=port, pin_code_str=pin)

    _LOGGER.debug(
        "Attempting to fetch initial panel configuration from %s:%s for validation",
        host,
        port,
    )
    # This call handles its own connect/disconnect.
    initial_config = await hass.async_add_executor_job(
        api.get_initial_panel_configuration
    )

    if initial_config is None:
        _LOGGER.error(
            "Connection/API error for %s:%s (API returned None)",
            host,
            port,
        )
        raise ConnectionError(
            "Failed to connect to the Inim panel or API error during fetch."
        )

    # Check for explicit errors reported by the API method
    if initial_config.get("errors"):
        _LOGGER.error(
            "Errors reported by API for %s:%s: %s",
            host,
            port,
            initial_config["errors"],
        )
        # If critical data is missing due to these errors, we'll catch it below.

    essential_keys = [
        "system_info",
        "areas",
        "zones",
        "zones_config",
        "scenarios",
        "scenario_activations",
    ]
    missing_or_failed_parts = [
        key for key in essential_keys if initial_config.get(key) is None
    ]

    if missing_or_failed_parts:
        error_message = f"Failed to retrieve essential data for: {', '.join(missing_or_failed_parts)}."
        _LOGGER.error("%s From %s:%s", error_message, host, port)
        if any("auth" in error.lower() for error in initial_config.get("errors", [])):
            raise ValueError("auth_failed")  # Specific error key for PIN failure
        raise ValueError(error_message)

    if not initial_config.get("system_info") or not initial_config["system_info"].get(
        "ascii"
    ):
        _LOGGER.error("System information is missing from initial configuration")
        raise ValueError("invalid_panel_response")

    _LOGGER.info(
        "Initial panel configuration successfully validated/fetched for %s:%s",
        host,
        port,
    )
    return initial_config


class InimAlarmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Inim Alarm."""

    VERSION = 1
    _flow_data: dict = {}  # To store data between steps

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step (host, port, pin, panel name)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Store user input for this step temporarily
            self._flow_data["user_input_step1"] = user_input

            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            )
            self._abort_if_unique_id_configured()

            try:
                _LOGGER.info(
                    "Validating Inim Alarm connection for: %s", user_input[CONF_HOST]
                )
                initial_panel_config = (
                    await _validate_connection_and_fetch_initial_config(
                        self.hass,
                        user_input[CONF_HOST],
                        user_input[CONF_PORT],
                        user_input[CONF_PIN],
                    )
                )
                self._flow_data[DATA_INITIAL_PANEL_CONFIG] = initial_panel_config
                _LOGGER.info(
                    "Inim Alarm connection validated for %s", user_input[CONF_HOST]
                )

                # Connection and initial data fetch successful, proceed to next step
                return await self.async_step_initial_options()

            except ConnectionError:
                _LOGGER.warning("Connection failed for %s", user_input[CONF_HOST])
                errors["base"] = "cannot_connect"
            except ValueError as vex:
                _LOGGER.warning(
                    "Validation error for %s: %s", user_input[CONF_HOST], vex
                )
                # Use specific error key if it's 'auth_failed' or 'invalid_panel_response'
                errors["base"] = (
                    str(vex)
                    if str(vex) in ["auth_failed", "invalid_panel_response"]
                    else "validation_error_detail"
                )
                if (
                    errors["base"] == "validation_error_detail"
                ):  # For generic ValueErrors with details
                    errors["detail"] = str(
                        vex
                    )  # Not standard, but an idea if form supported it
            except Exception as exc:
                _LOGGER.exception(
                    "Unexpected exception during Inim Alarm setup for %s: %s",
                    user_input[CONF_HOST],
                    exc,
                )
                errors["base"] = "unknown"

        user_data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=self._flow_data.get("user_input_step1", {}).get(
                        CONF_HOST, ""
                    ),
                ): str,
                vol.Required(
                    CONF_PORT,
                    default=self._flow_data.get("user_input_step1", {}).get(
                        CONF_PORT, DEFAULT_PORT
                    ),
                ): cv.port,
                vol.Required(
                    CONF_PIN,
                    default=self._flow_data.get("user_input_step1", {}).get(
                        CONF_PIN, ""
                    ),
                ): str,
                vol.Optional(
                    CONF_PANEL_NAME,
                    default=self._flow_data.get("user_input_step1", {}).get(
                        CONF_PANEL_NAME, DEFAULT_PANEL_NAME
                    ),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=user_data_schema, errors=errors
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
                CONF_HOST: step1_data[CONF_HOST],
                CONF_PORT: step1_data[CONF_PORT],
                CONF_PIN: step1_data[CONF_PIN],
                CONF_PANEL_NAME: step1_data.get(CONF_PANEL_NAME, DEFAULT_PANEL_NAME),
                DATA_INITIAL_PANEL_CONFIG: initial_config_data,  # Store all fetched initial data
            }

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
            vol.Optional(
                CONF_LIMIT_AREAS,
                default=step1_data.get(CONF_LIMIT_AREAS, DEFAULT_LIMIT_AREAS),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
            vol.Optional(
                CONF_LIMIT_ZONES,
                default=step1_data.get(CONF_LIMIT_ZONES, DEFAULT_LIMIT_ZONES),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
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
        self.config_entry = config_entry  # Base class provides this
        self.initial_panel_config = self.config_entry.data.get(
            DATA_INITIAL_PANEL_CONFIG, {}
        )
        # Combine initial data (from entry.data) with current options (from entry.options)
        # for populating form defaults correctly.
        self.current_pin = self.config_entry.data.get(CONF_PIN, "")  # Get current PIN
        self.current_settings = {**self.config_entry.data, **self.config_entry.options}

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
                    # Validate new PIN by trying to fetch initial config with it
                    await _validate_connection_and_fetch_initial_config(
                        self.hass,
                        self.config_entry.data[CONF_HOST],
                        self.config_entry.data[CONF_PORT],
                        new_pin,  # Use the new PIN for validation
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
                    errors[CONF_PIN] = (
                        "cannot_connect_new_pin"  # Custom error key for strings.json
                    )
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
            vol.Optional(
                CONF_LIMIT_AREAS, default=self.current_settings.get(CONF_LIMIT_AREAS)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
            vol.Optional(
                CONF_LIMIT_ZONES, default=self.current_settings.get(CONF_LIMIT_ZONES)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
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
