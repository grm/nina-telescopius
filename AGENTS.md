# AGENTS.md

Guidance for coding agents working on this repository. User-facing documentation
(in French) is in `README.md`.

## What this is

A local HTTP server that **pretends to be Stellarium's Remote Control plugin** so that
N.I.N.A. (astrophotography software, Windows) imports targets chosen from
**Telescopius**. NINA is not modified and no NINA plugin is involved: the user selects
"Stellarium" as NINA's planetarium, points it at this bridge, picks a target in the
bridge's web UI, then clicks NINA's "get coordinates from planetarium" button.

```
Browser ──/ui/api/*──▶ bridge (Python stdlib) ──HTTPS──▶ api.telescopius.com/v2.2
NINA ──────/api/*────▶ bridge (Stellarium emulation)
```

## Layout

| Path | Role |
|---|---|
| `telescopius_bridge/__main__.py` | CLI entry (`python -m telescopius_bridge`), startup messages |
| `telescopius_bridge/server.py` | HTTP server: Stellarium routes, UI JSON API, static files, socket binding |
| `telescopius_bridge/stellarium.py` | Builds the Stellarium-shaped responses NINA parses |
| `telescopius_bridge/telescopius.py` | Telescopius client (cache, errors) and **normalization** to one target shape |
| `telescopius_bridge/astro.py` | Dependency-free astronomy: precession, alt/az, Sun, night grid, altitude curves |
| `telescopius_bridge/config.py` | `config.json` + `state.json` (current selection), validation |
| `telescopius_bridge/static/` | Web UI: vanilla HTML/CSS/JS, no build step, French text |
| `tests/` | `unittest` suites + fixtures from real Telescopius responses |
| `tests/nina_compat/` | C# harness running NINA's real Stellarium driver against the bridge |

## Commands

```sh
python3 -m telescopius_bridge --config /tmp/x/config.json --port 18090   # run (default port 8090)
python3 -m unittest discover -s tests -t .                                 # all tests
tests/nina_compat/fetch_nina_sources.sh                                    # enable the NINA compat tests
DOTNET=/path/to/dotnet python3 -m unittest tests.test_server -v           # needs the .NET 8 SDK
```

Use a throwaway `--config` path and a non-default port when running the server for
testing: the repo's `config.json` is the user's real config, and port 8090 may be in
use by a real Stellarium. The NINA compat tests skip themselves when .NET or the
fetched sources are missing. Say so when reporting results instead of presenting them
as passed.

## Hard constraints

- **Python standard library only, Python ≥ 3.9.** No pip dependencies: the server must
  run on the user's Windows NINA PC with a stock Python. Use `from __future__ import
  annotations` and `typing` (`Optional`, `Dict`), not `X | None` at runtime, and no
  `match` statements. Test-only tools (astropy, Playwright) go in a scratch venv outside
  the repo, never in the code or requirements.
- **Never commit secrets.** `config.json` holds the Telescopius API key and is
  gitignored, as are `state.json` and `tests/nina_compat/vendor/` (NINA sources,
  MPL-2.0, downloaded on demand). Before committing, run `git grep` for the key. The
  UI API never returns the key (`public_state` exposes only `has_api_key` and a
  4-character hint).
- **Windows is the real target**, even though development happens on macOS:
  - static MIME types are hardcoded (the Windows registry can map `.js` to `text/plain`) ;
  - the main loop sleeps in short steps so Ctrl+C works ;
  - `allow_reuse_address` is off on Windows (there it would let two processes share a port) ;
  - `host: "localhost"` binds both `127.0.0.1` and `::1` (Windows may resolve `localhost` to `::1` first) ;
  - `.bat` files are CRLF (`.gitattributes`).

## The NINA contract (do not break)

NINA's driver is `NINA.Equipment/Equipment/MyPlanetarium/Stellarium.cs`. It only reads
the routes and fields below; field names, types and status codes are the contract.

| Route | What NINA reads |
|---|---|
| `GET /api/main/status` | `location.latitude`, `location.longitude`, `location.altitude` (site import). Returns 503 when no location is configured |
| `GET /api/objects/info?format=json` | `localized-name` (preferred), `name`, `designations`, `raJ2000`/`decJ2000` in **degrees, J2000**. **404 when nothing is selected**: NINA then falls back to `/api/main/view` |
| `GET /api/stelproperty/list?format=json` | `["Oculars.enableCCD"]["value"]` and `["Oculars.selectedCCDRotationAngle"]["value"]` |
| `GET /api/main/view` | `j2000` as a **quoted string** `"[x, y, z]"` (NINA strips the quotes before parsing). 404 without a selection, so that NINA shows an error instead of importing garbage |

- **Rotation.** NINA computes `PA = (360 − ocularAngle) mod 360`, so the bridge
  reports `ocularAngle = (360 − PA) mod 360` (`stellarium.ocular_angle_from_position_angle`).
  NINA only reads the angle when `Oculars.enableCCD` is true. In that case it takes the
  coordinates from `/api/main/view` instead of `objects/info`. So `enableCCD` is true
  **exactly when the selection carries a rotation**. Otherwise NINA's framing keeps its
  current rotation (an angle of 0 would reset it).
- The framing assistant applies `RectangleRotation = 360 − PA`; the sequencer sets
  `PositionAngle = PA`. The compat harness asserts both.
- Any change to `stellarium.py` or to the Stellarium routes must pass
  `NinaDriverCompatTest`. If .NET is unavailable, say the change is unverified against NINA.

## Telescopius API facts (verified against the live API)

- Base URL `https://api.telescopius.com/v2.2`, header `Authorization: Key <key>`.
  Rate limit **60 requests/min** (`x-ratelimit-remaining` is tracked). Hence the
  responses cache (10 min; 60 s for lists; `refresh=1` bypasses it). Never add
  per-target API calls in a loop.
