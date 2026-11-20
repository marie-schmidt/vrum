"""The sample project of the demo (./demo.sh): Riverside Bike Share, a bike sharing service for a
small city. This file only describes the data; tests/demo_world.py loads it through the real MCP
tools and the board's own calls, as a team and its agents would have made it: each agent with a key
of its own, the people through the board. Everything in it is invented: the product, the
people, the agents and the figures.

Dates are in days from the day the demo is made (negative is in the past), so that some work is
overdue, some is due this week and some is months away whenever the demo is started. An item,
acceptance criterion or note that something else names has a `key`. A text names it with
[[key]], which is written as a reference such as [[story:12]] once the numbers are known.

What a story or a task says about itself (its details, and its acceptance criteria) is in
tests/demo_details.py, by the item's title, so that this file stays a map of the work. A task
with no entry there has no details on purpose, so that the board's empty field shows.
"""

import re
import textwrap
from dataclasses import dataclass

from tests import demo_details

PROJECT = "Riverside Bike Share"
OWNER = "Demo Owner"
OWNER_EMAIL = "demo@example.com"

# A line that begins a list item or a quote, which is never joined to the line before it.
_STARTS_A_BLOCK = re.compile(r"^\s*([-*] |\d+[.)] |>)")


def md(text: str) -> str:
    """Markdown written in the code with its paragraphs and list items wrapped over several lines:
    the lines of one paragraph or item are joined, so that the text a person reads has no stray
    line breaks."""
    lines: list[str] = []
    in_code = False
    joinable = False
    for line in textwrap.dedent(text).strip().splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            lines.append(line)
            joinable = False
        elif in_code or not line.strip():
            lines.append(line)
            joinable = False
        elif joinable and not _STARTS_A_BLOCK.match(line):
            lines[-1] += " " + line.strip()
        else:
            lines.append(line)
            joinable = not line.startswith("#")
    return "\n".join(lines)


@dataclass(frozen=True)
class Item:
    kind: str
    title: str
    body: str = ""
    status: str = "new"
    priority: str = "normal"
    start: int | None = None
    due: int | None = None
    key: str | None = None
    children: tuple["Item", ...] = ()
    # The acceptance criteria placed on this story or task, by title (see demo_details.py).
    accepts: tuple[str, ...] = ()
    # How big it is (xs, s, m, l or xl), by title (see demo_details.py). A story that has tasks has
    # none of its own, and an epic never has.
    size: str | None = None


def task(
    title: str,
    status: str = "new",
    priority: str = "normal",
    due: int | None = None,
    *,
    start: int | None = None,
    key: str | None = None,
    body: str = "",
) -> Item:
    return Item(
        "task",
        title,
        body or demo_details.TASK_DETAILS.get(title, ""),
        status,
        priority,
        start,
        due,
        key,
        (),
        demo_details.ACCEPTS.get(title, ()),
        demo_details.SIZES.get(title),
    )


def story(
    title: str,
    body: str,
    *tasks: Item,
    status: str = "new",
    priority: str = "normal",
    due: int | None = None,
    start: int | None = None,
    key: str | None = None,
) -> Item:
    return Item(
        "story",
        title,
        body,
        status,
        priority,
        start,
        due,
        key,
        tasks,
        demo_details.ACCEPTS.get(title, ()),
        demo_details.SIZES.get(title),
    )


def epic(
    title: str,
    body: str,
    *stories: Item,
    status: str = "new",
    priority: str = "normal",
    due: int | None = None,
    start: int | None = None,
    key: str | None = None,
) -> Item:
    return Item("epic", title, body, status, priority, start, due, key, stories)


@dataclass(frozen=True)
class Link:
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class Criterion:
    """An acceptance criterion with a `key` that something else names. `node` is the key of the
    story or task it is placed on, or empty while it is placed nowhere."""

    key: str
    title: str
    body: str
    node: str | None
    status: str = "open"


@dataclass(frozen=True)
class Note:
    """What is decided or learned. `by` and `ago` say who decided it and how many days ago, and are
    empty for a note nobody decided; the first line of `text` is its headline."""

    key: str
    text: str
    why: str
    by: str | None = None
    ago: int | None = None
    nodes: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    project_wide: bool = False
    replaces: tuple[str, ...] = ()


def decided(
    key: str,
    title: str,
    text: str,
    why: str,
    by: str,
    ago: int,
    nodes: tuple[str, ...] = (),
    rules: tuple[str, ...] = (),
    project_wide: bool = False,
    replaces: tuple[str, ...] = (),
) -> Note:
    """A note somebody decided, with who decided it and how many days ago. What its title said is
    its first line."""
    return Note(key, f"{title}\n\n{text}", why, by, ago, nodes, rules, (), project_wide, replaces)


def learned(
    key: str,
    text: str,
    why: str,
    nodes: tuple[str, ...] = (),
    rules: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    replaces: tuple[str, ...] = (),
) -> Note:
    """A note nobody decided: something learned, with where it came from."""
    return Note(key, text, why, None, None, nodes, rules, notes, False, replaces)


@dataclass(frozen=True)
class Confirmation:
    key: str
    person: str
    quote: str = ""
    confirmed: bool = True


@dataclass(frozen=True)
class Withdrawal:
    key: str
    reason: str


@dataclass(frozen=True)
class Handoff:
    key: str
    actor: str
    outcome: str
    note: str


@dataclass(frozen=True)
class Held:
    key: str
    actor: str


@dataclass(frozen=True)
class Edit:
    key: str
    actor: str
    title: str | None = None
    body: str | None = None
    priority: str | None = None
    start: int | None = None
    due: int | None = None
    clear_due: bool = False


@dataclass(frozen=True)
class RuleEdit:
    key: str
    actor: str
    body: str | None = None
    status: str | None = None


# -- who does what -----------------------------------------------------------------------------

# The planner has an account of her own, as an editor of the project (she joins through an
# invitation the owner makes), and so does a viewer, who can look at everything and change nothing.
# They sign in with the owner's password. What the planner does is done through the board as her.
PLANNER = "Priya Nair"
PLANNER_EMAIL = "priya@example.com"
VIEWER = "Tom Berg"
VIEWER_EMAIL = "tom@example.com"
RECORDER = "planning-agent"

# Signing in with another provider is shown with the demo's stand-in providers (the scripted OpenID
# provider and GitHub, and the real directory with fictional people), which all know three people
# by the same names and addresses: the planner (the demo has an account for her address, which is
# confirmed, so signing in joins it to that account), the viewer (the demo has an account for his
# address that was never confirmed, so signing in is refused until he confirms it) and a new
# person (nobody has an account for the address, so signing in makes one, with no password). The
# addresses are the ones of tests/scripted_oidc.py, tests/scripted_github.py and
# tests/providers/ldap/people.ldif, which tests/test_demo.py keeps equal.
STAND_IN_CONFIRMED_EMAIL = PLANNER_EMAIL
STAND_IN_UNCONFIRMED_EMAIL = VIEWER_EMAIL
STAND_IN_NEW = "Noor Haddad"
STAND_IN_NEW_EMAIL = "noor@example.com"

# A name in this file that ends with "-agent" is an agent, which has a key of its own in the demo
# and is who the history says did what it did. Every other name is a person of the team: the owner,
# the planner and the viewer have accounts, and what any other person does is done through the
# board as the owner, who signs in for them. The names stay in the texts, in the "decided by" of a
# note and in the reviewer of a confirmation, which name people who may have no account.
AGENT_SUFFIX = "-agent"

# The agent keys that are still in force when the loading ends (every other agent's key is revoked
# then), each with what it may do, so that the Users page shows the choices: api-agent may use
# every tool, review-agent only the tools that read, and docs-agent also the tools that change
# something in these areas of the Tools page, but nothing in the others (no moving data, no index).
# Nobody holds their secrets: they are there to be looked at.
KEPT_KEYS = ("api-agent", "review-agent", "docs-agent")
DOCS_AGENT_AREAS = (
    "Work",
    "Links",
    "Claims and the working loop",
    "Acceptance criteria",
    "Notes",
)
# Notes a person records themselves, from what they read or measured, instead of the planning agent
# recording them for them. Every other note is recorded by the planning agent. (A note the planner
# decided is her own.)
NOTES_BY_PERSON = frozenset({"f-inbox", "f-city-24", "f-dpia", "f-swap"})

# Who moves the work of each epic along: the actor of its status changes.
WORKERS = {
    "platform": "api-agent",
    "unlock": "mobile-agent",
    "rides": "mobile-agent",
    "payments": "api-agent",
    "stations": "mobile-agent",
    "fleet": "ops-agent",
    "safety": "ops-agent",
    "support": "Kofi Mensah",
    "city": "Ines Duarte",
    "notifications": "mobile-agent",
    "rebalance": "ops-agent",
    "adaptive": "Priya Nair",
}

# -- the plan ----------------------------------------------------------------------------------

