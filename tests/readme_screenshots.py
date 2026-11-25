"""Takes the two screenshots in README.md. It is a developer tool, not part of the product and not
a way to seed an install: it makes one small fictional project on a THROWAWAY stack and captures
the real board in the light theme.

    COMPOSE_PROJECT_NAME=vrum-shots VRUM_PORT=20000 VRUM_DB_PASSWORD=... \\
        docker compose up -d --build --wait
    uv run python -m tests.readme_screenshots http://127.0.0.1:20000/mcp

It registers a fictional account and project through the board's own HTTP API, fills the project
through real MCP calls (as the agents and people of the story would), and writes
docs/screenshots/board.png and docs/screenshots/graph.png, both of the same project (or into the
folder VRUM_README_SHOTS names, to try it without changing them).

It refuses to run unless the server is on this machine and has never held another account: the
account it makes must be the first one. Run it against a stack that was just built, and take the
stack down afterwards (`docker compose down --volumes`). The account, the agent key and the
session exist only in this run and in that stack; nothing is written anywhere but the two images.
"""

import asyncio
import datetime
import os
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from playwright.async_api import Page, async_playwright

from tests.world import SESSION_COOKIE, Person, api

# The README's two pictures, unless VRUM_README_SHOTS names another folder (to try the tool without
# touching them).
OUT = Path(
    os.environ.get("VRUM_README_SHOTS")
    or Path(__file__).resolve().parent.parent / "docs" / "screenshots"
)
# Wide enough for the sidebar to be a column (the board puts it over the page below 1100 pixels)
# and for all six columns to be in view, and no wider, so that the text is still legible when the
# image is shown at about 800 pixels.
VIEWPORT = {"width": 1440, "height": 1250}

OWNER = "Sam Example"
OWNER_EMAIL = "sam@example.com"
SHOP_MANAGER = "Jo Sample"
AGENT = "build-agent"
PROJECT = "Pickup ordering"


class Stopped(SystemExit):
    """Something is wrong with where this was pointed, said in plain words."""


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    if result.is_error or result.structured_content is None:
        text = " ".join(getattr(block, "text", "") for block in result.content)
        raise Stopped(f"{tool} was refused: {text}")
    return dict(result.structured_content)


def sign_up(base: str) -> Person:
    """The fictional account. On a stack that has ever held another account it is not the first,
    and the tool stops before it makes anything else."""
    password = secrets.token_urlsafe(18)
    reply = api(
        base,
        "POST",
        "/board/api/accounts",
        {"email": OWNER_EMAIL, "name": OWNER, "password": password},
    )
    if reply.status != 201 or not reply.cookie:
        raise Stopped(
            f"The account was refused ({reply.status}). Use a stack that was just built: this "
            f"tool only runs against an install with no accounts. {reply.body}"
        )
    person = Person(
        base,
        OWNER_EMAIL,
        password,
        OWNER,
        reply.cookie,
        reply.body["csrf"],
        reply.body["account"]["id"],
    )
    if person.id != 1:
        raise Stopped(
            f"This server already held other accounts (the new one is number {person.id}). Use a "
            "stack that was just built, and take it down afterwards."
        )
    return person


def today() -> datetime.date:
    return datetime.datetime.now(datetime.UTC).date()


def in_days(days: int) -> str:
    return (today() + datetime.timedelta(days=days)).isoformat()