- Search and highlights results come in **`page_results`**, not `objects` as the
  OpenAPI spec says.
- `/targets/highlights` defaults to 10 results but honours the undocumented
  `results_per_page` (max 120) and `page`.
- `object.ra` is in **hours**, J2000; it is converted to degrees during normalization.
  `major_axis` and the `size_min` filter are in **arcseconds**.
- Solar-system objects have no `object.ra`: use `current_observation` (requested with
  `compute_current=1`), else `transit_observation`. Both are **geocentric J2000**
  (checked against astropy), so no precession is needed.
- Lists come from `/target-lists/{id}`: `ra_hr` (J2000; ignore `ra_hr_jnow`), `dec`,
  `position_angle_east` (→ rotation), `notes`. Lists carry no visibility data, so the
  bridge computes it.
- Per-target ephemerides (`ephemeris=daily`) only work for exact single-name
  searches, so altitude curves are computed locally in `astro.py`.
- Type filter codes that work: `DEEP_SKY_OBJECT`, `gxy`, `eneb`, `rneb`, `pneb`,
  `snr`, `opcl`, `glcl`, `planet`, `COMET`, `ASTEROID`. These are rejected:
  `rfneb`, `dneb`, `drkneb`.

## Data model

Every target, from any endpoint, goes through `telescopius._target()` and has the same
keys: `ra` and `dec` in J2000 degrees, `id`, `common_name`, `position_angle`,
`tonight` (Telescopius visibility), `visibility` (computed for lists), and so on.
UI responses add `night` (sampling grid: `start`, `step_minutes`, `count`, `sun_alt`,
`events`) and, for each target, `altitude_curve` on that same grid
(`App.add_sky_data`). They are omitted when no site is configured. The selection
stored in `state.json` is `{target, rotation, selected_at}`. The name sent to NINA is
computed on every request from `name_format` (`nina_name`).

## Astronomy accuracy

`astro.py` targets planning-grade accuracy. Tests pin it against astropy: alt/az within
0.02°, IAU 1976 precession within 0.0005°, Sun altitude within 0.05°. Coordinates sent
to NINA are never transformed: they stay J2000 exactly as Telescopius returned them.
If you change a formula, regenerate the reference values with astropy (scratch venv)
rather than loosening tolerances.

## UI conventions

- User-visible text is **French**. Code, comments, logs meant for developers and commit
  messages are **English**. Startup and console messages for the user are French.
- No framework and no build step. Build DOM with `textContent`/`createElement` and never
  `innerHTML` with API data (names come from Telescopius).
- Night mode (red) works through the CSS variables in `body.night`. Any new color must be
  a variable defined for both themes.
- Altitude chart rules:
  - a single 2px line in `--accent` with a 10% area wash ;
  - the sky bands (day / twilight / astronomical night) are background context ;
  - solid hairlines only, never dashed ;
  - a direct label on the post-sunset peak only ;
  - a crosshair tooltip reachable by pointer and keyboard ;
  - a text summary so values never depend on hovering.

  Check new colors for ≥ 3:1 contrast against every band.
- Check UI changes in a real browser (Playwright with `channel="chrome"` works on the
  dev Mac) and look at a screenshot, in both themes.

## Workflow

- Keep commits focused, with an imperative English subject and a body explaining *why*.
- Commit or push only when the user asks. The remote is `github.com/grm/nina-telescopius`
  (private).
- Update `README.md` (French) when behaviour visible to the user changes, and this file
  when a contract, constraint or verified API fact changes.