PLATFORM = epic(
    "Platform foundations",
    md("""
        ## Why this exists

        Everything else in Riverside Bike Share stands on a few shared foundations: who a rider
        is, how the apps talk to the servers, and how rider data is kept safe. Getting these
        right once is far cheaper than repairing them in every feature.

        ## In scope

        - Rider accounts, sign-in and account deletion ([[accounts]])
        - Privacy rules and how long rider data is kept ([[privacy]])
        - The API that the apps and partners use ([[api]])
        - The release pipeline, monitoring and on-call
        - A small design system shared by the rider app and the operator dashboard

        ## Ground rules

        Nine project-wide notes apply to all of this and to every other epic. Read them
        before changing anything shared. A call to the API looks like this:

        ```http
        GET /v1/stations?near=52.52,13.40&radius=500 HTTP/1.1
        Accept: application/json
        ```

        ## Not in scope

        Taking payments has its own epic: [[payments]].
    """),
    story(
        "Rider accounts and sign-in",
        md("""
            Riders sign up with an email address and a password, or with a one-time code sent to
            their inbox. Sign-in has to survive a bad connection: a rider at a station with one
            bar of signal should still get in. What we may keep about an account is set out in
            [[privacy]].
        """),
        task("Sign-up form with email and password", "done", "high", -80),
        task("Email verification link", "done", "normal", -74),
        task("One-time sign-in code by email", "done", "normal", -66),
        task("Password reset by email", "done", "normal", -60),
        task(
            "Lock the account for 15 minutes after 10 failed sign-ins",
            "in_progress",
            "normal",
            -3,
            key="lockout",
        ),
        task("Sign in with Apple and Google", due=21, key="social-signin"),
        task(
            "Delete my account and everything attached to it",
            "new",
            "high",
            14,
            key="delete-account",
        ),
        status="in_progress",
        priority="high",
        due=21,
        key="accounts",
    ),
    story(
        "Privacy and data retention",
        md("""
            The city's data protection officer will not sign off the launch until we can show
            what we store about a rider and for how long. The rules are in [[d-location]] and
            [[d-nosdk]]. Every task here should end with something a person from the city can
            read, not only with code.
        """),
        task(
            "Write the data map: every field we store, where and why",
            "done",
            "urgent",
            -40,
            key="datamap",
        ),
        task(
            "Trim ride routes to start and end 30 days after a ride",
            "in_progress",
            "high",
            -2,
            key="trim-routes",
        ),
        task("Export my data as a file", due=30, key="export-data"),
        task("Data protection impact assessment", "blocked", "urgent", -25, key="dpia"),
        task("Cookie-free analytics for the website", priority="low"),
        status="in_progress",
        priority="high",
        due=28,
        key="privacy",
    ),
    story(
        "API conventions and versioning",
        md("""
            One style for every endpoint, so that the rider app, the operator dashboard and the
            partners learn it once: nouns in the path, a version prefix, and errors that carry a
            stable code. The rule that fixes the version prefix is [[d-api]].
        """),
        task("Write the API style guide", "done", "normal", -55),
        task("Version prefix on every route", "done", "normal", -48),
        task("Errors with a stable code and a plain message", "done", "normal", -42),
        task("Pagination with opaque cursors", "done", "normal", -38),
        task("Rate limits per client", "dropped", "low", -30, key="rate-limit"),
        task("Use the gateway's rate limits", "done", "normal", -28, key="gateway-limits"),
        status="done",
        due=-28,
        key="api",
    ),
    story(
        "Audit log of staff actions",
        md("""
            Every change a staff member makes to a bike, a station or a rider's account is
            written to a log that cannot be edited, and the city can ask to see it. Each line says
            who made the change, what it was before and after, and when. Nothing here is built
            yet, because the log has to wait for the staff roles to be settled.
        """),
        task("Write a log line for every staff change", due=38, key="log-lines"),
        task("Log screen in the operator dashboard", due=45, key="log-screen"),
        task("Keep the log for seven years", priority="low"),
        due=45,
    ),
    story(
        "Environments and release pipeline",
        md("""
            Every change reaches riders the same way: built, tested, tried on a staging copy with
            fake bikes, then released without taking the service down. A release can be undone
            with one action if something looks wrong. The aim is that nobody is afraid of a Friday
            afternoon deploy.
        """),
        task("Build and test on every push", "done", "normal", -85),
        task("Staging environment with fake bikes", "done", "normal", -70),
        task(
            "Deploy to production without downtime", "in_progress", "high", 5, key="zero-downtime"
        ),
        task("Check database changes before they run", due=10, key="db-check"),
        task("One-click rollback", "new", "high", 12, key="rollback"),
        task("Release notes written from merged changes", priority="low"),
        status="in_progress",
        priority="high",
        due=12,
        key="pipeline",
    ),
    story(
        "Monitoring and on-call alerts",
        md("""
            We find out that unlocks are failing before riders tell us. This story is the
            dashboard, the alerts and the rota of who answers them.
        """),
        task("Dashboard for ride starts and failures", due=20, key="ride-dash"),
        task("Page the on-call engineer when unlocks fail", "new", "high", 30, key="page-oncall"),
        task("Weekly on-call rota", due=26),
        task("Write the first runbook", priority="low", due=35),
        priority="high",
        due=35,
    ),
    story(
        "Feature flags",
        "Turn a feature on for one station or one group of riders before everyone gets it. Not "
        "planned in detail yet: wait for the first pilot feedback.",
    ),
    story(
        "Backups and a restore drill",
        md("""
            A backup that has never been restored is a hope, not a backup. Every month someone
            restores last night's copy into a scratch database and checks that the rides are
            there.
        """),
        task("Nightly backup of the database", "done", "high", -45),
        task("Monthly restore drill with a written report", due=40),
        due=40,
    ),
    story(
        "Design system and shared components",
        md("""
            Colours, type, buttons and icons that the rider app and the operator dashboard share.
            Every screen has to meet the accessibility rule in [[d-wcag]].
        """),
        task("Colour and type tokens for light and dark", "done", "normal", -22),
        task("Button, input and list components", "in_progress", "normal", 4),
        task("Icon set for bikes, stations and warnings", due=18),
        status="in_progress",
        due=18,
        key="design-system",
    ),
    status="in_progress",
    priority="high",
    key="platform",
)

UNLOCK = epic(
    "Find and unlock a bike",
    md("""
        ## What riders see

        A rider walks up to a bike, scans the code on the frame and the lock opens. Nothing else:
        no menu, no map and no payment screen in between. If anything goes wrong the app says
        what happened and offers the nearest other bike.

        ## How an unlock works

        1. The phone reads the bike number from the QR code ([[bike-numbers]] explains how the
           numbers are painted).
        2. The app asks the server for a one-time unlock token ([[token]]).
        3. The server checks the rider, the bike and the price rules, then tells the lock to
           open.

        ```json
        {
          "bike": "RB-0417",
          "rider": "rdr_2f81",
          "token": "one-time, valid for 30 seconds"
        }
        ```

        ## Done when

        - A scan opens the lock within two seconds on a good connection ([[two-seconds]])
        - Every failure has a reason a rider can read ([[show-reason]])
        - A rider with no signal can still unlock a bike ([[offline-unlock]])
    """),
    story(
        "Scan a QR code to unlock",
        md("""
            The main way to start a ride. The code on the frame holds the bike number and nothing
            else, so a photo of it is useless to anyone who is not standing next to the bike with
            an account. The unlock token is described in [[d-token]].
        """),
        task("Print QR codes on every bike", "done", "normal", -70, key="print-qr"),
        task("Camera permission screen", "done", "normal", -66),
        task("Scan screen with a torch button", "done", "normal", -60),
        task("Signed one-time unlock token", "done", "high", -58, key="token"),
        task("Unlock request endpoint", "done", "normal", -52, key="unlock-endpoint"),
        task(
            "Lock opens within two seconds",
            "in_progress",
            "high",
            -10,
            key="two-seconds",
            body=md("""
                The server answers in about 400 ms. The rest is the lock: measured on the test
                bench it takes 1.1 s, in the field up to 3 s.
            """),
        ),
        task("Show a clear reason when the unlock fails", "new", "high", 6, key="show-reason"),
        task("Retry once on a weak connection", due=8, start=-6, key="retry-weak"),
        task("Bike number fallback when the camera is dark", due=9),
        task("Vibrate and play a sound when the lock opens", priority="low", due=12),
        task("Try the unlock on five older phones", due=14, key="old-phones"),
        task("Write the unlock help text", priority="low"),
        status="in_progress",
        priority="high",
        due=10,
        key="qr-unlock",
    ),
    story(
        "Unlock by typing the bike number",
        "For a rider whose camera is broken or whose phone is too old for the scan screen: type "
        "the number painted on the frame. The app checks the number against the bikes that are "
        "really nearby, so a typo cannot unlock a bike across town. It is the same unlock as the "
        "scan, only with a different way in.",
        task("Number pad screen", "done", "normal", -30),
        task("Check the number against bikes nearby", "done", "normal", -25, key="nearby-check"),
        status="done",
        due=-20,
        key="type-number",
    ),
    story(
        "Reserve a bike for ten minutes",
        "A rider can reserve a bike before walking to it, so that nobody else can unlock it for "
        "ten minutes. The reservation counts down on the map and ends by itself. A rider who "
        "needs more time has to reserve again, so that no bike is held for long.",
        task("Reserve endpoint with a ten minute timer", due=30, key="reserve-api"),
        task("Countdown on the map and a reminder at two minutes", due=34),
        task("Release the bike when the timer ends", due=36),
        task("Allow only one reservation per rider", due=36),
        task("Show reserved bikes as unavailable to everyone else", due=38),
        due=40,
        key="reserve",
    ),
    story(
        "Unlock a second bike for a friend",
        "Two people ride together and one account pays for both. The second rider does not need "
        "an account, only a phone number to receive the unlock. Not planned in detail yet: it "
        "waits until the single unlock is solid.",
        task("Invite a friend by phone number", priority="low"),
        task("Pay for two bikes on one account", priority="low"),
        task("Show both bikes on the live ride screen", priority="low"),
        priority="low",
        due=70,
        key="group-ride",
    ),
    story(
        "Recover from a failed unlock",
        md("""
            When the lock does not open the rider is standing in the street, so the app has to
            help straight away: say what went wrong, offer the nearest other bike and never
            charge for a ride that did not start. Operations also hear about the broken lock, so
            that the next rider does not find it. The story is blocked until the lock vendor tells
            us how to read a lock that is stuck.
        """),
        task("Detect a lock that did not open", "done", "normal", -12, key="detect-fail"),
        task("Offer the nearest other bike", "in_progress", "high", -3, key="nearest-other"),
        task("Report the broken lock to operations", "blocked", "normal", -6, key="report-lock"),
        task("Refund the unlock fee automatically", due=48, key="auto-refund"),
        status="blocked",
        priority="high",
        due=-4,
        key="failed-unlock",
    ),
    story(
        "Let a rider who loses mobile signal at the station still unlock a bike over Bluetooth, "
        "and keep the ride timer honest until the phone is online again",
        md("""
            Some stations are in places with no signal at all, such as the underground car park
            ([[f-signal]]). The lock vendor says Bluetooth works from a few metres ([[f-ble]]).
            Nothing here is designed yet: the first task is a prototype to find out what is
            possible ([[d-offline]]).
        """),
        task("Prototype Bluetooth unlock with the lock vendor's kit", due=60, key="ble-proto"),
        task("Decide how the lock proves the rider may open it", due=66),
        task("Keep the ride timer honest while offline", due=75),
        task("Test in the car park on Mill Street", priority="low", due=85),
        due=85,
        key="offline-unlock",
    ),
    status="in_progress",
    priority="high",
    start=-90,
    due=20,
    key="unlock",
)