async def populate(url: str, key: str, person: Person, project_id: int) -> dict[str, int]:
    """A small pickup-ordering project for a bakery, made through the same MCP operations an
    agent or the board uses. Returns the numbers of the records the screenshots need."""
    headers = {"Authorization": f"Bearer {key}"}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        hosted = await call(
            client,
            "add_note",
            text="Use the payment provider's hosted checkout page\n\n"
            "Customers enter their card details on the payment provider's hosted page. "
            "The shop's own pages and servers never receive a card number.",
            why="It keeps card data out of the shop's systems and avoids a larger security "
            "review before launch.",
            decided_by=SHOP_MANAGER,
        )
        slots = await call(
            client,
            "add_note",
            text="Pickup slots are 15 minutes long\n\n"
            "A customer picks a 15 minute window, and the kitchen works through orders one "
            "window at a time.",
            why="The kitchen bakes and packs in 15 minute rounds, so shorter slots would "
            "promise times it cannot keep.",
            decided_by=SHOP_MANAGER,
        )
        await call(
            client,
            "add_note",
            text="Prices include sales tax\n\n"
            "Every price shown to a customer includes sales tax, in the basket and on "
            "receipts too.",
            why="Customers compare the website with the shop's price board, which includes tax.",
            decided_by=SHOP_MANAGER,
            project_wide=True,
        )
        plan = await call(
            client,
            "create_plan",
            items=[
                {
                    "key": "epic",
                    "kind": "epic",
                    "title": "Order ahead for pickup",
                    "body": "Customers choose bakes on the website, pick a time and collect "
                    "them in the shop.",
                    "priority": "high",
                    "target_on": in_days(28),
                },
                {
                    "key": "menu",
                    "kind": "story",
                    "title": "Browse the menu",
                    "body": "Show today's bakes with photos, prices and allergen notes.",
                    "parent": "epic",
                    "target_on": in_days(7),
                },
                {
                    "key": "slots",
                    "kind": "story",
                    "title": "Choose a pickup time",
                    "body": "Offer only the time windows the kitchen can still fill, as decided "
                    f"in [[note:{slots['id']}]].",
                    "parent": "epic",
                    "priority": "high",
                    "target_on": in_days(14),
                },
                {
                    "key": "basket",
                    "kind": "story",
                    "title": "Place an order",
                    "body": "Add bakes to a basket, review the total and confirm the order.",
                    "parent": "epic",
                    "target_on": in_days(14),
                },
                {
                    "key": "pay",
                    "kind": "story",
                    "title": "Pay by card",
                    "body": "Send the customer to the hosted checkout page, as decided in "
                    f"[[note:{hosted['id']}]], and mark the order paid when it confirms.",
                    "parent": "epic",
                    "priority": "high",
                    "target_on": in_days(21),
                },
                {
                    "key": "list",
                    "kind": "task",
                    "title": "Menu list endpoint",
                    "body": "Return the day's bakes with prices and photo links.",
                    "parent": "menu",
                    "size": "s",
                },
                {
                    "key": "page",
                    "kind": "task",
                    "title": "Menu page with photos",
                    "parent": "menu",
                    "size": "m",
                },
                {
                    "key": "allergens",
                    "kind": "task",
                    "title": "Allergen labels on each bake",
                    "parent": "menu",
                    "size": "xs",
                },
                {
                    "key": "capacity",
                    "kind": "task",
                    "title": "Slot capacity rules",
                    "body": "Count the orders in each window and close it when it is full.",
                    "parent": "slots",
                    "priority": "high",
                    "target_on": in_days(3),
                    "size": "m",
                },
                {
                    "key": "picker",
                    "kind": "task",
                    "title": "Slot picker on the order page",
                    "parent": "slots",
                    "size": "l",
                },
                {
                    "key": "totals",
                    "kind": "task",
                    "title": "Basket and totals",
                    "body": "Keep the basket in the browser and total it with tax included.",
                    "parent": "basket",
                    "priority": "high",
                    "target_on": in_days(5),
                    "size": "s",
                },
                {
                    "key": "redirect",
                    "kind": "task",
                    "title": "Redirect to the hosted checkout page",
                    "parent": "pay",
                    "size": "m",
                },
            ],
            links=[
                {"source": "page", "target": "list", "link_type": "needs_first"},
                {"source": "allergens", "target": "list", "link_type": "needs_first"},
                {"source": "picker", "target": "capacity", "link_type": "needs_first"},
                {"source": "redirect", "target": "totals", "link_type": "needs_first"},
                {"source": "pay", "target": "basket", "link_type": "needs_first"},
                {"source": "slots", "target": "menu", "link_type": "relates_to"},
            ],
            rules=[
                {
                    "title": "Every bake lists its allergens",
                    "body": "A customer can see the allergens of a bake before adding it to "
                    "the basket.",
                    "node": "menu",
                },
                {
                    "title": "A window closes when it is full",
                    "body": "A pickup window also closes 30 minutes before it starts.",
                    "node": "slots",
                },
                {
                    "title": "The pickup time is shown before payment",
                    "body": "The customer sees the day and time they chose on the last "
                    "screen before they pay.",
                    "node": "basket",
                },
                {
                    "title": "No card numbers reach the shop",
                    "body": "Card details are entered only on the payment provider's page.",
                    "node": "pay",
                },
            ],
        )
        made = {item["key"]: str(item["ref"]) for item in plan["created"]}
        rules = [int(rule) for rule in plan["rule_ids"]]
        await call(
            client,
            "link_note",
            note_id=hosted["id"],
            nodes=[made["pay"], made["redirect"]],
            rule_ids=[rules[3]],
        )
        await call(
            client,
            "link_note",
            note_id=slots["id"],
            nodes=[made["slots"], made["capacity"]],
            rule_ids=[rules[1]],
        )
        capacity = await call(
            client,
            "add_note",
            text="The kitchen can prepare at most 12 orders in each 15 minute window.",
            why="The head baker, in the planning call on 4 November.",
            nodes=[made["slots"], made["capacity"]],
            note_ids=[slots["id"]],
        )
        await call(
            client,
            "add_note",
            text="Most customers look at the shop's website on a phone.",
            why="The shop's analytics export for October.",
            nodes=[made["menu"], made["page"]],
        )
        # Only a person can confirm, so these go through the board as the signed-in owner.
        for note, reviewer, quote in (
            (slots, OWNER, "Agreed in the planning call."),
            (capacity, SHOP_MANAGER, "Checked with the kitchen on 5 November."),
        ):
            confirmed = person.call(
                "POST",
                f"/board/api/projects/{project_id}/tool/set_confirmation",
                {
                    "note_id": note["id"],
                    "confirmation": "confirmed",
                    "person": reviewer,
                    "quote": quote,
                },
            )
            if confirmed.status != 200:
                raise Stopped(f"The confirmation was refused: {confirmed.body}")

        # The agent's work as the guide describes it: take the next free piece of work, then end
        # it with a handoff note. Two tasks end done, one blocked, and the last stays under way.
        async def take(story: str, expected: str, lease_seconds: int = 900) -> None:
            work = await call(
                client,
                "next_work",
                within_node=made[story],
                lease_seconds=lease_seconds,
            )
            if work["work"] is None or work["work"]["ref"] != made[expected]:
                raise Stopped(f"The agent was handed something other than {expected}: {work}")

        async def end(task: str, outcome: str, note: str) -> None:
            await call(
                client,
                "finish_work",
                node=made[task],
                outcome=outcome,
                note=note,
            )

        await take("menu", "list")
        await end(
            "list",
            "done",
            "GET /menu returns the day's bakes with prices in pence and a photo link. "
            "The menu page can use it as it is.",
        )
        await take("menu", "page")
        await end(
            "page",
            "done",
            "The page lists every bake with its photo and price. Most visitors are on phones, "
            "so it is one column.",
        )
        await take("menu", "allergens")
        await end(
            "allergens",
            "blocked",
            "The shop has not sent the allergen list for the new autumn bakes yet. The label "
            "component is ready and waits for that data.",
        )
        await take("slots", "capacity", lease_seconds=3600)
        return {"note": int(slots["id"])}


