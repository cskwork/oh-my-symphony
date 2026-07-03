#!/usr/bin/env python3
"""Real-browser acceptance gate for static todo app candidates."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import Page, async_playwright


WAIT_MS = 5_000


async def _first_visible(page: Page, selectors: list[str]):
    for selector in selectors:
        loc = page.locator(selector)
        for index in range(await loc.count()):
            item = loc.nth(index)
            if await item.is_visible():
                return item
    raise AssertionError(f"no visible selector found from {selectors}")


async def _click_named(page: Page, names: list[str]) -> None:
    for name in names:
        pattern = re.compile(name, re.I)
        button = page.get_by_role("button", name=pattern)
        if await button.count():
            await button.first.click()
            return
        text = page.get_by_text(pattern)
        if await text.count():
            await text.first.click()
            return
    raise AssertionError(f"no clickable control found for {names}")


async def _add(page: Page, text: str, *, via_enter: bool) -> None:
    field = await _first_visible(
        page,
        [
            "input[type=text]",
            "input:not([type])",
            "textarea",
            "[contenteditable=true]",
        ],
    )
    await field.fill(text)
    if via_enter:
        await field.press("Enter")
    else:
        await _click_named(page, ["add", "create", "new"])
    await page.get_by_text(text, exact=False).first.wait_for(
        state="visible", timeout=WAIT_MS
    )


async def _todo_row(page: Page, text: str):
    for selector in ("li", "[role='listitem']", "[data-id]", ".todo-item", ".todo"):
        rows = page.locator(selector).filter(has_text=text)
        for index in range(await rows.count()):
            row = rows.nth(index)
            if await row.is_visible():
                return row
    item = page.get_by_text(text, exact=False).first
    await item.wait_for(state="visible", timeout=WAIT_MS)
    return item


async def _toggle(page: Page, text: str) -> None:
    row = await _todo_row(page, text)
    checkbox = row.locator("input[type=checkbox]")
    if await checkbox.count():
        await checkbox.first.click()
    else:
        await row.click()
    decoration = await page.get_by_text(text, exact=False).first.evaluate(
        "(node) => getComputedStyle(node).textDecorationLine"
    )
    html = await row.evaluate("(node) => node.outerHTML")
    if "line-through" not in decoration and not re.search(
        r"completed|checked|done|true", html, re.I
    ):
        raise AssertionError("toggle did not expose completed state")


async def _delete(page: Page, text: str) -> None:
    row = await _todo_row(page, text)
    for pattern in ("delete", "remove", "x", "×"):
        control = row.get_by_role("button", name=re.compile(pattern, re.I))
        if await control.count():
            await control.first.click()
            await page.get_by_text(text, exact=False).first.wait_for(
                state="detached", timeout=WAIT_MS
            )
            return
    buttons = row.locator("button")
    if await buttons.count():
        await buttons.last.click()
        await page.get_by_text(text, exact=False).first.wait_for(
            state="detached", timeout=WAIT_MS
        )
        return
    raise AssertionError("no delete control found")


async def _open_edit(page: Page, text: str):
    row = await _todo_row(page, text)
    candidates = [
        row.locator("[data-action='edit']").filter(has_text=text),
        row.locator(".todo-title").filter(has_text=text),
        row.locator(".todo-label").filter(has_text=text),
        row.locator(".todo-item__text").filter(has_text=text),
        row.locator("label").filter(has_text=text),
        row.get_by_text(text, exact=False),
    ]
    edit_selector = (
        "[data-role='edit-input'], .todo-item__edit, .edit-input, input.edit, "
        "input[aria-label^='Edit'], textarea[aria-label^='Edit'], "
        "[contenteditable=true]"
    )
    for candidate in candidates:
        if not await candidate.count():
            continue
        target = candidate.first
        for action in (target.dblclick, target.click):
            await action()
            edit = page.locator(edit_selector).first
            try:
                await edit.wait_for(state="visible", timeout=1_000)
                return edit
            except Exception:
                continue
    raise AssertionError(f"no inline-edit target found for {text!r}")


async def _edit_enter(page: Page, original: str, updated: str) -> None:
    edit = await _open_edit(page, original)
    await edit.fill(updated)
    await edit.press("Enter")
    await page.get_by_text(updated, exact=False).first.wait_for(
        state="visible", timeout=WAIT_MS
    )


async def _edit_escape(page: Page, original: str, draft: str) -> None:
    edit = await _open_edit(page, original)
    await edit.fill(draft)
    await edit.press("Escape")
    await page.get_by_text(original, exact=False).first.wait_for(
        state="visible", timeout=WAIT_MS
    )
    if await page.get_by_text(draft, exact=False).count():
        raise AssertionError("Escape did not cancel inline edit draft")


async def _edit_empty_deletes(page: Page, text: str) -> None:
    edit = await _open_edit(page, text)
    await edit.fill("")
    await edit.press("Enter")
    await page.get_by_text(text, exact=False).first.wait_for(
        state="detached", timeout=WAIT_MS
    )


async def _assert_booted(page: Page, errors: list[str]) -> None:
    await page.wait_for_timeout(300)
    if errors:
        raise AssertionError("browser boot errors: " + " | ".join(errors[:3]))
    body = await page.locator("body").inner_text()
    if not re.search(r"empty|no todos?|nothing|start|add", body, re.I):
        raise AssertionError("empty-state text was not visible")


async def check_app(app_dir: Path) -> None:
    index = app_dir / "index.html"
    if not index.exists():
        raise AssertionError(f"{index} missing")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        await page.goto(index.resolve().as_uri(), wait_until="networkidle")
        await page.evaluate("localStorage.clear()")
        await page.reload(wait_until="networkidle")
        await _assert_booted(page, errors)

        await _add(page, "alpha task", via_enter=True)
        await _add(page, "beta task", via_enter=False)
        body = await page.locator("body").inner_text()
        if not re.search(
            r"2\s+active|active\s*[:(]?\s*2|2\s+items?\s+left|"
            r"2\s+item\(s\)\s+left|2\s+remaining",
            body,
            re.I,
        ):
            raise AssertionError("active count did not show two active todos")

        await _toggle(page, "alpha task")
        await _click_named(page, ["completed"])
        completed = await page.locator("body").inner_text()
        if "alpha task" not in completed or "beta task" in completed:
            raise AssertionError("completed filter did not isolate completed todo")
        await _click_named(page, ["active"])
        active = await page.locator("body").inner_text()
        if "beta task" not in active or "alpha task" in active:
            raise AssertionError("active filter did not isolate active todo")

        await _click_named(page, ["all"])
        await _edit_enter(page, "beta task", "beta edited")
        await _edit_escape(page, "beta edited", "discarded draft")
        await _click_named(page, ["active"])
        await page.reload(wait_until="networkidle")
        reloaded = await page.locator("body").inner_text()
        if "beta edited" not in reloaded or "alpha task" in reloaded:
            raise AssertionError("todos or selected filter did not persist")
        await _click_named(page, ["all"])
        await _edit_empty_deletes(page, "beta edited")
        await _click_named(page, ["clear completed", "clear"])
        if await page.get_by_text("alpha task", exact=False).count():
            raise AssertionError("clear completed did not remove completed todo")
        await _add(page, "delete me", via_enter=False)
        await _delete(page, "delete me")
        await browser.close()


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_dir", nargs="+", type=Path)
    args = parser.parse_args(argv[1:])

    failures: list[str] = []
    for app_dir in args.app_dir:
        try:
            await check_app(app_dir)
            print(f"PASS {app_dir}")
        except Exception as exc:
            failures.append(f"FAIL {app_dir}: {exc}")
            print(failures[-1])
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
