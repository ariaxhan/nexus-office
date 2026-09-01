"""The phone page, and the door it comes through.

The page itself is three files with no build step, so the things that can go
wrong with it are not compile errors. They are: it stops being reachable, it
starts being reachable by someone who is not Aria, it grows a script tag with
code inside it that the policy then has to be loosened for, or it starts asking
GitHub for a fresh snapshot on a timer and quietly spends the hour's budget from
a pocket. Each of those is one test here.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import http.client
import json
import pathlib
import re
import sys
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

PHONE = ROOT / "client" / "phone"
HTML = PHONE / "index.html"
CSS = PHONE / "phone.css"
JS = PHONE / "phone.js"

# The name Tailscale Serve puts in front of this door. Anything on it must carry
# a login header; loopback is this Mac and never does.
TAILNET = "this-mac.tailnet.ts.net"


class PageTest(unittest.TestCase):
    """A server of its own, on a free port, serving the real files."""

    @classmethod
    def setUpClass(cls):
        import serve

        cls.serve = serve
        cls.log_was = serve.log
        serve.log = lambda msg: None
        cls.was = (serve.office_sync.Access, serve.office_sync.build_snapshot)
        # Nothing here needs a snapshot, and nothing here may touch GitHub.
        serve.office_sync.Access = lambda: object()
        serve.office_sync.build_snapshot = lambda access: {"generated": "", "stations": []}

        cls.httpd = serve.make_server(serve.World(), 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.serve.office_sync.Access, cls.serve.office_sync.build_snapshot = cls.was
        cls.serve.log = cls.log_was

    def fetch(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"host": f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request("GET", path, headers=h)
        r = c.getresponse()
        body = r.read().decode("utf-8", "replace")
        out = (r.status, dict(r.getheaders()), body)
        c.close()
        return out

    def as_tailnet(self, login="aria"):
        """Put a name in front of the door, the way Tailscale Serve does."""
        was = (set(self.serve.TRUSTED_HOSTS), self.serve.LOGIN)
        self.serve.TRUSTED_HOSTS = set(self.serve.TRUSTED_HOSTS) | {TAILNET}
        self.serve.LOGIN = login
        self.addCleanup(self._restore, was)

    def _restore(self, was):
        self.serve.TRUSTED_HOSTS, self.serve.LOGIN = was

    # ── it is there ─────────────────────────────────────────────────────────
    def test_the_office_is_at_the_root(self):
        code, headers, body = self.fetch("/")
        self.assertEqual(code, 200)
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        self.assertIn("<title>The office</title>", body)

    def test_the_page_carries_the_policy_that_makes_three_files_worth_it(self):
        code, headers, _ = self.fetch("/")
        self.assertEqual(code, 200)
        csp = headers["content-security-policy"]
        for rule in ("default-src 'none'", "script-src 'self'", "style-src 'self'",
                     "connect-src 'self'", "img-src 'self'", "base-uri 'none'",
                     "form-action 'none'"):
            self.assertIn(rule, csp)
        # No 'unsafe-inline' anywhere in it, which is the whole reason the page
        # is three files and not one.
        self.assertNotIn("unsafe", csp)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["referrer-policy"], "no-referrer")

    def test_the_two_files_the_page_asks_for_arrive_as_themselves(self):
        for path, ctype, needle in (
            ("/index.html", "text/html; charset=utf-8", "<title>The office</title>"),
            ("/phone.css", "text/css; charset=utf-8", "--amber: #ffb020"),
            ("/phone.js", "text/javascript; charset=utf-8", "/api/gate"),
        ):
            code, headers, body = self.fetch(path)
            self.assertEqual(code, 200, path)
            self.assertEqual(headers["content-type"], ctype, path)
            self.assertIn(needle, body, path)
            self.assertIn("content-security-policy", headers, path)

    def test_anything_else_is_still_nothing(self):
        """The page is an exact map of names. A file that is not on it, a
        traversal out of it, and a directory it happens to sit next to are all
        the same answer: there is nothing here."""
        for path in ("/nope.js", "/phone", "/phone/index.html", "/index.html.bak",
                     "/../serve.py", "/phone.css/../serve.py"):
            code, headers, body = self.fetch(path)
            self.assertEqual(code, 404, path)
            self.assertEqual(json.loads(body)["error"], self.serve.NO_PAGE, path)
            self.assertNotIn("import", body, path)

    def test_the_favicon_chrome_asks_for_is_an_empty_yes_not_a_console_error(self):
        code, headers, body = self.fetch("/favicon.ico")
        self.assertEqual(code, 204)
        self.assertEqual(body, "")

    # ── the door in front of it ─────────────────────────────────────────────
    def test_the_page_obeys_the_same_door_as_every_api_call(self):
        """A name that is not this door reads nothing, page included: an
        attacker's hostname resolving to 127.0.0.1 must not get the office."""
        code, _, body = self.fetch("/", {"host": "evil.example.com"})
        self.assertEqual(code, 403)
        self.assertEqual(json.loads(body)["error"], "wrong host")

    def test_a_tailnet_request_without_a_login_never_sees_the_page(self):
        self.as_tailnet()
        code, _, body = self.fetch("/", {"host": TAILNET})
        self.assertEqual(code, 403)
        self.assertEqual(json.loads(body)["error"], "not you")
        self.assertNotIn("<title>", body)

    def test_a_tailnet_request_with_the_right_login_gets_the_page(self):
        self.as_tailnet()
        code, headers, body = self.fetch("/", {"host": TAILNET,
                                               "tailscale-user-login": "aria"})
        self.assertEqual(code, 200)
        self.assertIn("<title>The office</title>", body)
        self.assertIn("content-security-policy", headers)

    def test_a_forged_login_on_loopback_is_ignored(self):
        """Loopback is this Mac. The header only means something behind
        Tailscale Serve, and a page that started trusting it here would be a
        page that trusts a header anybody can type."""
        code, _, body = self.fetch("/", {"tailscale-user-login": "not-aria"})
        self.assertEqual(code, 200)
        self.assertIn("<title>The office</title>", body)


