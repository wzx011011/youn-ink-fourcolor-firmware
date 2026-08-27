# Local modifications to 78__esp-wifi-connect (fork of upstream v3.0.2)

This vendored component diverges heavily from the upstream
`78/esp-wifi-connect` 3.0.2 release. Do NOT run `idf.py update-component`
on it — that would wipe the changes below. Version stays at 3.0.2 only
because the registry metadata was left untouched.

Main local changes (vs upstream):

- `wifi_station.cc` (419 -> ~1500 lines):
  - FastRc: fast reconnect cache (BSSID/channel snapshot) for sub-second
    rejoins after deep sleep.
  - IP fast-path: ARP/gateway/DNS/TCP probes to shortcut the DHCP+scan
    flow when the lease is still valid.
  - `SuspendForExternalAp()` / `ResumeAfterExternalAp()`: event-handler
    detach/reattach so the AP transfer server (ap_transfer_server.cc) can
    borrow the radio without fighting the station state machine.
  - Power-save level management and extra NVS-persisted endpoints
    (ota_url / mqtt / websocket) loaded by `LoadHttpOtaUrl()` etc.
- `wifi_manager.cc`: `SuspendStationForExternalAp` / `ResumeStationAfterExternalAp`
  wrappers + `IsStationActive()` query (added by the 2bp review fixes) so AP
  exit does not re-enable WiFi the user turned off.
- `wifi_configuration_ap.cc`: extra `/advanced/submit` endpoint persisting
  advanced config to NVS.

Upstream URL: see `idf_component.yml`.
