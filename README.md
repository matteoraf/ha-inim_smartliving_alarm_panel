Home Assistant Integration for Inim SmartLiving Alarm Systems
=============================================================

> \[!WARNING\]This is an experimental component. I am not affiliated with Inim Electronics and this is not an official component from Inim. Use at your own risk.
> 
> I could only test this with SmartLiving 1050, I have no way to test and support any other SmartLiving system. Contributors are welcome.

This custom integration allows you to connect Home Assistant with Inim SmartLiving series alarm control panels that are equipped with a SmartLAN/SI network interface. It enables monitoring and control of your alarm system directly from Home Assistant.

Features
--------

*   **Comprehensive Alarm Control Panel**: The core of the integration, allowing for standard arming and disarming actions. During configuration, you can map your panel's specific scenarios to Home Assistant's default actions (arm\_home, arm\_away, disarm, etc.).
    
*   **Full Area Management**: Areas (partitions) are exposed as:
    
    *   **Switch entities**: For direct arming (turn on) and disarming (turn off) of each area.
        
    *   **Binary Sensor entities**: To show if an area is currently in an alarm (triggered) state.
        
*   **Detailed Zone Monitoring**: Zones are exposed as:
    
    *   **Binary Sensor Status entities**: With device classes automatically assigned based on zone type. "Double Balancing" and "Shutter" types are assigned the motion device class, while "Normally Closed" or "Normally Open" are assigned the opening class.
        
    *   Extended attributes provide additional configuration parameters for each zone.
    
    *   **Binary Sensor Triggered entities**: Telling if a zone has triggered the system.
    
    *   **Switch entities**: To allow individual zone exclusion.
        
*   **Advanced Scenario Control**: Scenarios are exposed as:
    
    *   **Button entities**: To activate any scenario directly. If activation is prevented by an alarmed zone, a persistent notification with details of the problematic zones is shown in Home Assistant.
        
    *   **Binary Sensor entities**: To indicate if a specific scenario is currently active.
        
    *   A dedicated sensor also displays the name of the single, currently active scenario on the panel.
        
*   **Configurable Event Log**:
    
    *   A sensor entity provides a log of recent panel events.
        
    *   The number of events stored in the sensor's attributes is configurable during setup.
        
    *   The log persists its last known state even if the connection to the alarm panel is temporarily lost.
        
    *   The sensor's state reflects the panel's latest event index, allowing for efficient, incremental event fetching.
        
*   **Detailed Panel Information**:
    
    *   Sensors for the panel's firmware version and system type.
        

Prerequisites
-------------

*   An Inim SmartLiving alarm control panel.
    
*   The panel must be equipped with a SmartLAN/SI network interface module.
    
*   The SmartLAN module must be connected to your local network and accessible from your Home Assistant instance.
    
*   You will need the IP address, port (typically 5004), and a valid user PIN code for the alarm panel.
    

Installation
------------