RIDES = epic(
    "Ride tracking and ending a ride",
    md("""
        ## Goal

        From the moment the lock opens to the moment it closes on a dock, the rider sees one calm
        screen: the time, the price so far and a button to end the ride. Everything the server
        needs to settle the ride is collected quietly in the background.

        ## Questions we still have

        - What do we do with a bike left outside a station? ([[end-ride]])
        - How exact must the route be? The privacy rules say less is better: [[d-location]].
        - What happens to a ride when the phone dies? That is handled with the offline work in
          [[offline-unlock]].

        ## What a finished ride looks like

        ```text
        Ride 18 May, 08:12 to 08:31 (19 min)
        From Station 12 Old Mill to Station 31 Mill Street
        Price 1.40 for 14 paid minutes, 5 minutes free
        ```
    """),
    story(
        "Ride timer and live ride screen",
        "A big clock, the price so far and one button. Nothing else on the screen while the "
        "bike is moving.",
        task("Count up the ride time", "done", "normal", -30),
        task("Show the price so far", "done", "normal", -28),
        task("Keep the screen awake during a ride", "done", "low", -26),
        status="done",
        due=-26,
        key="ride-timer",
    ),
    story(
        "End a ride at a station",
        md("""
            A ride ends when the lock is closed on a dock. This is where most pilot problems
            happened: full stations, docks that did not report and riders who walked away from a
            bike that was not locked. See [[d-bike-id]] for how the bike is named in the ride.
        """),
        task("Detect that the bike is docked", "done", "normal", -20, key="dock-detect"),
        task("Confirm the end of the ride on the phone", "done", "high", -16),
        task(
            "Handle a bike returned to a full station",
            "in_progress",
            "high",
            -1,
            key="full-station",
        ),
        task("Allow ending a ride outside a station for a fee", due=12, key="outside-fee"),
        task("Lock the bike remotely after the rider walks away", due=14, key="remote-lock"),
        task("Take a photo of the parked bike", priority="low", due=30),
        status="in_progress",
        priority="high",
        due=-2,
        key="end-ride",
    ),
    story(
        "Record the route of a ride",
        "The route is kept for disputes and for the city's reports, and it is trimmed after "
        "30 days as decided in [[d-location]]. A route is only as good as its points, so points "
        "with poor accuracy are dropped and the rest is simplified when the ride ends. A rider "
        "can see their own route on the receipt.",
        task("Location point every ten seconds", "done", "normal", -30),
        task("Drop points with poor accuracy", "in_progress", "normal", 4, key="drop-points"),
        task("Simplify the route when the ride ends", due=16, key="route-simplify"),
        task("Show the route on the receipt", priority="low", due=40, key="route-receipt"),
        status="in_progress",
        due=25,
        key="route-trace",
    ),
    story(
        "Ride history and receipts",
        "A list of past rides, each with its price, time and route, and a way to take them "
        "all out as a file. The newest ride is at the top, and a ride that is still open is not "
        "in the list yet.",
        task("List of past rides", due=46),
        task("Ride details with the route", due=48),
        task("Download all my rides", priority="low", due=50),
        due=50,
        key="ride-history",
    ),
    story(
        "Tunnels",
        "Riding through a tunnel must not end the ride or lose a minute of it. The phone keeps "
        "the ride events and sends them when the signal returns. It is the same idea as the "
        "offline unlock, but for the ride itself.",
        task("Queue ride events on the phone", priority="low"),
        task("Send queued events when the signal returns", priority="low"),
        priority="low",
        due=70,
        key="tunnels",
    ),
    status="in_progress",
    key="rides",
)

PAYMENTS = epic(
    "Payments, passes and receipts",
    md("""
        ## Prices at launch

        ```text
        Single ride    price per minute, the first 5 minutes free
        Monthly pass   the first 45 minutes of every ride included
        Annual pass    the same, paid once a year
        ```

        ## Principles

        - A rider sees the price **before** the ride starts, never after.
        - Card numbers never touch our servers ([[d-hosted]]).
        - Money is whole cents everywhere ([[d-money]]).
        - A failed charge is retried quietly before we ask the rider for anything.

        ## Open questions

        - Student and low income discounts need a way to prove who qualifies ([[student]]).
        - Prices that differ by city zone wait for the city's answer ([[city-zones]]).

        Refunds are part of this epic ([[refunds]]), and so are the emailed receipts
        ([[receipt-email]]).
    """),
    story(
        "Pay for a ride by card",
        md("""
            The rider adds a card once on the payment provider's own page and is charged when the
            ride ends. Where card details are entered is explained in [[d-hosted]]; what the
            provider does with the money is in [[f-payout]].
        """),
        task("Choose and sign up with the payment provider", "done", "high", -90),
        task("Hosted card entry page", "done", "high", -62, key="hosted-page"),
        task("Charge the rider when the ride ends", "in_progress", "high", 2, key="charge-end"),
        task("Retry a failed charge for three days", due=9, key="retry-charges"),
        task("3-D Secure challenge in the app", "blocked", "high", 6, key="three-ds"),
        task("Show the price before the ride starts", "new", "high", 7, key="price-before"),
        task("Say clearly when a card is expired or stolen", due=10),
        status="in_progress",
        priority="urgent",
        due=20,
        key="card-pay",
    ),
    story(
        "Monthly and annual passes",
        "A pass covers the first 45 minutes of every ride. Whether to start with a weekly pass "
        "was decided in [[d-passes]], and the survey behind it is [[f-survey]].",
        task("Pass plans and prices", "new", "high", 28, key="pass-plans"),
        task("Buy a pass in the app", due=34, key="pass-checkout"),
        task("Renew a pass on the same day each month", due=38),
        task("Show the free minutes left this month", due=40),
        task("Pause a pass for up to 30 days", priority="low", due=45),
        due=40,
        key="passes",
    ),
    story(
        "Refunds and credits",
        "Giving money back has to be easy for support and impossible to do twice. A refund "
        "always goes back to the card that paid. Credits are for goodwill and expire, so that "
        "they do not pile up on the books.",
        task("Refund a single ride from the operator dashboard", key="refund-single"),
        task("Refunds go back to the original card", due=36, key="refund-api"),
        task("Credits that expire after one year", priority="low", due=50, key="credits"),
        due=48,
        key="refunds",
    ),
    story(
        "Email receipts",
        "When a ride ends the rider gets one email with the price, the route and how long it "
        "lasted. It is written in the language the rider chose in the app. The email is the "
        "rider's proof of what was charged, so it must be sent for every ride, even a very "
        "short one.",
        task("Send the receipt email when the ride ends", due=24, key="receipt-send"),
        task("Receipt layout in the three launch languages", due=26),
        task("Resend a receipt from the operator dashboard", priority="low", due=30),
        due=30,
        key="receipt-email",
    ),
    story(
        "Student and low income discount",
        "Cheaper rides for people who qualify. Not planned yet: we first need to learn how the "
        "city wants qualification to be proven.",
        priority="normal",
        key="student",
    ),
    story(
        "Prices, free minutes and daily caps",
        md("""
            The rules that turn minutes into money. The pilot used a flat unlock fee
            ([[d-fee-old]]); the launch prices are in [[d-fee-new]].
        """),
        task("Price table by ride length", "in_progress", "high", -14, key="price-table"),
        task("Five free minutes on every single ride", "done", "normal", -20),
        task("Daily cap on what a rider pays", due=3),
        task("Different prices in different city zones", "blocked", "normal", -5, key="city-zones"),
        status="in_progress",
        priority="high",
        due=-20,
        key="pricing",
    ),
    status="in_progress",
    priority="high",
    start=-60,
    due=55,
    key="payments",
)

STATIONS = epic(
    "Stations and live availability",
    md("""
        ## What this is

        One screen that answers the rider's first question: where is the nearest bike, and will it
        still be there when I arrive?

        ## What we know

        - The city has a growing network of bike lanes ([[f-lanes]]).
        - The pilot fleet is 600 bikes across 48 stations ([[f-fleet]]).
        - Demand peaks twice a day ([[f-peak]]), so the numbers must be fresh.

        ## Ideas parked for later

        - Heat map of where riders start
        - A suggestion to ride to a nearby station that has free docks
    """),
    story(
        "Map of stations with live bike counts",
        md("""
            Every station on the map shows how many bikes can be taken and how many docks are
            free, no more than one minute old. A station that has not reported for a while is
            shown as unknown instead of empty. The acceptance criteria are on this story and on
            its tasks.
        """),
        task("Map screen with clustering", "done", "high", -50),
        task("Station marker with bikes and free docks", "done", "normal", -44),
        task("Refresh the counts every 30 seconds", "in_progress", "normal", 3, key="refresh-30s"),
        task("Show reserved and broken bikes differently", due=14),
        task("Dark map style", priority="low", due=30),
        task("Keep the map smooth with 200 stations", due=20),
        status="in_progress",
        priority="high",
        due=6,
        key="map",
    ),
    story(
        "Hold a bike for a rider before they arrive",
        "Let a rider reserve a bike for ten minutes before walking to the station, so that "
        "nobody else can unlock it. It starts from the station's page instead of from the bike. "
        "It looks very close to the reservation story in the unlock epic, and one of the two "
        "should go once someone has compared them.",
        priority="low",
        due=45,
        key="hold-bike",
    ),
    story(
        "Migrate LegacyStationTelemetryIngestionServiceConfiguration settings",
        md("""
            The old service that reads the stations' numbers has about forty settings. They move
            to the shared configuration service and the old service is deleted.
        """),
        task("Inventory every setting the old service reads", "done", "normal", -20),
        task("Move the settings to the new configuration service", "in_progress", "normal", -20),
        task("Delete the old configuration after one clean week", due=10),
        status="in_progress",
        due=-20,
        key="station-telemetry",
    ),
    story(
        "Search for a station or an address",
        "A search box on the map that understands street names and the numbers painted on "
        "stations. It suggests while the rider types and remembers the last few searches. A "
        "rider who types a street gets the stations nearest to that street, not the ones "
        "nearest to the rider.",
        task("Search box with suggestions", due=26),
        task("Search by street address", due=28),
        task("Remember recent searches", priority="low", due=34),
        due=28,
        key="find-near",
    ),
    story(
        "Tell riders where the next free dock or bike is",
        "When a station is full or empty the app points to the nearest one that is not. The "
        "distance is the walking distance, not the distance as the crow flies. It matters most "
        "at the morning and evening peaks, when the stations by the railway station fill and "
        "empty within minutes.",
        task("Nearest free dock when a station is full", "new", "high", 20),
        task("Nearest bike when a station is empty", "new", "high", 22),
        priority="high",
        due=22,
    ),
    status="in_progress",
    due=40,
    key="stations",
)

