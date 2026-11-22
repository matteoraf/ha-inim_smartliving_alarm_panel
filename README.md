Home Assistant Integration for Inim SmartLiving Alarm Systems
=============================================================

> [!WARNING]
> This is an experimental component. I am not affiliated with Inim Electronics and this is not an official component from Inim. Use at your own risk.
> 
> SmartLiving 1050 on firmware 6 has been tested and confirmed by multiple users. SmartLiving 10100 with a SmartLAN/G board has been tested by one user. The SmartLiving 505, 515 and all four models on firmware 5.1, are supported experimentally. Contributors are welcome.

This custom integration allows you to connect Home Assistant with Inim SmartLiving series alarm control panels that are equipped with a SmartLAN/SI or SmartLAN/G network interface (SmartLAN/G support is experimental). It enables monitoring and control of your alarm system directly from Home Assistant.

**Documentation: <https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/>**

At a glance
-----------

*   An alarm control panel entity whose modes run the panel's scenarios, and a button for each scenario you import.

*   Areas you import: arming and disarming, the arming mode (total, partial or instant), alarm, tamper and alarm memory.

*   Zones you import: status, alarm memory and tamper, the zone's programming as attributes, and bypass.

*   Outputs the panel lets your user code switch: on/off, monostable and dimmers.

*   The panel's event log, with the names programmed on the panel.

*   Local connection through a SmartLAN/SI board, or a SmartLAN/G board (experimental).

*   Supported panels: SmartLiving 1050, 10100, 515 and 505; see [Installation](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/installation/#requirements) for which are tested and the firmware needed.

Installation
------------

<!-- ha-marketplace:exclude-start -->

The integration is installed as a custom repository; it needs Home Assistant 2026.1 or later.

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=matteoraf&repository=ha-inim_smartliving_alarm_panel&category=integration)

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
    
<!-- ha-marketplace:exclude-end -->

Previews and betas are published as GitHub pre-releases: see [Preview versions](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/installation/#preview-versions).

Documentation
-------------

*   [Installation](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/installation/): requirements, supported panels, installing, preview versions.

*   [Setup](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/setup/): adding a panel, changing its connection details, re-reading its configuration.

*   [Options](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/options/)

*   [Entities](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/entities/)

*   [Actions and events](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/actions-events/)

*   [Troubleshooting](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/troubleshooting/)

*   [System report](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/system-report/)

*   [Upgrading](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/upgrading/)

*   [Known limitations](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/limitations/)

Upgrading from 0.3.x
--------------------

See [Upgrading](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/upgrading/#from-03x).

Sharing a system report
-----------------------

See [System report](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/system-report/).

Troubleshooting
---------------

See [Troubleshooting](https://matteoraf.github.io/ha-inim_smartliving_alarm_panel/dev/troubleshooting/).

Contributing
------------

Contributions to this integration are welcome! Please feel free to open an issue or submit pull requests.