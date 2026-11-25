# Inim SmartLiving Alarm for Home Assistant

This integration connects Home Assistant to an Inim SmartLiving alarm panel on your local network. You can see the state of every area and zone, arm and disarm, run the panel's scenarios and switch its outputs, from dashboards and automations.

![The panel's device page in Home Assistant](images/device-page-light.webp#only-light){ width="800" }
![The panel's device page in Home Assistant](images/device-page-dark.webp#only-dark){ width="800" }

!!! warning "Not an official Inim product"

    This integration is not made or supported by Inim Electronics. Use it at your own risk.

    The SmartLiving 1050 on firmware 6 is tested and confirmed by several users. The SmartLiving 10100 with a SmartLAN/G board is confirmed by one user. The SmartLiving 505 and 515, and all four models on firmware 5.1, are supported experimentally. Reports from users of these panels help.

## Getting started

1.  **Check the [requirements](installation.md#requirements)**: a supported panel, a SmartLAN/SI or SmartLAN/G board on your network, and a user code (PIN) of the panel.
2.  **[Install the integration](installation.md#installing)** and restart Home Assistant.
3.  **[Add your panel](setup.md#adding-a-panel)**: enter the board's address and the PIN, then choose what to import.
4.  **[Find your entities](entities.md)** on the panel's device page, and add them to a dashboard.

Coming from an earlier version? Read [Upgrading](upgrading.md) first, and take a backup before you update. Something not working? See [Troubleshooting](troubleshooting.md).

## What you get

| Part of the panel | What you can do | Page |
|---|---|---|
| The whole panel | Arm and disarm with the alarm panel card, using the panel's scenarios | [Entities](entities.md#the-alarm-panel) |
| Each area | Arm or disarm it, choose the arming mode, see alarm, tamper and alarm memory | [Entities](entities.md#areas) |
| Each zone | See whether it is open, in tamper or has an alarm memory; bypass it | [Entities](entities.md#zones) |
| Each scenario | Run it with a button; see whether it is active | [Entities](entities.md#scenarios) |
| Outputs | Switch lights, gates and other outputs; dim dimmers | [Entities](entities.md#outputs) |
| Keypad thermostats | Set the temperature and the mode of each keypad's thermostat; see the room temperature | [Entities](entities.md#thermostats) |
| Event log | See the panel's recent events, with names | [Entities](entities.md#sensors) |
| Automations | Arm areas, run scenarios and reset the alarm memory with actions; react to events | [Actions and events](actions-events.md) |

Before arming an area or running a scenario, the integration asks the panel which zones are not ready, and refuses or arms anyway with a warning, as you choose (see [Zones not ready](actions-events.md#zones-not-ready)).

## Words used on this site { #terms }

| Word | Meaning |
|---|---|
| **Panel** | The SmartLiving alarm control unit (central unit). |
| **SmartLAN board** | The network board in the panel: **SmartLAN/SI** or **SmartLAN/G**. Home Assistant talks to the panel through it. |
| **Area** | A part of the house that is armed and disarmed together, for example *Ground floor*. Other systems call it a partition. |
| **Zone** | One detector input: a door contact, a motion sensor, a smoke detector. Each zone belongs to one or more areas. |
| **Terminal** | A connection on the panel or on an expander. A terminal is programmed as an input (one zone), an output, or both. |
| **Double terminal and second zone** | A terminal programmed with two zones on the same wires. Its second zone has a number above the panel's terminals: 51 to 100 on the SmartLiving 1050, for example. |
| **Scenario** | A set of actions programmed on the panel, for example *Night*: arm some areas, disarm others. |
| **Output** | A terminal or one of the fixed outputs O1 to O3 that drives something: a light, a gate, a siren. |
| **Home automation output** | An output the installer marked for home automation in the panel's programming. Only these are ticked for you at setup. |
| **Alarm memory** | After an alarm, the panel remembers which areas and zones raised it, until the memory is reset. |
| **Bypass** | Excluding a zone, so it cannot raise an alarm until it is included again. |
| **User code and PIN** | A user code is a user programmed on the panel (for example *Master*). Its PIN is the number typed to use it. Home Assistant sends commands with one PIN, so it can control only the areas of that user code. |
| **Programming mode** | While someone programs the panel from a keypad, it ignores commands. No command is sent then. |

## Contributing

Contributions are welcome: open an issue or a pull request on [GitHub](https://github.com/matteoraf/ha-inim_smartliving_alarm_panel).