FLEET = epic(
    "Fleet operations and maintenance",
    md("""
        ## The people

        Eight technicians and two van drivers keep 600 bikes on the road. They work from a phone
        and a tablet, never from a laptop.

        ## What they need

        - A checklist for every inspection, with a photo as proof ([[inspect]])
        - A way to lock out a damaged bike immediately ([[damage]])
        - Batteries swapped before they run flat ([[battery]])
        - Every repair written down against the bike's own number ([[bike-numbers]])

        ## Inspection checklist (as the technician sees it)

        ```yaml
        bike: RB-0417
        checks:
          - brakes: front and rear
          - tyres: pressure and tread
          - lock: opens and closes
          - lights: both work
        ```
    """),
    story(
        "Inspection checklist on the technician's phone",
        "Every bike is inspected on a schedule. The checklist is the same for every bike and "
        "every inspection ends with a photo.",
        task("Checklist screen", "done", "normal", -40, key="checklist-screen"),
        task("Photo as proof", "done", "normal", -36),
        task("Sign-off with the technician's name", "done", "normal", -30, key="sign-off"),
        task("History of inspections for each bike", "done", "low", -24),
        status="done",
        due=-24,
        key="inspect",
    ),
    story(
        "Report and lock out a damaged bike",
        md("""
            Two reports from riders lock a bike out until a technician has looked at it
            ([[d-lockout]]). Locking out must be immediate, and a locked-out bike never shows up
            on the map.
        """),
        task("Rider can report damage in two taps", "done", "normal", -8),
        task("Lock out a bike after two reports", "in_progress", "high", 3, key="bike-lockout"),
        task("Damage report endpoint for operators", "in_progress", "high", 4, key="ops-api"),
        task("Tell the nearest technician", due=5),
        task("Tag a locked-out bike with its fault", due=8),
        task("Unlock a bike again after the repair", due=10),
        status="in_progress",
        priority="urgent",
        due=3,
        key="damage",
    ),
    story(
        "Battery swap schedule",
        "Electric bikes need a fresh battery before they run flat. The time a swap takes is in "
        "[[f-swap]].",
        task("Plan the swaps for the day", due=34),
        task("Alert at 20 percent charge", due=35),
        task("Route for the battery van", priority="low", due=40),
        due=35,
        key="battery",
    ),
    story(
        "Spare parts stock",
        "",
        task("List of parts and where they are", priority="low"),
        task("Reorder level for each part", priority="low"),
        priority="low",
    ),
    story(
        "Workshop jobs and repair history for each bike",
        "Every repair is written against the bike's own number, so that the next technician "
        "sees what has been done. A job card records the fault, the time spent and the parts "
        "used. The history shows on the bike's page and on the technician's tablet.",
        task("Job card for each repair", "done", "normal", -12),
        task("Repair history on the bike's page", "in_progress", "normal", 5),
        task("Time spent on each job", due=12),
        task("Parts used on each job", due=17),
        task("Workshop view on a tablet", due=20),
        status="in_progress",
        due=18,
        key="workshop",
    ),
    story(
        "Tyre wear predictions",
        "Predict from the distance ridden when a tyre needs replacing ([[f-tyres]]). Dropped: "
        "the technicians' own inspections are good enough for now.",
        task("Wear model from the distance ridden", "dropped", "low", -15, key="tyre-model"),
        task("Alert when a tyre is due", "dropped", "low", -10),
        status="dropped",
        priority="low",
        key="tyres",
    ),
    story(
        "Bike numbers painted on the frame",
        "Every bike carries its number on the frame in large digits, the same number that is in "
        "the system ([[d-bike-id]]). The paint wears off, so every bike is checked each season. "
        "A bike with a faded number is taken in for repainting before it is ridden again.",
        task("Order paint stencils", "done", "normal", -14),
        task("Check that every bike has a readable number", "in_progress", "normal", 6),
        due=12,
        key="bike-numbers",
    ),
    status="in_progress",
    priority="high",
    key="fleet",
)

SAFETY = epic(
    "Safety, incidents and insurance",
    md("""
        ## Our promise

        > Nobody should be left alone after a fall, and nobody should have to chase us for
        > the paperwork.

        ## What this covers

        - A crash or injury can be reported from the app in under a minute ([[crash]])
        - The city hears about every injury within 24 hours ([[d-24h]], [[f-city-24]])
        - Riders accept the insurance terms before their first ride ([[insurance]])
        - Helmets are encouraged, never required by the app ([[helmets]])

        ## What the operations team does

        1. Calls the rider the next day.
        2. Checks the bike and locks it out if it is damaged ([[damage]]).
        3. Files the report with the city and with the insurer.
    """),
    story(
        "Report a crash or injury in the app",
        "A short form that can be filled in by someone who is shaken: what happened, where, "
        "whether anyone is hurt and whether help is needed. The form never asks for anything "
        "that can wait until the next day. It shows the emergency numbers first and sends the "
        "report to operations at once.",
        task("Crash report form in the app", "in_progress", "high", 6, key="crash-form"),
        task("Send the city a report within 24 hours", "new", "urgent", 12, key="report-city"),
        task("Show first aid and emergency numbers", "done", "normal", -20),
        task("Attach photos to a report", due=14),
        task("Call the rider the next day", priority="low", due=16),
        status="in_progress",
        priority="urgent",
        due=12,
        key="crash",
    ),
    story(
        "Helmet reminders and partner discount",
        "A friendly reminder before the first ride and a discount code from a local shop. The "
        "reminder is shown once and never nags. Helmets are encouraged, never required, so the "
        "app lets the rider carry on either way.",
        task("Helmet partner order form", priority="low", due=70),
        task("Show a helmet reminder before the first ride", priority="low", due=75),
        task("Discount code at checkout", priority="low", due=80),
        priority="low",
        due=80,
        key="helmets",
    ),
    story(
        "Insurance terms riders must accept",
        "The insurer covers a ride only if the rider has accepted its terms in the app. The "
        "terms come from the insurer as text, and the acceptance is stored with the date and "
        "the version. A rider who has not accepted cannot unlock a bike.",
        task("Insurance terms text from the insurer", "new", "high", -4, key="insurance-text"),
        task(
            "Rider accepts the terms before the first ride", "new", "high", 18, key="accept-terms"
        ),
        priority="high",
        due=20,
        key="insurance",
    ),
    story(
        "Lights check before an evening ride",
        "Remind riders to check the lights after dark, and let them report a broken one. The "
        "reminder only appears when the sun has set where the rider is. A broken light is "
        "treated like any other damage report, so the bike can be locked out.",
    ),
    status="in_progress",
    priority="urgent",
    due=30,
    key="safety",
)

SUPPORT = epic(
    "Rider support",
    md("""
        ## How riders reach us

        1. The help centre inside the app answers the common questions.
        2. A message form sends the rest to a shared inbox.
        3. A chat is possible later, once the inbox is under control.

        ## The promise

        The rule for answering is [[d-support]]. The volume during the pilot was about 90 messages
        a week ([[f-inbox]]), and most of them were about three things: a lock that did not
        open, a charge that looked wrong, and a bike that was reported damaged.

        ## What the team needs

        - One inbox that shows who is answering what
        - Tags, so that the weekly report can count the three common causes
        - Answers that riders can find without writing at all ([[help-centre]])
    """),
    story(
        "Help centre inside the app",
        "Short articles that answer the questions riders actually asked during the pilot. They "
        "open on the problem the rider just had, for example from the screen that says an unlock "
        "failed. A rider should never need to write to us to learn how to end a ride.",
        task("Write the first twenty articles", due=36),
        task("Search inside the help centre", due=38),
        task("Show help from the failed unlock screen", "new", "high", 40),
        task("Mark every article with the day it was last checked", priority="low", due=44),
        due=40,
        key="help-centre",
    ),
    story(
        "Support inbox for the team",
        "One shared inbox where every message has an owner, a tag and a clock. Nobody has to ask "
        "who is answering a message, because the inbox says so. A message that has waited too "
        "long turns up in the weekly report of missed promises.",
        task("Shared inbox for support", "done", "normal", -20),
        task("Tags for unlock, charge, bike and other", "in_progress", "normal", 4),
        task("Reply templates", due=8),
        task("Weekly report of missed promises", due=12),
        status="in_progress",
        due=9,
        key="inbox",
    ),
    story(
        "Chat with support",
        "",
        priority="low",
        due=120,
    ),
    key="support",
)

CITY = epic(
    "City partnership and reporting",
    md("""
        ## Why the city cares

        The city lends us the street space and pays for part of the fleet, and in return it
        gets numbers it can use: how many rides, where, and who is not being served.

        ## Rules of the partnership

        - Reports are monthly and use the city's own station names
        - Trip data given to the city is anonymous ([[d-location]])
        - Injuries are reported within 24 hours ([[d-24h]])

        ## Contacts

        Ines Duarte is our liaison. She decides what the city may see and in what form.
    """),
    story(
        "Monthly ridership report for the city",
        "A report the city can open without help: rides by station and by hour, and a map of "
        "where bikes were not available. It is made once a month and uses the city's own "
        "station names. The city checks the numbers against its own counts before the first "
        "report is trusted.",
        task("Count rides by station and hour", due=30),
        task("Report layout that the city asked for", due=36),
        task("Send the report on the first of each month", due=48),
        task("Check the numbers with the city once", due=50),
        priority="high",
        due=50,
        key="monthly-report",
    ),
    story(
        "Open data feed of anonymous trips",
        "A daily file of trips with no rider, no bike and no exact times, for anyone to use. "
        "Start and end are rounded to the hour, and very short trips are left out because they "
        "are usually a mistake. The file is published at a fixed address so that other "
        "people can build on it.",
        task("Define what an anonymous trip record holds", due=90),
        task("Publish a daily file", due=100),
        task("Leave out trips shorter than 60 seconds", priority="low", due=100),
        due=100,
        key="open-data",
    ),
    story(
        "Station signs and permits",
        "Every station needs a permit from the city and a sign that tells riders how it works. "
        "The design of the sign is agreed, so what is left is the paperwork and the mounting. A "
        "station is not opened before its permit has arrived.",
        task("Sign design agreed with the city", "done", "normal", -30),
        task("Permits for 20 more stations", "in_progress", "high", -20, key="permits"),
        task("Print and mount the station signs", "new", "normal", -5),
        status="in_progress",
        due=-30,
        key="signage",
    ),
    status="in_progress",
    priority="high",
    key="city",
)

