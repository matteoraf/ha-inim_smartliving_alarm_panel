# Setup

## Adding a panel

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
        
    *   **Outputs**: the outputs your user code may switch, with their kind (on/off, monostable with its time, or dimmer); the section is shown only if there are some. **Only the outputs the installer marked as home automation outputs are ticked** (firmware 6): an output can drive a siren, a lock or a gate, so tick only the ones you mean to switch from Home Assistant (see [Outputs](entities.md#outputs)).
        
    The setup ticks what the panel shows in use: the zones that belong to an area, the areas of those zones, and the scenarios that change at least one area, and the outputs the installer marked as home automation outputs. Tick or untick anything you like.

    The same step lists, for reference, the keypads, readers, expanders and sirens enabled on the panel, by name.
    
8.  Then the **Initial Options**: see [Options](options.md#initial-options).
    
        
9.  Click **Submit** to complete the setup.

## Changing the connection details (Reconfigure)

If the IP address, port or PIN of your panel (or, for SmartLAN/G, the board's user name or password) changes, you don't need to remove and re-add the integration:

1.  Go to **Settings** -> **Devices & Services** and open **Inim SmartLiving Alarm**.
    
2.  Open the menu (⋮) of your panel's entry and choose **Reconfigure**.
    
3.  Enter the new **Host**, **Port**, **PIN** (and, for SmartLAN/G, **Board user name** and **Board password**) and click **Submit**. The PIN and the board password are not filled in: leave them empty to keep the current ones.
    

The integration contacts the panel with the new details, reads its configuration again and reloads. Your options (polling interval, imported areas, zones and scenarios, scenario mappings), entities and entity IDs are kept. If the panel cannot be reached, an error is shown and nothing is changed. An address already used by another configured panel is refused, and so is a PIN that does not match any user code of the panel. The board type (SmartLAN/SI or SmartLAN/G) cannot be changed here: remove the integration and add it again for that.

## Re-reading the panel configuration

The names of areas, zones, scenarios, user codes, keys, keypads, readers, expanders, timers and sirens, which keypads, readers, expanders and sirens are enabled, the zone setup and the scenario definitions are read from the panel when the integration is set up. If you change them on the panel later (from a keypad or the installer software), read them again with either:

*   the **Re-read panel configuration** button, in the *Configuration* section of your panel's device page, or
    
*   the **Re-read panel configuration** action (`inim_smartliving_alarm.reread_panel_configuration`), for example from an automation or from **Developer tools** -> **Actions**, where you pick the panel from a list:
    

```yaml
action: inim_smartliving_alarm.reread_panel_configuration
data:
  config_entry_id: <the panel's config entry ID>
```

The integration then reloads with the new configuration. Your options are not changed, and entity IDs stay the same; entity names follow the new names from the panel. The areas, zones and scenarios you import are not changed either: a zone wired later or a new scenario is listed, unticked, in the options under **Areas, zones, scenarios and outputs**. If the panel cannot be reached, or it is in programming mode, an error is shown and the stored configuration is kept: in programming mode, try again when programming has ended.
