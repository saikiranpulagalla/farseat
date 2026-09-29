#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "validation" / "reports" / "browser-e2e.json"
BASE = os.getenv("FARSEAT_E2E_BASE", "http://127.0.0.1:5173")


def git_identity() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ['git', 'status', '--porcelain'], cwd=ROOT, text=True, capture_output=True, check=True
            ).stdout.strip()
        )
        return commit, dirty
    except Exception:
        return None, None


def wait_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except Exception as exc:
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"server not ready: {url}: {last}")


def load_sample(page) -> None:
    page.goto(BASE, wait_until="networkidle")
    page.get_by_role("button", name="Try sample classroom").click()
    expect(page.get_by_text("PRESENTATION READY")).to_be_visible(timeout=20_000)
    expect(page.get_by_role("heading", name="farseat-demo.pdf")).to_be_visible()


def analyze_sample(page) -> None:
    page.get_by_role("button", name="Analyze modeled seats").click()
    expect(page.locator("#results-heading")).to_be_visible(timeout=20_000)
    expect(page.locator(".seat")).to_have_count(30)


def select_rear_seat(page, *, delayed: bool = False) -> None:
    if delayed:
        def handler(route):
            # Keep the request pending long enough to prove that the UI does not
            # replace a newer selection with an old/empty detail state.
            time.sleep(1.2)
            route.continue_()
        page.route("**/api/analyses/*/seats/*", handler)
    seat = page.locator(".seat").last
    # A synchronous route handler blocks Playwright's normal click completion.
    # Dispatching the DOM click lets this test observe the real intermediate loading
    # state while the intercepted detail response is intentionally delayed.
    if delayed:
        seat.evaluate("el => setTimeout(() => el.click(), 0)")
    else:
        seat.click()
    expect(page.get_by_role("heading", name="Row 5 · Seat 6")).to_be_visible()
    if delayed:
        expect(page.get_by_text("Loading seat details…")).to_be_visible()
        expect(page.get_by_text("No analyzed text elements below target for this seat.")).not_to_be_visible()
    expect(page.get_by_text("Loading seat details…")).not_to_be_visible(timeout=10_000)
    if delayed:
        page.unroute("**/api/analyses/*/seats/*")


def inspector_checks(page) -> None:
    rows = page.locator("button.element-row")
    if rows.count() == 0:
        raise AssertionError("rear demo seat has no affected element to inspect")
    rows.first.click()
    expect(page.locator("#slide-title")).to_be_visible()
    assert page.evaluate("document.activeElement && document.activeElement.id") == "slide-title"
    wrap = page.locator(".pdf-wrap")
    canvas = page.locator(".pdf-wrap canvas")
    expect(canvas).to_be_visible(timeout=10_000)
    box = wrap.bounding_box()
    if not box or box["width"] < 500:
        raise AssertionError(f"desktop inspector unexpectedly narrow: {box}")
    highlight = page.locator(".highlight")
    expect(highlight).to_be_visible()
    hb, wb = highlight.bounding_box(), wrap.bounding_box()
    assert hb and wb
    assert hb["x"] >= wb["x"] - 1 and hb["y"] >= wb["y"] - 1
    assert hb["x"] + hb["width"] <= wb["x"] + wb["width"] + 1
    assert hb["y"] + hb["height"] <= wb["y"] + wb["height"] + 1

    page.set_viewport_size({"width": 800, "height": 800})
    time.sleep(0.4)
    hb2, wb2 = highlight.bounding_box(), wrap.bounding_box()
    assert hb2 and wb2
    assert hb2["x"] >= wb2["x"] - 1 and hb2["x"] + hb2["width"] <= wb2["x"] + wb2["width"] + 1


def run() -> dict:
    wait_http(BASE)
    results: list[dict] = []
    with sync_playwright() as p:
        launch_args = {'headless': True, 'args': ['--no-sandbox']}
        executable = os.getenv('FARSEAT_CHROMIUM_EXECUTABLE')
        if executable:
            launch_args['executable_path'] = executable
        browser = p.chromium.launch(**launch_args)
        try:
            # Main desktop flow + deterministic sample + delayed detail integrity.
            context = browser.new_context(viewport={"width": 1200, "height": 900}, device_scale_factor=1)
            page = context.new_page()
            load_sample(page)
            # Frozen sample values must override prior/default drift.
            width_input = page.get_by_label("Usable display width (m)")
            depth_input = page.get_by_label("Room depth (m)")
            expect(width_input).to_have_value("3.2")
            expect(depth_input).to_have_value("12")
            analyze_sample(page)

            # Roving-tab keyboard behavior.
            first = page.locator(".seat").first
            first.focus(); page.keyboard.press("ArrowRight")
            active_label = page.evaluate("document.activeElement && document.activeElement.getAttribute('aria-label')")
            assert active_label and "seat 2" in active_label.lower()

            select_rear_seat(page, delayed=True)
            inspector_checks(page)
            page.get_by_role("button", name="Close").click()
            # Focus returns to the originating affected-element button.
            assert page.evaluate("document.activeElement && document.activeElement.classList.contains('element-row')") is True

            # Reset/sample must restore the frozen sample classroom.
            page.get_by_role("button", name="New analysis").click()
            load_sample(page)
            depth_input = page.get_by_label("Room depth (m)")
            depth_input.fill("3")
            page.get_by_role("button", name="New analysis").click()
            load_sample(page)
            expect(page.get_by_label("Room depth (m)")).to_have_value("12")
            context.close()
            results.append({"name": "desktop-flow-lifecycle-overlay", "status": "PASS"})

            # HiDPI backing store and mobile hit target.
            hidpi = browser.new_context(viewport={"width": 1200, "height": 900}, device_scale_factor=2)
            page2 = hidpi.new_page(); load_sample(page2); analyze_sample(page2); select_rear_seat(page2)
            rows = page2.locator("button.element-row"); assert rows.count() > 0; rows.first.click()
            expect(page2.locator(".pdf-wrap canvas")).to_be_visible(timeout=10_000)
            sizes = page2.locator(".pdf-wrap canvas").evaluate("el => ({w:el.width, css:el.getBoundingClientRect().width})")
            if sizes["w"] < sizes["css"] * 1.8:
                raise AssertionError(f"HiDPI backing store not scaled: {sizes}")
            hidpi.close()
            results.append({"name": "hidpi-render", "status": "PASS"})

            mobile = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
            page3 = mobile.new_page(); load_sample(page3); analyze_sample(page3)
            seat_box = page3.locator(".seat").first.bounding_box(); assert seat_box
            if seat_box["width"] < 44 or seat_box["height"] < 44:
                raise AssertionError(f"mobile seat hit target too small: {seat_box}")
            mobile.close()
            results.append({"name": "mobile-hit-target", "status": "PASS"})
        finally:
            browser.close()
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "status": "PASS", "checks": results}


def main() -> int:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    source_commit, source_dirty = git_identity()
    try:
        report = run()
        code = 0
    except Exception as exc:
        report = {"generated_at": datetime.now(timezone.utc).isoformat(), "status": "FAIL", "error": repr(exc)}
        code = 1
    report["source_commit"] = source_commit
    report["source_dirty"] = source_dirty
    REPORT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
