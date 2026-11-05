# CodeSync

Two people keep a normal project folder on their own machines. Saves show up on the other machine fast enough that Cursor, Codex, and Claude can work in that folder at the same time, on a LAN, with no commits and no mapped drive.

This is the build plan. The app is a Windows tray program. Its flyout is the Remote Islands taskbar flyout with CodeSync pages inside it.

## What a session looks like

1. Person A picks a project folder in the tray flyout and turns it into a share.
2. The flyout shows a share link. A copies it.
3. Person B, on the same network, pastes the link into their CodeSync flyout, chooses where the folder should live, and confirms the host fingerprint.
4. B receives a full copy of the folder. After that, each save on either machine is written into the other copy.
5. Each IDE is pointed at its own local path. The IDE’s normal file watcher sees incoming edits as ordinary disk changes.

The share stays up while both tray apps are running and can reach each other. Closing the flyout does not stop sync. Exiting the tray does.

## Product rules

- Each member has a real directory on local disk. Editors, agents, terminals, and build tools use that path directly.
- A share is one folder tree, synced in both directions, for a handful of machines (the case that matters is two).
- Files Git would ignore are not synced. Rules come from `.gitignore` files in the tree and from `.git/info/exclude`, including nested repositories, negation (`!`), and last-match-wins, the same way Git decides.
- The `.git` directory is never synced. Each machine keeps its own repository metadata. The working tree is the shared part. Syncing `.git` would fight index locks, HEAD, and refs the moment both people use Git.
- Changing a `.gitignore` re-filters the tree. Newly ignored files are deleted from peers. Newly un-ignored files are sent.
- App state lives in `%LocalAppData%\CodeSync`. Nothing CodeSync-specific is written into the project folder.
- The first version is Windows, on one LAN. The share link addresses the machine that created the share. If that machine is asleep or off the LAN, sync pauses until it is reachable again.
- There is no account, no cloud copy, and no history browser. Conflict copies on disk are the safety net.

Later, and not part of the first build: sync across the internet, phone clients, syncing `.git`, and a timeline of old file versions.

## Why this is fast enough for two agents

Cursor, Codex, and Claude all read and write the local project. They do not need a shared editor buffer. They need the other person’s save to land as a finished file before the next tool call.

On a quiet gigabit LAN the budget for a source file under 256 KB is:

| Step | Budget |
| --- | --- |
| Editor finishes the replace (temp file, then rename) | already done before we start |
| Watcher sees the settled file | 50 ms |
| Hash, send, and write on the peer | 100 ms |
| Peer IDE watcher notices the file | the IDE’s own debounce, often 50–200 ms |

Target: the peer’s file contents match within **200 ms** of the local file settling, for files under 256 KB. Larger files stream in chunks and can take longer. `node_modules`, build output, and other ignored trees never enter this path.

Two agents editing **different files** is the supported case and should feel immediate. Two agents editing **the same file** in the same moment is a conflict: both versions are kept, and the tray says so. CodeSync does not merge source. Agents rewrite whole files; a character-level merge would corrupt those writes.

## Sync behavior

### Watch

A watcher on each share uses Windows directory changes. Events are interpreted after the file settles for about 50 ms so an in-progress write is not sent:

- Editor atomic save (write temp, rename over the target) becomes one update of the final path.
- Create, replace, delete, and rename inside the share are all synced.
- A burst of writes to the same path collapses to the latest settled contents.
- A burst across many paths stays concurrent so an agent that writes ten files still moves ten files.

### What gets sent

Small files go in one message after a SHA-256. Files over 1 MB go in chunks with the same hash at the end. The receiver writes to a temp file in the CodeSync state directory, checks the hash, then replaces the destination. A failed transfer leaves the previous file in place.

Each path has a small record in a local SQLite database: relative path, hash, size, and a per-share counter that increases on every local or remote change. That record is how a peer knows whether it has the file.

### Echoes