<!--
It is recommended to install this integration via the [Home Assistant Community Store (HACS)](https://hacs.xyz/) if it's made available there.

**1\. HACS Installation (Recommended if available):**

1.  Open HACS in your Home Assistant.
    
2.  Go to "Integrations".
    
3.  Search for "Inim SmartLiving Alarm" (or the name you publish it under).
    
4.  Click "Install".
    
5.  Restart Home Assistant.
    

**2\. Manual Installation:**
-->
1.  Download the latest release inim\_smartliving\_alarm folder (or clone the repository) into your config/custom\_components folder.
    
2.  Restart Home Assistant.
    

Configuration
-------------

After installation and restarting Home Assistant:

1.  Go to **Settings** -> **Devices** & **Services**.
    
2.  Click the **\+ ADD INTEGRATION** button in the bottom right.
    
3.  Search for "Inim SmartLiving Alarm" and select it.
    
4.  A configuration dialog will appear. Enter the following details:
    
    *   **Host**: The IP address of your Inim SmartLAN module (e.g., 192.168.1.100).
        
    *   **Port**: The TCP port your SmartLAN module is listening on (default is 5004).
        
    *   **PIN**: A valid user PIN code for your alarm panel (The PIN won't be checked. If the PIN is not valid, it simply won't work, but you will not get any error message about it during the configuration process.).
        
    *   **Panel Name** (Optional): A friendly name for your alarm panel in Home Assistant (e.g., "Home Alarm").
        
5.  Click **Submit**. The integration will attempt to connect and fetch initial configuration data from your panel.
    
6.  If successful, a second dialog will appear for **Initial Options**:
    
    *   **Polling Interval**: How often Home Assistant should query the panel for status updates (in seconds).
        
    *   **Limit Areas/Zones/Scenarios**: The maximum number of each entity type to create. This is useful for managing the number of entities in Home Assistant.
        
    *   **Event Log Size**: The number of recent events to store and display in the event log sensor's attributes.
        
    *   **Scenario Mappings**: You can map your panel's scenarios to Home Assistant's standard alarm actions (Arm Home, Arm Away, etc.).

    *   **Readers Names**: You can provide a comma separated list of Proximity readers "friendly" names wich will be mapped to readers in the order in which they appear. The integration cannot query those names. If you don't provide any, you'll see them appear as "Reader X" in the log.
        
7.  Click **Submit** to complete the setup.
    

You can change these options later by navigating to the integration on the Devices & Services page and clicking "CONFIGURE".

Entities Provided
-----------------

This integration exposes your Inim alarm system's components as various Home Assistant entities. The entity IDs are programmatically generated based on the panel name and the names of your areas, zones, and scenarios fetched from the panel.

*   **alarm\_control\_panel.your\_panel\_name**: The main entity to arm and disarm the system. Its actions (Arm Home, Arm Away, etc.) are linked to the scenarios you mapped during setup.
    
*   **Areas**:
    
    *   *switch.your\_panel\_name\_area\_X*: Allows direct arming (on) and disarming (off) of an individual area.
        
    *   *binary\_sensor.your\_panel\_name\_area\_X\_triggered*: A binary sensor that turns on if the area is in an alarm (triggered) state.
        
*   **Zones**:
    
    *   *binary\_sensor.your\_panel\_name\_zone\_X*: Represents a single zone. It turns on if the zone is alarmed. The device class (motion or opening) is automatically assigned based on the zone's configuration on the panel. Extended attributes provide more details about the zone's configuration.

    *   *binary\_sensor.your\_panel\_name\_zone\_X_triggered_*: Represents a single zone. It turns on if the zone has triggered the system.

    *   *switch.your\_panel\_name\_zone\_X*: Lets you enable or disable (exclude) individual zones.
        
*   **Scenarios**:
    
    *   *button.your\_panel\_name\_scenario\_X*: Allows direct activation of a specific scenario. If activation fails due to an open zone, a persistent notification will appear in Home Assistant.
        
    *   *binary\_sensor.your\_panel\_name\_scenario\_X\_active*: A binary sensor that is 'on' if that specific scenario is currently the active one on the panel.
        
*   **Sensors**:
    
    *   *sensor.your\_panel\_name\_event\_log*: The state shows the panel's latest event index. The attributes contain a human-readable log of recent events.
        
    *   *sensor.your\_panel\_name\_active\_scenario*: A text sensor that displays the name of the currently active scenario.
        
    *   *sensor.your\_panel\_name\_firmware\_version* & *sensor.your\_panel\_name\_system\_type*: Provide static information about your alarm panel.
        

_(Replace your\_panel\_name, with actual value based on your setup)._

Usage Examples
--------------

### Basic Alarm Control

Use the alarm\_control\_panel entity in a standard Lovelace Alarm Panel card. If you've mapped scenarios, the arm actions will trigger those.

### Scenario Activation

Use the button entities in your dashboard or automations to directly activate specific panel scenarios.

### Displaying the Event Log

You can display the event log using a Markdown card in Lovelace, but I recommend the custom:flex-table-card (from HACS) to provide a nice tabular view.

### Sample UI Components

You'll find yaml samples for UI components in the repo.


Troubleshooting
---------------

*   **Cannot** Connect / **Connection Timed Out**:
    
    *   Verify the IP address and Port for your SmartLAN module are correct.
        
    *   Ensure your Home Assistant instance can reach the panel's IP address on the specified port (check firewalls, network segmentation).
        
    *   Make sure the SmartLAN module is powered and connected to the network.
        
*   **Scenarios not activating or Areas not arming/disarming**:
    
    *   Double-check that the PIN code entered during configuration is correct and is a valid user PIN for the panel with sufficient permissions.
        
*   **Entities Not Appearing or Not Updating**:
    
    *   Check the Home Assistant logs (Settings -> System -> Logs) for errors related to the inim\_smartliving\_alarm integration.
        
    *   Ensure the polling interval is set appropriately. Too short might overload the panel or network; too long will delay updates.
        
*   **Incorrect Number of Entities**:
    
    *   Verify the "Limit Areas/Zones/Scenarios" settings in the integration's configuration options.
        

Contributing
------------

Contributions to this integration are welcome! Please feel free to open an issue or submit pull requests.