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
