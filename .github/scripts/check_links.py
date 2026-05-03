"""
Link checker for UAT-BN-Guidelines.

Reads guidelines.json, checks each link, updates config.json
(maintenanceMode, guidelinesVersion, lastChecked), and emits
GitHub Actions outputs for the workflow to use in email steps.

Outputs (GITHUB_OUTPUT):
  all_pass        - "true" if every link returned 2xx/3xx, else "false"
  broken_count    - number of broken links
  config_changed  - "true" if config.json file was modified
  broken_html     - HTML table of broken links (only set when broken_count > 0)
"""

import json
import os
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import escape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDELINES_PATH = REPO_ROOT / "guidelines.json"
CONFIG_PATH = REPO_ROOT / "config.json"
TIMEOUT = 15  # seconds per request
MAX_WORKERS = 8

UA = (
    "Mozilla/5.0 (compatible; UAT-BN-Guidelines-LinkChecker/1.0; "
    "+https://github.com/johnzaccari04/UAT-BN-Guidelines)"
)


def github_output(key, value):
    """Append a key=value pair (or multiline block) to GITHUB_OUTPUT."""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        print(f"[output] {key}={value!r}")
        return
    value_str = str(value)
    with open(out, "a", encoding="utf-8") as f:
        if "\n" in value_str:
            delim = f"EOF_{key}_DELIM"
            f.write(f"{key}<<{delim}\n{value_str}\n{delim}\n")
        else:
            f.write(f"{key}={value_str}\n")


def check_link(url):
    """Return None if the URL is reachable, else an error string."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if 200 <= resp.status < 400:
                return None
            return f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"URLError: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


def bump_minor(version):
    """1.0 -> 1.1, 1.9 -> 1.10, 2.0 -> 2.1. Leaves major untouched."""
    parts = version.split(".", 1)
    if len(parts) != 2:
        return version
    major, minor = parts[0], parts[1]
    try:
        return f"{major}.{int(minor) + 1}"
    except ValueError:
        return version


def build_broken_html(broken):
    """Render the broken-links table for the email body."""
    th_style = (
        "padding:8px;border:1px solid #ddd;background:#f4f4f4;"
        "text-align:left;"
    )
    td_style = "padding:8px;border:1px solid #ddd;vertical-align:top;"
    rows = []
    for b in broken:
        link_html = (
            f'<a href="{escape(b["link"], quote=True)}">'
            f"{escape(b['link'])}</a>"
        )
        rows.append(
            "<tr>"
            f'<td style="{td_style}">{escape(b["id"])}</td>'
            f'<td style="{td_style}">{escape(b["description"])}</td>'
            f'<td style="{td_style}">{link_html}</td>'
            f'<td style="{td_style}">{escape(b["error"])}</td>'
            "</tr>"
        )
    return (
        '<table style="border-collapse:collapse;'
        'font-family:Arial,sans-serif;font-size:14px;">'
        "<thead><tr>"
        f'<th style="{th_style}">ID</th>'
        f'<th style="{th_style}">Description</th>'
        f'<th style="{th_style}">Link</th>'
        f'<th style="{th_style}">Error</th>'
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def main():
    event = os.environ.get("GITHUB_EVENT_NAME", "manual")

    guidelines = json.loads(GUIDELINES_PATH.read_text(encoding="utf-8"))
    config_text_old = CONFIG_PATH.read_text(encoding="utf-8")
    config = json.loads(config_text_old)

    items = []
    for category in guidelines:
        for item in category.get("items", []):
            items.append(
                {
                    "id": item.get("id", ""),
                    "description": item.get("description", ""),
                    "link": item.get("link", ""),
                    "category": category.get("category", ""),
                }
            )

    print(f"Event: {event}")
    print(f"Checking {len(items)} links...\n")

    broken = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_map = {pool.submit(check_link, item["link"]): item for item in items}
        for fut in as_completed(future_map):
            item = future_map[fut]
            err = fut.result()
            if err:
                broken.append({**item, "error": err})
                print(f"  BROKEN  {item['id']:14} {err:30} {item['link']}")
            else:
                print(f"  OK      {item['id']:14} {item['link']}")

    print()
    all_pass = len(broken) == 0
    print(f"Result: {'ALL PASS' if all_pass else f'{len(broken)} BROKEN'}")

    new_config = dict(config)
    new_config["maintenanceMode"] = not all_pass

    if all_pass:
        # Date in YYYY-MM-DD; rendered in browser via toLocaleDateString
        new_config["lastChecked"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if event == "push":
        new_config["guidelinesVersion"] = bump_minor(
            config.get("guidelinesVersion", "1.0")
        )

    config_text_new = json.dumps(new_config, indent=4) + "\n"
    config_changed = config_text_new != config_text_old

    if config_changed:
        CONFIG_PATH.write_text(config_text_new, encoding="utf-8")
        print(f"config.json updated:\n{config_text_new}")
    else:
        print("config.json unchanged")

    github_output("all_pass", "true" if all_pass else "false")
    github_output("broken_count", len(broken))
    github_output("config_changed", "true" if config_changed else "false")
    if broken:
        github_output("broken_html", build_broken_html(broken))


if __name__ == "__main__":
    main()
