# Working on nexus-office

Read this before changing anything. It is short because only a few things here
are non-obvious, and all of them have already cost a day.

## The one rule: a build passing proves nothing about a room

This is a 3D surface. Almost every defect this project has had was **invisible in
source and obvious on screen**:

| defect | `npm run build` | tests | a picture |
| --- | --- | --- | --- |
| lock screen covering a working office | passed | passed | obvious |
| near wing rendered 3x closer than the far one | passed | passed | obvious |
| villagers with black holes for heads | passed | passed | obvious |
| face plate hidden inside its own skull | passed | passed | obvious |
| name plaque parked across the face | passed | passed | obvious |
| raised hand reading as a shrug | passed | passed | obvious |

So: **after any change that could alter what the room looks like, take pictures
and look at them.**

```sh
npm run build && npm run shot        # every framing into shots/
npm run shot -- gate desk            # just those two
```

`shots/*.png` are real images. Open them. If you are an agent, **read the PNG
files** — you can see them, and that is the entire point of this harness existing.

It runs against `?demo=1`, so it needs no account, no session, no pipeline and no
network. A check that needs credentials is a check that stops running.

Add a framing to `scripts/shoot.mjs` when you add a feature the existing shots
would not reveal. A framing nobody looks at is a framing that rots.

## Verify gates

```sh
npm test        # node tests + python tests
npm run build
npm run shot    # then LOOK
```

The python tests cover the permission gate, which is the sharpest thing in here.
Do not weaken them to make a change pass.

## Things that are load bearing and look like they are not

- **`[hidden] { display: none !important; }`** in `styles.css`. An id selector
  with a `display` outranks the user agent's `[hidden]` rule, so without this the
  DOM reports `hidden === true` while the element sits there in full view.
- **`this.scene.updateMatrixWorld(true)` before raycasting** in `office.js`.
  Raycasting reads `matrixWorld`, which only the renderer refreshes. Without it
  every click on a desk silently finds nothing, with no error.
- **The face plate at z 0.36, tilted -0.3** in `villager.js`. The head sphere has
  radius 0.34; anything closer is inside it and loses the depth test. Tilting it
  further swings its lower edge into the skull and eats the mouth.
- **The question id in a permit** (`client/runtime.py`). Between a gate being
  shown and answered, the agent can time out and a *different* gate can open.
  Answering by position instead of by id would approve a command nobody saw.
- **Escaping before markup** in `ui/markdown.js`. Issue bodies are written by
  anyone who can open an issue and render into a page holding a session token.

## Adding something to the room

Anything that is not a desk is a **fixture**: the clock, the cost chart, the
mailroom. One fixture, one file, so several can be built at once without anyone
stepping on anyone.

```
src/scene/fixtures/<id>.js   the 3D object and its panel      (the contract is in all.js)
client/sources/<id>.py       the local data, if it needs any  (listed in client/sections.py)
tests/<id>.test.js           or tests/test_<id>.py
```

The data lands in the snapshot at `world.sections.<id>`. `scripts/shoot.mjs`
discovers fixtures from the directory and takes a picture of each, so a new
fixture cannot ship without one, and its shot stays red until it is built.

Nothing else needs editing. If a fixture makes you want to change `office.js`,
`panel.js` or `styles.css`, say so rather than doing it quietly: that is a seam
being wrong, and it is worth fixing once for everyone.

## Shape of the thing

```
client/serve.py       the whole API, on this machine. Loopback only.
src/scene/            the room: layout, furniture, camera, picking, characters
src/ui/               panel, markdown, filters
src/demo.js           the fake floor behind ?demo=1
client/office-sync.py the only process that holds credentials
client/runtime.py     the local agent runtime adapter: gates, runs, cost
scripts/shoot.mjs     the eyes
```

`window.office` is exposed in the browser on purpose. A 3D surface that can only
be inspected by squinting at screenshots is a surface nobody can debug.

## Rules that are not negotiable

- **The server binds loopback only.** Anything that needs it on the network goes
  through Tailscale Serve, never a bind address.
- **A gate is never hidden.** No filter, no "put this away", nothing removes a
  raised hand from the room.
- **Hidden is never silent.** If something put away starts needing a human, the
  room says so.
- **No hand-maintained lists.** Villagers are a pure function of the repo path.
- **Never present an estimate as a measurement.** The cost ledger has an
  `estimate` flag; a graph edge has a `confidence`. Flattening either is a lie
  with a decimal point on it.