NOTIFICATIONS = epic(
    "Notifications and messages",
    md("""
        ## Principles

        - Say it once, in the rider's language, at the right moment.
        - A message that asks for nothing and tells nothing new is not sent.
        - Riders can turn off every kind except safety messages.

        ## Kinds of message

        1. Reminders about a reservation or a ride that is still open
        2. Receipts ([[receipt-email]] is the story; [[receipt-msg]] is how it is sent)
        3. Safety messages from the operations team
        4. Notices from the city, such as a closed street
    """),
    story(
        "Push notifications for reservations and rides",
        "Short notifications for the two moments where a rider needs a nudge. One is a "
        "reservation that is about to end, the other a ride that has been open for a long time "
        "and may have been forgotten. Each kind can be turned off.",
        task("Push when a reservation is about to end", due=36),
        task("Push when a ride has been open for two hours", due=38),
        task("Settings to turn each kind off", due=42),
        due=38,
        key="push",
    ),
    story(
        "Send the rider an email receipt when a ride ends",
        "After each ride the rider gets an email with the price, the route and the ride time. "
        "This story is only the sending: what the email says is in the receipts story of the "
        "payments epic. It is kept apart because the two are done by different teams.",
        due=32,
        key="receipt-msg",
    ),
    story(
        "Quiet hours",
        "No push notification at night unless it is about safety. Riders can choose their own "
        "quiet hours instead of the ones we pick. A message that was held back is sent in the "
        "morning only if it still matters.",
        task("Do not push between 22:00 and 07:00", priority="low"),
        task("Let riders choose their own hours", priority="low"),
        priority="low",
    ),
    story(
        "Message every rider at one station",
        "When a station is closed for repairs, tell the riders who use it. The operations team "
        "picks the station, writes the message and sees what the riders will read before it "
        "goes out. Only riders who rode from that station in the last month are told.",
        task("Pick the station on a map", due=70),
        task("Write and preview the message", due=72),
        task("Send in the language of each rider", due=75),
        due=75,
        key="broadcast",
    ),
    key="notifications",
)

REBALANCE = epic(
    "Rebalancing: moving bikes between stations",
    md("""
        Bikes pile up at the stations downhill and run out uphill. The van drivers move them at
        night, when the streets are quiet ([[d-night]]).

        ## Where we are

        This epic has a single story for now. We will plan it properly once the pilot has run for
        three months and we can see where the bikes really go ([[f-peak]]).

        ## What a good night looks like

        - Every station that was empty at 18:00 has bikes again by 05:00
        - No van is in a bike lane after 05:00
        - The driver's list fits on one phone screen

        It depends on the live counts on the map ([[map]]), because the list is made from them.
    """),
    story(
        "Night-time moves between stations",
        "A list for the van drivers: take this many bikes from here to there, between 1 and 5 "
        "in the morning. The list is made from the live counts on the map and fits on one phone "
        "screen. Not planned in detail: it waits for three months of pilot data.",
        due=150,
        key="night-moves",
    ),
    due=150,
    key="rebalance",
)

ADAPTIVE = epic(
    "Adaptive and cargo bikes pilot",
    md("""
        ## Ideas only

        Some riders cannot use a standard bike: hand cycles, tricycles, bikes with a child seat or
        a cargo box. We would like to offer a few of each at a handful of stations.

        ## First step

        Ask the city's disability council what they need before we plan anything. Nothing is
        planned yet, so this epic has no stories.

        ## Questions to ask

        - Which kinds of bike would riders actually use?
        - Which stations have the space and the level ground for them?
        - Do the locks and the unlock flow ([[unlock]]) work for someone who cannot kneel to
          scan a code on the frame?
    """),
    priority="low",
    key="adaptive",
)

EPICS: tuple[Item, ...] = (
    PLATFORM,
    UNLOCK,
    RIDES,
    PAYMENTS,
    STATIONS,
    FLEET,
    SAFETY,
    SUPPORT,
    CITY,
    NOTIFICATIONS,
    REBALANCE,
    ADAPTIVE,
)

NEEDS = "needs_first"
RELATES = "relates_to"

LINKS: tuple[Link, ...] = (
    Link("unlock-endpoint", "token", NEEDS),
    Link("two-seconds", "unlock-endpoint", NEEDS),
    Link("retry-weak", "two-seconds", NEEDS),
    Link("old-phones", "two-seconds", NEEDS),
    Link("show-reason", "detect-fail", NEEDS),
    Link("auto-refund", "report-lock", NEEDS),
    Link("auto-refund", "charge-end", NEEDS),
    Link("retry-charges", "charge-end", NEEDS),
    Link("price-before", "price-table", NEEDS),
    Link("three-ds", "hosted-page", NEEDS),
    Link("db-check", "zero-downtime", NEEDS),
    Link("rollback", "zero-downtime", NEEDS),
    Link("export-data", "datamap", NEEDS),
    Link("delete-account", "datamap", NEEDS),
    Link("log-screen", "log-lines", NEEDS),
    Link("page-oncall", "ride-dash", NEEDS),
    Link("outside-fee", "dock-detect", NEEDS),
    Link("remote-lock", "outside-fee", NEEDS),
    Link("route-simplify", "drop-points", NEEDS),
    Link("route-receipt", "route-simplify", NEEDS),
    Link("route-receipt", "receipt-send", NEEDS),
    Link("credits", "refund-single", NEEDS),
    Link("pass-checkout", "pass-plans", NEEDS),
    Link("receipt-send", "charge-end", NEEDS),
    Link("accept-terms", "insurance-text", NEEDS),
    Link("report-city", "crash-form", NEEDS),
    Link("ride-history", "ride-timer", NEEDS),
    Link("passes", "card-pay", NEEDS),
    Link("refunds", "card-pay", NEEDS),
    Link("night-moves", "map", NEEDS),
    Link("report-lock", "ops-api", RELATES),
    Link("offline-unlock", "tunnels", RELATES),
    Link("map", "find-near", RELATES),
    Link("crash", "insurance", RELATES),
    Link("damage", "failed-unlock", RELATES),
    Link("privacy", "route-trace", RELATES),
    Link("receipt-email", "receipt-msg", "duplicates"),
    Link("gateway-limits", "rate-limit", "supersedes"),
)

# -- acceptance criteria -----------------------------------------------------------------------

# The criteria that something else names or that are shown being changed. Many more are on
# stories and tasks by title in tests/demo_details.py, and are made together with the plan. Each
# has a `node` that is the key of the story it is placed on, or none while it is placed nowhere.
RULES: tuple[Criterion, ...] = (
    Criterion(
        "r-two-secs",
        "A scan opens the lock within two seconds on a good connection",
        "Measured from the moment the camera reads the code to the moment the lock moves, with "
        "at least two bars of signal.",
        "qr-unlock",
    ),
    Criterion(
        "r-used-code",
        "A used or expired code is refused with a clear reason",
        "The rider is told whether the code was already used or has run out, never just that "
        "something went wrong.",
        "qr-unlock",
        "met",
    ),
    Criterion(
        "r-one-ride",
        "A rider can have only one open ride at a time",
        "A second unlock while a ride is open is refused and points to the open ride.",
        "qr-unlock",
    ),
    Criterion(
        "r-bike-number",
        "The bike number on the frame is the number a rider types",
        "A rider reads the number painted on the frame and types it in the app. It must find the "
        "same bike that the QR code finds.",
        "type-number",
        "met",
    ),
    Criterion(
        "r-ten",
        "A reservation lasts exactly ten minutes",
        "Not nine, not eleven: the timer on the phone and the server's timer must agree.",
        "hold-bike",
    ),
    Criterion(
        "r-one-reservation",
        "A rider has at most one reservation at a time",
        "A second reservation is refused with the time left on the first.",
        "reserve",
    ),
    Criterion(
        "r-lock-closed",
        "A ride ends only when the lock is closed on a dock",
        "Closing the app, running out of battery or walking away does not end a ride.",
        "end-ride",
    ),
    Criterion(
        "r-outside-fee",
        "Ending a ride outside a station costs a flat fee",
        "The fee is shown before the rider confirms and is waived when every station nearby is "
        "full.",
        "end-ride",
    ),
    Criterion(
        "r-route-trim",
        "Only the start and the end of a route are kept after 30 days",
        "The points in between are deleted. See [[d-location]] for why.",
        "route-trace",
        "met",
    ),
    Criterion(
        "r-no-card",
        "No card number ever reaches our servers",
        "Card details are entered on the payment provider's page only ([[d-hosted]]).",
        "card-pay",
        "met",
    ),
    Criterion(
        "r-price-first",
        "The price is shown before the ride starts",
        "On the unlock screen, in the rider's currency, including the free minutes.",
        "card-pay",
    ),
    Criterion(
        "r-retry",
        "A failed charge is retried for three days before the rider is asked",
        "Retries are spread over the three days and stop as soon as one works.",
        "card-pay",
    ),
    Criterion(
        "r-45",
        "A pass covers the first 45 minutes of every ride",
        "Minutes beyond 45 are charged at the normal price per minute.",
        "passes",
    ),
    Criterion(
        "r-renew",
        "A pass renews on the same day each month",
        "A pass bought on the 31st renews on the last day of shorter months.",
        "passes",
    ),
    Criterion(
        "r-refund-card",
        "A refund goes back to the card that paid",
        "Never to another card and never as cash.",
        "refunds",
    ),
    Criterion(
        "r-one-receipt",
        "Every finished ride gets exactly one receipt",
        "Not none and not two, even when the ride was ended by support.",
        "receipt-email",
    ),
    Criterion(
        "r-fresh",
        "The map never shows a count older than one minute",
        "When the data is older the station is shown as unknown.",
        "map",
    ),
    Criterion(
        "r-full-docks",
        "A station with no free dock looks different from one with docks",
        "A rider on the way to return a bike must be able to see this at a glance.",
        "map",
    ),
    Criterion(
        "r-two-reports",
        "Two rider reports lock a bike out until a technician has checked it",
        "The second report locks the bike at once, without waiting for anyone to read it.",
        "damage",
    ),
    Criterion(
        "r-not-on-map",
        "A locked-out bike is never offered on the map",
        "It does not count as available at its station either.",
        "damage",
    ),
    Criterion(
        "r-inspection-name",
        "Every inspection is recorded with the technician's name",
        "So that a question about a bike can be put to the person who last looked at it.",
        "inspect",
        "met",
    ),
    Criterion(
        "r-24h",
        "A crash report reaches the city within 24 hours",
        "From the moment the rider submits it, including weekends ([[d-24h]]).",
        "crash",
    ),
    Criterion(
        "r-terms",
        "A rider accepts the insurance terms before the first ride",
        "The acceptance is stored with the date and the version of the terms.",
        "insurance",
    ),
    Criterion(
        "r-code-once",
        "A sign-in code works once and expires after ten minutes",
        "Asking for a new code makes the old one stop working.",
        "accounts",
        "met",
    ),
    Criterion(
        "r-delete",
        "A rider can delete their account at any time",
        "Everything except what the law makes us keep for accounting is removed within 30 days.",
        None,
    ),
    Criterion(
        "r-help-dated",
        "Every help article says when it was last checked",
        "Dropped: the help centre shows the date of the last change instead.",
        "help-centre",
        "dropped",
    ),
    Criterion(
        "r-visible-number",
        "The number painted on the frame is easy to read",
        "A rider can read the number on the frame from two metres and type it in the app. It is in "
        "large digits, on both sides of the frame.",
        "bike-numbers",
    ),
    Criterion(
        "r-languages",
        "The app is available in at least three languages",
        "The three launch languages are chosen with the city.",
        None,
    ),
    Criterion(
        "r-old-hardware",
        "The app works on a phone that is three years old",
        "Measured on the oldest phone the supported system versions still run on ([[d-os]]).",
        None,
    ),
    Criterion(
        "r-staff-export",
        "Staff can export one rider's data when the rider asks",
        "The export is the same file the rider can take for themselves.",
        None,
    ),
    Criterion(
        "r-city-names",
        "Reports for the city use the city's own station names",
        "Our station numbers are never shown in a report for the city.",
        None,
    ),
    Criterion(
        "r-quiet",
        "Quiet hours never block a safety message",
        "A message from the operations team about safety is always delivered.",
        None,
    ),
)

