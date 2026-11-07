# CodeSync

CodeSync keeps a project folder in sync between Windows PCs on the same LAN. Each person has a real directory on their own disk. Cursor, Codex, Claude, and any other editor watch that path the way they already do. There is no shared drive, no account, and no commit required for a save to show up on the other machine.

The app lives in the system tray. Closing the flyout leaves sync running. Exiting the tray stops it. Shares are restored the next time the tray starts.

## How a session works

1. On the first PC, left-click the tray icon and choose **Sync a folder**.
2. Copy the `codesync://` link from the folder page.
3. On the second PC, choose **Join with link**, paste the link, choose a local folder, and choose **Start syncing**. The folder is created if it does not exist yet.
4. The folder page shows the host certificate fingerprint the first time that PC is seen. Choose **Trust this PC**. A later certificate for that host asks again.
5. CodeSync copies the tree, then keeps both copies updated as files are saved. Files the joiner already has, and the host does not, are sent the other way.

Point each IDE at its own local path. The creator of the share is the hub: other machines connect to it, and it relays changes so every copy converges. If that PC is asleep or off the LAN, joiners show **Offline** and retry until it is reachable again. The hub stays up and shows **Waiting** until someone connects.

The same PC can keep a second copy. On the folder page, **Sync another folder** prefills the link. Pick a different directory and start syncing. That copy talks to the hub over loopback and shows up under the folder name, not as a second connection from this computer.

The link looks like this:

```text
codesync://192.168.1.20:47331/s/<share-id>?t=<token>
```

The token is the permission to join. **Stop sharing** removes the share on this PC, drops its connections, and forgets its sync index. A hub that is stopped rejects the old link. Opening a `codesync://` link focuses the join page in the running tray app. The handler is registered for the current user on startup. A second launch does not start another tray. If that launch was given a link, the running app receives it.

## What syncs

A share is one folder tree, both ways, for a handful of machines. The case that matters is two.

- Saves, creates, deletes, and renames inside the share are synced. Empty folders are synced too. A deleted folder is removed on the other machine only after it is empty.
- The watcher waits about 50 ms after the last change to a path, so an editor’s temp-file-then-rename becomes one update of the final path. A short poll also walks the tree, so a missed watcher event still syncs.
- Each file is hashed with SHA-256. The receiver checks the hash before it writes. It then writes `name.codesync-tmp` beside the destination and replaces the file. A bad hash leaves the previous file in place. Those temp names are never synced. A file up to 8 MB goes in one message. A larger file, such as a Git pack, is sent in 8 MB pieces and assembled before the hash check. One message can carry up to 64 MB.
- A rename is sent as a rename when Windows reports the same file id, or when one path disappears and another appears with the same hash. A directory rename follows the files inside it.
- Symlinks are left alone.
- Files matching `.gitignore` or `.git/info/exclude` stay on the machine that has them. Nested ignore files, negation (`!`), and last-match-wins follow the same cutoff Git uses. An ignored directory hides its children. Those rules do not apply inside `.git`. A global `core.excludesFile` is not read.
- `.git` is synced, including a repository nested inside the share. The other machine receives the objects, refs, and config, so Git there can commit and push to the same remote. The pack file is sent before its index, and the index is written only after the pack is on disk. Git lock files and half-written `tmp_pack_*` files are skipped. Reflogs, `ORIG_HEAD`, and the index are per-machine bookkeeping, so the first sync of two existing repositories often keeps both copies of those files.
- Changing a `.gitignore` or `.git/info/exclude` re-filters the tree. Newly ignored files are removed from peers. Newly un-ignored files are sent.
- Nothing CodeSync-specific is written into the project folder. Sync state stays in the CodeSync data folder.

Two people editing different files is the intended case. Two writes to the same path that cross in flight are a conflict: the file already on disk is copied beside the original as `name.sync-conflict-<machine>-<YYYYMMDD-HHMMSS>.ext`, the incoming file takes the original path, and the folder page lists the copy until you choose **Dismiss**. Dismiss clears the list. The copy stays on disk, and it syncs like any other file. CodeSync does not merge source. The folder page keeps the latest 20 conflict names.

A delete does not remove a file the other side has already changed to a newer version. That newer file stays. A delete that arrives against different bytes at the same version keeps a conflict copy, then removes the path.

Applying a remote file updates the local index before the watcher sees the write. That echo is not sent back.

## Tray

The flyout is a 360 px panel anchored above the taskbar. The header shows the version (`0.0.x`). It opens when CodeSync starts. Left click toggles it. There is no native context menu.

**Folders** lists each share. A row shows the folder name, a short line (who is connected, **Waiting**, or the status detail), and a status: **In sync**, **Syncing**, **Offline**, **Paused**, **Conflict**, or **Confirm**.

**Sync a folder** picks a directory and opens that share with the link ready to copy.

