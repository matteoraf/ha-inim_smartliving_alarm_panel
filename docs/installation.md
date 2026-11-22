# Installation

This page says what you need, and how to install the integration in Home Assistant.

## Requirements

You need:

*   **Home Assistant 2026.1 or later.**
*   **A supported Inim SmartLiving panel**, on firmware 6.x or 5.1x (see the table below).
*   **A SmartLAN/SI or SmartLAN/G board** in the panel, connected to your network, that Home Assistant can reach.
*   **The board's address and port** (usually 5004). For a SmartLAN/G board, also the user name and password of the board's web page.
*   **A user code (PIN) of the panel.** Commands are sent with it, so it must control the areas you want to arm and disarm.

### Supported panels

| Model | Areas | Zone numbers | Status |
|---|---|---|---|
| SmartLiving 1050 | 10 | 1-100 | developed and tested on it |
| SmartLiving 10100 | 15 | 1-200 | confirmed working (SmartLAN/G) |
| SmartLiving 515 | 5 | 1-40 | **experimental** |
| SmartLiving 505 | 5 | 1-20 | **experimental** |

Zone numbers above the number of terminals are the second zones of double terminals.

??? note "What experimental means, and other firmware versions"

    *   **Experimental models** use the panel's memory map but have not been tested on a real panel yet; a warning in the log says so. Please report how it works for you.
    *   **Firmware 6.x** is supported as in the table above.
    *   **Firmware 5.1x is supported experimentally, and with limits**, on all four models:

        *   it has not been tested on a real panel on firmware 5.1;
        *   the panel's state flags (such as programming mode) and the results of commands are assumed to work as on firmware 6;
        *   the event log shows the free events that only firmware 5.1 has (free events 1 to 11) as unknown events;
        *   video sensors are not supported (firmware 5.1 has none).

        A warning in the log says the panel's support is experimental. Please report how it works for you.
    *   **Firmware 5.0x and older** cannot be set up ("The panel's firmware is not supported: firmware 5.1 or newer is needed").
    *   **Other SmartLiving models** cannot be set up ("This SmartLiving model is not supported yet"); please report your model and firmware on GitHub.

??? note "SmartLAN/SI and SmartLAN/G"

    Both boards are supported. The SmartLAN/G board is confirmed working on a SmartLiving 10100.

## Installing

The integration is installed as a custom repository. Use the button to open it in your Home Assistant, or follow the steps for your version below.

[Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/redirect/marketplace_repository/?owner=matteoraf&repository=ha-inim_smartliving_alarm_panel&category=integration){ .md-button }

The button opens the repository in the Marketplace on Home Assistant 2026.11 or later, and in HACS on earlier versions (HACS must be installed). Download **Inim Smartliving Alarm** there and restart Home Assistant.

=== "Marketplace (2026.11 or later)"

    1.  Go to **Settings** → **Marketplace**.
    2.  Add `https://github.com/matteoraf/ha-inim_smartliving_alarm_panel` as a custom repository of type **Integration**. Adding custom repositories needs the Marketplace's GitHub connection.
    3.  Open **Inim Smartliving Alarm** in the Marketplace and install it.
    4.  Restart Home Assistant.

=== "HACS (2026.1 to 2026.10)"

    1.  In HACS, open the menu (⋮) → **Custom repositories**.
    2.  Add `https://github.com/matteoraf/ha-inim_smartliving_alarm_panel` with type **Integration**.
    3.  Search for **Inim Smartliving Alarm** and download it.
    4.  Restart Home Assistant.

=== "Manual"

    1.  Download the `inim_smartliving_alarm` folder of the latest release.
    2.  Copy it into your `config/custom_components` folder.
    3.  Restart Home Assistant.

Then [add your panel](setup.md#adding-a-panel).

## Preview versions

Versions such as `0.4.0a1` (preview) or `0.4.0b1` (beta) are published as GitHub *pre-releases*. They are offered only to users who ask for them.

!!! warning "Take a backup first"

    **Take a Home Assistant backup before installing a preview or a beta** (**Settings** → **System** → **Backups**). A preview or a beta updates your configuration entry to a newer format that the stable 0.3.x versions do not fully understand: installed over it, 0.3.x creates entities again for 10 areas, 50 zones and 30 scenarios (including those you had not imported), cannot connect to SmartLAN/G boards, and leaves the entities only 0.4 has (such as the arming mode selects, the outputs and the diagnostic sensors) as unavailable entities. To go back to a stable version, restore that backup; this also undoes any other change made in Home Assistant since the backup. Going back from 0.5 to 0.4 needs that backup too: 0.4 does not load the configuration entry of 0.5.

=== "Marketplace (2026.11 or later)"

    On the integration's page in the Marketplace, open the menu (⋮) and choose **Include pre-releases** to be offered pre-releases, or **Install another version** and pick the version.

=== "HACS (2026.1 to 2026.10)"

    Turn on the repository's **Pre-release** switch (it is disabled by default: enable it first), or use **Redownload** on the integration's page and pick the version.

=== "Manual"

    Download the `inim_smartliving_alarm` folder from the pre-release on GitHub.

Please report problems with a preview or a beta with the [Beta report](https://github.com/matteoraf/ha-inim_smartliving_alarm_panel/issues/new?template=beta_report.yml) form, which asks for your panel model, firmware version and board type (SmartLAN/SI or SmartLAN/G).