# An acceptance criterion is first put on one story and moved to another, and one is put on a
# story after it was written down with none: (key, story, actor).
RULE_MOVES = (("r-ten", "reserve", "Priya Nair"),)
RULE_PLACES = (("r-delete", "privacy", "Priya Nair"),)
RULE_EDITS: tuple[RuleEdit, ...] = (
    RuleEdit(
        "r-two-reports",
        "Tomas Berg",
        body="The second report locks the bike at once, without waiting for anyone to read it. "
        "The two reports must come from two different riders.",
    ),
    RuleEdit("r-outside-fee", "Priya Nair", status="met"),
    RuleEdit("r-one-receipt", "Priya Nair", status="met"),
)

# What the whole project means by done, set once by a person (the owner) through the board's own
# call, since only a person can. It is shown on every task's page and handed to every agent.
DEFINITION_OF_DONE = md("""
    - Every acceptance criterion of the task and of its story is met.
    - The tests pass, and the change comes with a test that would have failed without it.
    - Another person has read the change.
    - The help articles, the operator notes and the changelog are up to date.
    - Nothing is left half done: no switch that is still off, no screen that leads nowhere.
    - Anything we learned or decided on the way is written down as a note.
    - [person] Looks right on a real phone or a real bike.
""")

# How the first six checks above were ticked on finished work, in the words of whoever did it. Each
# check has three wordings, so that a page does not show one sentence over and over. {title} is
# the title of the item, {tests} and {added} are numbers worked out from it, and {reviewer} is a
# person of the team. The last check is marked [person]: only a person ticks it.
CHECK_EVIDENCE: tuple[tuple[str, ...], ...] = (
    (
        "Every acceptance criterion was tried on the staging build and is met.",
        "Went through the criteria of this item and of its story one by one: all are met.",
        "Each criterion was checked against the staging build after the last change.",
    ),
    (
        "All {tests} tests pass, and the new test fails without the change.",
        "{tests} tests pass, {added} of them new; the new ones fail on the old code.",
        "The suite is green ({tests} tests), and the test written for this fails on the old code.",
    ),
    (
        "{reviewer} read the change and approved it.",
        "Reviewed by {reviewer}, who asked for one rename and then approved it.",
        "{reviewer} approved the change after reading it twice.",
    ),
    (
        "Help article and changelog updated for '{title}'.",
        "The changelog line is in, and the operator notes were checked for '{title}'.",
        "Updated the help article and the operator notes; the changelog has an entry.",
    ),
    (
        "No switch is left off and no screen leads nowhere: walked every path to the end.",
        "Checked every switch and every screen of this change: nothing is half done.",
        "Walked the whole flow from start to end on staging: no dead ends, no switches off.",
    ),
    (
        "Nothing new was decided on the way; the notes that apply still hold.",
        "What was learned is written down as a note and linked to this item.",
        "Recorded the one decision made on the way as a note, with its reason.",
    ),
)

# On every finished piece of work a person ticks the check marked [person] themselves, saying what
# they did, and once with no words at all.
PERSON_TRIED = (
    "Tried it on a real phone at the depot.",
    "Checked on a real bike by the field team.",
    "Tried it on two real phones, one of them old.",
)
PERSON_TICK_WITH_NO_WORDS = "Sign-off with the technician's name"


@dataclass(frozen=True)
class Untick:
    """A check that an agent ticked, a person took back, and the agent ticked again once it was
    put right, so that the history of one task shows all three."""

    task: str
    check: int
    first: str
    reason: str
    again: str


UNTICK = Untick(
    "Errors with a stable code and a plain message",
    3,
    "Updated the help article and the changelog.",
    "The error list in the help article still shows the old codes.",
    "The help article lists the new codes, and the changelog says which ones changed.",
)

