# Upgrading

## From 0.4

This version keeps the panel configuration in a new format. Your entity IDs and options stay as they are, and your configuration entry is updated automatically. Coming from 0.4.0b1 or 0.4.0b2, read also [the section below](#from-040b1-or-040b2).

### Before you update

1.  **Take a Home Assistant backup** (**Settings** → **System** → **Backups**). **To go back to 0.4, restore that backup**: 0.4 does not load a configuration entry in the new format.

### After you update

1.  **Look at the log** after the first start. The integration reads the panel configuration again once by itself, and says so. If it cannot, it tries again at every start (see below).

### What changed

| Change | What to do |
|---|---|
| New format of the configuration entry; the panel configuration is read again once | Nothing; to go back to 0.4, restore your backup |
| Switches, area sensors and arming mode selects no longer depend on the stored area names | Nothing |
| Zone bypass switches find the zone's terminal the same way as outputs | Nothing |
| The active scenario sensor says *No scenario matches* when no scenario matches the areas | *Check your automations* if they test for *Unknown Scenario (Index 255)* or index 255 |
| The event log no longer shows events twice after a poll that failed | Nothing |
| Entity names are translated and no longer repeat the panel name; *Firmware version* and *System type* are diagnostic | Nothing: entity IDs don't change; a name you set yourself is kept |
| The single polling interval is replaced by an interval for each part of the panel's state; the panel is read much more often | Nothing; to read less often, see the *Polling* options; to use the Inim app or the installer software on a SmartLAN/SI board, turn on *Slow polling mode* |
| System reports read a SmartLAN/SI board's details from the board itself, across subnets too; a *Full confidential* report shows the installer code | Nothing |
| Keypad thermostats: a climate entity and a temperature sensor for each keypad that reports a room temperature; the keypads are checked once, in the background after the first start, and their thermostats added | *Untick* the thermostats you don't use, under **Areas, zones, scenarios, outputs and thermostats** in the options |

??? note "Details"

    *   **Your configuration entry is updated to a new format (version 2.2).** It now keeps only the panel data as read, and names, zones and scenarios are worked out from it. At the first start the integration reads the panel configuration again once by itself (the log says so). If it cannot, it tries again at every start; until then the names of user codes, keys, readers, expanders, timers and sirens show their defaults (such as *Code 1*, *Reader 1* or *Key 5*), and on an entry first set up before 0.4.0b1 the zone bypass switches appear only once that read has succeeded. **Re-read panel configuration** reads it at once. Entity IDs and options do not change. **To go back to 0.4, restore the backup taken before the update**: 0.4 does not load an entry in the new format.
    *   **Switches, area sensors and arming mode selects no longer depend on the stored area names.** Before, a panel configuration stored without them (for example after an incomplete read) left the panel without any switch, including the zone bypass and output switches, and without the area alarm, tamper and alarm memory sensors and the arming mode selects. Now each of them is created on its own, an area without a name is called *Area N*, and the scenario buttons show what they do to each area.
    *   **Zone bypass switches find the zone's terminal in the panel's terminal list the same way as outputs.** A zone on the last position of the list (a keypad terminal on a fully used panel) now gets a bypass switch; before, it had none. If a terminal appears twice in the list, the switch uses its first position, and the zone's `internal_index` attribute shows it.
    *   **When no scenario matches the state of the areas** (no scenario matches, or the panel reports none), the *Active scenario* sensor now says *No scenario matches* and its `active_scenario_index` attribute is empty; before, it said *Unknown Scenario (Index 255)* and 255. `raw_active_scenario_hex` still gives the value the panel reported. The alarm panel still shows *Armed custom bypass* then, as before.
    *   **The event log no longer shows events twice.** When a poll failed after a good one, the events of that good poll were added to the log a second time.
    *   **Entity names are translated and read the same way everywhere**: *Zone 12 (Window)*, *Zone 12 tamper (Window)*, *Area 3 alarm (Kitchen)*, *Activate scenario 2 (Night)*, *Event log*, shown after the panel's name and in your Home Assistant language. The zone switch is now *Zone N included*. An item without a name on the panel shows its number alone (*Zone 5*, not *Zone 5 (Zone 5)*). Entity IDs don't change, so automations and dashboards keep working; cards that show an entity's name show the new one, and a name you set yourself is kept.
    *   ***Firmware version* and *System type*** move to the *Diagnostic* section of the device page.
    *   **New installations no longer get the panel's name twice in entity IDs** (for example `switch.home_area_3` rather than `switch.home_home_area_3`). Entity IDs of an existing installation stay as they are; to remove a doubled name, rename the entity ID in the entity's settings (**Settings** → **Devices & services** → **Entities**, the entity, then the gear icon), and update the automations and dashboards that use it.
    *   **The panel is read in parts, each at its own interval** ([Polling](options.md#polling)): zones and outputs every second, areas and the active scenario every 2 seconds, the event log every 10 seconds, the zone bypass and dimmer levels every 30 seconds, the panel state every 60 seconds, and every part at once after a command sent from Home Assistant. The old **Polling Interval** option is dropped and the new defaults apply, whatever it was set to; set the intervals in **Settings** → **Devices & services** → **Inim SmartLiving Alarm**, the gear icon on your panel's row, **Polling**. While it reads the panel at these intervals, Home Assistant keeps its connection to the board open; the SmartLAN/SI board accepts one local client at a time, so other apps on your network cannot connect to it meanwhile. To let the Inim app or the installer software connect, turn on the new **Slow polling mode** switch on the device page: Home Assistant then reads the whole panel every 30 seconds and closes its connection after each read, as earlier versions did, and the switch turns itself off after 60 minutes (see [Slow polling mode](options.md#slow-polling-mode)).
    *   **System reports read the SmartLAN/SI board's details from the board itself** (its network settings and firmware), so they are included when Home Assistant is on another subnet too. A **Full confidential** report also shows the panel's installer code (see [Finding the installer code](system-report.md#finding-the-installer-code)), and the board's dynamic-DNS user name now shows only at *Full confidential*, like its passwords.
    *   **Keypad thermostats are new** ([Thermostats](entities.md#thermostats)): each keypad that reports a room temperature gets a climate entity (off, heat or cool; the panel's manual, daily program, weekly program and antifreeze modes as presets) and a temperature sensor. At the first start, once the integration is running, it checks which keypads report a temperature, ticks their thermostats in the options and adds their entities. A keypad whose thermostat you don't use gets one too: untick it in the options to remove its entities. Changes from Home Assistant set only the mode, the season and the manual setpoint; the programs are not changed.

## From 0.4.0b1 or 0.4.0b2

*   **Check your automations:** in the `inim_smartliving_alarm_zones_not_ready` event, `zone_name` is now the zone's plain name (for example `Kitchen window`), as in the zone entities and the scenario failure event. Automations that match a name with the number in brackets, such as `Kitchen window (12)`, need updating; `zone_id` still gives the number.

## From 0.3.x

Version 0.4 reworks how the integration talks to the panel. Your entity IDs and options stay as they are, and your configuration entry is updated automatically.

### Before you update

1.  **Take a Home Assistant backup** (**Settings** → **System** → **Backups**), above all for a preview or a beta (see [Preview versions](installation.md#preview-versions)).
2.  **Read the changes below**, above all those marked *check your automations*.

### After you update

1.  **Look at the log** after the first start. The integration may read the panel configuration again by itself, once, and says so.
2.  **Delete leftover entities**: entities of terminals that are not zones, or of zones and areas your panel does not have, stay as unavailable entities and are listed in the log at every start. Delete them in **Settings** → **Devices & services** → **Entities**.
3.  **Tick what is new** in the options under **Areas, zones, scenarios, outputs and thermostats**: the second zones of double terminals, areas 11 to 15 and zones above 50 on a 10100, and outputs.
4.  **Choose what happens with zones not ready**: arming now checks the zones, and refuses by default (see [Zones not ready](actions-events.md#zones-not-ready)).
5.  **Check your automations and templates** against the changes marked *check your automations*.

### What changed

#### Areas and the alarm panel

| Change | What to do |
|---|---|
| Area switches are on for partial and instant arming too | *Check your automations* if they relied on the switch being off then |
| *Area X Triggered* is now *Area X alarm memory*; new *Area X alarm* and *Area X tamper* sensors | Nothing: same entity ID |
| The alarm panel shows *Triggered* only while an area is in alarm | *Check your automations*: use the *alarm memory* sensors for the memory |
| New arming mode select per area, and the `arm_area` action | Add them to dashboards and automations if you like |
| Each area switch can arm in its own mode | Optional: **Area switches arm mode per area** |
| Each area's delays are shown as attributes | Nothing |

??? note "Details"

    *   **Area switches** are now on for an area armed in partial or instant mode too (before, such an area showed as unknown and its switch was off). Turning a switch on still arms in total mode, unless you choose another mode with the new **Area switches default arm mode** option. The new `arming_mode` attribute tells the mode (`total`, `partial`, `instant` or `disarmed`); `status_text` stays `armed` or `disarmed`.
    *   **"Area X Triggered" is now called "Area X alarm memory"**: same entity ID, same meaning (on after an alarm until the memory is reset), now also for areas 9 and 10, with a `tamper_memory` attribute. Two new binary sensors per area show the **live** state: *Area X alarm* and *Area X tamper*.
    *   **The alarm panel shows Triggered only while an area is in alarm.** Before, it stayed Triggered until the alarm memory was reset; now it returns to its armed or disarmed state when the alarm ends, and the *alarm memory* sensors keep the memory. Automations that relied on Triggered lasting until the reset should use the *alarm memory* sensors instead.
    *   **New: an arming mode select per area** (*Area X arming mode*: disarmed, total, partial or instant) and the **`arm_area` action**, which arms or disarms several areas in one command (see [Arming areas from automations](actions-events.md#arming-areas-from-automations)).
    *   **Each area switch can arm in its own mode**: the new options step **Area switches arm mode per area** has one choice per imported area (use the default, total, partial or instant). Areas left at *Use the default* follow **Area switches default arm mode**, as before. The general option, called **Area switches arm in** in the 0.4.0a3 preview, is now **Area switches default arm mode**: if you changed it in that preview, set it again (it is back to total).
    *   **Each area's delays are shown**: the *Area X arming mode* select's attributes now include the area's exit and entry times, entry time 2, pre-arm warning and patrol times, timer program and three options, read from the panel. At the first start the integration reads the panel configuration again once by itself to get them (the log says so); if it cannot, they appear after the next start or a **Re-read panel configuration**.

#### Zones

| Change | What to do |
|---|---|
| Zones follow the panel's terminal types; second zones of double terminals can be added | Delete leftover entities; tick second zones if you want them |
| *Zone X Triggered* is now *Zone X alarm memory*; new *Zone X tamper* sensor | Nothing: same entity ID |
| Zones are checked before arming areas too, and arming is refused by default | Choose **When zones are not ready** |
| Zone attributes show the real programming; some are renamed | *Check your automations* and templates |
| Zone device classes follow the zone's programming | Set **Show as** on a zone to keep its old class |

??? note "Details"

    *   **Zones follow the panel's terminal types.** Output terminals (for example a siren) and unused terminals no longer get zone entities, and the second zones of double terminals (11 to 20 on the 505, 21 to 40 on the 515, 51 to 100 on the 1050, 101 to 200 on the 10100) can be added: tick them in the options under **Areas, zones, scenarios, outputs and thermostats** (existing installations keep the zones they had). Entities that earlier versions created for terminals that are not zones are **not deleted automatically**: they stay as unavailable entities, and the log lists them at every start (*"these entities belong to terminals that are not zones on the panel"*). Delete them in **Settings** → **Devices & services** → **Entities**.
    *   **"Zone X Triggered" is now called "Zone X alarm memory"** (same entity ID). Zones in short circuit or tamper are no longer shown as unknown: a new *Zone X tamper* sensor turns on, and the zone's `status_text` says `short` or `tamper`. Such zones are also listed when they prevent a scenario from being activated.
    *   **Zones are checked before arming areas too.** The area switches, the arming mode selects and the `arm_area` action now ask the panel which zones are not ready, as scenarios already did, and by default **refuse to arm** when there are some (before, they armed with zones open). The new option **When zones are not ready** (General settings) can arm anyway with a warning instead; `force: true` does it for one action call. A new action, `activate_scenario`, activates a scenario from automations, and a new event, `inim_smartliving_alarm_zones_not_ready`, lists the zones each time. The integration also reads whether the panel itself refuses arming with zones not ready (new diagnostic sensor *Refuses arming with zones not ready*): at the first start it reads the panel configuration again once by itself (the log says so). Zone sensors show each zone's programming options (`autobypass`, `no_arming_if_not_ready`, `interior` and more) as attributes.
    *   **Zone attributes now show the zone's real programming**, read from the panel configuration already stored (nothing is read again). Before, most zone types and balancings showed as *Unknown*, and two times were read wrongly. Some attributes are renamed, so check templates and automations that use them: `activation_type` is now `zone_type`, `balancing_type` is `balancing`, `sensor_type` is `sensor_kind`, `duration_ms` is now `alarm_pulse_duration` (or `shock_sensitivity` for shock sensors), `time` is `pulse_window_time` and `pulses` is `pulse_count`. New: `alarm_cycles`. Values are now lower-case English keys (for example `delayed`, `double_eol`, `input` instead of *Delayed*, *Double Balancing*, *Input*).
    *   **Zone device classes follow the zone's programming.** Single EOL zones are now *opening* (before, no class), shock sensors are *vibration* (before, *opening* or none), and roller-blind sensors are *opening* (before, *motion*). Normally open, normally closed and double EOL zones keep their class. To keep the old class on a zone, set it with **Show as** in the entity's settings.

#### Scenarios

| Change | What to do |
|---|---|
| New setups propose the Arm Away and Disarm scenarios | Nothing: existing installations keep their mappings |
| Scenarios that arm partially or instantly are read correctly | *Check your automations* that use `area_actions_details` |
| A scenario stopped by zones not ready lists the zones the panel reported | Nothing |

??? note "Details"

    *   **New setups propose the Arm Away and Disarm scenarios** from what your scenarios do to the areas you import. Existing installations keep their mappings.
    *   **Scenarios that arm partially or instantly are read correctly.** The `area_actions_details` attribute of the scenario buttons now says `total`, `partial` or `instant` for the areas a scenario arms (before, `arm` for total and `unknown` for the other two). A scenario that disarms some areas and arms others partially or instantly was treated as a disarm scenario, so if the panel refused it, the message did not list the zones that blocked it; now it does.
    *   **A scenario stopped by zones not ready lists the zones the panel reported.** The persistent notification and the `blocking_zones` of the `inim_smartliving_alarm_scenario_activation_failed` event now list the same zones as the error message, including zones at rest that the panel counts as not ready (delayed zones, for example), each with `status` `not_ready`. Before, they listed only the zones in alarm or tamper, and could say that no zones were found.

#### Commands and errors

| Change | What to do |
|---|---|
| Commands that are not carried out now show an error | *Check your automations*: a failed step stops them unless it has `continue_on_error: true` |
| Programming mode is detected; no command is sent then | Nothing |

??? note "Details"

    *   **Commands that are not carried out now show an error.** Before, a refused arm, disarm, bypass or reset was only written to the log, and the alarm panel's error notifications did not appear at all. Now the action fails with a message that tells why (for example a PIN the panel no longer accepts, or a PIN not enabled for that area), in the dashboard and in automation traces; see [When the panel refuses a command](troubleshooting.md#when-the-panel-refuses-a-command). An automation step that fails stops that automation unless the step has `continue_on_error: true`. The `inim_smartliving_alarm_scenario_activation_failed` event has a new `panel_result` field (the panel's answer, or `null` when the panel did not refuse the command), and its `reason` can now also be `command_failed` or `invalid_code`.
    *   **Programming mode is now detected.** While the panel is being programmed from a keypad, it ignores commands, and earlier versions reported such a command as carried out. Now no command is sent while the panel is in programming mode: the action fails with a message instead, and setup, **Reconfigure** and **Re-read panel configuration** are refused with a message: try again when programming has ended. A new diagnostic sensor, *Programming mode*, shows it. The `reason` of the `inim_smartliving_alarm_scenario_activation_failed` event can now also be `panel_programming`.

#### Event log and names

| Change | What to do |
|---|---|
| Event times are in Home Assistant's time zone; the state is the event's position in the log | *Check your automations* that use the state's value |
| The event log shows the names programmed on the panel; the **Reader names** option is removed | Nothing: the names come from the panel |
| Names with accented letters show them in full | Nothing |

??? note "Details"

    *   **Event log:** event times are now shown in Home Assistant's time zone, with daylight saving time (before, always UTC+1), and new events keep appearing after the panel's event log wraps around. The event log sensor's state is now the newest event's position in the panel's log (0 to 999, or 0 to 499 on panels that keep 500 events: the SmartLiving 505 and 515, and the 1050 on firmware 5.1) instead of a memory address: automations that only trigger on a change keep working, but compare the new numbers if you used the value itself.
    *   **The event log shows the names programmed on the panel**: who caused an event (the user code's or key's name, instead of *User 3* or *Key 5*) and where (the keypad's or reader's name). Commands sent remotely, from the app, the cloud, Home Assistant or the installer software, show *Remote* (before, *Mobile App*). The **Reader names** option is removed: reader names now come from the panel, and names typed in that option are no longer used. Your configuration entry is updated once more (version 1.5), and at the first start the integration reads the panel configuration again by itself to get the names (the log says so). A new diagnostic sensor, *Peripherals*, lists the keypads, readers, expanders and sirens enabled on the panel, and so does the **Areas, zones, scenarios, outputs and thermostats** step.
    *   **Names with accented letters now show them in full.** Area, zone, scenario and keypad names programmed with letters such as à, è, é or ù lost them before; they are now read with the panel's own character set, like the names of user codes, keys and other devices. The names come from the panel data already stored, so nothing is read again. Entity IDs do not change, and names you changed in Home Assistant stay as you set them.

#### Setup, options and connection

| Change | What to do |
|---|---|
| One connection to the panel, reused | Nothing; see [the Inim app](troubleshooting.md#the-inim-app-cannot-connect-locally) |
| New: SmartLAN/G boards, a network search, all areas of your user code in the active scenario | Nothing |
| The PIN is checked when you **Reconfigure**; changing it in the options works again | Nothing |
| You pick the areas, zones and scenarios from lists; unticking removes entities | Nothing: your counts became the same items |
| SmartLAN/G replies are read correctly | Nothing |

??? note "Details"

    *   Your configuration entry is updated automatically (it is marked as a SmartLAN/SI connection). Entity IDs and options do not change.
    *   Home Assistant now keeps one connection to the panel and reuses it, so updates are faster. The SmartLAN/SI board accepts only one local connection: see [the Inim app](troubleshooting.md#the-inim-app-cannot-connect-locally) under Troubleshooting.
    *   The PIN is checked from now on when you **Reconfigure**: a PIN that matches no user code of the panel is refused. If your current PIN matches none, the log shows a warning at every connection.
    *   New: SmartLAN/G boards, a network search when adding a panel, and the active scenario now takes all areas of your user code into account (previously areas 8 to 10 were ignored).
    *   Your configuration entry is updated once more (version 1.3): the zone list is completed from the panel data already stored, without contacting the panel. If that data is incomplete, a warning asks you to use **Re-read panel configuration**.
    *   **SmartLAN/G:** the board's replies are now read the way the board sends them (the data alone, without the panel's checksum). Before, every read failed and setup reported a wrong user name or password even with the right credentials. A wrong user name or password is now reported only when the board's reply cannot be decrypted; any other unexpected reply has its own message (see [Troubleshooting](troubleshooting.md#smartlang-the-reply-was-not-in-the-expected-format)).
    *   **You now pick the areas, zones and scenarios to import from lists**, by name, instead of giving how many of each. Your configuration entry is updated once more (version 1.4): the counts you had become the same items (for example, 7 areas become areas 1 to 7), so your entities do not change. A scenario used by an alarm panel mode beyond your scenario count is added, so that the mode keeps working. The options now open a menu: **Areas, zones, scenarios, outputs and thermostats** or **General settings**. Unticking an item there **removes its entities**; ticking it again brings them back with the same entity IDs. Entities left over from areas, zones or scenarios above your old counts are removed at the first start.
    *   **The `arm_area` action lists the imported areas only, by name**, and refuses areas that are not imported. The alarm panel's modes can be mapped only to imported scenarios.
    *   **Changing the PIN in the options works again**, and the PIN is no longer shown. The **New PIN Code** field in **General settings** is now empty: leave it empty to keep the current PIN, or enter a new one, which is checked with the panel as at setup. Before, the field showed the current PIN in clear text and any new PIN was refused. The PIN (and, for SmartLAN/G, the board password) is now a password field at setup and in **Reconfigure**, where it is no longer filled in: leave it empty to keep the current one. A new diagnostic sensor, *User code*, names the user code the PIN belongs to, and **General settings** says it too.
    *   **More of the integration is translated.** The notifications (a scenario blocked or failed, an area armed with zones not ready), the scenario choices of the alarm panel's modes in the options, and the names and fields of the actions now follow Home Assistant's language (English or Italian). The `message` of the `inim_smartliving_alarm_scenario_activation_failed` event stays in English. The `alarm_reset` action on a panel whose integration is not loaded now fails with a message, instead of doing nothing.

#### Panels and firmware

| Change | What to do |
|---|---|
| SmartLiving 10100, 505 and 515 use their own memory layout | Look at the log; delete leftover entities; tick new areas and zones |
| Firmware 5.1 panels use their own layout (experimental) | Look at the log |
| Firmware 5.0x and other models cannot be set up any more | Existing setups keep working, with a repair notice |

??? note "Details"

    *   **SmartLiving 10100 (experimental on firmware 5.1):** a 10100 now uses its own memory layout. Earlier versions read it as if it were a 1050, so its names and zones were wrong: at the first start after the update the integration reads the panel configuration again by itself (the log says so). Entity IDs of areas and zones stay the same; areas 11 to 15 and zones above 50 can be ticked in the options under **Areas, zones, scenarios, outputs and thermostats**.
    *   **SmartLiving 505 and 515 (experimental):** they now use their own memory layout. Earlier versions read them as if they were a 1050, so their names, zones, scenarios and event log were wrong. At the first start after the update the integration reads the panel configuration again by itself (the log says so). Entity IDs stay the same; zones and areas the panel does not have stay as unavailable entities and are listed in the log, as for the 10100: delete them in **Settings** → **Devices & services** → **Entities**. Please report how it works.
    *   **Firmware 5.1 (experimental):** panels on firmware 5.1 now use their own memory layout. Earlier versions read them as if they were a 1050 on firmware 6, so their names, zones, scenarios and event log were wrong. At the first start after the update the integration reads the panel configuration again by itself (the log says so), and the "panel not supported" repair notice goes away. Event log descriptions are now right on these panels. See the [limits of firmware 5.1 support](limitations.md). Please report how it works.
    *   **Firmware 5.0x and other models cannot be set up any more**: a new setup of a panel on firmware older than 5.1, or of a SmartLiving model without its own layout, is refused with a message. A panel of such a model, or one on firmware 5.0x, that is already set up keeps working as before and shows a repair notice, as its data may be wrong.

#### New features

| Change | What to do |
|---|---|
| Outputs can be switched and dimmed | Tick them in the options |
| System reports | See [System report](system-report.md) |
| The integration has its own icon | Shown on Home Assistant 2026.3 or later |

??? note "Details"

    *   **Outputs are new**: terminal outputs and the fixed outputs O1 to O3 can be switched, and dimmers dimmed, from Home Assistant. At the first start the integration reads the panel configuration again once by itself to get the outputs' programming and names (the log says so). No output is imported until you save the options under **Areas, zones, scenarios, outputs and thermostats**, where the outputs the installer marked as home automation outputs are ticked for you.
    *   **System reports are new**: downloading the integration's diagnostics now gives a system report for bug reports, at the level chosen with the new *Diagnostics level* select (disabled by default). See [Sharing a system report](system-report.md).
    *   **The integration has its own icon**, shown on Home Assistant 2026.3 or later; earlier versions show none, as before.

Limits of this version: see [Known limitations](limitations.md).
