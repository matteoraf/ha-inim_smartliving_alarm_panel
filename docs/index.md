# Home Assistant Integration for Inim SmartLiving Alarm Systems

!!! warning

    This is an experimental component. I am not affiliated with Inim Electronics and this is not an official component from Inim. Use at your own risk.

    SmartLiving 1050 on firmware 6 has been tested and confirmed by multiple users. SmartLiving 10100 with a SmartLAN/G board has been tested by one user. The SmartLiving 505, 515 and all four models on firmware 5.1, are supported experimentally. Contributors are welcome.

This custom integration allows you to connect Home Assistant with Inim SmartLiving series alarm control panels that are equipped with a SmartLAN/SI or SmartLAN/G network interface (SmartLAN/G support is experimental). It enables monitoring and control of your alarm system directly from Home Assistant.

New here? Start with [Installation](installation.md), then [Setup](setup.md). Coming from 0.3.x? Read [Upgrading](upgrading.md) first. Something not working? See [Troubleshooting](troubleshooting.md).

## Features

*   **Comprehensive Alarm Control Panel**: The core of the integration, allowing for standard arming and disarming actions. During configuration, you can map your panel's specific scenarios to Home Assistant's default actions (arm\_home, arm\_away, disarm, etc.).
    
*   **Full Area Management**: Areas (partitions) are exposed as:
    
    *   **Switch entities**: For direct arming (turn on) and disarming (turn off) of each area. The switch is on whenever the area is armed, in total, partial or instant mode; turning it on arms in the mode chosen in the options (total by default).
        
    *   **Select entities**: The arming mode of each area (Disarmed, Armed total, Armed partial, Armed instant); choosing a mode arms or disarms the area.
        
    *   **The `arm_area` action**: Arms or disarms one or more areas in a single command (see [Arming areas from automations](actions-events.md#arming-areas-from-automations)).
        
    *   **Zones checked before arming**: before arming an area or activating a scenario, the integration asks the panel which zones are not ready, and refuses or arms anyway with a warning, as you choose (see [Zones not ready](actions-events.md#zones-not-ready)).
        
    *   **Binary Sensor entities**: Per area, whether it is in alarm now, in tamper now, and whether it has an alarm memory (kept after the alarm until it is reset).
        
*   **Detailed Zone Monitoring**: Every zone of the panel (zones of input, input/output and double terminals, including the second zone of double terminals) is exposed as:
    
    *   **Binary Sensor Status entities**: On while the zone is in alarm, with a device class guessed from the zone's programming (see the zone sensor below) and the zone's programming as attributes.
        
    *   Extended attributes provide additional configuration parameters for each zone.
    
    *   **Binary Sensor Alarm memory entities**: Telling if a zone has triggered the system (until the alarm memory is reset).
    
    *   **Binary Sensor Tamper entities**: Telling if a zone is in tamper or short circuit.
    
    *   **Switch entities**: To allow individual zone exclusion.
        
*   **Advanced Scenario Control**: Scenarios are exposed as:
    
    *   **Button entities**: To activate any scenario directly. If a zone that is not ready prevents the activation, or the panel refuses it, Home Assistant shows an error and a persistent notification with the reason (see [When the panel refuses a command](troubleshooting.md#when-the-panel-refuses-a-command)).
        
    *   **Binary Sensor entities**: To indicate if a specific scenario is currently active.
        
    *   A dedicated sensor also displays the name of the single, currently active scenario on the panel.
        
*   **Outputs**: the panel's terminal outputs and its fixed outputs O1-O3 that your user code may switch, as **switch** entities (on/off and monostable outputs) and **light** entities with brightness (dimmers), named from the panel. Only the outputs the installer marked as home automation outputs are proposed (see [Outputs](entities.md#outputs)).
        
*   **Configurable Event Log**:
    
    *   A sensor entity provides a log of recent panel events, with the names programmed on the panel: who did it (user code or key) and where (keypad or reader).
        
    *   The number of events stored in the sensor's attributes is configurable during setup.
        
    *   The log persists its last known state even if the connection to the alarm panel is temporarily lost.
        
    *   The sensor's state reflects the panel's latest event index, allowing for efficient, incremental event fetching.
        
*   **Detailed Panel Information**:
    
    *   Sensors for the panel's firmware version and system type.
        
    *   A diagnostic sensor showing when the panel is in programming mode. No command is sent then: the action fails with a message (see [When the panel refuses a command](troubleshooting.md#when-the-panel-refuses-a-command)).
        
*   **Maintenance without re-adding the integration**:
    
    *   **Reconfigure** the host, port and PIN of an existing panel, for example after its IP address changed.
        
    *   **Re-read the panel configuration** (names, zone setup, scenarios) after changing it on the panel, with a button or an action.

## Contributing

Contributions to this integration are welcome! Please feel free to open an issue or submit pull requests.
