# Known limitations

Known limitations of this version, and what you can do about them:

*   The integration polls the panel (the areas every 2 seconds by default, see [Polling](options.md#polling)), so an alarm that starts and ends between two reads never shows as *Triggered* on the alarm panel entity or on the *Area N alarm* sensor. The *alarm memory* sensors of the area and the zone stay on afterwards and show that it happened, until the memory is reset (see [Resetting the alarm memory](actions-events.md#resetting-the-alarm-memory)).

*   While Home Assistant reads the panel at the [Polling](options.md#polling) intervals, it keeps its connection to the board open. The SmartLAN/SI board accepts one local client at a time, so other apps on your network (such as the Inim app or the installer software) cannot connect to it meanwhile. Remote access through Inim Cloud is not affected.

*   The event log names the most common events: alarms, arming, disarming and resets, zone bypasses, scenarios, programming, valid codes and keys, and failed calls. Other events show as *Unknown Action* with their code. Decoding the remaining events is planned for an upcoming release.

*   Panels on firmware 5.0x or of another model that were set up with an earlier version keep running with the 1050's firmware 6 layout, so their names, zones, scenarios and event log may be wrong and their areas' delays are not shown. A repair notice says so. New setups of such panels are refused.

*   Dimmer levels are read from the panel on firmware 6 only: firmware 5.1 does not report them. On firmware 5.1 a dimmer's brightness is the last one set from Home Assistant, so a level changed at a keypad is not shown.

*   Analogue outputs are not offered. A terminal output on one of the panel's first three terminal positions cannot be switched remotely: the panel's output command cannot address those positions.

*   Support for firmware 5.1 is experimental and limited: it has not been tested on a real panel on firmware 5.1; the panel's state flags (such as programming mode) and the results of commands are assumed to work as on firmware 6; the free events that only firmware 5.1 has (free events 1 to 11) are shown as unknown events in the event log; and firmware 5.1 has no video sensors.
