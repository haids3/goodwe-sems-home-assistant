# GoodWe SEMS Plus — Reverse-Engineered API Notes

Source: decompiled `com.goodwe.sems.plus` Android app (React Native / Hermes bytecode).
Goal: extend a Home Assistant custom integration with alarms + on/off-grid status
(and, as a bonus, live PV/battery/grid/load power flow).

## How this was produced (for reproducing / digging further)

- APK unpacked with `jadx` → `decompiled/` (Java bridge modules, manifest, resources).
  Not very useful for API logic — this is a React Native app, so the real logic is JS.
- The RN bundle `decompiled/resources/assets/index.android.bundle` is **Hermes bytecode**
  (not plain JS), version 96.
- Used [`hermes-dec`](https://github.com/P1sec/hermes-dec) (pure Python, no deps) to:
  - dump the string table → `/workspaces/sems-plus-app-decompile/strings.txt` (450k strings, useful for grepping
    URLs/endpoint paths/config keys)
  - fully decompile all ~80,000 functions to pseudocode → `/workspaces/sems-plus-app-decompile/decompiled.js`
    (200MB, ~3.9M lines). This is register-based pseudocode (`r0`, `r1`, ...), not clean
    JS, but original function/variable names are preserved as comments
    (`// Original name: xxx`), which made it possible to grep for things like
    `getGridStatusInfo`, `useAlarmListData`, `mergeApiEnergyFlowWithSecond`, etc.
- All line numbers below refer to `/workspaces/sems-plus-app-decompile/decompiled.js`.
  If that file isn't present in the new environment, re-run:
  ```
  python3 hbc-decompiler index.android.bundle decompiled.js
  ```
  (hermes-dec repo: `/home/hayden/tools/hermes-dec-main`, jadx: `/home/hayden/tools/jadx-1.5.6`)

A second source, added 2026-10-08, is a HAR capture of the SEMS+ **web portal**
(semsplus.goodwe.com). Its JS bundles are plain minified JavaScript and far easier
to read than the Hermes output. They are kept locally, not committed, in
`/workspaces/sems-plus-app-decompile/web-js-2026-10-08/`. Sections citing the
"2026-10-08 capture" or the "web JS" come from there. The second-data MQTT feed
was then verified with a listen-only probe.

Community SEMS API clients (pysems, etc.) already have auth nailed, so auth is included
here only for context/cross-reference — the new work is alarms, grid status, and energy flow.

> See [`handover-2026-09-30.md`](handover-2026-09-30.md) for the fork's work log,
> what remains unsolved, and the device-control thread in particular.
>
> **Verified against live accounts on 2026-09-29.** The alarms, grid-status and
> flow endpoints below were called for real and the responses checked. Several
> claims made from the decompile alone turned out to be wrong; those are
> corrected inline and marked **CORRECTED**. In particular: every enum arrives
> as a *string*, `alarmLevel` is a string enum rather than a number, and alarm
> rows have no `id` field.

---

## Base URLs (regional gateways)

```
https://us-gateway.semsportal.com/web/
https://eu-gateway.semsportal.com/web/
https://au-gateway.semsportal.com/web/
https://hk-gateway.semsportal.com/web/
https://hz-gateway.sems.com.cn/web/     (China)
```

Full endpoint URL = `<gateway>/web/` + `<service-prefix>` + `<path>`, e.g.:
```
https://us-gateway.semsportal.com/web/sems-alarm/api/v2/alarm/page
```

Endpoints are declared throughout the bundle as small route-config objects:
```js
{'prefix': '/sems-alarm/api', 'post': '/v2/alarm/page'}
```
(region around `decompiled.js:526300-531000` has ~250 of these across
`/sems-user`, `/sems-plant`, `/sems-remote`, `/sems-alarm`, `/sems-report` services —
worth a full read if you need more endpoints later. Quick list of raw paths also
in `/workspaces/sems-plus-app-decompile/api_paths.txt`.)

---

## Auth (for reference — already solved by community clients)

- Login: `POST /sems-user/api/v1/auth/cross-login` (REST-style, current) or legacy
  `POST /api/v2/Common/CrossLogin` / `/api/v3/Common/CrossLogin`.
- Every request carries a `Token` header = `JSON.stringify(tokenInfo)`.
- Pre-login bootstrap `tokenInfo` (`decompiled.js:869493`):
  ```json
  {
    "client": "semsplus_android",   // or semsplus_ios
    "code": "<build number>",
    "language": "en",
    "projectname": "pvmaster",
    "timestamp": 0,
    "token": "a5b3t89bf7",           // static bootstrap token, literal in the bundle
    "uid": "",
    "version": "<app version>"
  }
  ```
- Post-login, `{uid, token, timestamp}` from the login response replace the
  placeholder values (`decompiled.js:777018`) and get merged into the same envelope.
- Response envelope: `{code, msg, data}`. Success = `0` / `"0"` / `"00000"`
  depending on endpoint generation. Auth-invalid codes: `100001`
  (`SEMS_AUTH_ERROR_NO_ACCESS`), `100002` (`SEMS_AUTH_ERROR`), plus legacy string
  codes `C0602`/`Z0100`/`C0607` (`decompiled.js:736412`, `770372`).

---

## Alarms

Route config, prefix `/sems-alarm/api` (`decompiled.js:527001-527020`):

| purpose | method | path |
|---|---|---|
| list (paginated) | POST | `/v2/alarm/page` |
| detail | POST | `/v2/alarm/detail` |
| counts | POST | `/alarm/statistics` |
| acknowledge | POST | `/alarm/confirm` |
| delete | POST | `/alarm/delete` |
| star/favorite | POST | `/alarm/star` |
| filter templates | POST | `/filter/template/list` |
| gdpr export | GET | `/alarm/gdpr/{pwId}` |
| notify config (get) | GET | `/api/v2/alert-notify-config/user` |
| notify config (update) | POST | `/api/v2/alert-notify-config/update` |

### `alarm/statistics` response
Counts, good for a single "active alarms" sensor (`decompiled.js:1268693`).
**CORRECTED:** the values are *strings*, and the request needs a station filter:
`{"stationIds": [id]}`.
```json
{ "total": "16", "happened": "0", "recovery": "16" }
```
Semantics confirmed against a station with history: `total = happened +
recovery`, so `happened` is the count of currently-occurring alarms and
`recovery` the count of cleared ones.

### `alarm/page` request/response
Request needs at least `pageIndex`, `pageSize` (defaults to page size 20 if
omitted — `decompiled.js:1269188`); likely also accepts a `stationId` filter
(list is per-station in the UI) though the exact full param set wasn't traced
to a literal object — it's built up from component state. Worth checking with
a packet capture if you need date-range/level filters.

Response envelope (`decompiled.js:1269091-1269181`):
```json
{
  "code": 0,
  "data": {
    "dataList": [ /* alarm items, see below */ ],
    "current": 1,
    "total": 42,
    "size": 20
  }
}
```

### Alarm item shape — **CORRECTED**

Observed on a live `v2/alarm/page` response (not the shape the decompile
suggested). Every row carried every one of these keys:

```json
{
  "warningid": "eaef57e5d9c9d6f18520127657a315bb",
  "warning_code": "E-G3-0-12",
  "error_code": "4096",
  "warningNameEn": "Grid Waveform Abnormal",
  "warningNameZh": "电压波形检测异常",
  "warningname": "E-G3-0-12_warning",
  "cause": "E-G3-0-12_reason",
  "solution": "E-G3-0-12_solution",
  "alarmLevel": "Total_FaultLevel_alarm",
  "warninglevel": 0,
  "status": 1,
  "deviceName": "All-in-One 1",
  "devicesn": "...",
  "deviceType": "Total_DeviceType_inverter",
  "faultClassification": ["Total_FaultType_Protect"],
  "stationId": "...", "stationname": "...", "warningStationName": "...",
  "happentime": "1788717531000", "recoverytime": "1788717597843",
  "happentimes": "2026-09-07 03:58:51", "recoverytimes": "2026-09-07 03:59:57",
  "duration": "1m 6s",
  "confirmed": false, "starStatus": false, "attention": 0,
  "stationTimeSpan": -10.0, "adcode": "016300110064",
  "is_show": true, "is_add_task": 2,
  "date_format": "MM.dd.yyyy", "date_format_ym": "MM.yyyy",
  "current_user_id": "..."
}
```

Corrections to the earlier guesses:

- **There is no `id` field.** Only `warningid`. There is also no `name` and no
  `isCollected`.
- **`warningname` is a translation key**, not text (`E-G3-0-12_warning`). So are
  `cause` and `solution`. The human-readable name is **`warningNameEn`**
  (`warningNameZh` for Chinese).
- **`alarmLevel` is a string enum, not a number**: observed
  `Total_FaultLevel_alarm` and `Total_FaultLevel_Fault` — note the inconsistent
  casing after the prefix. A separate numeric `warninglevel` also exists (0 in
  every row seen), and is not the same thing.
- `status` is numeric as documented — `0 = OCCURRING`, `1 = RECOVERED`.
- `happentime`/`recoverytime` are epoch milliseconds as strings;
  `happentimes`/`recoverytimes` are pre-formatted station-local strings.
- `duration` is a pre-rendered human string ("1m 6s"), null while occurring.

Request body that works: `{"pageIndex": 1, "pageSize": 20, "stationIds": [id]}`.

**Filters work** (web capture, 2026-10-08). The same account and range returned no rows
with `"status": 0` and the recovered rows with `"status": 1`. The web sends
`{"timeType": 1, "startTime": "yyyy-MM-dd HH:mm:ss", "endTime": ..., "status": 0|1,
"confirmed": ["0"], "deviceType": [], "standardFaultLevel": [],
"faultClassification": [], "starStatus": [], "pageIndex", "pageSize"}`. Level
values come from `POST filter/template/alarm/level`: `Total_FaultLevel_Prompt`,
`Total_FaultLevel_alarm`, `Total_FaultLevel_Fault`. The web also calls
`alarm/statistics` with `{"status": 0, "stationId": id}`.
`alarm/detail` rejects `{id, warningid}` with `P0214 missing parameter`; its real
parameters were not determined (and were not needed — `alarm/page` already
carries everything above).

---

## On-grid / off-grid + station status

**Endpoint** (`decompiled.js:528838-528839`):
```
POST /sems-plant/api/app/v2/stations/basic/info?stationId={stationId}
```
Confirmed working as a POST with an empty `{}` body and the id in the query
string. Also returns `batteryCapacity`, `pvCapacity`, `installedPower`,
`timeZone`, `zoneId`, `isAllInOne`, `powerStationTypeUser` and a `permissions[]`
list, which is a cheap way to learn a station's capabilities.

Response includes:

- `gridStatus` — resolved via `getGridStatusInfo()` (`decompiled.js:1270156-1270178`):
  | value | meaning |
  |---|---|
  | `"1"` | `on_grid` |
  | `"0"` | `off_grid` |

  **CORRECTED:** the value is a string, and the field is **only present on
  battery/all-in-one stations** (`powerStationType: "2"`). PV-only stations
  (`powerStationType: "1"`, `batteryCapacity: 0.0`) omit `gridStatus` entirely —
  islanding does not apply to them — so an on/off-grid entity must not be
  created for those stations rather than left permanently unknown.

- `status` — overall station status, via `getStationStatusInfo()`
  (`decompiled.js:1270090-1270154`). **CORRECTED:** also a string (`"1"`):
  | value | meaning |
  |---|---|
  | `0` | offline |
  | `1` | running |
  | `2` | fault |
  | `3` | waiting |
  | `11` | constructing |
  | other (e.g. `4294967295`) | unknown/others |

Also present on this response (seen destructured in `decompiled.js:983700-983850`):
`name`, `permissions[]`, `hemsSn`, `powerStationType`, `powerStationTypeUser`,
`powerStationTypeActual`.

The web portal calls `POST /sems-plant/api/portal/stations/basic/info?stationId=`,
which returns the same fields plus the following (2026-10-08 capture):

- **`permissions[]` decides whether controls are allowed.** The web disables every
  device control unless the list contains **`INVERTER_REMOTE`**, and reading
  settings needs **`INVERTER_REMOTE_READ`** (`usePermissions`:
  `deviceRemoteOrg = perms.includes("INVERTER_REMOTE")`). A station shared to an
  installer carried `PV_CONFIG, DS_CONFIG, INVERTER_REMOTE, STATION_PRICE_CONFIG,
  INVERTER_REMOTE_READ, STATION_UNBIND, STATION_VIEW`. An installer's own station
  also had `STATION_EDIT, INVERTER_ADD/EDIT/DELETE, FIRMWARE_UPGRADE, …`. This is
  not an ownership signal (`isStationOwner` was false on all of them), but it is
  exactly what SEMS+ enforces.
- **`chartMap.energy_flow`**: a comma list of the flow fields this station has,
  e.g. `"pSystem,soc,pBat,pConsum,pGrid"`. One station added `pThird`; a PV-only
  station had `"pSystem,pConsum,pGrid"`.
- `isAllInOne`, `aiSn`, `aiType` (`"EMS"`), `isMicro`, `isParallel`, `isHems`,
  `hasThirdPartyDevice`, `isStationOwner`, `dataAuthorization`.

`GET /sems-plant/api/station-share/share-info?stationId=` → `{type: 2,
sharePermission: 1, sharePermissionName: "monitoring_control", ...}` on a shared
station.

One call → two useful HA binary_sensors (online/offline, on/off-grid).

---

## Live power flow (PV / battery / grid / load)

Found adjacent to the station-detail endpoint; very likely wanted alongside
grid status for the same coordinator refresh.

**Endpoint** (`decompiled.js:528848`):
```
GET /sems-plant/api/stations/flow?stationId={stationId}
```

Fields, from `useEnergyFlowNodes` destructuring (`decompiled.js:1779509-1779527`):
```json
{
  "pSystem": 0.0,     // total PV/generation power, kW
  "pThird": 0.0,      // third-party PV power, kW
  "pBat": 0.0,        // battery power, kW
  "pGrid": 0.0,        // grid power, kW
  "soc": 0,            // battery state of charge, %
  "pDiesel": 0.0,      // diesel generator power, kW
  "pEvChar": 0.0,      // EV charger power, kW
  "pConsum": 0.0,      // load/consumption power, kW
  "pHeatPump": 0.0,    // heat pump power, kW
  "flows": [ /* direction indicators, used to animate the flow diagram */ ]
}
```

**Sign conventions:**
- `pBat`: **positive = discharging**, **negative = charging**
- `pGrid`: **CORRECTED: positive = exporting, negative = importing.** The earlier
  "positive = importing" was wrong. A day of 1-minute history
  (`v1/hems/power`, below) shows `pGrid = -8.99` while the battery grid-charged
  at `pBat = -4.97` with `pConsum = 4.05` and no PV, and `+3.61` while exporting
  midday. `pSystem + pBat - pGrid = pConsum` holds in every sample. The
  second-data MQTT feed and the meter's `pAc` use the same sign.
- `pSystem` and `pConsum` were non-negative in every sample.

The response also carries `pAc`, `consumFlag`, `isGoodweInverter` and
**`refreshTime`**. That timestamp advances once per **minute**, as does device
telemetry, so polling faster than 60 s gains nothing.

`flows` is an object, not a list: `{"pSystem": ["pConsum", "pGrid"]}` means PV
feeds the load and the grid.

### Real-time push: second-data MQTT (live-verified 2026-10-08)

The web portal gets live power over MQTT rather than by polling. A
listen-only probe from plain Python (paho-mqtt over websockets) works:

1. `GET /sems-plant/api/second-data/enable?stationId=` → `true`/`false`.
2. `GET /sems-plant/api/second-data/config` → `{clientId, userName, password}`.
   The user name and password are opaque encrypted blobs that are **passed to the
   broker unchanged**. The client decrypts nothing.
3. Connect `wss://netty-wss-<region>.iot.goodwe-power.com:8885/mqtt` (`au`,
   `eu`, `hk`, `us`; China is `hz`) with TLS, websocket path `/mqtt`,
   `clean_session`, that client ID and those credentials. CONNACK was immediate.
4. Subscribe (QoS 0, granted) to:
   - `/goodwe/second-data/station/<stationId>`
   - `/goodwe/second-data/device/<sn>` for each device (inverter or All-in-One,
     each battery rack, meter)

Messages arrive **every 5 seconds**, not retained, within about 3 s of subscribing.
Offline devices and the dongle send nothing. The payload is plain JSON (the web code
also tolerates a `{title, message}` envelope with JSON inside `message`). Every
number arrives as a **string**, and fields can be `null`. `time` is station-local
with no zone.

```json
// station
{"stationId", "time": "yyyy-MM-dd HH:mm:ss", "pSystem": "5.931", "pAc", "pDc",
 "pConsum": "1.007", "pBat": "0.0", "pGrid": "4.924", "soc": "100.0",
 "qAc", "fAc", "pf", "flows": {"pSystem": ["pConsum", "pGrid"]}, "traceId"}
// inverter / All-in-One
{"sn", "time", "pAc", "pDc", "qAc", "va", "ACApparentPower", "pf", "pGrid",
 "pInv", "pBackup", "pBat", "pSystem", "soc", "status", "workStu",
 "workModeStu", "powerLimitStu": "1", "powerLimitValue": "5000",
 "mainInverter", "gridPF", "storagePF", "pDiesel", "rssi"}
// battery rack
{"sn", "time", "soc", "pBat", "a", "v", "voltage", "dcDcV",
 "batterySysNumber": "BB1", "batteryRackNumber": "6", "bmsCommStu",
 "bbWorkStu", "workStu", "status"}
// smart meter
{"sn", "time", "pAc", "totalPac", "qAc", "fAc", "pf", "communicationStatus"}
```

What is not known: whether the encrypted credentials expire (re-fetch them on
every reconnect), and how many concurrent clients one account may hold. The
meaning of the battery fields `a` and `v` is unclear: `v` read 86–89 while
`voltage` read about 400. The meter's `qAc` looks like var while the station's
looks like kVar. Never publish: the same broker carries
`/goodwe/ccm/server/frpset/<sn>`, which opens a remote tunnel on the device.

---

## Remote control (device settings)

Captured from the SEMS+ **web** portal on 2026-09-30 (the web and app share these
endpoints), then re-read live through `SemsApi`. Every path is under
`/sems-remote/api` unless it says otherwise. All reads below are safe; the two
writes were captured, not replayed.

### Discovering what a device can do

| purpose | path | body |
|---|---|---|
| tabs only | `v2/address/remote/getAllDeviceFunctionTabs` | `{"sn", "batIndex": "", "menuCode": 0}` |
| one tab's functions | `v2/address/remote/getDeviceFunctionTabMenus` | `{"sn", "menuId", "batIndex": "", "menuCode": 0}` |
| **whole tree, one call** | `v2/address/remote/getTopTreeByCode` | `{"sn", "menuCode": 0, "batIndex": ""}` |

**CORRECTED:** `getDeviceFunctionTabMenus` returned `{}` for every shape tried
earlier because it wants the tab's **`menuId`** (from `getAllDeviceFunctionTabs`),
not a `module` name. `module: "GENERAL_FUNCTIONS"` with `menuCode: 1` and the
cabinet's `batIndex` is a different view, which is why the battery controls
worked all along.

**The web's discovery sequence per device** (2026-10-08 capture, confirmed in the
web JS):

