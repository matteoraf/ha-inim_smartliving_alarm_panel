# Troubleshooting

Start with the table: find your problem, then follow the link for the details.

| Problem | What to check |
|---|---|
| The setup is refused | The message on the form: see [Why the setup can be refused](setup.md#why-the-setup-can-be-refused). |
| Home Assistant cannot connect | Address, port, network, firewall: see [Cannot connect](#cannot-connect). |
| **Search the network** finds no board | Same subnet, free UDP ports: see [Search finds no board](#search-finds-no-board). |
| A command fails with a message | The message says why: see [When the panel refuses a command](#when-the-panel-refuses-a-command). |
| Arming is refused because zones are not ready | Close or bypass the zones, or arm anyway: see [Zones not ready](actions-events.md#zones-not-ready). |
| Commands fail with "the panel is in programming mode" | Wait until programming has ended: see [Programming mode](#programming-mode). |
| Names, zones or scenarios are out of date | Use **Re-read panel configuration**: see [Setup](setup.md#re-reading-the-panel-configuration). |
| Entities are missing, too many, or not updating | See [Entities missing or not updating](#entities-missing-or-not-updating). |
| The Inim app cannot connect locally | See [The Inim app](#the-inim-app-cannot-connect-locally). |
| Reporting a problem | Attach a [system report](system-report.md) to the GitHub issue, and if you can a [debug log](#debug-log). |

## When the panel refuses a command

Arming, disarming, bypassing a zone, running a scenario, resetting the alarm memory and switching an output fail with a message when they are not carried out. The message says which of three things happened.

**1. The integration did not send the command.**

*   Before arming or running a scenario, the integration checks the zones (see [Zones not ready](actions-events.md#zones-not-ready)). If some are not ready and the option says *Refuse*, the message lists them and nothing is sent to the panel.
*   While the panel is in programming mode, it ignores commands, so none is sent (*"The command for Area 3 was not sent: the panel is in programming mode. Try again when programming has ended."*). If the panel enters programming mode while a command is being sent, the message says that the command may not have been carried out: check the area or zone, and try again when programming has ended.

**2. The panel refused it.** The message gives the panel's reason:

| Message | Reason in events | What to do |
|---|---|---|
| The panel did not accept the PIN | `invalid_code` | The user code was changed or deleted on the panel: enter the current PIN with **Reconfigure**. |
| The PIN is not enabled for it | `code_not_enabled` | The user code does not control that area: enable it on the panel or use another code. |
| It does not allow arming while zones are not ready | `open_zones` | Close or bypass the open zones of the area, then arm again. |
| The zone cannot be bypassed | `cannot_be_bypassed` | The zone's programming on the panel does not allow it. |
| The zone belongs to no area | `no_areas` | The zone's programming on the panel does not allow it. |
| Double zone error | `double_zone_error` | The zone's programming on the panel does not allow it. |
| It is already in the requested state | `already_in_state` | Nothing to do: the zone or area was already as requested. |
| Its terminal is out of range | `slot_out_of_range` | The zone's or output's terminal has changed on the panel: use **Re-read panel configuration**. |
| Its terminal is not connected | `slot_not_connected` | As above. |
| Its terminal is not in use | `null_terminal` | As above. |
| The output is not enabled | `output_not_enabled` | Your user code may not switch that output: enable it for the code on the panel, or untick the output. |
| The output is locked while its areas are armed | `areas_armed` | The panel does not let the output change while its areas are armed: disarm them first. |
| The panel refused the command | `refused` | The panel gave no reason. |
| The panel refused the command (result code N) | `unknown` | The panel gave a reason the integration does not know yet. |

**3. The command could not be completed.** The panel could not be reached, or its answer could not be read. See [Cannot connect](#cannot-connect); try again once the connection is back.

A failed scenario also shows a notification and fires the [`inim_smartliving_alarm_scenario_activation_failed`](actions-events.md#event-scenario-activation-failed) event, with the reason.

## Common problems

### Cannot connect

*   Verify the IP address and port of your SmartLAN board.
*   Make sure Home Assistant can reach the board's address on that port (check firewalls and network segmentation).
*   Make sure the board is powered and connected to the network.
*   If the board's address or port has changed, update it with **Reconfigure** (see [Changing the connection details](setup.md#changing-the-connection-details-reconfigure)) instead of removing the integration.

### Search finds no board

*   The search only reaches boards on the same subnet as Home Assistant (the boards answer only on their own network segment). Enter a board on another subnet or VLAN by hand.
*   The search listens on UDP ports 5001 (SmartLAN/SI) and 7001 (SmartLAN/G) of the Home Assistant host; another program using them, or a firewall, prevents it.
*   Boards that are already set up are not listed.

### SmartLAN/G: the reply was not in the expected format

The setup says *"The SmartLAN/G board answered, but its reply was not in the expected format"*. Check the board's user name and password first. If they are right, your board probably answers in a way this integration does not handle yet. Please open a GitHub issue with your panel model and firmware, the board's version and a [debug log](#debug-log) of a setup attempt.

### The Inim app cannot connect locally

The SmartLAN/SI board accepts only one local connection at a time. Home Assistant keeps its connection open while it reads the panel and for a few seconds after each update, so the app may have to retry. Remote access through Inim Cloud is not affected.

### Programming mode

*   Someone is programming the panel from a keypad, or the panel is reloading its configuration. Commands work again when programming has ended; the *Programming mode* sensor shows when.
*   Programming from the installer software on a PC does not block commands. After the software writes new programming, the panel may restart by itself, and Home Assistant cannot reach it for a short time. Then use **Re-read panel configuration** to pick up the changes.

### Commands not carried out

*   The error message tells why (see [When the panel refuses a command](#when-the-panel-refuses-a-command)).
*   Check that the PIN is a valid user PIN of the panel, with the areas you want to control. Setup and **Reconfigure** refuse a PIN that matches no user code; for a panel set up with an older version of the integration, the log shows a warning (*"The PIN does not match any user code of the panel"*) each time the integration connects. Change it with **Reconfigure** (see [Changing the connection details](setup.md#changing-the-connection-details-reconfigure)).
*   **Arming is refused because zones are not ready**: close or bypass the zones the message lists, or set **When zones are not ready** to *Arm anyway and warn* (or use `force: true` in an action). See [Zones not ready](actions-events.md#zones-not-ready).

### Entities missing or not updating

*   Check the Home Assistant logs (**Settings** → **System** → **Logs**) for errors from the `inim_smartliving_alarm` integration.
*   Check the polling interval in the [options](options.md#all-options). Too short can overload the panel or the network; too long delays updates.
*   Only ticked items get entities: check the options under **Areas, zones, scenarios and outputs**.
*   A zone is listed only if its terminal is programmed as a zone on the panel. If you changed the panel's programming, use **Re-read panel configuration** first.

??? note "Rare cases: no switches, no scenario buttons"

    The area, zone and output switches are created only when the panel data stored for the areas has names, and the scenario buttons only when the scenarios have names. If they are all missing, use **Re-read panel configuration**; the log says when the names are missing.

### Debug log

To get a debug log, add this to `configuration.yaml`, restart Home Assistant and repeat what failed:

```yaml
logger:
  logs:
    custom_components.inim_smartliving_alarm: debug
```

The log shows the requests and replies; the PIN is masked, and the board's user name and password are never logged. Remove the lines again afterwards.
