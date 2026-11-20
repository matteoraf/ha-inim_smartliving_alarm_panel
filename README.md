Home Assistant Integration for Inim SmartLiving Alarm Systems
=============================================================

> [!WARNING]
> This is an experimental component. I am not affiliated with Inim Electronics and this is not an official component from Inim. Use at your own risk.
> 
> I could only test this with SmartLiving 1050 on firmware 6. The SmartLiving 505, 515 and 10100, and all four models on firmware 5.1, are supported experimentally, without a test on a real panel. Contributors are welcome.

This custom integration allows you to connect Home Assistant with Inim SmartLiving series alarm control panels that are equipped with a SmartLAN/SI or SmartLAN/G network interface (SmartLAN/G support is experimental). It enables monitoring and control of your alarm system directly from Home Assistant.

Features
--------

*   **Comprehensive Alarm Control Panel**: The core of the integration, allowing for standard arming and disarming actions. During configuration, you can map your panel's specific scenarios to Home Assistant's default actions (arm\_home, arm\_away, disarm, etc.).
    
*   **Full Area Management**: Areas (partitions) are exposed as:
    
    *   **Switch entities**: For direct arming (turn on) and disarming (turn off) of each area. The switch is on whenever the area is armed, in total, partial or instant mode; turning it on arms in the mode chosen in the options (total by default).
        
    *   **Select entities**: The arming mode of each area (Disarmed, Armed total, Armed partial, Armed instant); choosing a mode arms or disarms the area.
        
    *   **The `arm_area` action**: Arms or disarms one or more areas in a single command (see [Arming areas from automations](#arming-areas-from-automations)).
        
    *   **Zones checked before arming**: before arming an area or activating a scenario, the integration asks the panel which zones are not ready, and refuses or arms anyway with a warning, as you choose (see [Zones not ready](#zones-not-ready)).
        
    *   **Binary Sensor entities**: Per area, whether it is in alarm now, in tamper now, and whether it has an alarm memory (kept after the alarm until it is reset).
        
*   **Detailed Zone Monitoring**: Every zone of the panel (zones of input, input/output and double terminals, including the second zone of double terminals) is exposed as:
    
    *   **Binary Sensor Status entities**: On while the zone is in alarm, with a device class guessed from the zone's programming (see the zone sensor below) and the zone's programming as attributes.
        
    *   Extended attributes provide additional configuration parameters for each zone.
    
    *   **Binary Sensor Alarm memory entities**: Telling if a zone has triggered the system (until the alarm memory is reset).
    
    *   **Binary Sensor Tamper entities**: Telling if a zone is in tamper or short circuit.
    
    *   **Switch entities**: To allow individual zone exclusion.
        
*   **Advanced Scenario Control**: Scenarios are exposed as:
    
    *   **Button entities**: To activate any scenario directly. If a zone that is not ready prevents the activation, or the panel refuses it, Home Assistant shows an error and a persistent notification with the reason (see [When the panel refuses a command](#when-the-panel-refuses-a-command)).
        
    *   **Binary Sensor entities**: To indicate if a specific scenario is currently active.
        
    *   A dedicated sensor also displays the name of the single, currently active scenario on the panel.
        
*   **Outputs**: the panel's terminal outputs and its fixed outputs O1-O3 that your user code may switch, as **switch** entities (on/off and monostable outputs) and **light** entities with brightness (dimmers), named from the panel. Only the outputs the installer marked as home automation outputs are proposed (see [Outputs](#outputs)).
        
*   **Configurable Event Log**:
    
    *   A sensor entity provides a log of recent panel events, with the names programmed on the panel: who did it (user code or key) and where (keypad or reader).
        
    *   The number of events stored in the sensor's attributes is configurable during setup.
        
    *   The log persists its last known state even if the connection to the alarm panel is temporarily lost.
        
    *   The sensor's state reflects the panel's latest event index, allowing for efficient, incremental event fetching.
        
*   **Detailed Panel Information**:
    
    *   Sensors for the panel's firmware version and system type.
        
    *   A diagnostic sensor showing when the panel is in programming mode. Commands wait until programming has ended (see [When the panel refuses a command](#when-the-panel-refuses-a-command)).
        
*   **Maintenance without re-adding the integration**:
    
    *   **Reconfigure** the host, port and PIN of an existing panel, for example after its IP address changed.
        
    *   **Re-read the panel configuration** (names, zone setup, scenarios) after changing it on the panel, with a button or an action.
        

Prerequisites
-------------

*   An Inim SmartLiving alarm control panel.
    
*   The panel must be equipped with a SmartLAN/SI or SmartLAN/G network interface module. SmartLAN/G support is **experimental**: it has only been tested against a simulated panel so far, so please report how it works for you.
    
*   A supported panel, with firmware 6.x or 5.1x:
    
    | Model | Areas | Zone numbers | Status |
    |---|---|---|---|
    | SmartLiving 1050 | 10 | 1-100 | developed and tested on it |
    | SmartLiving 10100 | 15 | 1-200 | **experimental** |
    | SmartLiving 515 | 5 | 1-40 | **experimental** |
    | SmartLiving 505 | 5 | 1-20 | **experimental** |
    
    Experimental models use the panel's memory map but have not been tested on a real panel yet; a warning in the log says so. Please report how it works for you. Zone numbers above the number of terminals are the second zones of double terminals.
    
    *   **Firmware 6.x** is supported as in the table above.

    *   **Firmware 5.1x is supported experimentally, and with limits**, on all four models:
        *   it has not been tested on a real panel on firmware 5.1;
        *   the panel's state flags (such as programming mode) and the results of commands are assumed to work as on firmware 6;
        *   the event log shows the free events that only firmware 5.1 has (free events 1 to 11) as unknown events;
        *   video sensors are not supported (firmware 5.1 has none).

        A warning in the log says the panel's support is experimental. Please report how it works for you.

    *   **Firmware 5.0x and older** cannot be set up ("The panel's firmware is not supported: firmware 5.1 or newer is needed").
        
    *   **Other SmartLiving models** cannot be set up ("This SmartLiving model is not supported yet"); please report your model and firmware on GitHub.
    
*   The SmartLAN module must be connected to your local network and accessible from your Home Assistant instance.
    
*   You will need the IP address, port (typically 5004), and a valid user PIN code for the alarm panel. For SmartLAN/G you also need the user name and password of the board's web page.
    

Installation
------------

The integration is installed as a custom repository; it needs Home Assistant 2026.1 or later.

**Home Assistant 2026.11 or later (Marketplace):**

1.  Go to **Settings** -> **Marketplace**.
    
2.  Add `https://github.com/matteoraf/ha-inim_smartliving_alarm_panel` as a custom repository of type **Integration**. Adding custom repositories needs the Marketplace's GitHub connection.
    
3.  Open **Inim Smartliving Alarm** in the Marketplace, install it and restart Home Assistant.
    

**Home Assistant 2026.1 to 2026.10 (HACS):**

1.  In HACS, open the menu (⋮) -> **Custom repositories** and add the same URL with type **Integration**.
    
2.  Search for **Inim Smartliving Alarm**, download it and restart Home Assistant.
    

**Manual installation:**

1.  Download the `inim_smartliving_alarm` folder of the latest release into your `config/custom_components` folder.
    
2.  Restart Home Assistant.
    

### Preview versions

Versions such as `0.4.0a1` (preview) or `0.4.0b1` (beta) are published as GitHub *pre-releases*. They are offered only to users who ask for them:

*   **Marketplace or HACS**: turn on the repository's **Pre-release** switch to be offered pre-releases, or use **Redownload** on the integration's page and pick the version.
    
*   **Manual**: download the `inim_smartliving_alarm` folder from the pre-release on GitHub.
    

**Take a Home Assistant backup before installing a preview** (**Settings** -> **System** -> **Backups**). A preview updates your configuration entry to a newer format that the stable 0.3.x versions do not fully understand: installed over it, 0.3.x creates entities again for 10 areas, 50 zones and 30 scenarios (including those you had not imported), cannot connect to SmartLAN/G boards, and leaves the entities only the previews have (such as the arming mode selects, the outputs and the diagnostic sensors) as unavailable entities. To go back to a stable version, restore that backup; this also undoes any other change made in Home Assistant since the backup.

Please report problems with a preview in a GitHub issue, with your panel model, firmware version and board type (SmartLAN/SI or SmartLAN/G).

### Upgrading from 0.3.x

Version 0.4 reworks how the integration talks to the panel; your entity IDs and options stay as they are:

*   Your configuration entry is updated automatically (it is marked as a SmartLAN/SI connection). Entity IDs and options do not change.
    
*   Home Assistant now keeps one connection to the panel and reuses it, so updates are faster. The SmartLAN/SI module accepts only one local connection: see [the Inim app](#troubleshooting) under Troubleshooting.
    
*   The PIN is checked from now on when you **Reconfigure**: a PIN that matches no user code of the panel is refused. If your current PIN matches none, the log shows a warning at every connection.
    
*   New: SmartLAN/G boards (experimental), a network search when adding a panel, and the active scenario now takes all areas of your user code into account (previously areas 8 to 10 were ignored).
    
*   **Area switches** are now on for an area armed in partial or instant mode too (before, such an area showed as unknown and its switch was off). Turning a switch on still arms in total mode, unless you choose another mode with the new **Area switches default arm mode** option. The new `arming_mode` attribute tells the mode (`total`, `partial`, `instant` or `disarmed`); `status_text` stays `armed` or `disarmed`.
    
*   **"Area X Triggered" is now called "Area X Alarm memory"**: same entity ID, same meaning (on after an alarm until the memory is reset), now also for areas 9 and 10, with a `tamper_memory` attribute. Two new binary sensors per area show the **live** state: *Area X Alarm* and *Area X Tamper*.
    
*   **The alarm panel shows Triggered only while an area is in alarm.** Before, it stayed Triggered until the alarm memory was reset; now it returns to its armed or disarmed state when the alarm ends, and the *Alarm memory* sensors keep the memory. Automations that relied on Triggered lasting until the reset should use the *Alarm memory* sensors instead.
    
*   **Zones follow the panel's terminal types.** Output terminals (for example a siren) and unused terminals no longer get zone entities, and the second zones of double terminals (51 to 100 on the 1050) can be added: tick them in the options under **Areas, zones, scenarios and outputs** (existing installations keep the zones they had). Entities that earlier versions created for terminals that are not zones are **not deleted automatically**: they stay as unavailable entities, and the log lists them at every start (*"these entities belong to terminals that are not zones on the panel"*). Delete them in **Settings** -> **Devices & services** -> **Entities**.
    
*   **"Zone X Triggered" is now called "Zone X Alarm memory"** (same entity ID). Zones in short circuit or tamper are no longer shown as unknown: a new *Zone X Tamper* sensor turns on, and the zone's `status_text` says `short` or `tamper`. Such zones are also listed when they prevent a scenario from being activated.
    
*   **SmartLiving 10100 (experimental):** a 10100 now uses its own memory layout. Earlier versions read it as if it were a 1050, so its names and zones were wrong: at the first start after the update the integration reads the panel configuration again by itself (the log says so). Entity IDs of areas and zones stay the same; areas 11 to 15 and zones above 50 can be ticked in the options under **Areas, zones, scenarios and outputs**.
    
*   **Event log:** event times are now shown in Home Assistant's time zone, with daylight saving time (before, always UTC+1), and new events keep appearing after the panel's event log wraps around. The event log sensor's state is now the newest event's position in the panel's log (0 to 999) instead of a memory address: automations that only trigger on a change keep working, but compare the new numbers if you used the value itself.
    
*   Your configuration entry is updated once more (version 1.3): the zone list is completed from the panel data already stored, without contacting the panel. If that data is incomplete, a warning asks you to use **Re-read panel configuration**.
    
*   **SmartLAN/G:** the board's replies are now read the way the board sends them (the data alone, without the panel's checksum). Before, every read failed and setup reported a wrong user name or password even with the right credentials. A wrong user name or password is now reported only when the board's reply cannot be decrypted; any other unexpected reply has its own message (see [Troubleshooting](#troubleshooting)).
    
*   **Commands that are not carried out now show an error.** Before, a refused arm, disarm, bypass or reset was only written to the log, and the alarm panel's error notifications did not appear at all. Now the action fails with a message that tells why (for example a PIN the panel no longer accepts, or a PIN not enabled for that area), in the dashboard and in automation traces; see [When the panel refuses a command](#when-the-panel-refuses-a-command). An automation step that fails stops that automation unless the step has `continue_on_error: true`. The `inim_smartliving_alarm_scenario_activation_failed` event has a new `panel_result` field (the panel's answer, or `null` when the panel did not refuse the command), and its `reason` can now also be `command_failed` or `invalid_code`.
    
*   **New: an arming mode select per area** (*Area X arming mode*: disarmed, total, partial or instant) and the **`arm_area` action**, which arms or disarms several areas in one command (see [Arming areas from automations](#arming-areas-from-automations)).
    
*   **Programming mode is now detected.** While the panel is being programmed from a keypad, it ignores commands, and earlier versions reported such a command as carried out. Now no command is sent while the panel is in programming mode: the action fails with a message instead, and setup, **Reconfigure** and **Re-read panel configuration** wait until programming has ended. A new diagnostic sensor, *Programming mode*, shows it. The `reason` of the `inim_smartliving_alarm_scenario_activation_failed` event can now also be `panel_programming`.
    
*   **A scenario stopped by zones not ready lists the zones the panel reported.** The persistent notification and the `blocking_zones` of the `inim_smartliving_alarm_scenario_activation_failed` event now list the same zones as the error message, including zones at rest that the panel counts as not ready (delayed zones, for example), each with `status` `not_ready`. Before, they listed only the zones in alarm or tamper, and could say that no zones were found.
    
*   **You now pick the areas, zones and scenarios to import from lists**, by name, instead of giving how many of each. Your configuration entry is updated once more (version 1.4): the counts you had become the same items (for example, 7 areas become areas 1 to 7), so your entities do not change. A scenario used by an alarm panel mode beyond your scenario count is added, so that the mode keeps working. The options now open a menu: **Areas, zones, scenarios and outputs** or **General settings**. Unticking an item there **removes its entities**; ticking it again brings them back with the same entity IDs. Entities left over from areas, zones or scenarios above your old counts are removed at the first start.
    
*   **The `arm_area` action lists the imported areas only, by name**, and refuses areas that are not imported. The alarm panel's modes can be mapped only to imported scenarios.
    
*   **The event log shows the names programmed on the panel**: who caused an event (the user code's or key's name, instead of *User 3* or *Key 5*) and where (the keypad's or reader's name). Commands sent remotely, from the app, the cloud, Home Assistant or the installer software, show *Remote* (before, *Mobile App*). The **Reader names** option is removed: reader names now come from the panel, and names typed in that option are no longer used. Your configuration entry is updated once more (version 1.5), and at the first start the integration reads the panel configuration again by itself to get the names (the log says so). A new diagnostic sensor, *Peripherals*, lists the keypads, readers, expanders and sirens enabled on the panel, and so does the **Areas, zones, scenarios and outputs** step.
    
*   **Changing the PIN in the options works again**, and the PIN is no longer shown. The **New PIN Code** field in **General settings** is now empty: leave it empty to keep the current PIN, or enter a new one, which is checked with the panel as at setup. Before, the field showed the current PIN in clear text and any new PIN was refused. The PIN (and, for SmartLAN/G, the board password) is now a password field at setup and in **Reconfigure**, where it is no longer filled in: leave it empty to keep the current one. A new diagnostic sensor, *User code*, names the user code the PIN belongs to, and **General settings** says it too.
    
*   **New setups propose the Arm Away and Disarm scenarios** from what your scenarios do to the areas you import. Existing installations keep their mappings.
    
*   **Scenarios that arm partially or instantly are read correctly.** The `area_actions_details` attribute of the scenario buttons now says `total`, `partial` or `instant` for the areas a scenario arms (before, `arm` for total and `unknown` for the other two). A scenario that disarms some areas and arms others partially or instantly was treated as a disarm scenario, so if the panel refused it, the message did not list the zones that blocked it; now it does.
    
*   **Each area switch can arm in its own mode**: the new options step **Area switches arm mode per area** has one choice per imported area (use the default, total, partial or instant). Areas left at *Use the default* follow **Area switches default arm mode**, as before. The general option, called **Area switches arm in** in the 0.4.0a3 preview, is now **Area switches default arm mode**: if you changed it in that preview, set it again (it is back to total).
    
*   **Zones are checked before arming areas too.** The area switches, the arming mode selects and the `arm_area` action now ask the panel which zones are not ready, as scenarios already did, and by default **refuse to arm** when there are some (before, they armed with zones open). The new option **When zones are not ready** (General settings) can arm anyway with a warning instead; `force: true` does it for one action call. A new action, `activate_scenario`, activates a scenario from automations, and a new event, `inim_smartliving_alarm_zones_not_ready`, lists the zones each time. The integration also reads whether the panel itself refuses arming with zones not ready (new diagnostic sensor *Refuses arming with zones not ready*): at the first start it reads the panel configuration again once by itself (the log says so). Zone sensors show each zone's programming options (`autobypass`, `no_arming_if_not_ready`, `interior` and more) as attributes.
    
*   **Zone attributes now show the zone's real programming**, read from the panel configuration already stored (nothing is read again). Before, most zone types and balancings showed as *Unknown*, and two times were read wrongly. Some attributes are renamed, so check templates and automations that use them: `activation_type` is now `zone_type`, `balancing_type` is `balancing`, `sensor_type` is `sensor_kind`, `duration_ms` is now `alarm_pulse_duration` (or `shock_sensitivity` for shock sensors), `time` is `pulse_window_time` and `pulses` is `pulse_count`. New: `alarm_cycles`. Values are now lower-case English keys (for example `delayed`, `double_eol`, `input` instead of *Delayed*, *Double Balancing*, *Input*).
    
*   **Zone device classes follow the zone's programming.** Single EOL zones are now *opening* (before, no class), shock sensors are *vibration* (before, *opening* or none), and roller-blind sensors are *opening* (before, *motion*). Normally open, normally closed and double EOL zones keep their class. To keep the old class on a zone, set it with **Show as** in the entity's settings.
    
*   **Each area's delays are shown**: the *Area X arming mode* select's attributes now include the area's exit and entry times, entry time 2, pre-arm warning and patrol times, timer program and three options, read from the panel. At the first start the integration reads the panel configuration again once by itself to get them (the log says so); if it cannot, they appear after the next start or a **Re-read panel configuration**.
    
*   **SmartLiving 505 and 515 (experimental):** they now use their own memory layout. Earlier versions read them as if they were a 1050, so their names, zones, scenarios and event log were wrong. At the first start after the update the integration reads the panel configuration again by itself (the log says so). Entity IDs stay the same; zones and areas the panel does not have stay as unavailable entities and are listed in the log, as for the 10100: delete them in **Settings** -> **Devices & services** -> **Entities**. Please report how it works.
    
*   **Firmware 5.1 (experimental):** panels on firmware 5.1 now use their own memory layout. Earlier versions read them as if they were a 1050 on firmware 6, so their names, zones, scenarios and event log were wrong. At the first start after the update the integration reads the panel configuration again by itself (the log says so), and the "panel not supported" repair notice goes away. Event log descriptions are now right on these panels. See the [limits of firmware 5.1 support](#known-limitations). Please report how it works.

*   **Firmware 5.0x and other models cannot be set up any more**: a new setup of a panel on firmware older than 5.1, or of a SmartLiving model without its own layout, is refused with a message. A panel of such a model, or one on firmware 5.0x, that is already set up keeps working as before and shows a repair notice, as its data may be wrong.
    
*   **Outputs are new**: terminal outputs and the fixed outputs O1 to O3 can be switched, and dimmers dimmed, from Home Assistant. At the first start the integration reads the panel configuration again once by itself to get the outputs' programming and names (the log says so). No output is imported until you save the options under **Areas, zones, scenarios and outputs**, where the outputs the installer marked as home automation outputs are ticked for you. Please try a harmless output first.

*   **System reports are new**: downloading the integration's diagnostics now gives a system report for bug reports, at the level chosen with the new *Diagnostics level* select (disabled by default). See [Sharing a system report](#sharing-a-system-report).

*   Limits of this version: see [Known limitations](#known-limitations).
    

Configuration
-------------

After installation and restarting Home Assistant:

1.  Go to **Settings** -> **Devices** & **Services**.
    
2.  Click the **\+ ADD INTEGRATION** button in the bottom right.
    
3.  Search for "Inim SmartLiving Alarm" and select it.
    
4.  Choose **Search the network** to look for SmartLAN boards: the boards that answer (and are not set up yet) are listed, and the one you pick has its address and port filled in on the next page. The search only finds boards on the **same subnet as Home Assistant**. Otherwise, or if your board is not found, choose how the panel is connected, **SmartLAN/SI board** or **SmartLAN/G board (experimental)**, and enter its details yourself.
    
5.  A configuration dialog will appear. Enter the following details:
    
    *   **Host**: The IP address of your Inim SmartLAN module (e.g., 192.168.1.100).
        
    *   **Port**: The TCP port your SmartLAN module is listening on (default is 5004).
        
    *   **Board user name** and **Board password** (SmartLAN/G only): the credentials of the board's web page. The SmartLAN/G board encrypts the connection with them.
        
    *   **PIN**: A valid user PIN code for your alarm panel (1 to 6 digits). Commands are sent with this code, so it needs access to the areas you want to control.
        
    *   **Panel Name** (Optional): A friendly name for your alarm panel in Home Assistant (e.g., "Home Alarm").
        
6.  Click **Submit**. The integration connects to your panel, checks it and reads its configuration. The setup is refused, with a message, if the panel cannot be reached, if the PIN does not match any user code of the panel, if the panel's firmware is older than 5.1, or if the panel is a SmartLiving model without its own layout (see the supported models above).
    
7.  If successful, choose the **areas, zones, scenarios and outputs** to import, in sections of checkboxes (each can be folded). Each one you tick gets its entities; they are listed with their number and the name programmed on the panel:
    
    *   **Areas**: every area of the panel (5 on a SmartLiving 505 or 515, 10 on a 1050, 15 on a 10100).
        
    *   **Zones**: the zones of the panel. Only terminals programmed as zones are listed (outputs and unused terminals are not), including the second zones of double terminals (11 to 20 on the 505, 21 to 40 on the 515, 51 to 100 on the 1050, 101 to 200 on the 10100).
        
    *   **Scenarios**: every scenario of the panel (30).
        
    *   **Outputs**: the outputs your user code may switch, with their kind (on/off, monostable with its time, or dimmer); the section is shown only if there are some. **Only the outputs the installer marked as home automation outputs are ticked** (firmware 6): an output can drive a siren, a lock or a gate, so tick only the ones you mean to switch from Home Assistant (see [Outputs](#outputs)).
        
    The setup ticks what the panel shows in use: the zones that belong to an area, the areas of those zones, and the scenarios that change at least one area, and the outputs the installer marked as home automation outputs. Tick or untick anything you like.

    The same step lists, for reference, the keypads, readers, expanders and sirens enabled on the panel, by name.
    
8.  Then the **Initial Options**:
    
    *   **Polling Interval**: How often Home Assistant should query the panel for status updates (in seconds).
        
    *   **Event Log Size**: The number of recent events to store and display in the event log sensor's attributes.
        
    *   **Scenario Mappings**: You can map your panel's scenarios to Home Assistant's standard alarm actions (Arm Home, Arm Away, etc.). Only the scenarios you import are offered. The setup proposes **Arm Away** and **Disarm**: for Arm Away, an imported scenario that arms every imported area (one that arms them all in total mode first, then the lowest number); for Disarm, the lowest-numbered imported scenario that disarms every imported area. What a scenario does to areas you don't import doesn't matter. If none fits, the mode stays unmapped. Check the proposal and change it if you like; the options keep what you saved.

    *   **Area switches default arm mode** (options only, not at setup): the mode an area switch arms in when turned on: total (the default), partial or instant. The *arming mode* select of each area offers every mode. In the options, **Area switches arm mode per area** sets a different mode for single areas (for example, partial for the bedrooms); the others follow this option.

    *   **When zones are not ready** (options only, not at setup): *Refuse* (the default) or *Arm anyway and warn*; see [Zones not ready](#zones-not-ready).

        
9.  Click **Submit** to complete the setup.
    

You can change all of this later: on the Devices & Services page, open the integration and click **CONFIGURE**, then choose **Areas, zones, scenarios and outputs**, **Area switches arm mode per area** (the mode each area's switch arms in, if not the general one) or **General settings** (everything else, including the PIN: leave **New PIN Code** empty to keep the current PIN; a new PIN is checked with the panel as at setup. The step names the user code the current PIN belongs to and the areas it controls). Unticking an area, zone or scenario removes its entities, together with any name or entity ID you gave them; ticking it again brings them back with their original entity IDs. A scenario that an alarm panel mode uses cannot be unticked: change the mode's scenario in the general settings first.

### Changing the connection details (Reconfigure)

If the IP address, port or PIN of your panel (or, for SmartLAN/G, the board's user name or password) changes, you don't need to remove and re-add the integration:

1.  Go to **Settings** -> **Devices & Services** and open **Inim SmartLiving Alarm**.
    
2.  Open the menu (⋮) of your panel's entry and choose **Reconfigure**.
    
3.  Enter the new **Host**, **Port**, **PIN** (and, for SmartLAN/G, **Board user name** and **Board password**) and click **Submit**. The PIN and the board password are not filled in: leave them empty to keep the current ones.
    

The integration contacts the panel with the new details, reads its configuration again and reloads. Your options (polling interval, imported areas, zones and scenarios, scenario mappings), entities and entity IDs are kept. If the panel cannot be reached, an error is shown and nothing is changed. An address already used by another configured panel is refused, and so is a PIN that does not match any user code of the panel. The board type (SmartLAN/SI or SmartLAN/G) cannot be changed here: remove the integration and add it again for that.

### Re-reading the panel configuration

The names of areas, zones, scenarios, user codes, keys, keypads, readers, expanders, timers and sirens, which keypads, readers, expanders and sirens are enabled, the zone setup and the scenario definitions are read from the panel when the integration is set up. If you change them on the panel later (from a keypad or the installer software), read them again with either:

*   the **Re-read panel configuration** button, in the *Configuration* section of your panel's device page, or
    
*   the **Re-read panel configuration** action (`inim_smartliving_alarm.reread_panel_configuration`), for example from an automation or from **Developer tools** -> **Actions**, where you pick the panel from a list:
    

```yaml
action: inim_smartliving_alarm.reread_panel_configuration
data:
  config_entry_id: <the panel's config entry ID>
```

The integration then reloads with the new configuration. Your options are not changed, and entity IDs stay the same; entity names follow the new names from the panel. The areas, zones and scenarios you import are not changed either: a zone wired later or a new scenario is listed, unticked, in the options under **Areas, zones, scenarios and outputs**. If the panel cannot be reached, or it is in programming mode, an error is shown and the stored configuration is kept: in programming mode, try again when programming has ended.

Entities Provided
-----------------

This integration exposes your Inim alarm system's components as various Home Assistant entities. The entity IDs are programmatically generated based on the panel name and the names of your areas, zones, and scenarios fetched from the panel.

*   **alarm\_control\_panel.your\_panel\_name**: The main entity to arm and disarm the system. Its actions (Arm Home, Arm Away, etc.) are linked to the scenarios you mapped during setup. It shows *Triggered* while any area is in alarm.
    
*   **Areas**:
    
    *   *switch.your\_panel\_name\_area\_X*: Allows direct arming (on) and disarming (off) of an individual area. It is on when the area is armed in any mode (total, partial or instant); turning it on arms in the area's own mode set under **Area switches arm mode per area**, else in the mode of the **Area switches default arm mode** option (total by default). Attributes: `status_text` (`armed`, `disarmed` or `unknown`) and `arming_mode` (`total`, `partial`, `instant`, `disarmed` or `unknown`).
        
    *   *select.your\_panel\_name\_area\_X\_arming\_mode* (named *Area X arming mode*): The area's arming mode: *Disarmed*, *Armed total*, *Armed partial* or *Armed instant* (unknown while the panel reports none of these). Choosing a mode arms the area in that mode or disarms it; if the panel refuses, an error says why. Attributes: `area_id` and `area_name`.
        
        Its attributes also show the area's delays and options, as programmed on the panel: `exit_time` (time to leave after arming) and `entry_time` (time to disarm after a delayed zone opens), for example `120 s` or `2 min`; `entry_time_2` (the second entry time, used by zones with the `entry_time_2` option) or `not used`; `pre_arm_warning_time` and `patrol_time` (in minutes, for example `3 min`); `timer_program` (the arm/disarm timer program, or `none`); and `reset_alarm_memory_on_arming`, `auto_arm_partial` and `clear_call_queue_on_disarm` (`true` or `false`). They are not shown for panels handled with another model's layout (see [Known limitations](#known-limitations)).
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_alarm*: On while the area is in alarm.
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_tamper*: On while the area is in tamper.
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_triggered* (named *Area X Alarm memory*): On after an alarm in the area until the alarm memory is reset (see [Resetting the alarm memory](#resetting-the-alarm-memory)). The `tamper_memory` attribute tells whether the area also has a tamper memory.
        
*   **Zones**:
    
    Only zones that exist on the panel get entities: zones of input, input/output and double terminals. Output terminals (for example a siren) and unused terminals have none. Zones 1 to N are the zones of the panel's N terminals (50 on the SmartLiving 1050, 100 on the 10100); a double terminal *n* also has a second zone, numbered N + *n* (51 to 100 on the 1050, 101 to 200 on the 10100).

    *   *binary\_sensor.your\_panel\_name\_zone\_X*: Represents a single zone. It is on only while the zone is in alarm (open); a short circuit or tamper shows in `status_text` (`clear`, `alarmed`, `short` or `tamper`) and in the zone's tamper sensor. `test` tells whether the zone is in test now.
        
        **Device class**: a wired zone does not tell the panel what detector it has, so the device class is a guess from the zone's programming: a shock sensor is *vibration*; a roller-blind sensor, a normally open or normally closed contact and a single EOL balancing are *opening*; a double EOL balancing is *motion*; anything else has no class. If the guess is wrong, change it in Home Assistant: open the entity's settings and pick **Show as**.
        
        **Attributes**: the zone's programming as read from the panel, with stable English values:
        
        *   `zone_type`: `instant`, `delayed`, `viewable_delayed`, `route`, `24_hour`, `automation`, `arm`, `disarm`, `switch`, `arm_disarm`, `patrol`, `emergency_exit_without_call` or `emergency_exit` (a number if the panel uses a value not listed here).
        *   `alarm_cycles`: how many times the zone can raise an alarm, or `unlimited`.
        *   `sensor_kind`: `generic`, `roller_blind` or `shock`, from the terminal's programming. It decides the next attributes:
            *   `alarm_pulse_duration` (generic sensors only), for example `300 ms` or `20 s` (the panel counts it in steps of 15 ms or of 10 s);
            *   `shock_sensitivity` (shock sensors only), 0 to 40;
            *   `pulse_window_time`, the time in which pulses are counted (for example `30 s` or `1 min`), and `pulse_count`, how many pulses in that time raise the alarm.
        *   `balancing`: `normally_open`, `normally_closed`, `single_eol`, `double_eol`, `double_zone`, `double_zone_eol` or `personalized`.
        *   `assigned_areas`, `terminal_index`, `terminal_type` (`input`, `in_out`, `double`, …), `second_zone` (the second zone of a double terminal) and `internal_index`.
        *   The programming options, each `true` or `false`: `interior`, `autobypass` (bypassed by the panel when its areas arm while it is open), `unbypassable`, `chime`, `test_programmed`, `autobypass_no_reinclude` (autobypassed and not re-included when back at rest), `no_arming_if_not_ready` (stops arming in every mode, when the panel refuses arming with zones not ready), `entry_time_2`, `last_exit_zone`, `reinclude_on_disarm`, `hold_up` and `fault_zone`. Options that only apply to wireless zones are not shown.

    *   *binary\_sensor.your\_panel\_name\_zone\_X\_triggered* (named *Zone X Alarm memory*): Turns on if the zone has triggered the system, until the alarm memory is reset. The `tamper_memory` attribute tells whether the zone's terminal has a tamper memory (both zones of a double terminal share it).

    *   *binary\_sensor.your\_panel\_name\_zone\_X\_tamper*: Turns on while the zone is in tamper or short circuit (`status_text` tells which).

    *   *switch.your\_panel\_name\_zone\_X*: Lets you enable or disable (exclude) individual zones, including the second zone of double terminals.
        
*   **Scenarios**:
    
    *   *button.your\_panel\_name\_scenario\_X*: Allows direct activation of a specific scenario. If the activation fails (a zone not ready, or the panel refuses it), an error and a persistent notification with the reason appear in Home Assistant. The `area_actions_details` attribute tells what the scenario does to each area: `keep`, `total`, `partial`, `instant` or `disarm`.
        
    *   *binary\_sensor.your\_panel\_name\_scenario\_X\_active*: A binary sensor that is 'on' if that specific scenario is currently the active one on the panel.
        
*   **Sensors**:
    
    *   *sensor.your\_panel\_name\_event\_log*: The state is the position of the newest event in the panel's event log (0 to 999; it starts again from 0 when the log wraps around), so it changes with every new event. The attributes contain a human-readable log of recent events: `authorized_by` is the name of the user code or key, `device` the name of the keypad or reader (*Remote* for commands from the app, the cloud, Home Assistant or the installer software). Event times are the panel's clock read in Home Assistant's time zone, with daylight saving time.
        
    *   *sensor.your\_panel\_name\_active\_scenario*: A text sensor that displays the name of the currently active scenario.
        
    *   *sensor.your\_panel\_name\_firmware\_version* & *sensor.your\_panel\_name\_system\_type*: Provide static information about your alarm panel.
        
*   **Outputs** (only the ones you tick; see [Outputs](#outputs) below):
    
    *   *switch.your\_panel\_name\_output\_name*: An on/off or monostable output. It is on while the panel shows the output active (read at every poll). A monostable output switches itself off after its time; the switch shows it off at the next poll.
        
    *   *light.your\_panel\_name\_output\_name*: A dimmer output, with brightness. On firmware 6 the level is read from the panel at every poll and after each change; on firmware 5.1 the panel does not report it, so the brightness is the last one set from Home Assistant.
        
    *   Both are named with the output's name on the panel (*Output n* or *O1* to *O3* without one). Attributes: `output_number`, `kind` (`on_off`, `monostable` or `dimmer`), `terminal_index` or `fixed_output` (1 to 3), `monostable_time` (for example `30 s`), `home_automation` (`true` if the installer marked it as a home automation output; not shown on firmware 5.1) and the output's options `normally_closed`, `flashing`, `buzzer`, `keep_on_restoral`, `toggle` and `relay_use` (`true` or `false`).
        
*   **Maintenance**:
    
    *   *button.your\_panel\_name\_reread\_panel\_configuration*: Reads the panel configuration again and reloads the integration (see [Re-reading the panel configuration](#re-reading-the-panel-configuration)). Shown in the *Configuration* section of the device page.
        
    *   *sensor.your\_panel\_name\_peripherals* (named *Peripherals*): How many keypads, readers, expanders and sirens are enabled on the panel; the `keypads`, `readers`, `expanders` and `sirens` attributes list their names. It does not tell whether a device is answering. Shown in the *Diagnostic* section of the device page.

    *   *sensor.your\_panel\_name\_user\_code* (named *User code*): The name of the user code the PIN belongs to (never the PIN); the `number`, `areas` (the areas it controls, by name) and `type` (standard, manager or master) attributes give more. Commands only work on those areas. Unknown if the PIN matches no user code: commands are refused then. Checked again whenever the integration reloads. Shown in the *Diagnostic* section of the device page.

    *   *binary\_sensor.your\_panel\_name\_refuses\_arming\_with\_zones\_not\_ready* (named *Refuses arming with zones not ready*): On when the installer set the panel to refuse arming with zones not ready (see [Zones not ready](#zones-not-ready)). Read with the panel configuration; unknown until the configuration is read again by this version. Shown in the *Diagnostic* section of the device page.

    *   *binary\_sensor.your\_panel\_name\_programming\_mode* (named *Programming mode*): On while the panel is in programming mode (being programmed from a keypad, or reloading its configuration). Commands are not sent then. Shown in the *Diagnostic* section of the device page. It is unavailable for panels handled with the SmartLiving 1050's layout (other models, firmware 5.0x).
        

    *   *select.your\_panel\_name\_diagnostics\_level* (named *Diagnostics level*): How much the next diagnostics download shows: *Redacted*, *Full* or *Full confidential* (see [Sharing a system report](#sharing-a-system-report)). Disabled by default; it goes back to *Redacted* after each restart and after a Full report has been downloaded. Shown in the *Diagnostic* section of the device page.

_(Replace your\_panel\_name, with actual value based on your setup)._

Usage Examples
--------------

### Outputs

> [!WARNING]
> An output switches whatever is wired to it: a siren, a lock, a gate, heating. Switching it from Home Assistant, or from an automation, acts on the real device. Before ticking an output, check what it drives; when you first try the integration's outputs, use one that is harmless to switch.

*   **What is offered**: outputs programmed on the panel as on/off, monostable or dimmer outputs (terminal outputs and O1 to O3) that your user code may switch. Analogue outputs are not offered. A terminal output on one of the panel's first three terminal positions cannot be switched remotely (the panel reserves those numbers for O1 to O3), so it is not listed.
    
*   **What is ticked for you**: only the outputs the installer marked as *home automation* outputs in the panel's programming (firmware 6; on firmware 5.1 the integration cannot read this mark, so nothing is ticked). The panel cannot tell what an output drives, so check them, and pick any other outputs yourself, in the setup or later in the options under **Areas, zones, scenarios and outputs**. The first time an existing installation shows the outputs in the options, the home automation outputs are ticked there too; after you save, your choice is kept.
    
*   **Nothing is switched by itself**: no output is switched at setup, at start, at a reload or when Home Assistant restores its state.
    
*   **When the panel refuses**: an output your user code may not switch, or one locked while its areas are armed, gives an error with the reason (see [When the panel refuses a command](#when-the-panel-refuses-a-command)). While the panel is in programming mode, no command is sent.
    

### Basic Alarm Control

Use the alarm\_control\_panel entity in a standard Lovelace Alarm Panel card. If you've mapped scenarios, the arm actions will trigger those.

### Scenario Activation

Use the button entities in your dashboard or automations to directly activate specific panel scenarios. From automations you can also use the action `inim_smartliving_alarm.activate_scenario`: pick the panel and the scenario from the lists (the scenarios you import, by name; in YAML, the scenario number as on the panel). Set `force: true` to activate it even if zones are not ready (see [Zones not ready](#zones-not-ready)).

```yaml
action: inim_smartliving_alarm.activate_scenario
data:
  config_entry_id: your_config_entry_id
  scenario: 2
```

### Resetting the alarm memory

After an alarm, the panel keeps the alarm memory of the areas involved until it is reset (the *Area X Alarm memory* sensors show it). The action `inim_smartliving_alarm.alarm_reset` resets every area that has an alarm or tamper memory, on the targeted panel:

```yaml
action: inim_smartliving_alarm.alarm_reset
target:
  entity_id: alarm_control_panel.your_panel_name
```

You can call it from a script, an automation or a dashboard button.

### Arming areas from automations

The action `inim_smartliving_alarm.arm_area` arms or disarms one or more areas of a panel in a single command, so they change together. Pick the panel and the areas from the lists in the action editor: the areas list shows the areas you import, by name. `mode` is `total`, `partial`, `instant` or `disarm`. In YAML, `areas` are the area numbers as on the panel:

```yaml
action: inim_smartliving_alarm.arm_area
data:
  config_entry_id: your_config_entry_id
  areas: [1, 3]
  mode: partial
```

With several panels, each area number in the list shows the area of every panel that imports it, with the panel's name; the panel you pick decides which one is armed. An area that is not imported from that panel (for example, one you unticked after writing the automation) is refused with a message, and nothing is sent.

Before arming, the action checks the zones, like the area switches and the arming mode selects (see [Zones not ready](#zones-not-ready)); add `force: true` to arm even if zones are not ready. Disarming is never checked. If the panel refuses the command, the action fails with the reason (see [When the panel refuses a command](#when-the-panel-refuses-a-command)).

### Zones not ready

Before it arms an area (switch, arming mode select, `arm_area` action) or activates a scenario (button, alarm panel, `activate_scenario` action), the integration asks the panel which zones are not ready for it: zones that are not at rest and would be armed. Zones the panel bypasses by itself when arming (autobypassable zones) are not listed. Then the option **When zones are not ready** (General settings) decides:

*   **Refuse** (the default): nothing is sent; the action fails with a message that lists the zones (for example *"Area 3 was not armed because these zones are not ready: Kitchen window (12)"*).
    
*   **Arm anyway and warn**: the command is sent, and a persistent notification lists the zones. They can set off an alarm unless they are closed or bypassed in time.
    

`force: true` on the `arm_area` and `activate_scenario` actions arms anyway for that call, whatever the option says.

Either way the `inim_smartliving_alarm_zones_not_ready` event fires, for example to announce the open zones: `target` (the area or scenario name), `action` (`refused`, `armed_anyway`, or `refused_by_panel` when it was sent anyway and the panel itself refused it), `zones` (each with `zone_id` and `zone_name`) and `panel_refuses_not_ready`.

The panel itself can also be set by the installer not to allow arming with zones not ready. The integration reads that setting with the panel configuration and shows it in the *Refuses arming with zones not ready* sensor (and as `panel_refuses_not_ready` in the event: `true`, `false`, or `null` before the configuration was read again). When it is on, the panel refuses such a command even when you arm anyway or use `force`: the integration still sends it, the message says the panel refused it, and the event's `action` is `refused_by_panel`. The panel's own rule depends on each zone's programming (for example, delayed zones do not stop total arming, and zones with `no_arming_if_not_ready` stop every mode), so it may let through zones the integration lists. When the setting is off, the panel arms whatever the zones' programming says.

### Displaying the Event Log

You can display the event log using a Markdown card in Lovelace, but I recommend the custom:flex-table-card (from HACS) to provide a nice tabular view.

### Sample UI Components

You'll find yaml samples for UI components in the repo.


Known limitations
-----------------

Known limitations of this version:

*   On recent Home Assistant versions, new installations get entity IDs with the panel name twice (for example `switch.home_home_area_3`); existing installations keep their IDs.
    
*   Panels on firmware 5.0x or of another model that were set up with an earlier version keep running with the 1050's firmware 6 layout, so their names, zones, scenarios and event log may be wrong and their areas' delays are not shown. A repair notice says so. New setups of such panels are refused.

*   Dimmer levels are read from the panel on firmware 6 only: firmware 5.1 does not report them. On firmware 5.1 a dimmer's brightness is the last one set from Home Assistant, so a level changed at a keypad is not shown.

*   Switching outputs has been tested only on unused outputs: when you first try it, use an output that is harmless to switch, and please report how it works.

*   Analogue outputs are not offered. A terminal output on one of the panel's first three terminal positions cannot be switched remotely: the panel's output command cannot address those positions.

*   Support for firmware 5.1 is experimental and limited: it has not been tested on a real panel on firmware 5.1; the panel's state flags (such as programming mode) and the results of commands are assumed to work as on firmware 6; the free events that only firmware 5.1 has (free events 1 to 11) are shown as unknown events in the event log; and firmware 5.1 has no video sensors.
    

### When the panel refuses a command

Arming, disarming, bypassing a zone, activating a scenario, resetting the alarm memory and switching an output fail with a message when they are not carried out. The message says which of three things happened:

*   **The integration did not send the command**:
    
    *   Before arming or activating a scenario, the integration checks the zones (see [Zones not ready](#zones-not-ready)). If some are not ready and the option says *Refuse*, the message lists them and nothing is sent to the panel.
        
    *   While the panel is in programming mode, it ignores commands, so none is sent (*"The command for Area 3 was not sent: the panel is in programming mode. Try again when programming has ended."*). If the panel enters programming mode while a command is being sent, the message says that the command may not have been carried out: check the area or zone, and try again when programming has ended.
    
*   **The panel refused it**: the message gives the panel's reason:
    
    | Message | What to do |
    |---|---|
    | The panel did not accept the PIN | The user code was changed or deleted on the panel: enter the current PIN with **Reconfigure**. |
    | The PIN is not enabled for it | The user code does not control that area: enable it on the panel or use another code. |
    | The panel does not allow arming while zones are not ready | Close or bypass the open zones of the area, then arm again. |
    | The zone cannot be bypassed / belongs to no area | The zone's programming on the panel does not allow it. |
    | Already in the requested state | Nothing to do: the zone or area was already as requested. |
    | Its terminal is out of range / not connected / not in use | The zone's or output's terminal has changed on the panel: use **Re-read panel configuration**. |
    | The output is not enabled | Your user code may not switch that output: enable it for the code on the panel, or untick the output. |
    | The output is locked while its areas are armed | The panel does not let the output change while its areas are armed: disarm them first. |
    | The panel refused the command (result code N) | The panel gave no reason, or one this integration does not know yet. |
    
*   **The command could not be completed**: the panel could not be reached, or its answer could not be read. See *Cannot Connect* below; try again once the connection is back.
    

Scenario activations that fail also show a persistent notification and fire the `inim_smartliving_alarm_scenario_activation_failed` event, as before.

Sharing a system report
-----------------------

When you report a problem, a system report tells what the integration knows about your panel: model and firmware, board, the panel's programming as read from it, status, the event log, the integration's options and entities, and connection statistics. Download it from **Settings** -> **Devices & services** -> *Inim SmartLiving* -> the three dots of your panel -> **Download diagnostics** (the panel's device page has the same entry). It takes about 40 seconds on SmartLAN/SI; commands are not held up meanwhile.

The *Diagnostics level* select (enable it on the device page, in the *Diagnostic* section) chooses how much the report shows:

| Level | What it contains |
|---|---|
| **Redacted** (default) | Everything public: the programming, status, events with their times, statistics. Names are cut to their first 3 characters, the serial number to its last 4 digits, the board's MAC address to its last 2 bytes; addresses, PINs, phone numbers, key serials and passwords are left out. Safe to post. |
| **Full** | Also the names, the serial number and the network addresses. PINs, phone numbers, key serials and passwords are still left out. |
| **Full confidential** | Everything, including the PINs, phone numbers, key serials and the board's passwords. |

At **Redacted** one download gives a plain file you can attach to a GitHub issue as it is.

At **Full** and **Full confidential** you get two files from the same reading of the panel:

1.  The first download is **encrypted**: only the maintainer can read it. It also holds the Redacted view in clear. **Attach this file to your GitHub issue**; it is the only file to share.
2.  Download diagnostics once more within 10 minutes to get a **readable copy** of the same report (it holds `PRIVATE COPY`), to check what you are sharing. **Do not share it**: keep it private and delete it when you no longer need it. A notification reminds you of this after the first download. Both files get the same name, so your browser saves the second one with a number added (for example `(1)`): attach the first one, which holds `encrypted` and `redacted_view`, never the one that holds `PRIVATE COPY`.

Then the level goes back to *Redacted*. Nothing is stored: the report is held in memory only until the second download, for 10 minutes at most.

The board's own details (its network settings and firmware) are included only when Home Assistant is on the same network as the board. What is never read, at any level: the panel's cloud keys, account ID and address, and the board's internal keys. If GitHub refuses the file, zip it first.

### Reading a report

The repository has a small viewer for reports, `scripts/inim_report.py` (Python 3.13 or newer; nothing to install for reading). Download the repository from GitHub, then:

*   `python3 scripts/inim_report.py show REPORT.json` shows a report: a summary first, then areas, zones, scenarios, devices, outputs, status, events and the connection. Add `--html report.html` for one page to open in a browser.
*   `python3 scripts/inim_report.py check REPORT.json` says whether a file is the one to attach (the Redacted report, or the encrypted one) or the private copy, which you should not share.

The maintainer reads an encrypted report with `show REPORT.json --decrypt` (add `--html report.html` for the page); it needs the maintainer's key. It reads the files only: it connects to nothing. You can view your own Redacted report and your readable copy with it; the encrypted file shows its Redacted view.

Troubleshooting
---------------

*   **Reporting a problem**: attach a system report to the GitHub issue (see [Sharing a system report](#sharing-a-system-report)), and if you can a debug log (see below).
    
*   **Cannot** Connect / **Connection Timed Out**:
    
    *   Verify the IP address and Port for your SmartLAN module are correct.
        
    *   Ensure your Home Assistant instance can reach the panel's IP address on the specified port (check firewalls, network segmentation).
        
    *   Make sure the SmartLAN module is powered and connected to the network.
        
    *   If the module's IP address or port has changed, update it with **Reconfigure** (see [Changing the connection details](#changing-the-connection-details-reconfigure)) instead of removing the integration.
        
*   **Search the network finds no board**:
    
    *   The search only reaches boards on the same subnet as Home Assistant (the boards answer only on their own network segment). Enter a board on another subnet or VLAN by hand.
        
    *   The search listens on UDP ports 5001 (SmartLAN/SI) and 7001 (SmartLAN/G) of the Home Assistant host; another program using them, or a firewall, prevents it. Boards that are already set up are not listed.
        
*   **SmartLAN/G: "the board answered, but its reply was not in the expected format"** during setup:
    
    *   Check the board's user name and password first. If they are right, your board probably answers in a way this integration does not handle yet. Please open a GitHub issue with your panel model and firmware, the board's version and a debug log of a setup attempt. To get the log, add this to `configuration.yaml`, restart Home Assistant and try the setup again:
        
        ```yaml
        logger:
          logs:
            custom_components.inim_smartliving_alarm: debug
        ```
        
        The log shows the requests and replies (the PIN is masked; the board's user name and password are never logged). Remove the lines again afterwards.
        
*   **The Inim app cannot connect locally while Home Assistant is running**:
    
    *   The SmartLAN/SI module accepts only one local connection at a time. Home Assistant keeps its connection open while it reads the panel and for a few seconds after each update, so the app may have to retry. Remote access through Inim Cloud is not affected.
        
*   **Names, zones or scenarios out of date** after changing them on the panel:
    
    *   Use the **Re-read panel configuration** button or action (see [Re-reading the panel configuration](#re-reading-the-panel-configuration)).
        
*   **Scenarios not activating or Areas not arming/disarming**: the error message tells why (see [When the panel refuses a command](#when-the-panel-refuses-a-command)).
    
    
    *   Double-check that the PIN code entered during configuration is correct and is a valid user PIN for the panel with sufficient permissions. Setup and **Reconfigure** refuse a PIN that matches no user code; for a panel set up with an older version of the integration, the log shows a warning ("The PIN does not match any user code of the panel") each time the integration connects. You can change it with **Reconfigure** (see [Changing the connection details](#changing-the-connection-details-reconfigure)).
        
*   **Commands fail with "the panel is in programming mode"**:
    
    *   Someone is programming the panel from a keypad, or the panel is reloading its configuration. Commands work again when programming has ended; the *Programming mode* sensor shows when.
        
    *   Programming from the installer software on a PC does not block commands. After the software writes new programming, the panel may restart by itself, and Home Assistant cannot reach it for a short time. Then use **Re-read panel configuration** to pick up the changes.
        
*   **Arming is refused because zones are not ready**: close or bypass the zones the message lists, or set **When zones are not ready** to *Arm anyway and warn* (or use `force: true` in an action). See [Zones not ready](#zones-not-ready).
    
*   **Entities Not Appearing or Not Updating**:
    
    *   Check the Home Assistant logs (Settings -> System -> Logs) for errors related to the inim\_smartliving\_alarm integration.
        
    *   Ensure the polling interval is set appropriately. Too short might overload the panel or network; too long will delay updates.
        
*   **Incorrect Number of Entities**:
    
    *   Check the integration's options under **Areas, zones, scenarios and outputs**: only ticked items get entities.
        
    *   A zone is listed only if its terminal is programmed as a zone on the panel. If you changed the panel's programming, use **Re-read panel configuration** first.
        

Contributing
------------

Contributions to this integration are welcome! Please feel free to open an issue or submit pull requests.