1. `POST v2/address/remote/get-related-sn {"sn", "menuCode"}` → `data.sn`
   ("realSn"). A **smart meter** (`VD3001000<inverter sn>`) resolves to its host
   inverter, and every later call uses that sn. Inverters, All-in-Ones and dongles
   resolve to themselves.
2. `GET v2/address/remote/get-work-mode?sn=` → `{"workMode": "3.0"|"2.0", "arm": "745"}`.
   This picks the work-mode UI version (see *Work modes* below).
3. `getAllDeviceFunctionTabs {"sn": realSn, "batIndex": "", "menuCode"}`.
4. `getDeviceFunctionTabMenus {"sn": realSn, "module": "GENERAL_FUNCTIONS",
   "batIndex": "", "menuCode"}` returns the **curated quick-settings set** the device
   page shows: run/stop, restart, export limit, work modes, TOU groups, peak
   shave, delayed charge. That is about 60 functions on an All-in-One, against
   about 270 in the full tree, which makes it the natural source for Home
   Assistant entities.
5. The full settings page then loads `getTopTreeByCode`,
   `v1/address/remote/battery/GetBatteryList`, `v2/address/remote/safetycountry/recommend`
   (about 690 KB, a list of safety countries), and per tab `getDeviceFunctionTabMenus
   {"menuId"}` plus `get-cache-device-function-parameters`.

