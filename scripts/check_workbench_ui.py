"""Offline browser regression checks; installs no server or shared workspace.

Run with PYTHONPATH=. python scripts/check_workbench_ui.py --chromium /path/to/chrome.
Requires the optional Playwright package and an installed Chromium browser.
"""

import argparse
import io
import json
import tempfile
import threading
from pathlib import Path

from openpyxl import Workbook
from playwright.sync_api import expect, sync_playwright

from workiva_mcp.workbench import Workbench
from workiva_mcp.workbench_server import create_server


def check(page, wb, url, screenshots):
    errors = []
    external = []

    def local_only(route):
        if route.request.url.startswith(url + "/"):
            route.continue_()
        else:
            external.append(route.request.url)
            route.abort()

    page.route("**/*", local_only)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.locator("#demo-v1").click()
    page.get_by_role("tab", name="Mappings", exact=True).click()
    expect(page.locator("#sheet-grid button")).to_have_count(160)
    page.locator("#add-rule").click()
    page.locator('[name="description"]').fill("Visual cash reconciliation")
    page.locator('#sheet-grid [data-cell="B2"]').click()
    page.locator("#mapping-target").select_option("supporting:0")
    page.locator("#sheet-select").select_option("Support")
    expect(page.locator("#preview-status")).to_contain_text("Support: rows")
    page.locator('#sheet-grid [data-cell="B2"]').click()
    page.locator(".add-support").click()
    page.locator("#mapping-target").select_option("supporting:1")
    page.locator('#sheet-grid [data-cell="B3"]').click()
    # Changing views/rules must not discard the unsaved description or references.
    page.locator("#rule-select").select_option("cash")
    page.locator("#rule-select").select_option("rule-5")
    expect(page.locator('[name="description"]')).to_have_value("Visual cash reconciliation")
    expect(page.locator("#confirm-form button")).to_be_disabled()
    page.get_by_role("button", name="Save all mapping drafts", exact=True).click()
    expect(page.locator("#mapping-draft-state")).to_have_text("Draft matches the saved mappings")
    rule = wb.state()["rules"][-1]
    assert rule["reported"] == {"sheet": "Report", "cell": "B2", "scale": "units"}
    assert [(ref["sheet"], ref["cell"]) for ref in rule["supporting"]] == [
        ("Support", "B2"),
        ("Support", "B3"),
    ]
    page.locator("#mapping-reviewer").fill("Synthetic reviewer")
    page.get_by_role("button", name="Confirm mappings", exact=True).click()
    expect(page.locator("#confirmation-state")).to_have_text("Confirmed")
    page.get_by_role("tab", name="Overview / Findings", exact=True).click()
    page.locator("#run-checks").click()
    expect(page.locator('[data-rule-id="rule-5"]')).to_contain_text("passed")
    assert wb.state()["findings"][-1]["difference"] == "0.00"
    page.get_by_role("tab", name="Mappings", exact=True).click()

    # Exercise the sign control with an asymmetric expectation: 1m - (600k - 400k).
    page.locator('[data-index="1"] [name="sign"]').select_option("subtract")
    page.get_by_role("button", name="Save all mapping drafts", exact=True).click()
    expect(page.locator("#mapping-draft-state")).to_have_text("Draft matches the saved mappings")
    page.get_by_role("button", name="Confirm mappings", exact=True).click()
    expect(page.locator("#confirmation-state")).to_have_text("Confirmed")
    page.get_by_role("tab", name="Overview / Findings", exact=True).click()
    page.locator("#run-checks").click()
    expect(page.locator('[data-rule-id="rule-5"]')).to_contain_text("failed")
    assert wb.state()["findings"][-1]["difference"] == "800000.00"
    page.get_by_role("tab", name="Mappings", exact=True).click()

    # A concurrent revision must not silently overwrite a retained draft.
    page.locator('[name="description"]').fill("Retained local description")
    other = wb.state()
    wb.save_rules(other["rules"], other["revision"])
    page.get_by_role("button", name="Save all mapping drafts", exact=True).click()
    expect(page.locator("#rebase-draft")).to_be_visible()
    expect(page.locator('[name="description"]')).to_have_value("Retained local description")
    assert wb.state()["rules"][-1]["description"] == "Visual cash reconciliation"
    page.get_by_role("button", name="Save all mapping drafts", exact=True).click()
    assert wb.state()["rules"][-1]["description"] == "Visual cash reconciliation"
    page.locator("#rebase-draft").click()
    page.get_by_role("button", name="Save all mapping drafts", exact=True).click()
    expect(page.locator("#mapping-draft-state")).to_have_text("Draft matches the saved mappings")
    assert wb.state()["rules"][-1]["description"] == "Retained local description"

    # Structurally invalid JSON must remain editable without breaking the UI.
    page.locator("#advanced-rules summary").click()
    page.locator("#rules-json").fill("[null]")
    page.locator("#apply-json").click()
    expect(page.locator("#alert")).to_contain_text("Each rule needs")
    expect(page.locator("#add-rule")).to_be_disabled()
    page.locator("#discard-json").click()
    expect(page.locator("#add-rule")).to_be_enabled()
    page.locator("#advanced-rules summary").click()

    # Formula evidence stays literal, and absent caches are not zero.
    page.locator("#sheet-select").select_option("Report")
    expect(page.locator("#preview-status")).to_contain_text("Report: rows")
    # Hold one old response and deliver it after a newer sheet selection.
    held = []

    def hold_support(route):
        held.append(route)

    page.route("**/api/preview*sheet=Support*", hold_support)
    page.locator("#sheet-select").select_option("Support")
    expect(page.locator("#sheet-grid button")).to_have_count(0)
    page.locator("#sheet-select").select_option("Report")
    expect(page.locator("#preview-status")).to_contain_text("Report: rows")
    assert len(held) == 1
    held[0].fulfill(json=wb.preview(wb.state()["revision"], "Support"))
    page.unroute("**/api/preview*sheet=Support*", hold_support)
    page.locator('#sheet-grid [data-cell="B6"]').click()
    expect(page.locator("#cell-inspector")).to_contain_text("=SUM(Support!B2:B3)")
    expect(page.locator("#cell-inspector")).to_contain_text("formula cached result unavailable")
    # Page at the extreme Excel edge cannot overflow valid coordinates.
    page.locator("#cell-jump").fill("XFD1048576")
    page.locator("#cell-jump-form button").click()
    expect(page.locator('#sheet-grid [data-cell="XFD1048576"]')).to_have_count(1)
    page.locator("#cell-jump").fill("A1")
    page.locator("#cell-jump-form button").click()
    expect(page.locator('#sheet-grid [data-cell="B2"]')).to_have_count(1)
    page.locator('#sheet-grid [data-cell="B2"]').click()
    page.locator("#rule-select").select_option("cash")
    page.evaluate("document.querySelector('.mapping-editor').scrollIntoView()")
    if screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / "mapping-desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator(".sheet-panel").scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(page.locator("#sheet-select")).to_be_visible()
    if screenshots:
        page.screenshot(path=str(screenshots / "mapping-narrow.png"))
    # A replacement workbook may have no previous sheet names. The preview must
    # reset, preserve literal text safely, and distinguish zero from a blank.
    book = Workbook()
    book.active.title = "Different sheet"
    book.active["A1"] = '<img src=x onerror="window.injected=true">'
    book.active["B2"] = 0
    content = io.BytesIO()
    book.save(content)
    wb.import_workbook(
        content.getvalue(), "synthetic.xlsx", "Different sheets", wb.state()["revision"]
    )
    page.locator("#refresh-mappings").click()
    expect(page.locator("#preview-status")).to_contain_text("Different sheet: rows")
    expect(page.locator('#sheet-grid [data-cell="B2"]')).to_have_text("0")
    expect(page.locator('#sheet-grid [data-cell="B3"]')).to_have_text("")
    expect(page.locator("#sheet-grid img")).to_have_count(0)
    assert page.evaluate("window.injected === undefined")
    assert not external, external
    assert not errors, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chromium", help="Installed Chromium executable (otherwise Playwright default)"
    )
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="workbench-ui-") as directory:
        root = Path(directory)
        server = create_server(root, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.chromium, headless=True)
                try:
                    page = browser.new_page(
                        viewport={"width": 1440, "height": 1100}, device_scale_factor=2
                    )
                    check(
                        page,
                        Workbench(root),
                        f"http://127.0.0.1:{server.server_port}",
                        args.screenshots,
                    )
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print(json.dumps({"status": "WORKBENCH_UI_PASS", "data": "synthetic", "external_calls": 0}))


if __name__ == "__main__":
    main()