async def settle(page: Page) -> None:
    """Waits until the board has drawn the project and the news about it has been read."""
    await page.wait_for_selector("#sidebar:not([hidden])")
    await page.wait_for_selector("#epic-list li[role=treeitem]")
    await page.wait_for_load_state("networkidle")
    await page.evaluate("document.fonts.ready")


async def capture(base: str, person: Person, project_id: int, ids: dict[str, int]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(
                viewport=VIEWPORT,  # type: ignore[arg-type]
                color_scheme="light",
                reduced_motion="reduce",
            )
            await context.add_cookies(
                [
                    {
                        "name": SESSION_COOKIE,
                        "value": person.cookie,
                        "domain": urlparse(base).hostname or "127.0.0.1",
                        "path": "/board",
                        "httpOnly": True,
                        "sameSite": "Strict",
                    }
                ]
            )
            await context.add_init_script("localStorage.setItem('vrum.theme', 'light');")
            page = await context.new_page()
            board = f"{base}/board/p/{project_id}"

            await page.goto(f"{board}#/board")
            await settle(page)
            # The embedding service indexes the work in the background after it is written; wait
            # until it is done, so the pictures show the settled board and the filled meaning list.
            await page.wait_for_selector("#index-state", state="hidden", timeout=180_000)
            await page.screenshot(path=OUT / "board.png")

            chosen = f"note:{ids['note']}"
            await page.goto(f"{board}#/graph?tab=around&focus={chosen}")
            await settle(page)
            await page.wait_for_selector(f"#graph-hub[data-ref='{chosen}']")
            await page.wait_for_selector("#graph-around-loading", state="detached")
            await page.wait_for_timeout(500)
            # Nothing has the focus in the picture, so no ring is drawn anywhere.
            await page.evaluate(
                "document.activeElement instanceof HTMLElement && document.activeElement.blur()"
            )
            await page.screenshot(path=OUT / "graph.png")
        finally:
            await browser.close()


async def main(url: str) -> None:
    host = urlparse(url).hostname
    if host not in ("127.0.0.1", "localhost", "::1") or not url.endswith("/mcp"):
        raise Stopped(
            "Give the MCP address of a throwaway stack on this machine, such as "
            "http://127.0.0.1:20000/mcp."
        )
    base = url.removesuffix("/mcp")
    person = sign_up(base)
    made = person.call("POST", "/board/api/projects", {"name": PROJECT})
    if made.status != 201:
        raise Stopped(f"The project was refused: {made.body}")
    project_id = int(made.body["id"])
    key = person.call("POST", f"/board/api/projects/{project_id}/keys", {"name": AGENT})
    if key.status != 201:
        raise Stopped(f"The key was refused: {key.body}")
    ids = await populate(url, key.body["secret"], person, project_id)
    await capture(base, person, project_id, ids)
    for name in ("board.png", "graph.png"):
        size = (OUT / name).stat().st_size
        print(f"{OUT / name}: {size // 1024} KB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1]))
