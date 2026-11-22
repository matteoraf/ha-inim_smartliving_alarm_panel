# Troubleshooting

## When the panel refuses a command

Arming, disarming, bypassing a zone, activating a scenario, resetting the alarm memory and switching an output fail with a message when they are not carried out. The message says which of three things happened:

*   **The integration did not send the command**:
    
    *   Before arming or activating a scenario, the integration checks the zones (see [Zones not ready](actions-events.md#zones-not-ready)). If some are not ready and the option says *Refuse*, the message lists them and nothing is sent to the panel.
        
    *   While the panel is in programming mode, it ignores commands, so none is sent (*"The command for Area 3 was not sent: the panel is in programming mode. Try again when programming has ended."*). If the panel enters programming mode while a command is being sent, the message says that the command may not have been carried out: check the area or zone, and try again when programming has ended.
    
*   **The panel refused it**: the message gives the panel's reason:
    
    | Message | What to do |
    |---|---|
    | The panel did not accept the PIN | The user code was changed or deleted on the panel: enter the current PIN with **Reconfigure**. |
    | The PIN is not enabled for it | The user code does not control that area: enable it on the panel or use another code. |
    | The panel does not allow arming while zones are not ready | Close or bypass the open zones of the area, then arm again. |
    | The zone cannot be bypassed / belongs to no area | The zone's programming on the panel does not allow it. |
    | Double zone error | The zone's programming on the panel does not allow it. |
    | Already in the requested state | Nothing to do: the zone or area was already as requested. |
    | Its terminal is out of range / not connected / not in use | The zone's or output's terminal has changed on the panel: use **Re-read panel configuration**. |
    | The output is not enabled | Your user code may not switch that output: enable it for the code on the panel, or untick the output. |
    | The output is locked while its areas are armed | The panel does not let the output change while its areas are armed: disarm them first. |
    | The panel refused the command | The panel gave no reason. |
    | The panel refused the command (result code N) | The panel gave a reason this integration does not know yet. |
    
*   **The command could not be completed**: the panel could not be reached, or its answer could not be read. See *Cannot Connect* below; try again once the connection is back.
    

Scenario activations that fail also show a persistent notification and fire the `inim_smartliving_alarm_scenario_activation_failed` event, as before. The event has `reason`, `panel_result`, `blocking_zones` (each with `zone_id`, `zone_name`, `status` and `areas`) and `message` (always in English, whatever Home Assistant's language). The `reason` is `zones_blocked`, `no_areas_identified`, `unknown_zones_error`, `no_data_available`, `disarm_failed`, `unknown`, `panel_programming`, `command_failed`, or the reason the panel gave (for example `invalid_code`).

## Common problems

*   **Reporting a problem**: attach a system report to the GitHub issue (see [Sharing a system report](system-report.md)), and if you can a debug log (see below).
    
*   **Cannot** Connect / **Connection Timed Out**:
    
    *   Verify the IP address and Port for your SmartLAN module are correct.
        
    *   Ensure your Home Assistant instance can reach the panel's IP address on the specified port (check firewalls, network segmentation).
        
    *   Make sure the SmartLAN module is powered and connected to the network.
        
    *   If the module's IP address or port has changed, update it with **Reconfigure** (see [Changing the connection details](setup.md#changing-the-connection-details-reconfigure)) instead of removing the integration.
        
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
    
    *   Use the **Re-read panel configuration** button or action (see [Re-reading the panel configuration](setup.md#re-reading-the-panel-configuration)).
        
*   **Scenarios not activating or Areas not arming/disarming**: the error message tells why (see [When the panel refuses a command](#when-the-panel-refuses-a-command)).
    
    
    *   Double-check that the PIN code entered during configuration is correct and is a valid user PIN for the panel with sufficient permissions. Setup and **Reconfigure** refuse a PIN that matches no user code; for a panel set up with an older version of the integration, the log shows a warning ("The PIN does not match any user code of the panel") each time the integration connects. You can change it with **Reconfigure** (see [Changing the connection details](setup.md#changing-the-connection-details-reconfigure)).
        
*   **Commands fail with "the panel is in programming mode"**:
    
    *   Someone is programming the panel from a keypad, or the panel is reloading its configuration. Commands work again when programming has ended; the *Programming mode* sensor shows when.
        
    *   Programming from the installer software on a PC does not block commands. After the software writes new programming, the panel may restart by itself, and Home Assistant cannot reach it for a short time. Then use **Re-read panel configuration** to pick up the changes.
        
*   **Arming is refused because zones are not ready**: close or bypass the zones the message lists, or set **When zones are not ready** to *Arm anyway and warn* (or use `force: true` in an action). See [Zones not ready](actions-events.md#zones-not-ready).
    
*   **Entities Not Appearing or Not Updating**:
    
    *   Check the Home Assistant logs (Settings -> System -> Logs) for errors related to the inim\_smartliving\_alarm integration.
        
    *   Ensure the polling interval is set appropriately. Too short might overload the panel or network; too long will delay updates.
        
*   **Incorrect Number of Entities**:
    
    *   Check the integration's options under **Areas, zones, scenarios and outputs**: only ticked items get entities.
        
    *   A zone is listed only if its terminal is programmed as a zone on the panel. If you changed the panel's programming, use **Re-read panel configuration** first.
