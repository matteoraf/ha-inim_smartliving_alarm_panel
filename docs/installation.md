# Installation

## Requirements

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

## Installing

The integration is installed as a custom repository; it needs Home Assistant 2026.1 or later.

[Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/redirect/marketplace_repository/?owner=matteoraf&repository=ha-inim_smartliving_alarm_panel&category=integration){ .md-button }

The button opens this repository in your Home Assistant: in the Marketplace on 2026.11 or later, in HACS on earlier versions (HACS must be installed). Download **Inim Smartliving Alarm** there and restart Home Assistant. Or add it yourself:

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

## Preview versions

Versions such as `0.4.0a1` (preview) or `0.4.0b1` (beta) are published as GitHub *pre-releases*. They are offered only to users who ask for them:

*   **Marketplace** (Home Assistant 2026.11 or later): on the integration's page in the Marketplace, open the menu (⋮) and choose **Include pre-releases** to be offered pre-releases, or **Install another version** and pick the version.
    
*   **HACS** (Home Assistant 2026.1 to 2026.10): turn on the repository's **Pre-release** switch (it is disabled by default: enable it first), or use **Redownload** on the integration's page and pick the version.
    
*   **Manual**: download the `inim_smartliving_alarm` folder from the pre-release on GitHub.
    

**Take a Home Assistant backup before installing a preview or a beta** (**Settings** -> **System** -> **Backups**). A preview or a beta updates your configuration entry to a newer format that the stable 0.3.x versions do not fully understand: installed over it, 0.3.x creates entities again for 10 areas, 50 zones and 30 scenarios (including those you had not imported), cannot connect to SmartLAN/G boards, and leaves the entities only 0.4 has (such as the arming mode selects, the outputs and the diagnostic sensors) as unavailable entities. To go back to a stable version, restore that backup; this also undoes any other change made in Home Assistant since the backup.

Please report problems with a preview or a beta with the [Beta report](https://github.com/matteoraf/ha-inim_smartliving_alarm_panel/issues/new?template=beta_report.yml) form, which asks for your panel model, firmware version and board type (SmartLAN/SI or SmartLAN/G).