**Join** takes the link and a local folder (**Choose local folder**, then **Start syncing**).

The folder page shows:

- the local path
- an ignored-path row when the tree has ignore rules (`1 ignored`, `12 ignored`). Open it to browse the ignored tree. `.git` and `*.codesync-tmp` are not listed there
- who is connected, by computer name. A brief disconnect keeps the previous names for a few seconds
- the link (**Copy**)
- **Open folder**, **Pause** / **Resume**, **Sync another folder**, and **Stop sharing**
- **Trust this PC** while a fingerprint is waiting. The value is the SHA-256 of the host certificate, in groups of four
- **Kept both copies** with **Dismiss** while conflicts are listed

Right click opens controls: **Start with Windows: On** / **Off**, **Open CodeSync folder**, and **Exit**. A development checkout also has **Update connected clients**. That runs the build (tests, version bump, `dist\CodeSync.exe`) and pushes the exe to peers running the packaged app. Those peers check the hash, replace `CodeSync.exe`, and restart. The update is sent in 1 MB chunks and is capped at 256 MB. A packaged app does not offer this button, and a source checkout does not replace itself.

Hovering the icon shows one line per folder, up to six, while both flyouts are closed. Example: `Project · 1 connected`.

## Requirements

- Windows
- Python 3.12, to run from source or to build
- The patched PySide6 build that provides `QWidget.setWindowAnchor`. `requirements.txt` installs it from `%USERPROFILE%\Documents\DEV\QT\qt-wheels`

Peers who only run `CodeSync.exe` do not need Python. The exe is a windowed PyInstaller build and carries the VC++ runtime it needs on a clean PC. Git is not required to sync a repository. It is required on a PC that will commit or push using the synced `.git`.

## Run from source

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
run.bat
```

`run.bat` starts the tray through `run.py`. Pass a `codesync://` link to land on the join page. A second launch forwards that link to the instance that is already running.

## Data and ports

App state lives in `%LOCALAPPDATA%\CodeSync`. Set `CODESYNC_HOME` to put that directory somewhere else.

| File | Role |
| --- | --- |
| `config.json` | Machine id, listen port, trusted fingerprints, shares |
| `index.db` | Per-path hash, size, mtime, NTFS file id, and change counter |
| `cert.pem` | TLS certificate for the sync listener |
| `key.dpapi` | Private key, protected with Windows DPAPI for the current user |
| `codesync.log` | Log |
| `tray.ico` | Icon written for the tray |

The certificate is generated on first run and lasts about ten years. The private key is unwrapped only while the listener loads, then the temporary `key.pem` is removed.

The sync port defaults to **47331** and is saved after the first successful bind. If that port is taken, CodeSync listens on another port and the link uses it. The listener accepts LAN connections. A localhost control port on **47330** only hands `codesync://` links to the running instance.

Logs may include relative paths and connect errors. They do not include share tokens or file contents.

Sync uses TLS 1.2 or newer. The client checks the certificate fingerprint in the flyout instead of a public CA. The token is checked with a constant-time compare before any file data is read. TCP keepalive probes a peer that vanished without closing (sleep, Wi-Fi drop).

## Build

```bat
build.bat
```

That creates `.venv` if needed, installs dependencies, checks for `QWidget.setWindowAnchor`, runs the tests, writes `assets\CodeSync.ico`, bumps the patch version in `codesync\__init__.py`, and produces `dist\CodeSync.exe`.

## Tests

```bat
.venv\Scripts\python.exe -m unittest discover -s tests
```

`tests\test_reliability.py` covers quiet-save timing, in-progress writes, and overlapping edits. The other modules cover ignore rules, the send plan, icon bytes, the client update, and a two-folder session.

## Layout

```text
run.py                entry point used by run.bat and the protocol handler
codesync/
  app.py              tray process, single-instance lock, protocol handler
  link.py             codesync:// links
  identity.py         TLS certificate and DPAPI key
  paths.py            %LOCALAPPDATA%\CodeSync and CODESYNC_HOME
  protocol.py         length-prefixed JSON header plus a file body
  flyout/             tray icon, acrylic panel, taskbar placement, pages
  sync/
    ignore.py         gitignore matching
    gitfiles.py       Git pack order, chunk size, and lock files
    watch.py          directory watcher and 50 ms settle
    index.py          SQLite path records
    plan.py           which side sends, and rename collapsing
    node.py           shares, LAN sessions, send and receive
    files.py          hashing, temp replace, conflict copies
    update.py         build and push CodeSync.exe to connected clients
packaging/            PyInstaller spec and build script
tests/
```

Messages are a 4-byte length, a JSON header, and an optional body. Sync messages are `hello`, `hello_ok`, `manifest`, `put`, `delete`, and `rename`. `upgrade` carries a new exe. `error` rejects a hello.
