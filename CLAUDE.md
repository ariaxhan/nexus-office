# Working on nexus-office

Read this before changing anything. It is short because only a few things here
are non-obvious, and all of them have already cost a day.

## The one rule: a build passing proves nothing about a screen

Almost every defect this project has had was **invisible in source and obvious
on screen**:

| defect | `npm test` | a picture |
| --- | --- | --- |
| lock screen covering a working office | passed | obvious |
| villagers with black holes for heads | passed | obvious |
| the app launching with no window at all | passed | obvious |
| a stray keystroke filtering four desks out of a framing | passed | obvious |

So: **after any change that could alter what the app looks like, take pictures
and look at them.**

```sh
npm run shot        # builds the app, runs it on the fixture, five framings into shots/
```

`shots/app-*.png` are real images. Open them. If you are an agent, **read the PNG
files**: you can see them, and that is the entire point of this harness existing.

It runs against `app/Demo/demo.json`, so it needs no account, session, pipeline
or network: a check that needs credentials is a check that stops running. It does
need a screen that is awake, which is why it caffeinates. `screencapture` returns
a black frame from a sleeping display and no error.

Add a framing to `scripts/shoot.sh` when you add something the existing five
would not reveal. A framing nobody looks at is a framing that rots.

## Verify gates

```sh
npm test        # python door + Swift state rules, both headless
npm run shot    # then LOOK
```

The python tests cover the permission gate, which is the sharpest thing in here.
Do not weaken them to make a change pass.

## Things that are load bearing and look like they are not

- **The window is AppKit, not a SwiftUI `Window` scene** (`OfficeApp.swift`). A
  `Window` scene decides for itself whether to restore, and it intermittently
  launched with nothing on screen. Owning the window means it opens every time.
- **`screencapture -R` on the window's frame, never `-l <windowid>`**
  (`Shot/ShotHarness.swift`). A sheet is its own window, so a window capture of
  the gate framing photographs an office with no gate in it, which is the exact
  lie the harness exists to catch.
- **`.interactiveDismissDisabled()` on the gate sheet** (`Views/GateSheet.swift`).
  No close button, no click outside, Escape does nothing. The sheet leaves when
  the gate stops being pending, which is a fact about the agent and not about
  this window.
- **The one ordering in `Model/StateRules.swift`**: gate, waiting, locked,
  parked, refused, landed, working, idle. A repo that both landed a PR and is
  blocked on a question is blocked. Foundation only, so it tests with no app host.
- **The question id in a permit** (`client/runtime.py`). Between a gate being
  shown and answered, the agent can time out and a *different* gate can open.
  Answering by position instead of by id would approve a command nobody saw.
- **The `Host` and origin checks on writes** (`client/serve.py`). The bind address
  keeps the network out, not the browser: any page you have open can POST to
  `127.0.0.1`, and a `text/plain` form post needs no preflight. So a write must
  name this door as its Host and be JSON, from this origin or from no page at all.

## Adding something to the app

One thing, one file, so several can be built at once without collisions.

```
client/sources/<id>.py       the local data                  (listed in client/sections.py)
app/Office/Views/<Id>.swift  what it looks like
scripts/shoot.sh             a framing, if the five would not reveal it
tests/test_<id>.py           or app/OfficeTests/<Id>Tests.swift
```

The data lands in the snapshot at `world.sections.<id>`.

If a new thing makes you want to change `Model/Store.swift`, `Views/RosterView.swift`
or `Model/Api.swift`, say so rather than doing it quietly: that is a seam being
wrong, and it is worth fixing once for everyone.

## Shape of the thing

```
client/serve.py       the whole API, on this machine. Loopback only.
client/chat.py        the chatroom: bots, one history each
client/office-sync.py the only process that holds credentials
client/runtime.py     the local agent runtime adapter: gates, runs, cost
app/Office/           the Mac app: roster, threads, gate sheet, menu bar dot
scripts/shoot.sh      the eyes
```

## Rules that are not negotiable

- **The server binds loopback only.** Anything that needs it on the network goes
  through Tailscale Serve, never a bind address.
- **A gate is never hidden.** No filter, no "put this away", nothing removes a
  raised hand.
- **Put away means not polled.** A desk you put away leaves every GitHub query;
  that is the whole point of putting it away, and it comes back with the last
  data it had. A gate is from the runtime, not from a desk, so it still shows.
- **GitHub is a budget, not a faucet.** 5000 GraphQL points an hour, shared with
  the pipeline and with Aria's own `gh`. One batched query per ten desks with
  `comments(last: 1)`, every 300 s, and a pause until `reset_at` when the budget
  runs low. A failed fetch never blanks a desk: it keeps the last-good data and
  says "as of" when. Nothing in this repo may call `gh issue list` or `gh pr list`
  in a loop.
- **No hand-maintained lists.** Desks are a pure function of the repo path;
  put-away is a set of exceptions to that list, not a list.
- **Never present an estimate as a measurement.** The cost ledger has an
  `estimate` flag; a graph edge has a `confidence`. Flattening either is a lie
  with a decimal point on it.