Applying a remote file creates a local watcher event. That event is dropped when the path and hash match the write we just applied. Our own saves still propagate.

### Conflicts

A conflict is: this machine has a local change the peer has not acknowledged, and a remote change for the same path arrives.

- The file that was already on disk is moved beside itself as `name.sync-conflict-<machine>-<time>.ext`.
- The incoming file is written to the original path.
- The tray lists the conflict until the user dismisses it or deletes the copy.
- If only one side changed the file, that version is the file. The other side updates in place.

Deletes follow the same rule. A delete does not remove a file the other side has changed since the delete was generated. That change wins, and the tray records it.

### Initial copy

Joining a share builds a file list from the host (paths, sizes, hashes), skips ignored paths, then copies missing or different files. The joiner’s existing files that the host does not have are sent the other way after the first snapshot, so an existing folder can be adopted. The user picks an empty directory or an existing one and sees a one-line summary before the copy starts (`248 files, 12 MB`).

## Share link

The creating machine listens on a LAN port (default **47331**). The link looks like:

```text
codesync://192.168.1.20:47331/s/<share-id>?t=<token>
```

- `share-id` identifies the folder.
- `token` is a long random secret. Possession of the link is permission to join.
- The tray registers the `codesync://` protocol so opening the link focuses the flyout on the join page. Pasting the same text works when the protocol handler is unavailable.
- The link uses the LAN address the creator is advertising. A later refresh can rewrite the host if DHCP changes it; the token and share id stay.
- The listener binds to LAN interfaces. The token is checked before any file data is read.

First connection to a host shows that host’s certificate fingerprint in the flyout. The user accepts it once. Later connections to a different certificate stop and ask again. This is the same confirm-once idea as the Remote Islands certificate row, with CodeSync copy.

The flyout can copy the link. It can also show a short join code as a fallback spelling of the same token, for reading aloud on a call. Both grant the same access.

## Tray flyout

CodeSync is its own app. It copies the Remote Islands flyout shell and keeps that shell’s behavior:

| Remote Islands piece | Use in CodeSync |
| --- | --- |
| `studio_flyout/tray.py` | pystray icon. Left click toggles the flyout. Right click opens the controls flyout. Hover starts the peek. The native context menu stays removed. |
| `studio_flyout/window.py` | Frameless 360 px panel, stacked pages, taskbar-safe placement. |
| `studio_flyout/styles.py` | Same stylesheet, Segoe UI Variable 10 pt, dark and light tokens. |
| `studio_flyout/slide_fade.py` and the constants in `constants.py` | Same open and close timing (170 ms / 150 ms, fade 120 ms). |
| `studio_flyout/win32_dwm.py` | Acrylic and Windows accent on the panel. |
| `studio_flyout/win32_taskbar.py` | Anchor above the taskbar, including auto-hide and multi-monitor. |
| `studio_flyout/hover_peek.py` | Compact status while the main flyout is closed. |
| `studio_flyout/service.py` | Qt lifecycle tied to the tray icon. |

Remote Islands host, capture, discovery grid, and elevated-device pages are not brought over. Those modules stay in `C:\Users\wumbl\AndroidStudioProjects\RemoteIslands`. CodeSync vendors a copy of the shell files so the two apps can change independently. The visual spec is: same width, same acrylic, same type, same motion, same click and hover behavior.

### Pages

**Left click — main flyout**

- **Folders.** One row per share: name, local path, and a status word (`In sync`, `Syncing`, `Offline`, `Conflict`). Primary actions: **Sync a folder** and **Join with link**.
- **Sync a folder.** Windows folder picker, then the new share with the link selected and a **Copy link** button.
- **Join.** Link field, local folder picker, fingerprint confirm, summary of what will copy, then **Start**.
- **Folder.** Peers by computer name, last activity, **Copy link**, **Open folder**, **Pause**, **Stop sharing**. Conflicts listed here with **Show in Explorer**.

