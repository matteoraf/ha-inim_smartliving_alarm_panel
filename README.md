Home Assistant Integration for Inim SmartLiving Alarm Systems
=============================================================

> \[!WARNING\]This is an experimental component. I am not affiliated with Inim Electronics and this is not an official component from Inim. Use at your own risk.
> 
> I could only test this with SmartLiving 1050. The SmartLiving 10100 is supported experimentally, without a test on a real panel. Contributors are welcome.

This custom integration allows you to connect Home Assistant with Inim SmartLiving series alarm control panels that are equipped with a SmartLAN/SI or SmartLAN/G network interface (SmartLAN/G support is experimental). It enables monitoring and control of your alarm system directly from Home Assistant.

Features
--------

*   **Comprehensive Alarm Control Panel**: The core of the integration, allowing for standard arming and disarming actions. During configuration, you can map your panel's specific scenarios to Home Assistant's default actions (arm\_home, arm\_away, disarm, etc.).
    
*   **Full Area Management**: Areas (partitions) are exposed as:
    
    *   **Switch entities**: For direct arming (turn on) and disarming (turn off) of each area. The switch is on whenever the area is armed, in total, partial or instant mode; turning it on arms in the mode chosen in the options (total by default).
        
    *   **Select entities**: The arming mode of each area (Disarmed, Armed total, Armed partial, Armed instant); choosing a mode arms or disarms the area.
        
    *   **The `arm_area` action**: Arms or disarms one or more areas in a single command (see [Arming areas from automations](#arming-areas-from-automations)).
        
    *   **Binary Sensor entities**: Per area, whether it is in alarm now, in tamper now, and whether it has an alarm memory (kept after the alarm until it is reset).
        
*   **Detailed Zone Monitoring**: Every zone of the panel (zones of input, input/output and double terminals, including the second zone of double terminals) is exposed as:
    
    *   **Binary Sensor Status entities**: With device classes automatically assigned based on zone type. "Double Balancing" and "Shutter" types are assigned the motion device class, while "Normally Closed" or "Normally Open" are assigned the opening class.
        
    *   Extended attributes provide additional configuration parameters for each zone.
    
    *   **Binary Sensor Alarm memory entities**: Telling if a zone has triggered the system (until the alarm memory is reset).
    
    *   **Binary Sensor Tamper entities**: Telling if a zone is in tamper or short circuit.
    
    *   **Switch entities**: To allow individual zone exclusion.
        
*   **Advanced Scenario Control**: Scenarios are exposed as:
    
    *   **Button entities**: To activate any scenario directly. If a zone that is not ready prevents the activation, or the panel refuses it, Home Assistant shows an error and a persistent notification with the reason (see [When the panel refuses a command](#when-the-panel-refuses-a-command)).
        
    *   **Binary Sensor entities**: To indicate if a specific scenario is currently active.
        
    *   A dedicated sensor also displays the name of the single, currently active scenario on the panel.
        
*   **Configurable Event Log**:
    
    *   A sensor entity provides a log of recent panel events.
        
    *   The number of events stored in the sensor's attributes is configurable during setup.
        
    *   The log persists its last known state even if the connection to the alarm panel is temporarily lost.
        
    *   The sensor's state reflects the panel's latest event index, allowing for efficient, incremental event fetching.
        
*   **Detailed Panel Information**:
    
    *   Sensors for the panel's firmware version and system type.
        
*   **Maintenance without re-adding the integration**:
    
    *   **Reconfigure** the host, port and PIN of an existing panel, for example after its IP address changed.
        
    *   **Re-read the panel configuration** (names, zone setup, scenarios) after changing it on the panel, with a button or an action.
        

Prerequisites
-------------

*   An Inim SmartLiving alarm control panel.
    
*   The panel must be equipped with a SmartLAN/SI or SmartLAN/G network interface module. SmartLAN/G support is **experimental**: it has only been tested against a simulated panel so far, so please report how it works for you.
    
*   Panel firmware 5 or later. Supported panels:
    
    *   **SmartLiving 1050** with firmware 6.x: developed and tested on it.
        
    *   **SmartLiving 10100** with firmware 6.x: **experimental**. It has 15 areas and zone numbers 1 to 200, and has not been tested on a real panel yet; a warning in the log says so. Please report how it works for you.
        
    *   Firmware 5.x and other SmartLiving models (505, 515) can be set up, but are handled like a 1050 on firmware 6 and may show wrong data until they are supported (a warning is logged); firmware older than 5 is refused.
    
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
    

To go back, install the latest stable version (for example `0.3.2`) the same way and restart Home Assistant. Please report problems with a preview in a GitHub issue, with your panel model, firmware and board type (SmartLAN/SI or SmartLAN/G).

### Upgrading from 0.3.x

Version 0.4 reworks how the integration talks to the panel; your entity IDs and options stay as they are:

*   Your configuration entry is updated automatically (it is marked as a SmartLAN/SI connection). Entity IDs and options do not change.
    
*   Home Assistant now keeps one connection to the panel and reuses it, so updates are faster. The SmartLAN/SI module accepts only one local connection: see [the Inim app](#troubleshooting) under Troubleshooting.
    
*   The PIN is checked from now on when you **Reconfigure**: a PIN that matches no user code of the panel is refused. If your current PIN matches none, the log shows a warning at every connection.
    
*   New: SmartLAN/G boards (experimental), a network search when adding a panel, and the active scenario now takes all areas of your user code into account (previously areas 8 to 10 were ignored).
    
*   **Area switches** are now on for an area armed in partial or instant mode too (before, such an area showed as unknown and its switch was off). Turning a switch on still arms in total mode, unless you choose another mode with the new **Area switches arm in** option. The new `arming_mode` attribute tells the mode (`total`, `partial`, `instant` or `disarmed`); `status_text` stays `armed` or `disarmed`.
    
*   **"Area X Triggered" is now called "Area X Alarm memory"**: same entity ID, same meaning (on after an alarm until the memory is reset), now also for areas 9 and 10, with a `tamper_memory` attribute. Two new binary sensors per area show the **live** state: *Area X Alarm* and *Area X Tamper*.
    
*   **The alarm panel shows Triggered only while an area is in alarm.** Before, it stayed Triggered until the alarm memory was reset; now it returns to its armed or disarmed state when the alarm ends, and the *Alarm memory* sensors keep the memory. Automations that relied on Triggered lasting until the reset should use the *Alarm memory* sensors instead.
    
*   **Zones follow the panel's terminal types.** Output terminals (for example a siren) and unused terminals no longer get zone entities, and the second zones of double terminals (51 to 100 on the 1050) are added. Raise the zone limit in the options to 100 to see second zones: existing installations keep their limit (50 by default before). Entities that earlier versions created for terminals that are not zones are **not deleted automatically**: they stay as unavailable entities, and the log lists them at every start (*"these entities belong to terminals that are not zones on the panel"*). Delete them in **Settings** -> **Devices & services** -> **Entities**.
    
*   **"Zone X Triggered" is now called "Zone X Alarm memory"** (same entity ID). Zones in short circuit or tamper are no longer shown as unknown: a new *Zone X Tamper* sensor turns on, and the zone's `status_text` says `short` or `tamper`. Such zones are also listed when they prevent a scenario from being activated.
    
*   **SmartLiving 10100 (experimental):** a 10100 now uses its own memory layout. Earlier versions read it as if it were a 1050, so its names and zones were wrong: at the first start after the update the integration reads the panel configuration again by itself (the log says so). Entity IDs of areas and zones stay the same; areas 11 to 15 and zones above 50 appear if your limits allow them (raise them in the options: up to 15 areas and 200 zones).
    
*   **Event log:** event times are now shown in Home Assistant's time zone, with daylight saving time (before, always UTC+1), and new events keep appearing after the panel's event log wraps around. The event log sensor's state is now the newest event's position in the panel's log (0 to 999) instead of a memory address: automations that only trigger on a change keep working, but compare the new numbers if you used the value itself.
    
*   Your configuration entry is updated once more (version 1.3): the zone list is completed from the panel data already stored, without contacting the panel. If that data is incomplete, a warning asks you to use **Re-read panel configuration**.
    
*   **SmartLAN/G:** the board's replies are now read the way the board sends them (the data alone, without the panel's checksum). Before, every read failed and setup reported a wrong user name or password even with the right credentials. A wrong user name or password is now reported only when the board's reply cannot be decrypted; any other unexpected reply has its own message (see [Troubleshooting](#troubleshooting)).
    
*   **Commands that are not carried out now show an error.** Before, a refused arm, disarm, bypass or reset was only written to the log, and the alarm panel's error notifications did not appear at all. Now the action fails with a message that tells why (for example a PIN the panel no longer accepts, or a PIN not enabled for that area), in the dashboard and in automation traces; see [When the panel refuses a command](#when-the-panel-refuses-a-command). An automation step that fails stops that automation unless the step has `continue_on_error: true`. The `inim_smartliving_alarm_scenario_activation_failed` event has a new `panel_result` field (the panel's answer, or `null` when the panel did not refuse the command), and its `reason` can now also be `command_failed` or `invalid_code`.
    
*   **New: an arming mode select per area** (*Area X arming mode*: disarmed, total, partial or instant) and the **`arm_area` action**, which arms or disarms several areas in one command (see [Arming areas from automations](#arming-areas-from-automations)).
    
*   Not changed yet: see [Known limitations](#known-limitations).
    

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
        
6.  Click **Submit**. The integration connects to your panel, checks it and reads its configuration. The setup is refused, with a message, if the panel cannot be reached, if the PIN does not match any user code of the panel, or if the panel's firmware is older than 5.
    
7.  If successful, a second dialog will appear for **Initial Options**:
    
    *   **Polling Interval**: How often Home Assistant should query the panel for status updates (in seconds).
        
    *   **Limit Areas/Zones/Scenarios**: The maximum number of each entity type to create. This is useful for managing the number of entities in Home Assistant. The setup proposes your panel's own counts: 10 areas and zone numbers 1 to 100 on a SmartLiving 1050, 15 areas and zone numbers 1 to 200 on a 10100. The zone limit counts zone numbers: zones above it get no entities, so keep it at the proposed value if you use double terminals, whose second zones are numbered after the panel's terminals (51 to 100 on the 1050, 101 to 200 on the 10100).
        
    *   **Event Log Size**: The number of recent events to store and display in the event log sensor's attributes.
        
    *   **Scenario Mappings**: You can map your panel's scenarios to Home Assistant's standard alarm actions (Arm Home, Arm Away, etc.).

    *   **Area switches arm in** (options only, not at setup): the mode an area switch arms in when turned on: total (the default), partial or instant. The *arming mode* select of each area offers every mode.

    *   **Readers Names**: You can provide a comma separated list of Proximity readers "friendly" names wich will be mapped to readers in the order in which they appear. The integration cannot query those names. If you don't provide any, you'll see them appear as "Reader X" in the log.
        
8.  Click **Submit** to complete the setup.
    

You can change these options later by navigating to the integration on the Devices & Services page and clicking "CONFIGURE".

### Changing the connection details (Reconfigure)

If the IP address, port or PIN of your panel (or, for SmartLAN/G, the board's user name or password) changes, you don't need to remove and re-add the integration:

1.  Go to **Settings** -> **Devices & Services** and open **Inim SmartLiving Alarm**.
    
2.  Open the menu (⋮) of your panel's entry and choose **Reconfigure**.
    
3.  Enter the new **Host**, **Port**, **PIN** (and, for SmartLAN/G, **Board user name** and **Board password**) and click **Submit**.
    

The integration contacts the panel with the new details, reads its configuration again and reloads. Your options (polling interval, limits, scenario mappings), entities and entity IDs are kept. If the panel cannot be reached, an error is shown and nothing is changed. An address already used by another configured panel is refused, and so is a PIN that does not match any user code of the panel. The board type (SmartLAN/SI or SmartLAN/G) cannot be changed here: remove the integration and add it again for that.

### Re-reading the panel configuration

The names of areas, zones and scenarios, the zone setup and the scenario definitions are read from the panel when the integration is set up. If you change them on the panel later (from a keypad or the installer software), read them again with either:

*   the **Re-read panel configuration** button, in the *Configuration* section of your panel's device page, or
    
*   the **Re-read panel configuration** action (`inim_smartliving_alarm.reread_panel_configuration`), for example from an automation or from **Developer tools** -> **Actions**, where you pick the panel from a list:
    

```yaml
action: inim_smartliving_alarm.reread_panel_configuration
data:
  config_entry_id: <the panel's config entry ID>
```

The integration then reloads with the new configuration. Your options are not changed, and entity IDs stay the same; entity names follow the new names from the panel. If the panel cannot be reached, an error is shown and the stored configuration is kept.

Entities Provided
-----------------

This integration exposes your Inim alarm system's components as various Home Assistant entities. The entity IDs are programmatically generated based on the panel name and the names of your areas, zones, and scenarios fetched from the panel.

*   **alarm\_control\_panel.your\_panel\_name**: The main entity to arm and disarm the system. Its actions (Arm Home, Arm Away, etc.) are linked to the scenarios you mapped during setup. It shows *Triggered* while any area is in alarm.
    
*   **Areas**:
    
    *   *switch.your\_panel\_name\_area\_X*: Allows direct arming (on) and disarming (off) of an individual area. It is on when the area is armed in any mode (total, partial or instant); turning it on arms in the mode set by the **Area switches arm in** option (total by default). Attributes: `status_text` (`armed`, `disarmed` or `unknown`) and `arming_mode` (`total`, `partial`, `instant`, `disarmed` or `unknown`).
        
    *   *select.your\_panel\_name\_area\_X\_arming\_mode* (named *Area X arming mode*): The area's arming mode: *Disarmed*, *Armed total*, *Armed partial* or *Armed instant* (unknown while the panel reports none of these). Choosing a mode arms the area in that mode or disarms it; if the panel refuses, an error says why. Attributes: `area_id` and `area_name`.
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_alarm*: On while the area is in alarm.
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_tamper*: On while the area is in tamper.
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_triggered* (named *Area X Alarm memory*): On after an alarm in the area until the alarm memory is reset (see [Resetting the alarm memory](#resetting-the-alarm-memory)). The `tamper_memory` attribute tells whether the area also has a tamper memory.
        
*   **Zones**:
    
    Only zones that exist on the panel get entities: zones of input, input/output and double terminals. Output terminals (for example a siren) and unused terminals have none. Zones 1 to N are the zones of the panel's N terminals (50 on the SmartLiving 1050, 100 on the 10100); a double terminal *n* also has a second zone, numbered N + *n* (51 to 100 on the 1050, 101 to 200 on the 10100).

    *   *binary\_sensor.your\_panel\_name\_zone\_X*: Represents a single zone. It turns on if the zone is alarmed (open). The device class (motion or opening) is automatically assigned based on the zone's configuration on the panel. Extended attributes provide more details about the zone's configuration, including `terminal_type`, `status_text` (`clear`, `alarmed`, `short` or `tamper`) and `test` (zone in test mode).

    *   *binary\_sensor.your\_panel\_name\_zone\_X\_triggered* (named *Zone X Alarm memory*): Turns on if the zone has triggered the system, until the alarm memory is reset. The `tamper_memory` attribute tells whether the zone's terminal has a tamper memory (both zones of a double terminal share it).

    *   *binary\_sensor.your\_panel\_name\_zone\_X\_tamper*: Turns on while the zone is in tamper or short circuit (`status_text` tells which).

    *   *switch.your\_panel\_name\_zone\_X*: Lets you enable or disable (exclude) individual zones, including the second zone of double terminals.
        
*   **Scenarios**:
    
    *   *button.your\_panel\_name\_scenario\_X*: Allows direct activation of a specific scenario. If the activation fails (a zone not ready, or the panel refuses it), an error and a persistent notification with the reason appear in Home Assistant.
        
    *   *binary\_sensor.your\_panel\_name\_scenario\_X\_active*: A binary sensor that is 'on' if that specific scenario is currently the active one on the panel.
        
*   **Sensors**:
    
    *   *sensor.your\_panel\_name\_event\_log*: The state is the position of the newest event in the panel's event log (0 to 999; it starts again from 0 when the log wraps around), so it changes with every new event. The attributes contain a human-readable log of recent events. Event times are the panel's clock read in Home Assistant's time zone, with daylight saving time.
        
    *   *sensor.your\_panel\_name\_active\_scenario*: A text sensor that displays the name of the currently active scenario.
        
    *   *sensor.your\_panel\_name\_firmware\_version* & *sensor.your\_panel\_name\_system\_type*: Provide static information about your alarm panel.
        
*   **Maintenance**:
    
    *   *button.your\_panel\_name\_reread\_panel\_configuration*: Reads the panel configuration again and reloads the integration (see [Re-reading the panel configuration](#re-reading-the-panel-configuration)). Shown in the *Configuration* section of the device page.
        

_(Replace your\_panel\_name, with actual value based on your setup)._

Usage Examples
--------------

### Basic Alarm Control

Use the alarm\_control\_panel entity in a standard Lovelace Alarm Panel card. If you've mapped scenarios, the arm actions will trigger those.

### Scenario Activation

Use the button entities in your dashboard or automations to directly activate specific panel scenarios.

### Resetting the alarm memory

After an alarm, the panel keeps the alarm memory of the areas involved until it is reset (the *Area X Alarm memory* sensors show it). The action `inim_smartliving_alarm.alarm_reset` resets every area that has an alarm or tamper memory, on the targeted panel:

```yaml
action: inim_smartliving_alarm.alarm_reset
target:
  entity_id: alarm_control_panel.your_panel_name
```

You can call it from a script, an automation or a dashboard button.

### Arming areas from automations

The action `inim_smartliving_alarm.arm_area` arms or disarms one or more areas of a panel in a single command, so they change together. `areas` are the area numbers as on the panel (also areas without entities); `mode` is `total`, `partial`, `instant` or `disarm`. Pick the panel from the list in the action editor, or give its entry ID:

```yaml
action: inim_smartliving_alarm.arm_area
data:
  config_entry_id: your_config_entry_id
  areas: [1, 3]
  mode: partial
```

Like the area switches and the arming mode selects, the action does not check the zones first. The panel refuses only when it is programmed not to allow arming with zones that are not ready; otherwise it arms, and an open zone can set off an alarm right away. In a later version, arming will follow a readiness setting of the integration. If the panel refuses the command, the action fails with the reason (see [When the panel refuses a command](#when-the-panel-refuses-a-command)).

### Displaying the Event Log

You can display the event log using a Markdown card in Lovelace, but I recommend the custom:flex-table-card (from HACS) to provide a nice tabular view.

### Sample UI Components

You'll find yaml samples for UI components in the repo.


Known limitations
-----------------

These are known and will be fixed in the coming versions:

*   On recent Home Assistant versions, new installations get entity IDs with the panel name twice (for example `switch.home_home_area_3`); existing installations keep their IDs.
    

### When the panel refuses a command

Arming, disarming, bypassing a zone, activating a scenario and resetting the alarm memory fail with a message when they are not carried out. The message says which of three things happened:

*   **The integration did not send the command**: before a scenario, the integration checks the zones it arms. If some are not at rest, the message lists them (for example *"Night was not activated because these zones are not ready: Kitchen window (12)"*) and nothing is sent to the panel.
    
*   **The panel refused it**: the message gives the panel's reason:
    
    | Message | What to do |
    |---|---|
    | The panel did not accept the PIN | The user code was changed or deleted on the panel: enter the current PIN with **Reconfigure**. |
    | The PIN is not enabled for it | The user code does not control that area: enable it on the panel or use another code. |
    | The panel does not allow arming while zones are not ready | Close or bypass the open zones of the area, then arm again. |
    | The zone cannot be bypassed / belongs to no area | The zone's programming on the panel does not allow it. |
    | Already in the requested state | Nothing to do: the zone or area was already as requested. |
    | Its terminal is out of range / not connected / not in use | The zone's terminal has changed on the panel: use **Re-read panel configuration**. |
    | The panel refused the command (result code N) | The panel gave no reason, or one this integration does not know yet. |
    
*   **The command could not be completed**: the panel could not be reached, or its answer could not be read. See *Cannot Connect* below; try again once the connection is back.
    

Scenario activations that fail also show a persistent notification and fire the `inim_smartliving_alarm_scenario_activation_failed` event, as before.

Troubleshooting
---------------

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
        
*   **The panel armed an area with a zone open**: the area switches, the arming mode selects and the `arm_area` action do not check the zones before arming. The panel refuses only when it is programmed not to allow arming with zones that are not ready; otherwise it arms, and an open zone can set off an alarm right away. A check of the zones before arming is planned for a later version.
    
*   **Entities Not Appearing or Not Updating**:
    
    *   Check the Home Assistant logs (Settings -> System -> Logs) for errors related to the inim\_smartliving\_alarm integration.
        
    *   Ensure the polling interval is set appropriately. Too short might overload the panel or network; too long will delay updates.
        
*   **Incorrect Number of Entities**:
    
    *   Verify the "Limit Areas/Zones/Scenarios" settings in the integration's configuration options.
        

Contributing
------------

Contributions to this integration are welcome! Please feel free to open an issue or submit pull requests.