NOTES: tuple[Note, ...] = (
    # Twenty notes that somebody decided, which come first, and then nineteen that nobody decided,
    # so that a note is numbered 1 to 20 when it was decided and 21 to 39 when it was not.
    #
    # Nine project-wide notes. Every one concerns the whole project, and all nine are
    # linked to the platform epic (and to the work they touch most).
    decided(
        "d-location",
        "Rider location is kept only while a ride is under way",
        "The apps record a location point every ten seconds while a ride is open and at no other "
        "time. Thirty days after a ride ends, its route is reduced to the start station and the "
        "end station and the points in between are deleted. Nothing is recorded while the app "
        "is in the background with no ride open, and location is never shared with a partner "
        "except as the anonymous trip data described in [[open-data]].",
        "The city's data protection officer asked for the least location data that still lets us "
        "answer a rider's complaint about a ride, and thirty days is long enough for nearly all "
        "of them. Recording only during rides also makes the privacy notice short enough for a "
        "rider to read. Keeping routes for longer would make the impact assessment much harder "
        "([[dpia]]).",
        "Ines Duarte",
        100,
        ("platform", "privacy", "route-trace"),
        ("r-route-trim",),
        True,
    ),
    decided(
        "d-money",
        "Money is stored in whole cents and shown in the rider's own currency format",
        "Every amount in the database, the API and the apps is a whole number of cents together "
        "with a three-letter currency code. An amount is formatted for display only at the last "
        "step, using the rider's language and the currency of the city they are riding in. No "
        "calculation anywhere uses decimal fractions.",
        "Early prototypes added up prices as decimals and produced receipts that were a cent off, "
        "which the finance team had to correct by hand. Whole numbers make totals exact and let "
        "the accounting export match the receipts line by line. Choosing the currency by city "
        "leaves room to open in a second country later without touching stored data.",
        "Marcus Hale",
        98,
        ("platform", "card-pay", "pricing"),
        (),
        True,
    ),
    decided(
        "d-wcag",
        "Every screen meets WCAG 2.2 level AA",
        "Every screen in the rider app, the operator dashboard and the website must meet the "
        "Web Content Accessibility Guidelines 2.2 at level AA before it ships. That includes "
        "text contrast, touch targets of at least 44 points, a working order for screen readers "
        "and no information given by colour alone. A screen that fails is not released, even "
        "when it is the last one of a milestone.",
        "Riders include people who cannot read small grey text in bright sun, people who use a "
        "screen reader, and people who ride with one hand. The city's contract also names this "
        "standard, so meeting it later would cost more and could delay the launch. Checking at "
        "every screen costs a little each time; checking everything at the end would cost "
        "weeks.",
        "Priya Nair",
        95,
        ("platform", "design-system"),
        (),
        True,
    ),
    decided(
        "d-time",
        "Times are stored in UTC and shown in the station's time zone",
        "Every timestamp is stored in UTC. A time shown to a rider or a technician is converted "
        "to the time zone of the station concerned, not to the setting of the phone, and it "
        "says which zone it is when the two differ. Dates without a time, such as the renewal "
        "day of a pass, are stored as dates in the city's zone.",
        "Prices and passes are defined by the city's clock: a ride that starts at 23:55 and "
        "ends at 00:10 is one ride on one pass day. Using the phone's setting would give a rider "
        "who travels a different receipt for the same ride. Storing UTC keeps the database free "
        "of daylight saving surprises.",
        "Lena Okafor",
        92,
        ("platform", "ride-timer", "passes"),
        (),
        True,
    ),
    decided(
        "d-api",
        "The public API is versioned in the address and never breaks within a version",
        "Every API route starts with a version, such as /v1/. Within a version we only add "
        "fields and endpoints; we never rename, remove or change the meaning of one. A breaking "
        "change gets a new version, and the old version keeps working for at least twelve "
        "months after the new one is announced.",
        "Partners such as the city's reporting team and the helmet shop build on the API and "
        "cannot update on the day we do. Old versions of the app also stay installed for "
        "months. A clear promise lets the teams move quickly without a freeze before every "
        "release ([[api]]).",
        "Lena Okafor",
        90,
        ("platform", "api"),
        (),
        True,
    ),
    decided(
        "d-bike-id",
        "A bike is identified by the number painted on its frame, never by its lock",
        "The number painted on the frame, such as RB-0417, is the only identifier that people "
        "see or type, and it is the key in every system. The serial number of a lock is kept as "
        "an attribute that can change when a lock is swapped. If a frame is repainted the number "
        "moves with the physical bike, never with the lock.",
        "Technicians swap locks far more often than they replace frames, and a rider who reports "
        "a problem reads the number off the frame. Keying everything on the lock produced "
        "duplicate bikes in the pilot every time a lock was replaced. One visible number keeps "
        "support calls short ([[bike-numbers]]).",
        "Tomas Berg",
        88,
        ("platform", "bike-numbers", "type-number"),
        ("r-bike-number",),
        True,
    ),
    decided(
        "d-os",
        "The apps support the last two major versions of iOS and Android",
        "The rider app is built and tested for the two newest major versions of each phone "
        "system and may refuse to install on older ones. When a new major version comes out, "
        "support for the oldest one ends after three months. Riders on an older phone are "
        "pointed to the website, which works in any recent browser.",
        "Supporting every old phone doubles the testing time, and a security fix cannot reach a "
        "phone that no longer gets updates. The pilot showed how few riders that affects "
        "([[f-old-phones]]), so the cost to riders is small. The website stays as the fallback "
        "for anyone left behind.",
        "Lena Okafor",
        85,
        ("platform", "old-phones"),
        ("r-old-hardware",),
        True,
    ),
    decided(
        "d-support",
        "Support promises a first reply within one working day",
        "Every message to support gets a first reply from a person within one working day, and "
        "a message about a bike that cannot be unlocked or a charge that looks wrong gets one "
        "within four working hours. Automatic replies do not count. The support lead reviews "
        "missed promises every Monday and reports them to the product lead.",
        "During the pilot, riders who waited more than two days stopped writing and told their "
        "friends instead. A promise short enough to keep, and one that the team can see itself "
        "missing, is better than a longer promise that nobody measures. The numbers in "
        "[[f-inbox]] show that the planned staffing can keep it.",
        "Kofi Mensah",
        80,
        ("platform", "inbox"),
        (),
        True,
    ),
    decided(
        "d-nosdk",
        "No advertising or third-party analytics code in the apps",
        "The rider app and the operator dashboard contain no advertising code and no "
        "third-party analytics libraries. Use is measured only with the events that we send to "
        "our own servers, and every event is listed in the data map. Adding any outside code "
        "that talks to the internet needs a note recorded here first.",
        "The two analytics libraries we tried send the phone's advertising identifier by default "
        "([[f-sdk]]), which would contradict the privacy notice that we promised the city. Our "
        "own events give the product team everything they asked for. Keeping outside code out "
        "also shortens the privacy review ([[privacy]]).",
        "Priya Nair",
        75,
        ("platform", "privacy"),
        (),
        True,
    ),
    # Notes about particular work that somebody decided.
    decided(
        "d-hosted",
        "Card details are entered only on the payment provider's page",
        "The rider enters a card number on a page that belongs to the payment provider and "
        "never in a screen of our own. Our apps and servers receive only a token that stands "
        "for the card.",
        "It keeps card numbers out of our systems and avoids a much larger security review "
        "before launch. The provider's page is certified to the card industry's standard "
        "([[f-hosted]]), which we could not match in the time we have.",
        "Lena Okafor",
        78,
        ("card-pay", "hosted-page"),
        ("r-no-card",),
    ),
    decided(
        "d-token",
        "Unlock codes are signed, used once and expire after 30 seconds",
        "The code the phone sends to open a lock is signed by the server, can be used once and "
        "expires after 30 seconds. A second attempt with the same code is refused and logged.",
        "A photograph of a code must not open a bike later, and someone who watches the network "
        "must not be able to replay a code. Thirty seconds is enough to walk to the bike after "
        "the request.",
        "Lena Okafor",
        70,
        ("qr-unlock", "token"),
        ("r-used-code",),
    ),
    decided(
        "d-fee-old",
        "Single rides cost an unlock fee plus a price per minute",
        "A single ride costs a flat unlock fee plus a price for every minute after the first "
        "five. The unlock fee is charged even when the ride is short, and it is the same in "
        "every part of the city.",
        "The pilot charged this way and the finance team found it easy to explain on the "
        "website. The fee also covers the cost of a ride that fails after the lock has opened.",
        "Marcus Hale",
        65,
        ("pricing", "card-pay"),
    ),
    decided(
        "d-passes",
        "No weekly pass in the first release",
        "The first release sells monthly and annual passes only. A weekly pass is considered "
        "again after three months of ride data.",
        "The survey found interest in a monthly pass ([[f-survey]]), and a weekly pass would "
        "double the number of renewals to handle on the first day. Fewer products also keep the "
        "first release easy to explain.",
        "Priya Nair",
        50,
        ("passes",),
    ),
    decided(
        "d-reserve",
        "Reservations hold a bike for ten minutes",
        "A reservation holds one bike for exactly ten minutes. When the time ends the bike is "
        "released at once and a notification tells the rider. A rider can hold one reservation "
        "at a time.",
        "Ten minutes is the walk to the farthest station in the pilot area, and holding bikes "
        "for longer left stations looking empty to everyone else.",
        "Priya Nair",
        60,
        ("reserve", "hold-bike"),
        ("r-ten", "r-one-reservation"),
    ),
    decided(
        "d-lockout",
        "A bike with two damage reports is locked out until a technician checks it",
        "When two different riders report damage on the same bike, the bike is locked out at "
        "once and disappears from the map. It stays locked out until a technician has inspected "
        "it and cleared it.",
        "One report can be a mistake or a bad mood; two almost never are. Letting a damaged bike "
        "stay in use is a safety risk and an insurance risk, and the cost of a wrongly locked "
        "bike is one short trip for a technician.",
        "Tomas Berg",
        45,
        ("damage", "bike-lockout"),
        ("r-two-reports", "r-not-on-map"),
    ),
    decided(
        "d-sms",
        "Receipts are sent by text message as well as by email",
        "Every receipt is sent by email and also by text message to the phone number on the "
        "account.",
        "Some riders do not read their email, and a text message arrives while they are still at "
        "the station.",
        "Priya Nair",
        40,
        ("receipt-email", "receipt-msg"),
    ),
    decided(
        "d-24h",
        "Crash reports go to the city within 24 hours",
        "Every crash or injury report that a rider submits is passed to the city's transport "
        "office within 24 hours, including at weekends. The operations team checks at the start "
        "of each day that nothing is waiting.",
        "The partnership agreement requires it ([[f-city-24]]), and a missed report could cost "
        "us the permits for the stations.",
        "Ines Duarte",
        30,
        ("crash", "report-city"),
        ("r-24h",),
    ),
    decided(
        "d-night",
        "Bikes are moved between stations only between 1 and 5 in the morning",
        "The vans move bikes between stations only between one and five in the morning. During "
        "the day a van only collects a damaged bike or delivers a battery.",
        "A van parked in a bike lane at rush hour blocks the riders it is meant to help, and the "
        "demand peaks are sharp ([[f-peak]]). The night is also when the stations are emptiest "
        "and quickest to refill.",
        "Tomas Berg",
        25,
        ("night-moves", "rebalance"),
    ),
    decided(
        "d-offline",
        "The Bluetooth unlock is prototyped before it is designed",
        "Nobody designs the offline unlock until a prototype has opened a real lock over "
        "Bluetooth from a phone with no signal.",
        "The vendor's datasheet promises five metres ([[f-ble]]) but nobody on the team has "
        "tried it, and a design built on a promise that does not hold would be thrown away.",
        "Lena Okafor",
        12,
        ("offline-unlock", "ble-proto"),
    ),
    decided(
        "d-fee-new",
        "Single rides cost a price per minute, with the first five minutes free",
        "A single ride has no unlock fee. The first five minutes are free and every minute after "
        "that is charged at the same price per minute. A ride that ends within five minutes costs "
        "nothing.",
        "Riders in the pilot survey said that the unlock fee felt like a penalty for short trips, "
        "which are the trips we want most. This replaces [[d-fee-old]]; without the fee, the "
        "income per minute is still enough ([[f-ride-14]]).",
        "Marcus Hale",
        20,
        (),
        (),
        False,
        ("d-fee-old",),
    ),
    # Nineteen notes nobody decided: what was learned, and where it came from.
    learned(
        "f-lanes",
        "The city has 142 km of bike lanes, 38 km of them separated from traffic.",
        "City transport office, open data download, spring edition.",
        ("stations", "find-near"),
    ),
    learned(
        "f-fleet",
        "The pilot fleet is 600 bikes across 48 stations, 120 of the bikes electric.",
        "Operations plan, version 3, section 2.",
        ("fleet", "stations", "battery"),
    ),
    learned(
        "f-ble",
        "The lock vendor says its locks can be opened over Bluetooth from up to 5 metres away.",
        "Vendor datasheet, revision C, page 14.",
        ("offline-unlock", "ble-proto"),
        (),
        ("d-offline",),
    ),
    learned(
        "f-payout",
        "The payment provider pays out three days after a charge settles, and can reverse a "
        "disputed charge for 120 days.",
        "Payment provider agreement, section 4.",
        ("card-pay", "refunds"),
    ),
    learned(
        "f-survey",
        "62 of 100 survey respondents said they would pay for a monthly pass.",
        "Rider survey, 410 respondents, February.",
        ("passes",),
        (),
        ("d-passes",),
    ),
    learned(
        "f-ride-17",
        "The average pilot ride lasts 17 minutes.",
        "Pilot dashboard export for the first month.",
        ("ride-timer", "pricing"),
    ),
    learned(
        "f-signal",
        "There is no mobile signal in the underground car park on Mill Street, where station 31 "
        "stands.",
        "Field visit notes from a technician's round.",
        ("offline-unlock", "tunnels"),
    ),
    learned(
        "f-swap",
        "Swapping the battery of an electric bike takes about four minutes.",
        "Timed during the workshop trial with two technicians.",
        ("battery",),
    ),
    learned(
        "f-inbox",
        "The support inbox received about 90 messages a week during the pilot.",
        "Support inbox report, weeks 1 to 8.",
        ("inbox", "help-centre"),
    ),
    learned(
        "f-city-24",
        "The city requires a report within 24 hours of any injury on one of its bikes.",
        "Partnership agreement, clause 9.2.",
        ("crash", "insurance"),
        ("r-24h",),
        ("d-24h",),
    ),
    learned(
        "f-helmet",
        "The helmet partner can deliver to 12 stations.",
        "The partner's sales call.",
        ("helmets",),
    ),
    learned(
        "f-old-phones",
        "Phones running Android 9 or older make up 4 percent of riders.",
        "App store analytics report for the last quarter.",
        ("old-phones",),
        ("r-old-hardware",),
        ("d-os",),
    ),
    learned(
        "f-peak",
        "Weekday demand peaks between 8:00 and 9:00 and between 17:00 and 18:30.",
        "Pilot dashboard export, rides by hour.",
        ("rebalance", "night-moves", "map"),
        (),
        ("d-night",),
    ),
    learned(
        "f-dpia",
        "The city's data protection officer needs the impact assessment two weeks before the "
        "launch decision.",
        "Email from the city's data protection officer.",
        ("platform", "privacy", "dpia"),
    ),
    learned(
        "f-hosted",
        "The payment provider's hosted page is certified to the card industry's data security "
        "standard.",
        "The provider's compliance page, read by Lena Okafor.",
        ("platform", "card-pay"),
        (),
        ("d-hosted",),
    ),
    learned(
        "f-retain",
        "The city allows rider data that is needed for accounting to be kept for up to 13 months.",
        "Partnership agreement, schedule 3.",
        ("platform", "privacy"),
        ("r-delete",),
        ("d-location",),
    ),
    learned(
        "f-sdk",
        "The two analytics libraries we tried send the phone's advertising identifier by default.",
        "A test with a network proxy, written up by Lena Okafor.",
        ("platform",),
        (),
        ("d-nosdk",),
    ),
    learned(
        "f-tyres",
        "Tyres last about 3,000 km before they need replacing.",
        "Workshop log for the pilot fleet.",
        ("tyres",),
    ),
    learned(
        "f-ride-14",
        "The average pilot ride lasts 14 minutes once rides by staff are left out.",
        "Pilot dashboard export for the first three months, staff rides removed. It replaces the "
        "first figure, which counted rides that staff made while testing.",
        ("ride-timer", "pricing"),
        (),
        (),
        ("f-ride-17",),
    ),
)

