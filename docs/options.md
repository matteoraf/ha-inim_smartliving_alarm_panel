# Options

## Initial options

*   **Polling Interval**: How often Home Assistant should query the panel for status updates (in seconds).
    
*   **Event Log Size**: The number of recent events to store and display in the event log sensor's attributes.
    
*   **Scenario Mappings**: You can map your panel's scenarios to Home Assistant's standard alarm actions (Arm Home, Arm Away, etc.). Only the scenarios you import are offered. The setup proposes **Arm Away** and **Disarm**: for Arm Away, an imported scenario that arms every imported area (one that arms them all in total mode first, then the lowest number); for Disarm, the lowest-numbered imported scenario that disarms every imported area. What a scenario does to areas you don't import doesn't matter. If none fits, the mode stays unmapped. Check the proposal and change it if you like; the options keep what you saved.

*   **Area switches default arm mode** (options only, not at setup): the mode an area switch arms in when turned on: total (the default), partial or instant. The *arming mode* select of each area offers every mode. In the options, **Area switches arm mode per area** sets a different mode for single areas (for example, partial for the bedrooms); the others follow this option.

*   **When zones are not ready** (options only, not at setup): *Refuse* (the default) or *Arm anyway and warn*; see [Zones not ready](actions-events.md#zones-not-ready).

## Changing the options later

You can change all of this later: on the Devices & Services page, open the integration and click **CONFIGURE**, then choose **Areas, zones, scenarios and outputs**, **Area switches arm mode per area** (the mode each area's switch arms in, if not the general one) or **General settings** (everything else, including the PIN: leave **New PIN Code** empty to keep the current PIN; a new PIN is checked with the panel as at setup. The step names the user code the current PIN belongs to and the areas it controls). Unticking an area, zone or scenario removes its entities, together with any name or entity ID you gave them; ticking it again brings them back with their original entity IDs. A scenario that an alarm panel mode uses cannot be unticked: change the mode's scenario in the general settings first.
