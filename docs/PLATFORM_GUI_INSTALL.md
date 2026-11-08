# Native desktop GUI installation by operating system

Graphlink uses **pywebview** for its native window. It does not use Qt.
The Python backend and browser-based E2E test can run headless, but opening
a native window additionally requires the platform's GUI engine.

This page supplements the [Getting Started instructions](../README.md).
On a source checkout, build `web_ui/dist/app` with Node 24 (`npm ci && npm run build`
in `web_ui`). A prebuilt Graphlink wheel already bundles that frontend.

## Windows (primary development target)

- Install Python 3.12+ and the locked Python dependencies.
- Install Microsoft's **WebView2 Runtime** (Evergreen recommended).
- `pywebview` uses pythonnet / the Windows .NET desktop environment.
- Launch `python graphlink_desktop.py` from the checkout or `graphlink` from an installed wheel.

See [pywebview's Windows prerequisites](https://pywebview.idepy.com/en/guide/installation).

## Linux (GTK, no Qt)

Graphlink's core requirements intentionally do **not** force PyGObject onto
Linux servers and CI jobs that only run the FastAPI backend. A desktop
machine must have GTK and WebKit2 4.1 support. For Ubuntu/Debian systems
whose distribution offers these packages:

```bash
sudo apt update
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install 'pywebview[gtk]'
```

Use the distro's supported Python 3.12+ interpreter when using
`--system-site-packages`, because the apt-provided PyGObject is tied to the
interpreter ABI. On an alternate Python build, follow
[PyGObject's installation steps](https://pygobject.gnome.org/getting_started.html)
and install its development headers first.

When installing an already-built Graphlink wheel, the corresponding optional
dependency is `graphlink[linux-gtk]`. It adds the Python GTK bindings but
cannot install OS-provided WebKitGTK/GTK packages or start a display server.
Start Graphlink from a graphical desktop session, not a headless SSH shell.
`python -m backend.headless` is available for a CLI/headless backend.

On Fedora/other distributions use their equivalents of GTK 3,
WebKit2GTK 4.1, PyGObject and cairo introspection bindings.

## macOS (Cocoa, no Qt)

Install Python 3.12+ and the base dependencies. Python distributed inside
some macOS runtimes already includes PyObjC; for standalone Homebrew/
python.org Python, install:

```bash
python -m pip install pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-WebKit pyobjc-framework-Security
```

A prebuilt wheel also exposes `graphlink[macos-cocoa]`, which installs
these bindings on macOS only.

## Scope and verification

CI validates Python on Windows and frontend/Playwright on Linux. A browser
test against FastAPI is **not** a smoke test of a native GTK/Cocoa window.
Native desktop initialization should be tested on supported Linux/macOS
GUI environments when they are available. If `webview.start()` fails,
Graphlink logs an actionable platform-prerequisite message instead of an
unexplained native import failure.

Upstream reference: [pywebview installation guide](https://pywebview.idepy.com/en/guide/installation).
