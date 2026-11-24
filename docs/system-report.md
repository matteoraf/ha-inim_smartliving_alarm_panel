# System report

When you report a problem, a system report tells what the integration knows about your panel: model and firmware, board, the panel's programming as read from it, status, the event log, the integration's options and entities, and connection statistics.

## Sharing a report

1.  Open your panel's device page: **Settings** → **Devices & services** → **Inim SmartLiving Alarm** → your panel.
2.  Click **Download diagnostics** in the *Device info* card.

    ![Download diagnostics on the device page](images/download-diagnostics.webp){ width="344" }

3.  Wait for the file: it takes about 40 seconds on SmartLAN/SI. Commands are not held up meanwhile.
4.  Attach the file to your GitHub issue. If GitHub refuses the file, zip it first.

The same entry is in the menu (⋮) of your panel on the integration's page.

### How much the report shows

The *Diagnostics level* select chooses how much the next report shows. It is disabled by default: enable it on the device page, in the *Diagnostic* section.

| Level | What it contains |
|---|---|
| **Redacted** (default) | Everything public: the programming, status, events with their times, statistics. Names are cut to their first 3 characters, the serial number to its last 4 digits, the board's MAC address to its last 2 bytes; addresses, PINs, phone numbers, key serials and passwords are left out. Safe to post. |
| **Full** | Also the names, the serial number and the network addresses. PINs, phone numbers, key serials, passwords and the board's dynamic-DNS user name are still left out. |
| **Full confidential** | Everything, including the PINs (the installer code too), phone numbers, key serials and the board's passwords and dynamic-DNS user name. |

At **Redacted**, one download gives a plain file you can attach to a GitHub issue as it is.

??? note "Full and Full confidential: two files"

    At **Full** and **Full confidential** you get two files from the same reading of the panel:

    1.  The first download is **encrypted** to the maintainer's key: only the matching private key decrypts it. It also holds the Redacted view in clear. **Attach this file to your GitHub issue**; it is the only file to share.
    2.  Download diagnostics once more within 10 minutes to get a **readable copy** of the same report (it holds `PRIVATE COPY`), to check what you are sharing. **Do not share it**: keep it private and delete it when you no longer need it. A notification reminds you of this after the first download. Both files get the same name, so your browser saves the second one with a number added (for example `(1)`): attach the first one, which holds `encrypted` and `redacted_view`, never the one that holds `PRIVATE COPY`.

    Then the level goes back to *Redacted*. Nothing is stored: the report is held in memory only until the second download, for 10 minutes at most.

??? note "What is and is not read"

    The board's own details (its network settings and firmware) are read from the board itself. A SmartLAN/SI board is asked directly, so they are included even when Home Assistant is on another subnet; if the board is in use by another app at that moment, the report falls back to a search of Home Assistant's own network. A SmartLAN/G board's details are included only when Home Assistant is on the same network as the board. What is never read, at any level: the panel's cloud keys, account ID and address, and the board's internal keys.

### Finding the installer code

If you don't have your panel's installer code, a **Full confidential** report shows it (panel firmware 5.1 or newer):

1.  Set the *Diagnostics level* to *Full confidential* and download diagnostics twice, as described above.
2.  Open the second file, the readable copy, with the viewer: `python3 scripts/inim_report.py show REPORT.json`. The summary's **Installer code** row shows it, with its entry in the panel's PIN table. In the file itself it is under `panel` → `installer_code`.

The readable copy holds every PIN of your panel: keep it private, and delete it once you have the code. Reports at other levels never show the installer code.

## Reading a report

The repository has a small viewer for reports, `scripts/inim_report.py` (Python 3.13 or newer; nothing to install for reading). Download the repository from GitHub, then:

*   `python3 scripts/inim_report.py show REPORT.json` shows a report: a summary and the findings first, then areas, zones, scenarios, codes, devices, outputs, the areas' state now, status, events, the integration's options and the connection. Add `--html report.html` for one page to open in a browser. The zone list marks the zone numbers that are not zones on the panel (second zones of terminals that are not double, zones of output or unused terminals), and the counts of zone states leave them out; reports made by 0.4 have no such marks.
*   `python3 scripts/inim_report.py check REPORT.json` says whether a file is the one to attach (the Redacted report, or the encrypted one) or the private copy, which you should not share.

An encrypted report is read with `show REPORT.json --decrypt` (add `--html report.html` for the page); it needs the matching private key and the Python package `cryptography`. It reads the files only: it connects to nothing. You can view your own Redacted report and your readable copy with it; the encrypted file shows its Redacted view.