**Right click — controls flyout**

Same panel chrome. Rows for start with Windows, open the CodeSync data folder, and **Exit**. Exit stops sync and removes the tray icon.

**Hover peek**

One or two lines while neither flyout is open: `2 folders · In sync`, or `Project · Syncing 4 files`, or `Project · Offline`.

## Process layout

```text
CodeSync tray (Python 3.12, PySide6, pystray)
  flyout/          vendored Remote Islands shell, CodeSync pages
  sync/
    ignore.py      gitignore matching
    watch.py       directory watcher and settle timer
    store.py       SQLite path records
    engine.py      per-share send and receive
    transfer.py    hash, temp write, replace
  net/
    listener.py    LAN port, TLS, token check
    session.py     one connection to a peer
    link.py        parse and build codesync:// links
```

One tray process owns the UI and the sync engine. Shares run concurrently. A share with two members is a single TCP session from the joiner to the creator. A third member also connects to the creator; the creator relays file events so every copy converges. The creator is the hub for that share. This matches the link, which already points at the creator.

Messages are length-prefixed JSON headers plus raw file bytes. Names to implement: `hello`, `manifest`, `put`, `delete`, `rename`, `ack`, `conflict`. `hello` carries the machine id, display name (the Windows computer name), share id, and token.

Machine id is a random UUID stored in the CodeSync data folder.

## Ignore matching

Use a Git-compatible matcher (the `pathspec` rules for `gitwildmatch`), fed with:

- every `.gitignore` from the share root down to the file
- `.git/info/exclude` when that file exists
- a built-in rule that always matches `.git` and everything under it

A global `core.excludesFile` is included when Git can report it (`git config --get core.excludesFile`), so machine-local junk the user already told Git to skip stays local. If Git is not installed, the tree’s own `.gitignore` files and `.git/info/exclude` still apply.

Ignored watcher events are discarded before hash or network work.

## Security on the LAN

- TLS on the sync port. The private key is generated on first run and stored with Windows DPAPI for the current user.
- The token is required on `hello` and is not reusable as a file path.
- The fingerprint confirm is stored per host machine id.
- Stopping a share invalidates that share’s token. Existing peers drop off. A new link can be issued from the folder page.
- Logs may contain relative paths. They must not contain tokens or file contents.

## Build order

1. **Shell.** Tray icon, left flyout, right flyout, hover peek, empty Folders page. Confirm side by side with Remote Islands that width, acrylic, font, placement, and slide match.
2. **Ignore and watch.** Point at a sample repo, change files, and print the settled events with ignored paths removed. Include a nested `.gitignore`, a negation, and `.git`.
3. **Two processes, one folder.** Loopback sync between two directories in under the 200 ms budget for a small file. Echoes do not bounce. A simultaneous edit produces a conflict copy.
4. **Link and join.** Create share, copy link, join from a second Windows session on the LAN, fingerprint confirm, initial copy, then live edits.
5. **Tray status.** Pause, offline when the hub sleeps, conflict row, hover line, start with Windows.
6. **Agent soak.** Two checkouts. One machine edits with Cursor, the other with a second agent, on different files, for a few minutes. Confirm each save lands, ignored folders stay put, and a deliberate same-file collision keeps both copies.

## Acceptance

- A `.gitignore`d file created on one machine never appears on the other. A file removed from `.gitignore` does appear.
- `.git` on each machine stays independent through a sync session.
- Saving a 20 KB source file on machine A changes that path on machine B within 200 ms on a quiet LAN, measured from the settled local file to the matching hash on B.
- An atomic editor save produces one peer update, not a temp-file flicker.
- Two overlapping writes to one path keep both bodies.
- The flyout is the Remote Islands flyout shell: 360 px, acrylic, same slide timings, left click, right click, hover peek, no native menu.
- A second machine joins only with the link, picks a local folder, and ends up with a working copy the IDE can open.