class SourceTest(unittest.TestCase):
    """What the three files are allowed to contain.

    The policy above refuses inline script and inline style at run time. These
    check the files never grew any, because the failure mode is not a broken
    page: it is somebody widening the policy to make one work.
    """

    def test_the_html_has_no_code_in_it(self):
        html = HTML.read_text(encoding="utf-8")
        for tag in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S):
            self.assertEqual(tag.strip(), "", "a script tag with a body in it")

    def test_the_html_has_no_inline_style(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertNotIn('style="', html)
        self.assertNotIn("style='", html)
        self.assertNotIn("<style", html)

    def test_nothing_is_loaded_from_anywhere_but_this_door(self):
        html = HTML.read_text(encoding="utf-8")
        for attr, value in re.findall(r"\b(src|href)\s*=\s*[\"']([^\"']*)[\"']", html):
            self.assertTrue(value.startswith("/"), f"{attr}={value}")
            self.assertFalse(value.startswith("//"), f"{attr}={value}")
        for path in (HTML, CSS, JS):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("http://", text, path.name)
            self.assertNotIn("https://", text, path.name)

    def test_fresh_is_a_button_and_never_a_timer(self):
        """Every fresh build asks GitHub, and the hour holds 5000 points. A
        phone polling `?fresh=1` in a pocket would spend them on a screen
        nobody is reading."""
        js = JS.read_text(encoding="utf-8")
        self.assertEqual(js.count("fresh=1"), 1, "one fresh path, and only one")

        before = js[:js.index("fresh=1")]
        self.assertIn("async function pollWorld(nowPlease)", before)
        # It sits behind the caller saying so out loud, inside that function.
        self.assertIn("if (nowPlease)", before[before.rindex("async function pollWorld"):])

        # And nothing on a clock ever says so.
        for at in [m.start() for m in re.finditer(r"setInterval\(", js)]:
            window = js[at:at + 140]
            self.assertNotIn("true", window, window)
            self.assertNotIn("fresh", window, window)

        # The one caller that does is the refresh button's own handler.
        self.assertEqual(js.count("pollWorld(true)"), 1)
        lead = js[:js.index("pollWorld(true)")]
        self.assertIn('getElementById("refresh").addEventListener', lead[-200:])

    def test_the_page_reads_every_raised_hand_and_not_only_the_oldest(self):
        """Two bots can be waiting at once. A page that asks for one of them
        cannot know a second is there, and a hand nobody can see is the single
        failure this surface is not allowed to have."""
        js = JS.read_text(encoding="utf-8")
        self.assertIn('read("/api/gates")', js)

    def test_a_bot_thread_shows_its_cadence(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("bot.frequency", js)

    def test_the_gate_answer_carries_the_id_the_card_drew(self):
        """The sharpest edge in the project, on the smallest screen. Between a
        gate being shown and being tapped the agent can time out and a different
        gate can open, so the answer names the question it is answering and a
        drifted one posts nothing at all."""
        js = JS.read_text(encoding="utf-8")
        at = js.index('write("/api/gate"')
        post = js[at:at + 220]
        # The field name serve.py._gate validates, carrying the id the card drew.
        self.assertIn("question_id", post)
        self.assertIn("drawn", post)
        self.assertIn('answer: verdict', post)
        self.assertIn("always", post)

        moved = js.index("that question moved on")
        self.assertLess(moved, at, "the drift check runs before the write, not after")
        guard = js[js.index("async function answer("):moved]
        self.assertIn("drawn !== live", guard)


if __name__ == "__main__":
    unittest.main()


class PhotoTest(unittest.TestCase):
    """A picture, from a pocket, through the same door.

    The page has no build step and no test runner, so these are grep-level: they
    hold down the four things about this that would be silent if they broke. It
    posts `attachments` rather than some other shape. It offers the camera and
    the library rather than an accept nothing matches. It shrinks before it
    sends, because the door refuses half a megabyte. And it still writes to the
    DOM with textContent, because a page that grows an `innerHTML` is a page
    whose content policy has to be loosened to keep working.
    """

    def js(self):
        return JS.read_text(encoding="utf-8")

    def test_a_photo_rides_the_message_as_attachments(self):
        js = self.js()
        at = js.index("async function sayTo(")
        body = js[at:at + 1400]
        self.assertIn("turn.attachments = [{", body)
        self.assertIn("mime_type", body)
        self.assertIn("data_base64", body)
        # Through the one writer, which is what carries this page's Host and
        # Origin to the door. A photo posted any other way is a photo posted
        # past every check the door makes.
        self.assertIn('write("/api/chat", turn)', body)

    def test_the_picker_takes_a_picture_from_the_library_or_the_camera(self):
        js = self.js()
        self.assertIn('input.accept = "image/*"', js)
        self.assertIn('input.capture = "environment"', js)
        self.assertIn('input.type = "file"', js)
        # Created in script, not sitting in the markup, because it belongs to
        # whichever thread is open.
        self.assertIn('document.createElement("input")', js)

    def test_the_picture_is_shrunk_before_it_is_sent(self):
        js = self.js()
        # The door's ceiling is 512 KB for the whole request; the page aims
        # under that and measures the base64, not the bytes it encodes.
        self.assertIn("const PHOTO_CEILING = 480 * 1024", js)
        self.assertIn("const PHOTO_LONGEST = 1200", js)
        self.assertIn("function base64Length(bytes)", js)
        self.assertIn('canvas.toBlob(', js)
        self.assertIn('"image/jpeg"', js)
        # And it comes down a rung at a time rather than giving up at 0.8.
        self.assertGreaterEqual(js.count("quality:"), 5)

    def test_a_format_the_page_cannot_read_is_said_out_loud(self):
        """Safari hands over the photo library's own HEIC and no browser will
        decode one. A picture that silently did not go is worse than one that
        refused where a person can see it."""
        js = self.js()
        self.assertIn("take the photo with the camera option", js)
        at = js.index("createImageBitmap")
        self.assertIn("catch", js[at:at + 200])
        self.assertIn("CANNOT_READ", js[at:at + 400])

    def test_the_page_still_never_writes_markup(self):
        js = self.js()
        html = HTML.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("outerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        # And no handler written into the markup, which is the other half of the
        # same policy: the page's only script is the file the door serves.
        self.assertNotIn("<input", html)
        for handler in ("onclick", "onchange", "onload", "oninput", "onerror"):
            self.assertNotIn(handler + "=", html)

    def test_a_turn_that_carried_a_photo_is_marked_in_the_thread(self):
        """The bytes are ephemeral by design, so the mark is all there is. It
        comes off the wire when the harness echoes anything, and off this page's
        own record of what it sent when the harness echoes nothing."""
        js = self.js()
        self.assertIn("function carriedPhoto(turn, botId)", js)
        self.assertIn("turn.attachments", js)
        self.assertIn("state.sentPhotos", js)
        self.assertIn('"with a photo"', js)


class AutomationAndSessionsTest(unittest.TestCase):
    """The two bands #42 and #38 added, and the rules they must not break.

    Both are checked in the source rather than in a browser for the same reason
    the rest of this file is: the page has no build step, so its failures are
    not compile errors. They are a band that quietly derives its own version of
    a state the server already measured, a link that points at the wrong
    comment, and a send button over a session nothing will ever read.
    """

    def js(self):
        return JS.read_text(encoding="utf-8")

    def test_the_automation_band_exists_and_sessions_live_inside_their_desk(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('id="automation"', html)
        self.assertNotIn('id="sessions"', html)
        js = self.js()
        self.assertIn('getElementById("automation")', js)
        self.assertIn("sessionsForDesk", js)

    def test_the_automation_band_derives_no_state_of_its_own(self):
        """Every word on it was measured by the server, so that the phone and
        the Mac app say one sentence about one machine. The headline in
        particular is never composed here."""
        js = self.js()
        self.assertIn("page.headline", js)
        self.assertNotIn("state.world.automation.headline =", js)
        # The mechanism explanation comes from the server too, so there is one
        # copy of it rather than one here and one in Swift.
        self.assertNotIn("A launchd job wakes the runner", js)

    def test_a_deep_link_is_only_offered_when_the_office_knows_the_comment(self):
        """A human replying moves the last comment. Linking to it as the
        runner's words would be wrong in the one place a person goes to check
        what the runner said."""
        js = self.js()
        at = js.index("row.comment_url || row.issue_url")
        window = js[at:at + 400]
        self.assertIn('row.comment_url ? "read the comment" : "open the issue"', window)

    def test_the_activity_list_never_hides_what_it_dropped(self):
        js = self.js()
        self.assertIn("page.activity_dropped", js)
        self.assertIn("newest ", js)

    def test_the_reason_nothing_arrives_is_shown_and_not_only_the_silence(self):
        """"Quiet" and "nothing can reach us" read identically from inside a
        quiet room, and the second one lasts for weeks."""
        js = self.js()
        self.assertIn("trig.blocked_by", js)

    def test_a_reply_is_a_message_and_this_page_never_types_at_a_terminal(self):
        js = self.js()
        self.assertIn('write("/api/session/say"', js)
        for typing in ("inject", "term inject", "/api/session/screen"):
            self.assertNotIn(typing, js, "this page reads, it does not type")

    def test_there_is_no_send_button_over_a_session_that_would_not_read_it(self):
        js = self.js()
        at = js.index("function sessionComposer(session)")
        window = js[at:at + 700]
        self.assertIn("if (!session.reachable)", window)
        self.assertLess(window.index("if (!session.reachable)"), window.index('button("send"'),
                        "the guard has to come before the button, not after it")

    def test_a_refused_reply_puts_the_words_back_in_the_box(self):
        """A message that was refused AND vanished is a message a person has to
        retype from memory."""
        js = self.js()
        at = js.index("async function replyToSession")
        window = js[at:at + 900]
        self.assertIn("state.drafts[key] = words;", window)

    def test_a_desk_can_start_either_agent_through_the_office(self):
        """A pocket starts work from inside a desk, never from its roster row."""
        js = self.js()
        at = js.index("async function startDeskSession")
        window = js[at:at + 800]
        self.assertIn('write("/api/session/start", { tool: tool, repo: repo })', window)
        self.assertIn('["claude", "codex"]', js)
        self.assertIn("got.body.error", window)
        desks = js[js.index("function drawDesks()"):js.index("async function startDeskSession")]
        self.assertNotIn("deskLaunchers", desks)
        self.assertNotIn('button(open ? "hide" : "start"', desks)

    def test_not_being_able_to_see_never_draws_as_nothing_running(self):
        """An empty list is a claim that nothing is running. When hcom cannot be
        asked, the office does not get to make that claim."""
        js = self.js()
        at = js.index("function drawDeskSessions(")
        window = js[at:at + 1400]
        self.assertIn('roster.state !== "ok" && roster.state !== "empty"', window)
        self.assertIn("roster.detail", window)

    def test_the_session_poll_is_on_its_own_clock_and_spends_no_github_budget(self):
        js = self.js()
        self.assertIn("SESSION_EVERY_MS", js)
        # It reads the local door only. `?fresh=1` is the one path that costs
        # GitHub points and nothing here may be near it.
        at = js.index("async function pollSessions()")
        window = js[at:at + 500]
        self.assertNotIn("fresh", window)

    def test_the_moods_stay_inside_the_page_s_own_three_words(self):
        """A fourth mood word is a dot with no colour behind it, and it draws as
        nothing at all rather than as an error."""
        js = self.js()
        at = js.index("function sessionMood(session)")
        window = js[at:at + 400]
        css = CSS.read_text(encoding="utf-8")
        for word in re.findall(r'return "([a-z]+)"', window):
            self.assertIn(".m-" + word, css, word)


class DriftAndNoiseTest(unittest.TestCase):
    def test_a_swapped_question_disarms_the_buttons_for_a_beat(self):
        js = (PHONE / "phone.js").read_text()
        # The guard is real only if the redraw on an id change arms a timer the
        # answer path checks; a guard that compares the live id to itself is none.
        self.assertIn("state.gateArmAt = Date.now() + ARM_MS", js)
        self.assertIn("if (Date.now() < (state.gateArmAt || 0))", js)
        self.assertIn("b.disabled = disarmed", js)

    def test_a_door_that_answers_500_is_said_out_loud(self):
        js = (PHONE / "phone.js").read_text()
        self.assertIn("the door is not answering", js)


class MobileDeskTest(unittest.TestCase):
    def js(self):
        return JS.read_text(encoding="utf-8")

    def test_a_desk_row_opens_a_real_desk_panel(self):
        html = HTML.read_text(encoding="utf-8")
        js = self.js()
        self.assertIn('id="desk"', html)
        at = js.index("function deskRow(")
        row = js[at:at + 900]
        self.assertIn('el("button", "row")', row)
        self.assertIn("openDesk(desk.repo)", row)
        self.assertIn("function drawDesk()", js)

    def test_work_contains_the_desk_s_issues_prs_sessions_and_launchers(self):
        js = self.js()
        at = js.index("function drawDeskWork(")
        work = js[at:js.index("function drawDeskContext(", at)]
        for fact in ("desk.issues", "desk.prs", "drawDeskSessions", "deskLaunchers"):
            self.assertIn(fact, work)

    def test_context_lists_and_opens_markdown_through_the_existing_door(self):
        js = self.js()
        self.assertIn('"/api/context?repo=" + encodeURIComponent(repo)', js)
        self.assertIn("read(target)", js)
        self.assertIn("encodeURIComponent(path)", js)
        self.assertIn("context.files", js)
        self.assertIn("markdownView(context.text)", js)

    def test_context_can_refresh_and_document_reads_keep_the_existing_index(self):
        js = self.js()
        self.assertIn('button("refresh"', js)
        self.assertIn("existing.files", js)
        self.assertIn("got.body.files", js)
