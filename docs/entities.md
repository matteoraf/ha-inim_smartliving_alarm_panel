# Entities

This integration exposes your Inim alarm system's components as various Home Assistant entities. The entity IDs are programmatically generated based on the panel name and the names of your areas, zones, and scenarios fetched from the panel.

*   **alarm\_control\_panel.your\_panel\_name**: The main entity to arm and disarm the system. Its actions (Arm Home, Arm Away, etc.) are linked to the scenarios you mapped during setup. It shows *Triggered* while any area is in alarm. A scenario active on the panel that is not mapped to a mode shows as *Armed custom bypass*. Without a mapped Disarm scenario, the state is unknown when no mapping matches.
    
*   **Areas**:
    
    *   *switch.your\_panel\_name\_area\_X*: Allows direct arming (on) and disarming (off) of an individual area. It is on when the area is armed in any mode (total, partial or instant); turning it on arms in the area's own mode set under **Area switches arm mode per area**, else in the mode of the **Area switches default arm mode** option (total by default). Attributes: `status_text` (`armed`, `disarmed` or `unknown`; `Unknown` when there is no status for the area) and `arming_mode` (`total`, `partial`, `instant`, `disarmed` or `unknown`).
        
    *   *select.your\_panel\_name\_area\_X\_arming\_mode* (named *Area X arming mode*): The area's arming mode: *Disarmed*, *Armed total*, *Armed partial* or *Armed instant* (unknown while the panel reports none of these). Choosing a mode arms the area in that mode or disarms it; if the panel refuses, an error says why. Attributes: `area_id` and `area_name`.
        
        Its attributes also show the area's delays and options, as programmed on the panel: `exit_time` (time to leave after arming) and `entry_time` (time to disarm after a delayed zone opens), for example `120 s` or `2 min`; `entry_time_2` (the second entry time, used by zones with the `entry_time_2` option) or `not used`; `pre_arm_warning_time` and `patrol_time` (in minutes, for example `3 min`); `timer_program` (the arm/disarm timer program, or `none`); and `reset_alarm_memory_on_arming`, `auto_arm_partial` and `clear_call_queue_on_disarm` (`true` or `false`). They are not shown for panels handled with another model's layout (see [Known limitations](limitations.md)).
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_alarm*: On while the area is in alarm.
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_tamper*: On while the area is in tamper.
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_triggered* (named *Area X Alarm memory*): On after an alarm in the area until the alarm memory is reset (see [Resetting the alarm memory](actions-events.md#resetting-the-alarm-memory)). The `tamper_memory` attribute tells whether the area also has a tamper memory.
        
*   **Zones**:
    
    Only zones that exist on the panel get entities: zones of input, input/output and double terminals. Output terminals (for example a siren) and unused terminals have none. Zones 1 to N are the zones of the panel's N terminals (50 on the SmartLiving 1050, 100 on the 10100); a double terminal *n* also has a second zone, numbered N + *n* (51 to 100 on the 1050, 101 to 200 on the 10100).

    *   *binary\_sensor.your\_panel\_name\_zone\_X*: Represents a single zone. It is on only while the zone is in alarm (open); a short circuit or tamper shows in `status_text` (`clear`, `alarmed`, `short` or `tamper`) and in the zone's tamper sensor. `test` tells whether the zone is in test now.
        
        **Device class**: a wired zone does not tell the panel what detector it has, so the device class is a guess from the zone's programming: a shock sensor is *vibration*; a roller-blind sensor, a normally open or normally closed contact and a single EOL balancing are *opening*; a double EOL balancing is *motion*; anything else has no class. If the guess is wrong, change it in Home Assistant: open the entity's settings and pick **Show as**.
        
        **Attributes**: the zone's programming as read from the panel, with stable English values:
        
        *   `zone_id` and `zone_name`: the zone's number and name.
        *   `zone_type`: `instant`, `delayed`, `viewable_delayed`, `route`, `24_hour`, `automation`, `arm`, `disarm`, `switch`, `arm_disarm`, `patrol`, `emergency_exit_without_call` or `emergency_exit` (a number if the panel uses a value not listed here).
        *   `alarm_cycles`: how many times the zone can raise an alarm, or `unlimited`.
        *   `sensor_kind`: `generic`, `roller_blind` or `shock`, from the terminal's programming. It decides the next attributes:
            *   `alarm_pulse_duration` (generic sensors only), for example `300 ms` or `20 s` (the panel counts it in steps of 15 ms or of 10 s);
            *   `shock_sensitivity` (shock sensors only), 0 to 40.
        *   `pulse_window_time`, the time in which pulses are counted (for example `30 s` or `1 min`), and `pulse_count`, how many pulses in that time raise the alarm.
        *   `balancing`: `normally_open`, `normally_closed`, `single_eol`, `double_eol`, `double_zone`, `double_zone_eol` or `personalized`.
        *   `assigned_areas`, `terminal_index`, `terminal_type` (`input`, `in_out`, `double`, …), `second_zone` (the second zone of a double terminal) and `internal_index`.
        *   The programming options, each `true` or `false`: `interior`, `autobypass` (bypassed by the panel when its areas arm while it is open), `unbypassable`, `chime`, `test_programmed`, `autobypass_no_reinclude` (autobypassed and not re-included when back at rest), `no_arming_if_not_ready` (stops arming in every mode, when the panel refuses arming with zones not ready), `entry_time_2`, `last_exit_zone`, `reinclude_on_disarm`, `hold_up` and `fault_zone`. Options that only apply to wireless zones are not shown.

    *   *binary\_sensor.your\_panel\_name\_zone\_X\_triggered* (named *Zone X Alarm memory*): Turns on if the zone has triggered the system, until the alarm memory is reset. The `tamper_memory` attribute tells whether the zone's terminal has a tamper memory (both zones of a double terminal share it).

    *   *binary\_sensor.your\_panel\_name\_zone\_X\_tamper*: Turns on while the zone is in tamper or short circuit (`status_text` tells which).

    *   *switch.your\_panel\_name\_zone\_X*: Lets you enable or disable (exclude) individual zones, including the second zone of double terminals.
        
*   **Scenarios**:
    
    *   *button.your\_panel\_name\_scenario\_X*: Allows direct activation of a specific scenario. If the activation fails (a zone not ready, or the panel refuses it), an error and a persistent notification with the reason appear in Home Assistant. The `area_actions_details` attribute tells what the scenario does to each area: `keep`, `total`, `partial`, `instant` or `disarm`. X is the scenario number minus 1 (scenario 1 is `scenario_0`), unlike area and zone numbers and the `scenario` field of `activate_scenario`.
        
    *   *binary\_sensor.your\_panel\_name\_scenario\_X\_active*: A binary sensor that is 'on' if that specific scenario is currently the active one on the panel (X as for the buttons: the scenario number minus 1).
        
*   **Sensors**:
    
    *   *sensor.your\_panel\_name\_event\_log*: The state is the position of the newest event in the panel's event log (0 to 999, or 0 to 499 on panels that keep 500 events: the SmartLiving 505 and 515, and the 1050 on firmware 5.1; it starts again from 0 when the log wraps around), so it changes with every new event. The attributes contain a human-readable log of recent events: `authorized_by` is the name of the user code or key, `device` the name of the keypad or reader (*Remote* for commands from the app, the cloud, Home Assistant or the installer software). Event times are the panel's clock read in Home Assistant's time zone, with daylight saving time.
        
    *   *sensor.your\_panel\_name\_active\_scenario*: The active scenario's name followed by its index (0-based), for example `Night (Index 2)`; the attribute `active_scenario_index` gives the index alone.
        
    *   *sensor.your\_panel\_name\_firmware\_version* & *sensor.your\_panel\_name\_system\_type*: Provide static information about your alarm panel.
        
*   **Outputs** (only the ones you tick; see [Outputs](#outputs) below):
    
    *   *switch.your\_panel\_name\_output\_name*: An on/off or monostable output. It is on while the panel shows the output active (read at every poll). A monostable output switches itself off after its time; the switch shows it off at the next poll.
        
    *   *light.your\_panel\_name\_output\_name*: A dimmer output, with brightness. On firmware 6 the level is read from the panel at every poll and after each change; on firmware 5.1 the panel does not report it, so the brightness is the last one set from Home Assistant.
        
    *   Both are named with the output's name on the panel (*Output n* or *O1* to *O3* without one). Attributes: `output_number`, `kind` (`on_off`, `monostable` or `dimmer`), `terminal_index` or `fixed_output` (1 to 3), `monostable_time` (for example `30 s`), `home_automation` (`true` if the installer marked it as a home automation output; not shown on firmware 5.1) and the output's options `normally_closed`, `flashing`, `buzzer`, `keep_on_restoral`, `toggle` and `relay_use` (`true` or `false`).
        
*   **Maintenance**:
    
    *   *button.your\_panel\_name\_reread\_panel\_configuration*: Reads the panel configuration again and reloads the integration (see [Re-reading the panel configuration](setup.md#re-reading-the-panel-configuration)). Shown in the *Configuration* section of the device page.
        
    *   *sensor.your\_panel\_name\_peripherals* (named *Peripherals*): How many keypads, readers, expanders and sirens are enabled on the panel; the `keypads`, `readers`, `expanders` and `sirens` attributes list their names. It does not tell whether a device is answering. Shown in the *Diagnostic* section of the device page.

    *   *sensor.your\_panel\_name\_user\_code* (named *User code*): The name of the user code the PIN belongs to (never the PIN); the `number`, `areas` (the areas it controls, by name) and `type` (standard, manager or master) attributes give more. Commands only work on those areas. Unknown if the PIN matches no user code: commands are refused then. Checked again whenever the integration reloads. Shown in the *Diagnostic* section of the device page.

    *   *binary\_sensor.your\_panel\_name\_refuses\_arming\_with\_zones\_not\_ready* (named *Refuses arming with zones not ready*): On when the installer set the panel to refuse arming with zones not ready (see [Zones not ready](actions-events.md#zones-not-ready)). Read with the panel configuration; unknown until the configuration is read again by this version. Shown in the *Diagnostic* section of the device page.

    *   *binary\_sensor.your\_panel\_name\_programming\_mode* (named *Programming mode*): On while the panel is in programming mode (being programmed from a keypad, or reloading its configuration). Commands are not sent then. Shown in the *Diagnostic* section of the device page. It is unavailable for panels handled with the SmartLiving 1050's layout (other models, firmware 5.0x).
        

    *   *select.your\_panel\_name\_diagnostics\_level* (named *Diagnostics level*): How much the next diagnostics download shows: *Redacted*, *Full* or *Full confidential* (see [Sharing a system report](system-report.md)). Disabled by default; it goes back to *Redacted* when the integration restarts or reloads, after the readable copy of a Full report has been downloaded, and 10 minutes after a Full report's first download. Shown in the *Diagnostic* section of the device page.

_(Replace your\_panel\_name, with actual value based on your setup)._

## Outputs

!!! warning

    An output switches whatever is wired to it: a siren, a lock, a gate, heating. Switching it from Home Assistant, or from an automation, acts on the real device. Before ticking an output, check what it drives; when you first try the integration's outputs, use one that is harmless to switch.

*   **What is offered**: outputs programmed on the panel as on/off, monostable or dimmer outputs (terminal outputs and O1 to O3) that your user code may switch. Analogue outputs are not offered. A terminal output on one of the panel's first three terminal positions cannot be switched remotely (the panel reserves those numbers for O1 to O3), so it is not listed.
    
*   **What is ticked for you**: only the outputs the installer marked as *home automation* outputs in the panel's programming (firmware 6; on firmware 5.1 the integration cannot read this mark, so nothing is ticked). The panel cannot tell what an output drives, so check them, and pick any other outputs yourself, in the setup or later in the options under **Areas, zones, scenarios and outputs**. The first time an existing installation shows the outputs in the options, the home automation outputs are ticked there too; after you save, your choice is kept.
    
*   **Nothing is switched by itself**: no output is switched at setup, at start, at a reload or when Home Assistant restores its state.
    
*   **When the panel refuses**: an output your user code may not switch, or one locked while its areas are armed, gives an error with the reason (see [When the panel refuses a command](troubleshooting.md#when-the-panel-refuses-a-command)). While the panel is in programming mode, no command is sent.
