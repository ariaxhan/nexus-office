The project-owned `scripts/complexity.py` and `scripts/complexity.sh` were copied from KERNEL 9.10.3 (Copyright 2026 Aria Han, MIT). The full license is in `kernel-complexity-MIT.txt`.

KERNEL's simplify workflow credits the Apache-2.0 cyclomatic-complexity-skill by saurabhkumar8112 for its refactoring method. The checked-in analyzer scripts carry KERNEL's MIT license; no upstream skill text is redistributed here.

The desktop viewer bundles noVNC 1.7.0 (MPL-2.0), including its third-party
components. Its authors and license texts are in `novnc/`; corresponding source:
https://github.com/novnc/noVNC/tree/v1.7.0 . Rebuild with `npm run bundle:desktop`.

`client/vendor/websocket.py` is the WebSocket library (only trailing whitespace normalized) from websockify
0.13.0, Copyright 2011 Joel Martin and 2016 Pierre Ossman, LGPL-3.0. Its license
and the incorporated GPL text are `websockify-LGPL-3.txt` and
`websockify-GPL-3.txt`. Corresponding source:
https://github.com/novnc/websockify/blob/v0.13.0/websockify/websocket.py . Office
subclasses it in `client/desktops.py`; users may replace this separate library.
