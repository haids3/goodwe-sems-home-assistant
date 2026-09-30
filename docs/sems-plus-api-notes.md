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

**Sign conventions (confirmed against real inverter, not just decompiled):**
- `pBat`: **positive = discharging**, **negative = charging**
- `pGrid`: **positive = importing**, **negative = exporting**

(`pSystem`/`pConsum`/etc. sign conventions not separately verified — likely
always non-negative, i.e. magnitude-only, but worth a sanity check against
live data before assuming.)

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

`getTopTreeByCode` is the one to use: on an All-in-One it returned ~215 KB, 70-odd
menus and ~260 functions (device start/stop, energy management, environmental
control, AC side, PV, battery, protection, general settings). Menus nest via
`children`; functions sit in each menu's `functions`. A function looks like:

```json
{
  "address": "45218", "id": "1989877704267702274",
  "translateKey": "run_stop", "funcKey": null,
  "rwType": "RW",            // RW | RO | WO
  "control": 8,              // 8 switch, 3 number, 4 read-only enum, 16 button,
                             // 20 select, 24 time (HHmm), 25 bitmask, 33 switch w/ dependants
  "controlAttr": "[{\"transKey\":\"remote_Switch_on\",\"value\":\"1\"}, ...]",  // JSON string
  "range": "[0,1]", "gain": 1, "unit": "N/A", "type": "U16", "size": 1,
  "cpuType": "DSP", "preCommand": "FB"
}
```

Menus reuse function `translateKey`s (`backup_mode` is both), but only functions
carry `address` and `id`. Time pairs (start/end) come as a function plus
`relationFuncs`.

### Reading and writing values

- **Read:** `v1/address/remote/get-cache-device-function-parameters`
  `{"sn", "addresses": [...], "addrFuncMap": {address: id}}` → `data.data` is
  `{address: value}`. Values appear to be **raw ÷ `gain`**: TOU slot power reads
  `32` here, `320` via `/remote/get`, and its function has `gain: 10`.
- **Write:** `v1/address/remote/setDeviceFunctionParameters`
  `{"sn", "addressMap": {address: value}, "addrFuncMap", "controlItemLogs",
  "waitingForDevice": true, "plantId", "deviceName", "virtualSn"}`. This is what
  the battery controls use. Whether `addressMap` takes raw or ÷gain values for a
  `gain ≠ 1` function is **not confirmed**.

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
  `cd_mod`, `import_power_soc`, `discharge_limit_pw`). The captured "response"
  echoed the request shape with the slot switched off, so it was most likely a
  second request rather than the reply. The real reply shape is unknown.
- **Slot names:** `POST /sems-plant/api/tou/metric-config/query` `{"sn"}` →
  `data.config` is a JSON **string**:
  `{"timePeriodNameMap": {"TOU1": "", "TOU2": "10% export", ...}}`.

Field meanings:

- `TOUWeekEnable`: `249` = slot enabled, `6` = disabled (the function tree lists
  these as the day function's `highAttr`). One unused slot read `85`, meaning unknown.
- `TOUWeek`: `0` = Sunday ... `6` = Saturday.
- `TOUMonth`: `0` = January ... `11` = December. The web UI also sends `12`,
  which is not a month and seems harmless.
- `ChargeDischargePW`: per-mille of rated power, **positive = discharge,
  negative = charge** (`-1000` = charge at full rate; `320` logged as
  `cd_mod: discharge`, `discharge_limit_pw: 32`).
- `ChargeCutOffSet`: SOC % at which the slot stops.

The same slots are registers in the `tou_mode` menu, six per slot: start, end
(HHmm as an int, `1800`), day word (high byte = enable `249`/`6`, low byte =
weekday bitmask, bit 0 = Sunday, so `63871` = `0xF97F` = on, every day), power
(÷gain, see above), cutoff SOC, month bitmask (`4095` = Jan–Dec). Matched by value
on one unit: TOU1 → `47547–47552`, TOU2 → `47553–47558`, TOU3 → `47559–47564`,
TOU4 → `47565–47570`. The other four register groups (`47577`, `47583`, `47840`,
`47852`) were all zero, so slots 5–8 are unmapped. The tree has **8** work groups
while `/remote/get` offers **12** slots. The menu names (`工作组_N`) do **not** match
slot numbers.

### Other endpoints seen in the capture

- `POST v1/remote/get` `{"functionName": ["INVCurrentWorkMode"], "sn"}` →
  `{"INVCurrentWorkMode": 1}`. Read `102` later the same day, so it is an
  undocumented enum.
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
- Whether `alarm/page` honours `statusList`, `beginTime`/`endTime`, `orderBy` and
  `deviceSn`. Those names all exist in the Hermes string table and the endpoint
  accepts them without complaint, but every test account returned the same rows
  with and without them, so filtering could not be observed. `pageIndex`,
  `pageSize` and `stationIds` are confirmed to work.
- The numeric `warninglevel` field's scale (0 in every observed row).
- `pSystem`/`pConsum` sign conventions (only `pBat` and `pGrid` were confirmed).
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
