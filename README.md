<p align="center">
  <img src="docs/banner.png" alt="StreamingCommunity Downloader" width="720"/>
</p>

<p align="center">
  A self-hosted web panel to download films, TV series and anime into a Jellyfin library.<br/>
  Local and single-user: request queue, notifications to Discord and Telegram, and an integrated
  file manager.
</p>

<p align="center">
  <b>u064241 fork</b> — Windows installer, portable mode, verified releases and in-panel update checks.
</p>

> Releases for this variant are published from
> [u064241/StreamingCommunity-downloader](https://github.com/u064241/StreamingCommunity-downloader).

---

## Screenshots

<p align="center">
  <img src="docs/home.png" alt="Start page" width="90%"/>
</p>
<p align="center">
  <sub><b>The start page</b> — the source's own shelves, browsable before you have searched for
  anything.</sub>
</p>

### Finding something

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/search.png" alt="Search results"/><br/>
      <sub><b>Search</b> — films, series and anime across both sources, with the state of each
      title readable on the card.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/film-detail.png" alt="Film page"/><br/>
      <sub><b>Film page</b> — plot, genres, rating, artwork and trailer, taken from the title page
      itself. No API key involved.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/serie-detail.png" alt="Series page"/><br/>
      <sub><b>Series page</b> — seasons and episodes, with the audio and subtitle tracks each one
      actually offers.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/downloads.png" alt="Downloads"/><br/>
      <sub><b>Downloads</b> — live progress per phase (video → audio → merge), as one list rather
      than a wall of cards.</sub>
    </td>
  </tr>
</table>

### Asking and approving

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/request.png" alt="My requests"/><br/>
      <sub><b>My requests</b> — what you asked for and how far it got. A request can be withdrawn
      until it is resolved.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/request-approval.png" alt="Request queue"/><br/>
      <sub><b>Request queue</b> — approving re-resolves the title and verifies the tracks first: a
      request that cannot find the audio asked for is parked, never substituted.</sub>
    </td>
  </tr>
</table>

### Running it

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/settings.png" alt="Settings"/><br/>
      <sub><b>Settings</b> — libraries, performance, naming templates, notification channels and
      download hooks.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/file-manager.png" alt="File manager"/><br/>
      <sub><b>File manager</b> — drag-and-drop, video streaming in place, and the free space left
      on the media volume.</sub>
    </td>
  </tr>
</table>

---

## Features

- Search and download films, TV series, and anime
- StreamingCommunity homepage with **Coming soon** titles and **genre shelves**, loaded progressively
- **Browse all** opens a paginated catalogue for a shelf or genre, with film/TV filters and linkable navigation
- Upcoming-title badges distinguish unavailable titles from series with a new season on the way
- **Recovers the source domain on its own** when it rotates: finds the new one, verifies it,
  and proposes it for one click
- **Plot, genres, rating, artwork and trailers** on the title page — no API key required
- Automatic quality selection (1080p → 720p → 480p → 360p)
- Parallel HLS segment download with AES-CBC decryption
- Multi-audio track merge via FFmpeg
- Subtitle download (`.vtt`) for non-Italian audio tracks
- Real-time download progress with per-phase steps (video → audio → merge)
- Integrated file manager with drag-and-drop, video streaming and free space on the media volume
- Jellyfin-friendly library paths and naming
- **Post-download webhooks** with a body template
- **Configurable file and folder names**
- Scheduled downloads
- **Notifications to Discord, Telegram, ntfy and anything else Apprise speaks**, with per-channel
  event selection
- One notification per download — and a single summary for a whole season or series
- Local and single-user: no login, no accounts; it answers only on this PC (loopback and
  `ALLOWED_HOSTS`) and refuses cross-site writes
- Request queue with approval, preserving the audio and subtitle tracks chosen
- Request status on the search result cards, and an in-app notification bell
- MCP server for AI agents (Claude Code, Cursor, …): search, download and work the request queue
- Windows installer with an Install/Portable wizard
- Portable installations can be removed from Windows Installed apps; extracted ZIPs register on first launch
- Prominent panel version badges and highlighted **Browse all** buttons on catalogue shelves
- Higher-contrast secondary text and placeholders throughout the dark interface
- Floating up/down arrows for quick navigation through long card lists
- Portable ZIP distribution with a dedicated updater helper
- Update badge, release notes and manual update check from the panel
- Portable updates with manifest validation, SHA-256 verification, backup and rollback
- Release artifacts for this fork are published independently from the upstream project

---

## Quick Start

### Windows installer and portable release

Download the latest release from the
[u064241 releases page](https://github.com/u064241/StreamingCommunity-downloader/releases/latest).

- `StreamingCommunityDownloader-Setup-v<version>.exe` opens a wizard where you can choose **Install** or
  **Portable only**.
- `StreamingCommunityDownloader-<version>-Portable.zip` is the ready-to-extract portable package.
- Portable mode selected in Setup registers **StreamingCommunity Downloader**
  entry in Windows Installed apps. It does not replace the installed edition's uninstall entry.
- An extracted portable ZIP registers itself on first launch for the current user, without admin
  rights. Its uninstaller preserves settings, databases and downloads. If the folder is moved,
  uninstall the old copy before moving it and launch the new copy at its final location.
- The installed version receives update notices and release notes in the panel. The portable
  version can download and verify a newer portable package, keep the previous EXE as a backup,
  and roll back if replacement verification fails.
- Updates are read only from this fork's releases. The upstream repository is used by the
  maintainer for reviewing changes, not as an update source for end users.

The release also contains `update-manifest.json` and `SHA256SUMS.txt`. They are used to validate
the package before a portable update. The installer update remains user-confirmed and guided by
Inno Setup.

### Building a Windows release from source

Before each release:

1. Implement and test the changes, then update the Features above and any affected instructions.
2. Update `app/__init__.py` with a new version; published versions and tags must not be reused.
3. Write user-facing release notes **in English** in `releases/v<version>.md`, including changes and
   known limitations. Keep release titles and any annotated tag descriptions in English as well.
4. Commit the code, version, README and release notes together. Integrate the reviewed changes into
   `main` to update the documentation displayed on the repository homepage; publishing a release
   alone only pushes a release branch.

For a local build, including when there are uncommitted changes:

```powershell
.\build.ps1 -Release -CleanReleaseArtifacts
```

To increment only the patch version and create a release-notes template:

```powershell
.\build.ps1 -BumpPatchVersion
```

Add `-Commit` to create the version commit automatically after the template is generated:

```powershell
.\build.ps1 -BumpPatchVersion -Commit
```

Review the generated `releases\v<version>.md` and update the Features above if needed, then build
and publish the release with the command below.

To publish the four final artifacts to the fork, authenticate GitHub CLI first with `gh auth login`
and run:

```powershell
.\build.ps1 -Release -CleanReleaseArtifacts -PublishRelease
```

The command publishes a `v<version>` release and uploads the installer, portable ZIP, manifest and
checksums. It does not overwrite the upstream repository.
Before compiling and again before publishing, it checks for uncommitted changes, existing release
tags and the correct fork remote. Release notes are taken from `releases/v<version>.md` rather than
generated from commit titles. A GitHub or network error stops publication without assuming the tag
is available. Local builds without `-PublishRelease` do not require a clean working tree.

After publishing, GitHub keeps the packages of the latest two stable Windows releases. Once both
retained releases have complete assets and matching manifest checksums, the publishing script
removes this variant's package assets from older stable releases. Tags, source history and release
notes remain available; older binary download links stop working. This retention rule does not
manage local files. A failed retention check leaves the existing packages available.

Preview or retry GitHub cleanup independently:

```powershell
.\scripts\github-release-retention.ps1 -WhatIf
.\scripts\github-release-retention.ps1
```

### From source

```bash
git clone https://github.com/u064241/StreamingCommunity-downloader.git
cd StreamingCommunity-downloader
pip install -r requirements.txt
python main.py

pip install -r requirements-dev.txt   # tests only
pytest -q
```

**Prerequisites:** Python ≥ 3.11, FFmpeg.

---

## Local use, permissions and requests

The panel is a **local, single-user application**: each person installs it on their own PC and
opens it from their own browser. There is no login, no account and no session. Two guards replace
them, applied to every request:

- **Host check** — only `localhost`, loopback addresses (`127.0.0.0/8`, `::1`) and the names in
  `ALLOWED_HOSTS` are answered. This is what stops another site from reaching the panel through a
  DNS name of its own (DNS rebinding).
- **Origin check** — a write (`POST`, `PUT`, `DELETE`, …) must come from the panel's own origin
  (`Sec-Fetch-Site` / `Origin`), so a page you visit cannot drive the panel behind your back.

The one local user holds every permission. Routes still declare what they need, so the bits keep
documenting what each action does:

| Permission | Grants |
|---|---|
| `DOWNLOAD` | start and schedule downloads directly |
| `REQUEST` | create requests |
| `MANAGE_REQUESTS` | see the queue, approve, deny, fix |
| `MANAGE_SETTINGS` | source domain, libraries, performance |
| `MANAGE_FILES` | move, rename and delete in the file manager |
| `VIEW_LIBRARY` | browse and stream the library |

The **request queue** still works locally: you are both requester and approver. A request keeps
the audio and subtitle tracks you chose; on approval the source is re-resolved against the current
domain — a dead link or a missing audio track parks the request for you to fix instead of
downloading the wrong thing. (Internally the local user is one row of `panel.db`, created by a
database migration, because requests reference their requester.)

Asking for the same content with the same tracks twice joins the open request. Asking for
**different** tracks makes two distinct requests, but they resolve to the same path in the library,
so the download fetches the union: one film in Italian and English becomes one file carrying both.
A request whose tracks are already in the file is satisfied without downloading anything: the check
reads the file's audio and subtitle streams, not its name.

Earlier versions had Jellyfin login, imported users and an `AUTH_ENABLED` variable. All of that is
gone; an existing `panel.db` is upgraded in place and its old tables are left untouched.

---

---

## Notifications

The panel notifies in two directions.

**In-app**, the bell holds request and download events, and they can be marked read or deleted,
one at a time or all at once.

**Outward**, any number of channels can be configured in **Impostazioni → Notifiche** by pasting an
[Apprise](https://github.com/caronc/apprise/wiki) URL — Discord, Telegram, ntfy, Gotify, Slack,
email, and everything else Apprise supports. No key in a configuration file, no restart. Each channel
chooses **which events it wants**; selecting none means all of them. Messages carry a title and are
coloured by outcome where the service supports it, and errors have their query strings stripped
before they leave the panel, since external channels are somebody else's servers.

Download notifications are grouped by the action that caused them — one for a film or a single
episode, and a **single summary** for a whole season, series or anime, listing any episodes that
failed and why.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | bind address |
| `VIDEOS_DIR` | `videos` | download destination |
| `DB_FILE` | `panel.db` | requests, notifications, channels, hooks — **put this on a persistent volume** |
| `DATA_FILE` | `data.json` | source domain, libraries, performance settings |
| `SCHEDULE_FILE` | `schedule.json` | scheduled downloads |
| `TMP_DIR` | `tmp` | HLS segments while a job runs, cleaned up afterwards |
| `FFMPEG_PATH` | — | full path to `ffmpeg`, when it is not on `PATH` |
| `FFPROBE_PATH` | — | full path to `ffprobe`; see the note below |
| `DOMAIN_SOURCE_URL` | a public page | where replacement domains are read from |
| `DOMAIN_NAME_PATTERN` | `streaming(community\|unity)…` | which names may be adopted automatically |
| `ALLOWED_HOSTS` | — | extra host names the panel answers to, comma-separated (loopback is always allowed) |
| `FRAME_ANCESTORS` | — | origins allowed to embed the panel in a frame |
| `TRUST_PROXY_HEADERS` | `0` | set to `1` only behind a reverse proxy you control |

The source domain and the Jellyfin library paths are configurable from **Impostazioni** in the UI.

**FFmpeg and ffprobe.** `ffmpeg` is required. On Windows the release includes a conservative
fallback through `imageio-ffmpeg`, selected for compatibility with older drivers. Users with a
newer GPU can install a newer FFmpeg separately and set `FFMPEG_PATH` (or put it first on `PATH`);
the application will use that executable instead. `ffprobe` is optional but recommended because it
lets the panel inspect the audio and subtitle tracks already present in a file.

`panel.db` holds notification-channel and webhook URLs, which often carry tokens: keep it out of
any web-served directory and off world-readable storage.

**Run one process.** Download state is held in memory by a single process, so do not add
`--workers` or scale the service — a second replica would neither see nor report on the first's
downloads.

### Embedding in another page

The panel refuses to be framed by default. To embed it (for example as a Jellyfin custom tab on the
same PC), list the embedding page's origin in `FRAME_ANCESTORS`, and its host name in
`ALLOWED_HOSTS` if it is not a loopback name.

---

## When the source moves

The source's domain changes every few weeks, and until now that simply stopped the panel: searches
failed and nothing said why. It now notices, reads the current address from a public page, checks
it actually serves the source, and shows a banner offering to switch.

It **proposes**; you apply. That page is edited by people nobody here controls, and the domain
decides where every search, image request and download referer goes — so adopting one because a web
page said so, with nobody looking, is not something the panel does by default. Only second-level
domains whose name matches the expected one are ever considered, and only if they answer like the
source. Anything refused is reported rather than swallowed, because a genuine rebrand and an edited
page look identical from the inside; you can always type a domain in by hand.

**Impostazioni → Sorgente** has a switch for adopting the new domain without asking, off unless you
turn it on, and a "Controlla ora" button.

---

## Metadata

Opening a title shows its plot, genres, rating, backdrop and trailer. All of it comes from the
source's own title page — the same page the panel already reads — so there is **nothing to
configure, no account anywhere, and no request beyond the one already being made**.

TMDB was tried as a second provider and dropped. Measured on real titles it was not an upgrade: the
site copies its synopses from TMDB, so the text was usually identical, while the round trip lost a
trailer on one title, a logo on another and 1100 characters of plot on a third.

Artwork is proxied through the panel, so the browser never talks to anybody else.
Metadata is fetched when you open a title, never for a whole page of search results.

---

## When the stream will not resolve

The video is resolved through the source's embed page, which sits behind
Cloudflare and occasionally refuses. When that happens the title's page now says
so, instead of leaving you with a download button that looks fine and a job that
fails minutes later.

A second route through another provider was built and then removed: the service
it relied on no longer publishes anything usable without reverse-engineering its
internals, which would break silently every time they deploy. The seam it plugged
into is still there, so adding one later is a small change rather than a new
feature.

---

## After a download

**Impostazioni → Hook** can tell something else that a file has landed.

- **Webhook**: a URL, a method and an optional body, where `{title}`, `{path}`, `{status}`,
  `{type}`, `{season}`, `{episode}`, `{year}` and `{error}` are substituted. With no body a JSON
  object carrying all of them is sent.

There is no "run a command" hook, deliberately: a settings change smuggled in through the browser
must never become a shell. A webhook can point at your own network — that is how it reaches a
Jellyfin library refresh URL — so the panel reports only whether the call succeeded, never what
came back.

---

## New versions

The panel asks GitHub for newer releases shortly after it starts and every 6 hours after that. When
there is one, a badge appears beside the version number in the sidebar and opens the release notes of every version you are behind. The check installs nothing.

**Impostazioni → Aggiornamenti** shows the last check, checks on demand and turns the periodic check
off. It is the one call the panel makes that downloading does not need. It sends GitHub your address
and the panel's version, as any request to it would, and needs no token.

---

## Naming

**Impostazioni → Nomi** sets how files and folders are named, per type. Each field is left blank
when it matches the default, and shows that default as its placeholder — so an untouched field
reads as "nothing changed here" rather than as a value somebody chose. Saving a blank field keeps
the default.

The defaults follow the structure [Jellyfin's own documentation](https://jellyfin.org/docs/general/server/media/movies/)
recommends, which is the layout below. Change them only if your library already follows a different
convention: Jellyfin recognises this one with no configuration at all.

Placeholders: `{title}`, `{year}`, `{season}`, `{season2}`, `{episode}`, `{episode2}` — the `2`
variants are zero-padded. Anything in square brackets appears only if the placeholders inside it
have a value, so `[ ({year})]` disappears entirely for a title with no year. Each field previews
itself as you type.

Changing a rule does not rename what is already there. Existing files keep being recognised, so
nothing is downloaded twice; they simply keep their old names until you rename them in the file
manager.

---

## Model Context Protocol (MCP)

The panel can act as an MCP server, so an AI agent (Claude Code, Cursor, or any
client speaking Streamable HTTP) can search titles, start and watch downloads,
and file or approve requests. It is served by the panel itself at
`/mcp` — no extra port, and the same reverse proxy and TLS as the web interface.

Off by default. Enable it under **Impostazioni → MCP**, then generate a token.
The token is shown once (only its hash is stored), and **the agent acts as the
panel's local user**, exactly as the browser does: requests it files are yours.

Client configuration (the settings page shows it pre-filled):

```json
{
  "mcpServers": {
    "streamingcommunity": {
      "type": "http",
      "url": "https://panel.example.com/mcp",
      "headers": { "Authorization": "Bearer <TOKEN>" }
    }
  }
}
```

With Claude Code: `claude mcp add --transport http streamingcommunity https://panel.example.com/mcp --header "Authorization: Bearer <TOKEN>"`.

Tools: `search_content`, `get_content_details`, `get_series_episodes`,
`get_anime_episodes`, `get_home_shelves`, `download_film`, `download_episode`,
`download_season`, `download_anime_episode`, `list_downloads`,
`get_download_progress`, `cancel_download`, `retry_download`, `submit_request`,
`list_requests`, `approve_request`, `get_system_status`, `list_libraries`.

---

## Output structure

```
videos/
├── Movie (2020)/
│   ├── Movie (2020).mkv
│   └── Movie (2020).en.vtt
└── Series (2019)/
    └── Season 01/
        ├── Series S01E01.mkv
        └── Series S01E02.mkv
```

The container is yours to pick, in **Settings -> Download -> Output format**: `.mkv` (the default,
which holds any combination of tracks) or `.mp4`, for devices that cannot play Matroska — plenty of
older smart TVs, Chromecast and consoles. Nothing is re-encoded either way, so the choice costs no
download time: the source is already H.264 with AAC audio, which both containers take as they are.

Subtitles follow a second setting: muxed into the file, or written beside it as
`{name}.{lang}.vtt`, the layout Jellyfin expects. Inside an MP4 they have to be converted to
`mov_text`, which drops colours, bold and positioning — keeping them as separate files avoids that
entirely.

Changing either setting leaves what is already downloaded alone. Both extensions keep counting as
present, so nothing is re-downloaded because of it.

---

## Optional SoftEther installer bundle

On Windows, `build.ps1 -IncludeSoftEtherInstaller` bundles the verified official
SoftEther client setup under `tools/softether` in the Inno Setup distribution.
It checks the pinned SHA256, size and Authenticode signer before building.
It does not run the VPN setup; installation remains a separate guided action.
The separate portable ZIP does not include this optional bundle.
See [bundle preparation and limitations](tools/softether/README.md) and the
[VPN update-check design](tools/softether/UPDATE-DESIGN.md).
SoftEther and its dependencies retain their own licenses and notices.

## License

MIT

---

## Disclaimer

This project is published for **informational and educational purposes only**. It exists to
demonstrate how HLS streams are parsed, how AES-CBC segment decryption works, how a download queue
and a permission model are built, and how the result is organised into a Jellyfin library.

It **hosts, stores and distributes nothing**. It contains no content, no catalogue and no index. It
is a client: it talks to third-party websites that it neither operates nor controls, and it has no
affiliation, sponsorship or endorsement from any of them — StreamingCommunity, AnimeUnity and
Jellyfin included. Every trademark belongs to its owner. Whether those sites are lawful to use, and
whether they remain reachable at all, is entirely outside this project's control.

Using it is your decision and your responsibility. You are the one who has to comply with the
copyright law of your country and with the terms of service of any site you point it at, and you
should only download content you hold the rights to or are otherwise entitled to access. Neither
the authors nor the contributors take any responsibility for how the software is used, nor for any
damage or legal consequence that follows from using it.

The software is provided "as is", without warranty of any kind, as set out in the
[LICENSE](LICENSE).

If you represent a rights holder and believe something here is a problem, please open an issue and
it will be addressed.
