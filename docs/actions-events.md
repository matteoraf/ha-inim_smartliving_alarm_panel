# Actions and events

## Basic Alarm Control

Use the alarm\_control\_panel entity in a standard Lovelace Alarm Panel card. If you've mapped scenarios, the arm actions will trigger those.

## Scenario Activation

Use the button entities in your dashboard or automations to directly activate specific panel scenarios. From automations you can also use the action `inim_smartliving_alarm.activate_scenario`: pick the panel and the scenario from the lists (the scenarios you import, by name; in YAML, the scenario number as on the panel). Set `force: true` to activate it even if zones are not ready (see [Zones not ready](#zones-not-ready)).

```yaml
action: inim_smartliving_alarm.activate_scenario
data:
  config_entry_id: your_config_entry_id
  scenario: 2
```

## Resetting the alarm memory

After an alarm, the panel keeps the alarm memory of the areas involved until it is reset (the *Area X Alarm memory* sensors show it). The action `inim_smartliving_alarm.alarm_reset` resets every area that has an alarm or tamper memory, on the targeted panel (if that panel's integration is not loaded, the action fails with a message):

```yaml
action: inim_smartliving_alarm.alarm_reset
target:
  entity_id: alarm_control_panel.your_panel_name
```

You can call it from a script, an automation or a dashboard button.

## Arming areas from automations

The action `inim_smartliving_alarm.arm_area` arms or disarms one or more areas of a panel in a single command, so they change together. Pick the panel and the areas from the lists in the action editor: the areas list shows the areas you import, by name. `mode` is `total`, `partial`, `instant` or `disarm`. In YAML, `areas` are the area numbers as on the panel:

```yaml
action: inim_smartliving_alarm.arm_area
data:
  config_entry_id: your_config_entry_id
  areas: [1, 3]
  mode: partial
```

With several panels, each area number in the list shows the area of every panel that imports it, with the panel's name; the panel you pick decides which one is armed. An area that is not imported from that panel (for example, one you unticked after writing the automation) is refused with a message, and nothing is sent.

Before arming, the action checks the zones, like the area switches and the arming mode selects (see [Zones not ready](#zones-not-ready)); add `force: true` to arm even if zones are not ready. Disarming is never checked. If the panel refuses the command, the action fails with the reason (see [When the panel refuses a command](troubleshooting.md#when-the-panel-refuses-a-command)).

## Zones not ready

Before it arms an area (switch, arming mode select, `arm_area` action) or activates a scenario (button, alarm panel, `activate_scenario` action), the integration asks the panel which zones are not ready for it: zones that are not at rest and would be armed. Zones the panel bypasses by itself when arming (autobypassable zones) are not listed. Then the option **When zones are not ready** (General settings) decides:

*   **Refuse** (the default): nothing is sent; the action fails with a message that lists the zones (for example *"Area 3 was not armed because these zones are not ready: Kitchen window (12)"*).
    
*   **Arm anyway and warn**: the command is sent, and a persistent notification lists the zones. They can set off an alarm unless they are closed or bypassed in time.
    

`force: true` on the `arm_area` and `activate_scenario` actions arms anyway for that call, whatever the option says.

Either way the `inim_smartliving_alarm_zones_not_ready` event fires, for example to announce the open zones: `config_entry_id` (the panel), `target` (the area or scenario name), `action` (`refused`, `armed_anyway`, or `refused_by_panel` when it was sent anyway and the panel itself refused it), `zones` (each with `zone_id` and `zone_name`) and `panel_refuses_not_ready`.

The panel itself can also be set by the installer not to allow arming with zones not ready. The integration reads that setting with the panel configuration and shows it in the *Refuses arming with zones not ready* sensor (and as `panel_refuses_not_ready` in the event: `true`, `false`, or `null` before the configuration was read again). When it is on, the panel refuses such a command even when you arm anyway or use `force`: the integration still sends it, the message says the panel refused it, and the event's `action` is `refused_by_panel`. The panel's own rule depends on each zone's programming (for example, delayed zones do not stop total arming, and zones with `no_arming_if_not_ready` stop every mode), so it may let through zones the integration lists. When the setting is off, the panel arms whatever the zones' programming says.

## Switching outputs

See [Outputs](entities.md#outputs).

## Events

*   `inim_smartliving_alarm_scenario_activation_failed`: see [When the panel refuses a command](troubleshooting.md#when-the-panel-refuses-a-command).

*   `inim_smartliving_alarm_zones_not_ready`: see [Zones not ready](#zones-not-ready).

## Displaying the Event Log

You can display the event log using a Markdown card in Lovelace, but I recommend the custom:flex-table-card (from HACS) to provide a nice tabular view.

## Sample UI Components

You'll find yaml samples for UI components in the [repo](https://github.com/matteoraf/ha-inim_smartliving_alarm_panel/tree/main/Examples).