The sn the web sends is `deviceType == SWITCH_CAB ? deviceSn : batterySn || realSn || deviceSn`.

**`menuCode` is the device class** (the web's device-type table):

| deviceType | menuCode |
|---|---|
| `INVERTER`, `ENERGY_STORAGE_INTEGRATED_CABINET`, `PCS`, `MICRO_INVERTER` | 0 |
| `BAT_SYS` | 1 |
| `SMART_METER` | 2 |
| `DONGLE` | 3 |
| `EV_CHARGER` | 4 |
| `SWITCH_CAB` | 5 |
| `DATA_LOGGER` | 6 |
| `BAT_BUSBAR` | 8 |
| `DIESEL_GEN` | 9 |

A meter's tree (menuCode 2, host inverter sn) holds meter binding, CT
checks and direction (RO), and `meter_target_offset` (W). A dongle's (menuCode 3)
holds soft restart, Bluetooth, Modbus-TCP, shell, auto-upgrade and LAN switches,
none of which belong in Home Assistant.

`getTopTreeByCode` is the one to use: on an All-in-One it returned ~215 KB, 70-odd
menus and ~260 functions (device start/stop, energy management, environmental
control, AC side, PV, battery, protection, general settings). Menus nest via
`children`; functions sit in each menu's `functions`. A function looks like:

```json
{
  "address": "45218", "id": "1989877704267702274",
  "translateKey": "run_stop", "funcKey": null,
  "rwType": "RW",            // RW | RO | WO
  "control": 8,              // see the control-type table below
  "controlAttr": "[{\"transKey\":\"remote_Switch_on\",\"value\":\"1\"}, ...]",  // JSON string
  "range": "[0,1]", "gain": 1, "unit": "N/A", "type": "U16", "size": 1,
  "cpuType": "DSP", "preCommand": "FB"
}
```

Menus reuse function `translateKey`s (`backup_mode` is both), but only functions
carry `address` and `id`. Time pairs (start/end) come as a function plus
`relationFuncs`.

Control types, from the web's renderer:

| control | widget | notes |
|---|---|---|
| 3 | number | `range` is raw; the displayed range is raw ÷ `gain` |
| 4 | radio select | **CORRECTED:** writable, not read-only (`breathing_light` 47879 is RW, options in `controlAttr`) |
| 8 | switch | on/off values in `controlAttr` |
| 16 | button / command | `restart` writes `361`; grid-tie `start_up`/`shutdown` write `0` |
| 19 | safety country | |
| 20 | dropdown select | |
| 22 | status text / mode | mostly RO |
| 24 | time range | start function plus end in `relationFuncs`, HHmm as an int |
| 25 | bitmask (month / weekday) | |
| 26–36 | composites | 27 spans several registers via `subAddress`, 28 is a 32-bit bitmask, 35 is a date-time split into YYMM/DDHH/mmss |

Some functions write single bits: the body then carries `bitAddresses: [{address,
bitAddress, dataFormat, dataIndex, writeValue}]` alongside `addressMap`.

**Identifiers.** `funcKey` is stable English (`PWLimitEnable`, `PWLimitThr`,
`SelfUseMode`, `BackupMode`, `TOUMode`, `OffGridMode`, `BreathLightSet`, `Restart`,
`ShutDown`) but **mostly null**. `translateKey` is always set but **not unique
within a menu**: the grid-tie inverter has two `limit_setting`, one in `W`
(40328) and one in `%Pn` (40336). Match on `funcKey`, else on `translateKey`
together with the menu path and unit.

### Reading and writing values

- **Read:** `v1/address/remote/get-cache-device-function-parameters`
  `{"sn", "addresses": [...], "addrFuncMap": {address: id}}` → `data.data` is
  `{address: value}`. Values appear to be **raw ÷ `gain`**: TOU slot power reads
  `32` here, `320` via `/remote/get`, and its function has `gain: 10`.
- **Write:** `v1/address/remote/setDeviceFunctionParameters`
  `{"sn", "addressMap": {address: value}, "addrFuncMap", "controlItemLogs",
  "waitingForDevice": true, "plantId", "deviceName", "virtualSn"}`. This is what
  the battery controls use. `virtualSn` is the device's own sn for
  `SMART_METER`, `INVERTER`, `MICRO_INVERTER` and
  `ENERGY_STORAGE_INTEGRATED_CABINET`, and is omitted otherwise.
- **Reply** (2026-10-08, six writes): `{"code": "00000", "description", "traceId"}`,
  with **no `data`**. `v1/remote/set` replies the same way.
- **Latency.** `waitingForDevice: true` blocks until the device acknowledges:
  1.0–1.6 s usually, but **29.4–30.2 s** for four of nine writes, all still
  `00000`. A client needs a timeout well above 30 s for writes, and should not
  hold up polling while one is in flight.
- **Rejection:** `{"code": "P0215", "translationCode": "op_fail", "description":
  "operation failed"}` after about 1 s (a grid-tie `mode_select` write).
- **Gain on write.** Still unconfirmed on hardware, because every captured write
  hit a `gain: 1` function. The web code points to **÷gain (display) units**: the
  number input validates against `range ÷ gain` (`Wl(range, gain)`), sends the
  typed value unchanged in `addressMap`, and then stores that same value in its
  cache of read values, which are ÷gain.

### Start / stop

Tab `device_start_stop`:

| address | translateKey | rw | values |
|---|---|---|---|
| `45218` | `run_stop` | RW | `1` run (`remote_Switch_on`), `0` stop |
| `45221` | `restart` | WO | `361` restarts |

`run_stop` reads `1` on a running All-in-One. This replaces the hard-coded
`80017` / `2043643517552594945` pair, which has no function behind it on this
model. A write through `setDeviceFunctionParameters` has not been observed in a
capture or on hardware yet.

**A grid-tie inverter (GW5000-DNS-30) has no `run_stop`.** Its `device_start_stop`
tab holds `start_up` 40330, `shutdown` 40331 (`funcKey: ShutDown`) and `restart`
40332, all control 16 writing `0`, plus `rapid_shutdown` 40337 (switch). Its export
limit is `grid-tie_power_limit` 40327 (switch), `limit_setting` 40328 (W) or 40336
(%Pn), `hard_limit` 40345 and `mode_select` 40343 (single/three-phase). All are
DSP/F7 rather than the All-in-One's ARM/F7 registers.

### Energy management highlights (All-in-One)

| address | translateKey | notes |
|---|---|---|
| `47509` / `47510` | `grid-tie_power_limit` / `limit_setting` | export limit enable, limit in W (5000 seen) |
| `47511` | `self_use` | RO, `1` = self-use mode active |
| `47605` / `47870` / `47606` | backup mode, grid charge enable, charge power % | |
| `47612` | `tou_mode_enable` | `1` = TOU mode on |
| `47038` | `off_grid_mode` | |
| `47609` | `delayed_charge` ("smart charging") | |

### TOU (time-of-use) schedule

Higher-level endpoints the web page uses:

- **Read:** `POST v1/remote/get` `{"functionName": ["TOU1", ..., "TOU12"], "sn"}` →
  `data.items[]` of `{functionName, value}`, in no particular order. Slot N:
  ```json
  {"TOUStartN": "18:00", "TOUEndN": "21:00", "TOUWeekEnableN": 249,
   "TOUWeekN": [0,1,2,3,4,5,6], "ChargeDischargePWN": 320,
   "ChargeCutOffSetN": 60, "TOUMonthN": [0,...,11]}
  ```
  Unused slots are all zeros with empty lists.
- **Write:** `POST v1/remote/set`
  `{"functionName": "TOU3", "sn", "plantId", "deviceName", "virtualSn",
  "data": {<slot 3 fields as above>}, "waitingForDevice": true,
  "controlItemLogs": {...}}`. The body `controlItemLogs` carried is a
  human-readable audit trail (`start_t`, `end_t`, `switch`, `wkly_rep`,
  `cd_mod`, `import_power_soc`, `discharge_limit_pw` or `rated_power`).
  **Reply** (2026-10-08): `{"code": "00000", "description", "traceId"}`, with no
  data. A `remote/get` straight afterwards showed the new value. Writes took 1.4 s,
  16.8 s and 30.2 s.
- **Slot count:** the web allows **8 slots on work-mode V3** (`get-work-mode`
  `"3.0"`) and **4 otherwise**, even though `remote/get` always returns TOU1–TOU12.
- **Scale:** `ChargeDischargePW` is per-mille on V2/V3; the web divides by 10 for
  its log. On **V1** it is already a percentage (divisor 1). V1 also omits the
  months and the cutoff SOC from its log.
- **Capability bits** (`remote/get` `ARMFunction2`, `ARMFunction4`): ARMFunction2
  bit 11 means TOU supports a discharge SOC, and ARMFunction4 bit 12 means TOU
  supports power-limit mode.
- **Naming a slot** (`tou/metric-config/save {"sn", "config": "<JSON string>"}`)
  follows each `remote/set`, rewriting the whole `timePeriodNameMap`.
- **Slot names:** `POST /sems-plant/api/tou/metric-config/query` `{"sn"}` →
  `data.config` is a JSON **string**:
  `{"timePeriodNameMap": {"TOU1": "", "TOU2": "10% export", ...}}`.

Field meanings:

- `TOUWeekEnable`: `249` = slot enabled, `6` = disabled (the function tree lists
  these as the day function's `highAttr`). One unused slot read `85`, meaning unknown.
- `TOUWeek`: `0` = Sunday ... `6` = Saturday.
- `TOUMonth`: `0` = January ... `11` = December. **CORRECTED:** a `12` is not
  noise. On a **discharge** slot it selects the *power limit method*: with `12`
  the slot's power limits **export** to the grid ("GRID"), without it the
  **battery discharge** ("BAT"). The web reads it as
  `TOUMonth.includes(12) ? "GRID" : "BAT"`, sends it only for discharge slots,
  and only offers the choice when ARMFunction4 bit 12 is set. It strips `12`
  before showing or logging months.
- `ChargeDischargePW`: per-mille of rated power. **Zero or negative = charge,
  positive = discharge**: the web's mode is `PW <= 0 ? charge : discharge`, so a
  discharge slot cannot have zero power. Charging, the magnitude is "charging
  from grid power". Discharging, it is the discharge power, or the export
  power with the export limit method. (`-1000` = charge at full rate; `320`
  logged as `cd_mod: discharge`, `discharge_limit_pw: 32`.)
- `ChargeCutOffSet`: SOC % at which the slot stops. The web labels it "end
  charge SOC" on charge slots and "discharge cutoff SOC" on discharge slots;
  the latter only appears with ARMFunction2 bit 11.

The same slots are registers in the `tou_mode` menu, six per slot: start, end
(HHmm as an int, `1800`), day word (high byte = enable `249`/`6`, low byte =
weekday bitmask, bit 0 = Sunday, so `63871` = `0xF97F` = on, every day), power
(÷gain, see above), cutoff SOC, month bitmask (`4095` = Jan–Dec). Matched by value
on one unit: TOU1 → `47547–47552`, TOU2 → `47553–47558`, TOU3 → `47559–47564`,
TOU4 → `47565–47570`. The other four register groups (`47577`, `47583`, `47840`,
`47852`) were all zero, so slots 5–8 are unmapped. The tree has **8** work groups
while `/remote/get` offers **12** slots. The menu names (`工作组_N`) do **not** match
slot numbers.

The 2026-10-08 `GENERAL_FUNCTIONS` view lists the tou_mode work groups 1–8 as
registers `47559, 47565, 47553, 47547, 47852, 47583, 47577, 47840`, so slot
numbers stay unmapped to registers. Use the `remote/get`/`remote/set` `TOUn`
shape. A month word of `8191` (bit 12 set) is the export limit method, the
register form of month `12` above.

### Work modes (decoded from the web JS, 2026-10-08)

**Running mode:** `remote/get {"functionName": ["INVCurrentWorkMode"]}`. The web's
table:

| value | mode | value | mode |
|---|---|---|---|
| -1 | AI | 9 | import_elec |
| 1 | self_use | 10 | export_elec |
| 2 | pv_priority_charging | 11 | bat_charging |
| 3 | pv_priority_export | 12 | bat_discharge |
| 4 | priority_import_power | 100 | backup_mode |
| 5 | priority_export_power | 101, 102 | TOU |
| 6 | energy_saving | 103, 104 | delayed_charge |
| 7 | off_grid_mode | 105, 106 | peak_shave |
| 8 | battery_standby | 107 | pv_priority_export_power |
| | | 255 | forced_shutdown_standby |

Anything else displays as self_use. This answers the "undocumented 102" below.

**Configured modes** depend on `get-work-mode.workMode`:

- **V1 (`"1.0"`): one exclusive mode.** Each mode has a code: self-use `0`,
  off-grid `1`, backup `2`, TOU `3` (`Kn` in the web JS). The web reads
  `remote/get {"functionName": ["SelfUseMode", "BackupMode", "TOUMode",
  "OffGridMode"]}` and shows a mode as active when `value[<name>]` equals its
  code (likely one register behind all four). Selecting a mode writes
  `remote/set {"functionName": "TOUMode", "data": {"TOUMode": 3},
  "controlItemLogs": {"TOU": "remote_Switch_on"}, "waitingForDevice": true, ...}`
  (log key = the mode's `transKey`: `self_use`, `backup_mode`, `TOU`,
  `off_grid_mode`) after a "confirm mode switch" dialog. A card cannot be
  switched off: the toggle always writes that mode's code. Cards come from the
  same `WORK_MODE` menu (`visible: 0`); only TOU has a settings page on V1, so
  there are no backup, peak-shaving or delayed-charge settings.
  V1 TOU editor (2026-10-10): reads only `TOU1`–`TOU4`; `ChargeDischargePW` is a
  whole percentage (no ÷10); no months and no month `12`, so no limit-method
  choice; no charge-slot cutoff SOC (the discharge cutoff SOC still follows
  ARMFunction2 bit 11).
- **V2 (`"2.0"`) and V3 (`"3.0"`): independent toggles.** One read covers them:
  `remote/get {"functionName": ["SelfConsumption", "Backup", "OffGridEnable",
  "TOUModeEnable", "DemandOrDelayed1", "DemandOrDelayed2", "GreenModeEnable",
  "DelayedChargeEnable"]}`, adding `"AutoOffGridModeEnable"` when ARMFunction4
  bit 0 is set. Observed values: `{"SelfConsumption": 1}`, `{"BackupModeEnable": 0,
  "BackupPChargeP": 0}`, `{"TOUModeEnable": 1}`, `{"OffGridEnable": 0}`,
  `GreenModeEnable: {}`, and each `DemandOrDelayedN` carries `…StartN/EndN/
  WeekEnableN/WeekN/PowerLimitN/SOCN/MonthN`. The web treats a mode as active
  when:
  - backup: `BackupModeEnable == 1`. TOU: `TOUModeEnable == 1`. Off-grid:
    `OffGridEnable == 1`.
  - peak shave: `DemandOrDelayedWeekEnable{1,2} == 252`. Delayed charge: `== 250`,
    and only when `DelayedChargeEnable` is also on.
  - self-use: always shown as on.

  Writes go through `remote/set`: `{"functionName": "TOUModeEnable", "data":
  {"TOUModeEnable": 1}}`, `{"functionName": "Backup", "data": {"BackupModeEnable":
  1}}`, `{"functionName": "OffGridEnable", "data": {"OffGridEnable": 1}}`. Peak
  shave writes `{"DemandOrDelayedWeekEnableN": 252 on / 3 off, "DemandOrDelayedWeekN":
  [...]}`, and delayed charge writes `250` on / `5` off followed by a second
  `DelayedChargeEnable` write. Mutually exclusive: backup and peak shave; peak
  shave and {backup, TOU, delayed charge}. V2 shows no off-grid card.
- **Which modes a device offers:** `getDeviceFunctionTabMenus {"sn", "module":
  "WORK_MODE", "menuCode"}`. Its children with `visible: 0` are the mode cards
  the web shows, by `funcKey` (`selfUseMode`, `backupMode`, `TOUMode`,
  `offGridMode`, `peakShaveMode`, `delayMode`; `greenMode` and
  `systemBackupMode` were hidden on the All-in-Ones checked).
- **Which DemandOrDelayed setting holds which mode** (the web's rule): if
  `DemandOrDelayedWeekEnable1` is 252/3 or `…2` is 250/5, setting 1 is peak
  shaving and 2 delayed charge; if `…1` is 250/5 or `…2` is 252/3, the other
  way round; otherwise 1 = peak shaving, 2 = delayed charge. A never-used
  setting reads `85` and is saved as "off" (3 or 5).
- **Peak shaving editor** writes to its setting N: `Start`, `End`, `SOC`
  (battery reserve %), `PowerLimit` (**grid import limit in kW**, max 655.34 on
  V3 and 500 otherwise), `Week` all days, and `WeekEnable` kept. Firmware with
  ARMFunction4 bit 5 also takes `PeakshavingStart1..4`/`End1..4` extra windows
  (`"255:255"` = unused). Log `{peak_shaving_soc, import_pw_peaklimit, start_t,
  end_t}`.
- **Delayed charge editor** writes `End` (the web labels it the *start* time),
  `PowerLimit` (**export limit, per-mille of rated power**), `Month`,
  `ChargePriority` (0 = PV charges the battery first), `WeekEnable` kept and
  `Week` all days. Log `{peak_power_sales_limit, pv_prioritize_battery_charge,
  end_t, month}`; the web maps priority 0 to `"export_grid_first"`.
- **Backup editor** writes `{"functionName": "Backup", "data":
  {"BackupChargeModelEnable": 0|1, "BackupPChargeP": <%>}}`, sending the power
  only with grid charging on. Reads return `BackupModeEnable` and
  `BackupPChargeP` but **not** `BackupChargeModelEnable`. The register behind it,
  47870 (`gird_pur_charge`, sic), returns no cached value until it has been
  written.
- **Green mode** (V3) writes `{"functionName": "GreenModeEnable", "data":
  {"OnGridSOCLowerLimit", "OffGridSOCLowerLimit", "OnGridSOCUpperLimit"}}`.
- **Off-grid with auto switching** (ARMFunction4 bit 0) writes `data:
  {"OffGridEnable", "AutoOffGridModeEnable", "AutoOffGridSOCUpperLimit",
  "AutoOffGridSOCLowerLimit"}`.
- ARMFunction4 bit 5 flags peak-shave v5 support.

### Other endpoints seen in the capture

- `POST v1/remote/get` `{"functionName": ["INVCurrentWorkMode"], "sn"}` →
  `{"INVCurrentWorkMode": 1}`. Decoded under *Work modes* above.
- `POST /sems-plant/api/web/device/station/page {"stationId", "current", "size"}` →
  every device with **`model`** (`GW9.999K-EHA-G20`, `GW8.3-BAT-D-G20`,
  `GW5000-DNS-30`), `brand`, `subtype`, `wirelessSignalStrength`, `status`.
  One call gives every device's model.
- `GET /sems-plant/api/equipments/<sn>/information?deviceType=&pwId=` → a list of
  `{code, data}`: firmware `safetyVersion`, `ratedPower`, `gridConnStu`,
  `bat1MRSn`/`bat2MRSn` (inverter); `commModuleVer`, `communicationMode`,
  `wirelessSignalStrength` (dongle); `ctPoint` (meter).
- Battery rack `telemetry` also carries `soh`, `vMaxCell`/`vMinCell` (mV),
  `tempMaxCell`/`tempMinCell`, `pMaxChar`/`pMaxDischar` (kW), `version`,
  `dcdcVersion`, `serCellTotal`.
- `POST /sems-plant/api/v1/hems/power/<stationId> {"stationId", "items":
  ["pSystem","soc","pBat","pConsum","pGrid"], "timeScale": 1, "timeZone": -11,
  "startTime", "endTime"}` → **1-minute** station power for the range, as
  `dataList[{item, powerData[{tp, power}]}]`.
- `POST /sems-plant/api/portal/equipments/<sn>/timeSeriesData {"sn", "deviceType",
  "stationId", "group", "module": "chart", "startDateTime", "endDateTime",
  "timeGranularity": "1"}` → 5-minute device series. The groups come from
  `GET equipments/<sn>/getMetricConfig?module=chart`.
- `GET /sems-plant/api/web/device/getAllDeviceType?stationId=` → the device types
  present. `GET /sems-plant/api/v1/hems/plant/basic?plantId=` → `{supportVpp}`.
- `GET /sems-user/api/v1/auth/default-service` (global host, before login) →
  the region, e.g. `"au"`.
- `POST v1/firmware-management/exist-remind {"plantId"}` → `{existRemind, count}`.
- `POST v1/address/remote/battery/GetBatteryList` `{"sn"}` → the configured
  battery model tree (`high`/`low` voltage). Each leaf has a `battery` with
  `model` (`GW5.1/8.3-BAT-D-G20`), `manufacturer`, `capacity`, charge/discharge
  voltage and current, and depth-of-discharge. That is a model string for the
  battery rack device, which `getWebInverterDevices` lacks.
- `POST v1/firmware-management/exist-force-upgrade` `{"plantId", "sn"}` →
  `{existForceUpgrade, existUpgrading, canOwnerForceUpgrade, taskGroupIds}`.
- `/sems-alarm/api/alarm/count`: seen, body and response not captured.

---

## What's NOT nailed down

Mostly resolved by the 2026-09-29 live verification. What remains:

- `alarm/detail` parameters (`P0214 missing parameter` for `{id, warningid}`).
  Not needed in practice: `alarm/page` returns the full row already.
- `alarm/page` filtering is settled (see *Alarms*): the web uses `status`,
  `startTime`/`endTime`, `confirmed` and friends rather than `statusList`/`beginTime`.
- The numeric `warninglevel` field's scale (0 in every observed row).
- Whether `setDeviceFunctionParameters` takes raw or ÷gain values for `gain ≠ 1`
  (the web code points to ÷gain).
- Second-data MQTT: credential lifetime, the limit on concurrent clients, and the
  battery `a`/`v` fields.
- A smart meter's `telemetry` returned `totalPac` and `pAc` with different values
  in one response (2.204 vs 3.437 kW), but the same value over MQTT. Which one is grid
  power is unsettled.
- Full login request body beyond `{account, pwd}` — not needed, auth is solved
  by existing clients.

## Raw artifacts (if you need to dig further)

Deliberately **not committed**: `decompiled.js` alone is 191 MiB, past GitHub's
hard 100 MiB per-file limit, and the decompiled output is GoodWe's code rather
than ours. Regenerate it with the command above instead. In this container they
live at `/workspaces/sems-plus-app-decompile/`:
- `/workspaces/sems-plus-app-decompile/decompiled.js` — full pseudocode decompile (200MB, ~3.9M lines)
- `/workspaces/sems-plus-app-decompile/strings.txt` — full Hermes string table (450k lines, good for
  quick `grep` on endpoint paths / config keys / field names)
- `/workspaces/sems-plus-app-decompile/api_paths.txt` — pre-filtered list of `/api/...` and `/v.../...`
  path literals
- `decompiled/` — jadx Java output (native RN bridge modules; not much API
  logic here, mostly BLE/camera/permissions/native glue)
- Tools used: `hermes-dec` at `/home/hayden/tools/hermes-dec-main`,
  `jadx` at `/home/hayden/tools/jadx-1.5.6`

To search for a new endpoint or field: `grep -n "<term>" /workspaces/sems-plus-app-decompile/decompiled.js`
then read a window around the hit — route configs and enums read as plain
JS object literals even though everything else is register soup.
