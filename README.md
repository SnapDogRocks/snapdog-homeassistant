<div align="center">

<img src="https://raw.githubusercontent.com/SnapDogRocks/snapdog/main/assets/snapdog-logo.svg" alt="SnapDog" width="200">

**SnapDog for Home Assistant**

Every SnapDog zone as a media player. Local, push-driven, no cloud.

[![CI](https://github.com/SnapDogRocks/snapdog-homeassistant/actions/workflows/ci.yml/badge.svg)](https://github.com/SnapDogRocks/snapdog-homeassistant/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/SnapDogRocks/snapdog-homeassistant?include_prereleases&sort=semver)](https://github.com/SnapDogRocks/snapdog-homeassistant/releases)
[![HACS: Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Home Assistant 2026.10+](https://img.shields.io/badge/Home%20Assistant-2026.10%2B-41BDF5.svg)](https://www.home-assistant.io)
[![IoT class: local push](https://img.shields.io/badge/IoT%20class-local%20push-brightgreen.svg)](custom_components/snapdog/manifest.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

</div>

---

This integration connects Home Assistant to a [SnapDog](https://github.com/SnapDogRocks/snapdog)
server, the multi-room audio server that plays AirPlay, Spotify, Subsonic and internet radio into
synchronized zones. It talks to the server's REST API and keeps its state current from the
WebSocket, so a change made in the SnapDog web interface, on a KNX wall switch or in Home
Assistant shows up everywhere within a moment. It uses the client library
[`pysnapdog`](https://github.com/SnapDogRocks/pysnapdog).

> [!NOTE]
> **Alpha.** The first release covers zones as media players. Clients, equalizer, sources and
> browsing, services and diagnostics follow. Entities and options may still change between 0.x
> releases. The integration needs SnapDog 0.30.0, which is the next server release.

## Requirements

| | |
|---|---|
| Home Assistant | **2026.10** or newer |
| SnapDog server | **0.30.0** or newer (needs `GET /api/v1/eq/presets` and the corrected API contract) |
| Network | Home Assistant can reach the server (port `5555` by default) |
| API key | Only if you turned on authentication in `snapdog.toml` (`[http].api_keys`) |

Home Assistant installs `pysnapdog` from PyPI the first time it loads the integration, so the
host needs access to PyPI for that first start.

## Install

The integration is **not yet part of the HACS default list** and not part of Home Assistant core.
Until it is, you install it as a custom repository or copy it by hand. Both ways are supported
and both give you the same code.

### With HACS (recommended)

1. In HACS, open the menu at the top right and choose **Custom repositories**.
2. Enter `https://github.com/SnapDogRocks/snapdog-homeassistant`, choose the type
   **Integration** and select **Add**.
3. Search for **SnapDog** in HACS, open it and select **Download**.
4. Restart Home Assistant.

HACS installs from the GitHub releases and shows you updates.

### By hand

1. Download the source of the latest [release](https://github.com/SnapDogRocks/snapdog-homeassistant/releases).
2. Copy the folder `custom_components/snapdog` into the `custom_components` folder of your
   Home Assistant configuration directory.
3. Restart Home Assistant.

You will not get update notices this way.

### When it becomes official

Once enough people have used it, the plan is the HACS default list first, which makes the manual
repository entry unnecessary, and a proposal for Home Assistant core after that. Your entities
stay as they are in both cases, and this section will say so when it happens.

## Set up

SnapDog announces itself with `_snapdog._tcp` on the local network, so Home Assistant usually
offers it under **Settings → Devices & services** without you doing anything. Otherwise choose
**Add integration**, search for **SnapDog** and enter the host, the port and, if you use one,
the API key.

If the server rejects the key later, Home Assistant asks for a new one and keeps your entities.

## What you get

| | |
|---|---|
| 🔊 **One media player per zone** | Play, pause, stop, next and previous, seek (when the source allows it), volume, mute, shuffle and repeat. |
| 🎵 **What is playing** | Title, artist, album, track, duration, position, playlist and the source (radio, Subsonic, AirPlay, Spotify, URL). |
| ⚡ **Push, not polling** | The WebSocket delivers every change. Entities are unavailable while it is down, and the whole state is read again when it comes back. |
| 🧠 **No guessing** | What you see is what the server reports, never what was asked for. A command shows up in the state when the server confirms it. |
| 🌍 **Eight languages** | English, German, French, Spanish, Italian, Dutch, Polish and Portuguese, the languages of the SnapDog web interface plus Polish and Portuguese. |
| 🏠 **Server and zones as devices** | One device for the server, one per zone. |

Planned next: clients as devices with volume, latency, zone and speaker correction, the equalizer,
playlist browsing, `play_media` and services.

### Zone identity

A zone is identified by the server and its **position in `snapdog.toml`**, the same way the API
paths, MQTT topics and KNX addresses are. New `[[zone]]` blocks go at the end of the file.
Inserting, deleting or reordering a block moves the zones behind it, and Home Assistant then sees
different zones under the old entities.

### Good to know

- **Mute needs a connected client in the zone.** SnapDog mutes through the Snapcast group, which
  exists only while a client is connected. Without one, the mute command changes nothing.
- **Volume is absolute.** The integration sends the level you choose. Relative steps on the
  server are added to its last known value, which lags a moment.

## Troubleshooting

Turn on debug logging for the integration and for `pysnapdog` in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.snapdog: debug
    pysnapdog: debug
```

Report problems at the [issue tracker](https://github.com/SnapDogRocks/snapdog-homeassistant/issues)
with the Home Assistant version, the SnapDog version and the relevant log lines.

## Development

```bash
uv sync
uv run lefthook install    # local hooks; needs gitleaks (brew install gitleaks)
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

The tests run against a mocked `pysnapdog` and need no server. CI also runs Home Assistant's
`hassfest` and the HACS validation. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull
request, commits follow [Conventional Commits](https://www.conventionalcommits.org).

## License

[Apache-2.0](LICENSE)