CONFIRMATIONS: tuple[Confirmation, ...] = (
    Confirmation("d-location", "Ines Duarte", "Agreed in the privacy review."),
    Confirmation("d-money", "Marcus Hale", "Checked against the accounting export."),
    Confirmation("d-nosdk", "Priya Nair", "Said so in the planning meeting."),
    Confirmation("d-hosted", "Marcus Hale", "Matches the provider's agreement."),
    Confirmation("d-reserve", "Priya Nair", "Agreed with the operations team."),
    Confirmation("d-lockout", "Tomas Berg", "Agreed with the technicians."),
    Confirmation("d-24h", "Ines Duarte", "Matches clause 9.2 of the agreement."),
    Confirmation("d-token", "Lena Okafor", "Reviewed with the lock vendor."),
    Confirmation(
        "d-token",
        "Lena Okafor",
        "The lifetime is under review after the field test.",
        False,
    ),
    Confirmation("d-fee-new", "Marcus Hale", "Approved with the finance team."),
    Confirmation("f-fleet", "Tomas Berg", "Counted at the depot."),
    Confirmation("f-payout", "Marcus Hale", "Read the agreement."),
    Confirmation("f-inbox", "Kofi Mensah", "These are my own numbers."),
    Confirmation("f-city-24", "Ines Duarte", "I have the clause in front of me."),
    Confirmation("f-dpia", "Ines Duarte", "From the officer's own email."),
    Confirmation("f-hosted", "Lena Okafor", "Checked on the provider's site."),
    Confirmation("f-swap", "Tomas Berg", "Timed it myself."),
    Confirmation(
        "f-swap",
        "Tomas Berg",
        "The new batteries are slower, so it has to be timed again.",
        False,
    ),
    Confirmation("f-tyres", "Priya Nair", "Checked against the workshop log."),
)

WITHDRAWALS: tuple[Withdrawal, ...] = (
    Withdrawal(
        "d-sms",
        "The city's privacy review asked us to use email only, since a phone number is not "
        "otherwise needed.",
    ),
    Withdrawal(
        "f-helmet",
        "The contract says 20 stations. The figure of 12 came from an early call and was wrong.",
    ),
)

# A handoff note is left by an agent that takes a piece of work and ends it with finish_work, which
# is for agents: a person ends their claim with a note on the board, and changes the status on the
# item's page. So every actor here is an agent.
HANDOFFS: tuple[Handoff, ...] = (
    Handoff(
        "datamap",
        "docs-agent",
        "done",
        md("""
            The data map is written and lives in the shared drive under *Privacy / Data map*.

            - It lists **41 fields** in 9 groups: account, ride, route, payment, bike report,
              support message, device, staff action and analytics event.
            - For every field it says why we need it and how long we keep it.
            - Nothing is kept that has no reason written next to it.

            One question is open. I assumed that a route reduced to its start and end station is
            no longer location data, and I said so on the first page. The city's officer should
            confirm that before the launch decision.
        """),
    ),
    Handoff(
        "token",
        "api-agent",
        "done",
        "Unlock tokens are signed with the shared server key, used once and expire after 30 "
        "seconds, as decided. A replayed token is refused with the reason `already_used`, and "
        "the attempt is logged. The token's lifetime is a setting, not a constant, because the "
        "field test may want to change it.",
    ),
    Handoff(
        "unlock-endpoint",
        "api-agent",
        "done",
        md("""
            The endpoint is live on staging: `POST /v1/unlocks` with the bike number and a token.

            It answers in about 400 ms. Errors carry a stable code (`bike_unknown`,
            `bike_locked_out`, `rider_has_open_ride`, `token_expired`). The mobile app can show
            the plain message that comes with each code, so it does not need its own list.
        """),
    ),
    Handoff(
        "print-qr",
        "ops-agent",
        "done",
        "All 600 bikes have a QR sticker. 14 had to be stuck on again because the glue failed in "
        "the rain. We re-read a sample of 60 stickers with an old phone and 59 scanned at the "
        "first try; the one that did not was scratched.",
    ),
    Handoff(
        "two-seconds",
        "api-agent",
        "released",
        md("""
            Server side is done: the request takes about 400 ms from the phone to the lock
            command. The rest of the time is the lock itself.

            What I measured:

            - on the test bench the lock opens in 1.1 s
            - in the field it takes between 1.8 s and 3 s, with a few over 3 s in the cold

            I could not get under two seconds in the cold with the current lock firmware. This
            needs someone with the lock vendor's contact, and the phone side may help by showing
            progress at once. I am handing it on.
        """),
    ),
    Handoff(
        "dock-detect",
        "ops-agent",
        "done",
        "A dock now reports within a second of the lock closing. Three stations report late "
        "because of a weak mobile signal at the cabinet; those are on the list for a repeater.",
    ),
    Handoff(
        "nearby-check",
        "mobile-agent",
        "done",
        "Typing a number now looks only at bikes within 150 metres of the phone, which removed "
        "all the mistaken unlocks of the pilot. If the phone has no location the number is "
        "accepted as before, and the ride is marked for a look by support.",
    ),
    Handoff(
        "checklist-screen",
        "ops-agent",
        "done",
        "The checklist screen is in the technicians' app. It works with gloves on: the buttons "
        "are 56 points high. Technicians asked for a way to skip the lights check in daylight; "
        "I left it out because every check has to be done every time.",
    ),
    Handoff(
        "sign-off",
        "ops-agent",
        "done",
        "Sign-off stores the technician's name, the time and the checklist version. A "
        "technician who is not signed in cannot finish an inspection.",
    ),
    Handoff(
        "rate-limit",
        "api-agent",
        "dropped",
        "Dropped: the gateway that the city hosts for us already limits every client, and a "
        "second limit of our own would only add a way to be wrong twice. The gateway's limits "
        "are tracked in another task.",
    ),
    Handoff(
        "tyre-model",
        "ops-agent",
        "dropped",
        "Dropped with the story. Three months of data is not enough to predict tyre wear better "
        "than the technicians' own inspections, which already catch worn tyres.",
    ),
    Handoff(
        "dpia",
        "docs-agent",
        "blocked",
        md("""
            Blocked on the city. We cannot finish without their template for the assessment.

            - Asked on Monday, asked again on Thursday: no reply yet.
            - Everything else is drafted: the data map, the retention rules and the list of
              people who can see rider data.
            - Once the template arrives the rest should take two days.

            Ines is chasing it through the city liaison.
        """),
    ),
    Handoff(
        "report-lock",
        "ops-agent",
        "blocked",
        "Blocked on the operator dashboard's damage endpoint, which is not finished yet. The "
        "app can already collect the report. I stopped here so that no report is lost while "
        "the endpoint is missing.",
    ),
    Handoff(
        "three-ds",
        "api-agent",
        "blocked",
        "Blocked until the payment provider sends us test cards that trigger the extra "
        "authentication step. They promised them for the end of the week. The code is ready "
        "and waits on a branch.",
    ),
    Handoff(
        "refund-single",
        "mobile-agent",
        "released",
        "I started this but it belongs with the operator dashboard, and I do not work there. "
        "Nothing is written yet. The first thing to decide is who is allowed to refund and up "
        "to what amount.",
    ),
)

# Claims that are held now, for the longest time a claim may be held (24 hours), so that they
# are still held when someone looks.
HELD: tuple[Held, ...] = (
    Held("zero-downtime", "api-agent"),
    Held("two-seconds", "mobile-agent"),
    Held("lockout", "Lena Okafor"),
    Held("charge-end", "api-agent"),
    Held("damage", "ops-agent"),
)

# A claim that has run out without a handoff note.
LAPSED: tuple[Held, ...] = (Held("drop-points", "review-agent"),)

# Changes made after the work was planned: a date that slipped, a priority that rose, a title
# or text that was sharpened. The keys name the node; each is a change to a field the node
# was planned with.
EDITS: tuple[Edit, ...] = (
    Edit("two-seconds", "Priya Nair", due=-1),
    Edit("lockout", "Lena Okafor", priority="high"),
    Edit("card-pay", "Marcus Hale", due=8),
    Edit("pricing", "Marcus Hale", due=-6),
    Edit("end-ride", "Priya Nair", priority="urgent"),
    Edit("privacy", "Priya Nair", priority="urgent"),
    Edit("passes", "Marcus Hale", priority="high"),
    Edit("station-telemetry", "Lena Okafor", due=-8),
    Edit("signage", "Ines Duarte", due=-12),
    Edit("dpia", "Ines Duarte", due=-9),
    Edit(
        "old-phones",
        "Priya Nair",
        body="Use the five oldest phones in the drawer in the office, including one with a "
        "cracked screen. The unlock has to work on all of them.",
    ),
    Edit("unlock", "Priya Nair", due=30),
    Edit("receipt-email", "Priya Nair", title="Email a receipt after every ride"),
    Edit("night-moves", "Tomas Berg", clear_due=True),
    Edit(
        "retry-weak",
        "Priya Nair",
        body="One retry, after one second, then the failure screen. More than one retry makes "
        "the rider wait longer than starting again would.",
    ),
    Edit("find-near", "Priya Nair", priority="high"),
    Edit("charge-end", "Marcus Hale", priority="urgent"),
    Edit("page-oncall", "Lena Okafor", due=24),
    Edit("report-city", "Ines Duarte", due=10),
    Edit("crash-form", "Priya Nair", priority="urgent"),
    Edit("zero-downtime", "Lena Okafor", due=3),
    Edit("refunds", "Marcus Hale", due=44),
    Edit("open-data", "Ines Duarte", due=110),
    Edit("two-seconds", "Priya Nair", priority="urgent"),
    Edit("hold-bike", "Priya Nair", due=60),
    Edit("insurance", "Priya Nair", due=15),
)
