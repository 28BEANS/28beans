#!/usr/bin/env python3
"""Generate a 30-day, pixel-art GitHub contribution line graph as SVG."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


GRAPHQL_URL = "https://api.github.com/graphql"
DEFAULT_USERNAME = "28BEANS"
DEFAULT_DAYS = 30


def iso_z(day: date, end_of_day: bool = False) -> str:
    clock = time(23, 59, 59) if end_of_day else time(0, 0, 0)
    value = datetime.combine(day, clock, tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def fetch_contributions(username: str, token: str, days: int) -> list[dict[str, Any]]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    payload = json.dumps(
        {
            "query": query,
            "variables": {
                "login": username,
                "from": iso_z(start),
                "to": iso_z(end, end_of_day=True),
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{username}-retro-contribution-graph",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach the GitHub API: {error.reason}") from error

    if result.get("errors"):
        messages = "; ".join(item.get("message", "Unknown GraphQL error") for item in result["errors"])
        raise RuntimeError(messages)

    user = result.get("data", {}).get("user")
    if not user:
        raise RuntimeError(f"GitHub user {username!r} was not found")

    weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    raw_days = [day for week in weeks for day in week["contributionDays"]]
    return normalize_days(raw_days, start, end)


def normalize_days(raw_days: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    counts = {
        date.fromisoformat(str(item["date"])): max(0, int(item["contributionCount"]))
        for item in raw_days
    }
    output: list[dict[str, Any]] = []
    current = start
    while current <= end:
        output.append({"date": current.isoformat(), "contributionCount": counts.get(current, 0)})
        current += timedelta(days=1)
    return output


def load_data_file(path: Path, days: int) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    raw_days = value["days"] if isinstance(value, dict) else value
    if not isinstance(raw_days, list) or not raw_days:
        raise ValueError("The data file must contain a non-empty array of contribution days")
    ordered = sorted(raw_days, key=lambda item: item["date"])[-days:]
    end = date.fromisoformat(str(ordered[-1]["date"]))
    start = end - timedelta(days=days - 1)
    return normalize_days(ordered, start, end)


def nice_max(value: int) -> int:
    # Four equal integer intervals keep the pixel grid and its labels aligned.
    return max(4, math.ceil(value / 4) * 4)


def render_svg(username: str, days: list[dict[str, Any]]) -> str:
    width, height = 960, 300
    plot_left, plot_top = 58, 92
    plot_width, plot_height = 870, 144
    plot_bottom = plot_top + plot_height
    counts = [int(item["contributionCount"]) for item in days]
    total = sum(counts)
    peak = max(counts, default=0)
    y_max = nice_max(peak)
    x_step = plot_width / max(1, len(days) - 1)

    points: list[tuple[int, int]] = []
    for index, count in enumerate(counts):
        x = round(plot_left + index * x_step)
        raw_y = plot_bottom - (count / y_max) * plot_height
        y = round(raw_y / 4) * 4
        points.append((x, y))

    line_parts = [f"M {points[0][0]} {points[0][1]}"]
    for x, y in points[1:]:
        line_parts.append(f"H {x} V {y}")
    line_path = " ".join(line_parts)
    area_path = f"M {points[0][0]} {plot_bottom} V {points[0][1]} " + " ".join(line_parts[1:])
    area_path += f" V {plot_bottom} H {points[0][0]} Z"

    grid_lines: list[str] = []
    for index in range(5):
        y = round(plot_top + index * plot_height / 4)
        value = round(y_max * (4 - index) / 4)
        grid_lines.append(
            f'<path d="M {plot_left} {y} H {plot_left + plot_width}" class="grid"/>'
            f'<text x="{plot_left - 10}" y="{y + 4}" class="axis" text-anchor="end">{value}</text>'
        )

    date_labels: list[str] = []
    label_indexes = sorted({0, 7, 14, 21, len(days) - 1})
    for index in label_indexes:
        day = date.fromisoformat(str(days[index]["date"]))
        x = points[index][0]
        anchor = "start" if index == 0 else "end" if index == len(days) - 1 else "middle"
        date_labels.append(
            f'<path d="M {x} {plot_top} V {plot_bottom}" class="vgrid"/>'
            f'<text x="{x}" y="{plot_bottom + 24}" class="axis" text-anchor="{anchor}">{day.strftime("%b %d").upper()}</text>'
        )

    markers = []
    for (x, y), item in zip(points, days):
        label = f'{item["date"]}: {item["contributionCount"]} contributions'
        markers.append(
            f'<rect x="{x - 4}" y="{y - 4}" width="8" height="8" class="point">'
            f'<title>{html.escape(label)}</title></rect>'
        )

    start_label = date.fromisoformat(str(days[0]["date"])).strftime("%Y-%m-%d")
    end_label = date.fromisoformat(str(days[-1]["date"])).strftime("%Y-%m-%d")
    accessible = (
        f"{username} made {total} public contributions from {start_label} through {end_label}; "
        f"the busiest day had {peak}."
    )
    title = html.escape(f"{username} 30-day contribution graph")
    description = html.escape(accessible)
    safe_username = html.escape(username.upper())

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">{title}</title>
  <desc id="desc">{description}</desc>
  <defs>
    <pattern id="scanlines" width="4" height="4" patternUnits="userSpaceOnUse">
      <rect width="4" height="1" fill="#b6ff4a" opacity="0.035"/>
    </pattern>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="3" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <style>
      text {{ font-family: "Courier New", "Liberation Mono", monospace; }}
      .axis {{ fill: #7aa72e; font-size: 12px; font-weight: 700; }}
      .grid {{ stroke: #254014; stroke-width: 1; shape-rendering: crispEdges; }}
      .vgrid {{ stroke: #1b3110; stroke-width: 1; shape-rendering: crispEdges; }}
      .line {{ fill: none; stroke: #b6ff4a; stroke-width: 4; stroke-linejoin: miter; stroke-linecap: square; shape-rendering: crispEdges; }}
      .point {{ fill: #0a0d09; stroke: #e7ff9a; stroke-width: 3; shape-rendering: crispEdges; }}
    </style>
  </defs>
  <rect width="960" height="300" fill="#090c08"/>
  <path d="M 8 28 V 8 H 28 M 932 8 H 952 V 28 M 8 272 V 292 H 28 M 932 292 H 952 V 272" fill="none" stroke="#b6ff4a" stroke-width="4" shape-rendering="crispEdges"/>
  <rect x="18" y="18" width="6" height="6" fill="#b6ff4a"/>
  <rect x="28" y="18" width="6" height="6" fill="#7aa72e"/>
  <rect x="38" y="18" width="6" height="6" fill="#254014"/>
  <text x="58" y="43" fill="#e7ff9a" font-size="22" font-weight="700">{safe_username}.EXE</text>
  <text x="58" y="66" fill="#7aa72e" font-size="12" font-weight="700">30-DAY CONTRIBUTION SIGNAL // {start_label}—{end_label}</text>
  <text x="928" y="42" fill="#b6ff4a" font-size="14" font-weight="700" text-anchor="end">TOTAL {total:03d}</text>
  <text x="928" y="63" fill="#7aa72e" font-size="12" font-weight="700" text-anchor="end">PEAK {peak:02d}/DAY</text>
  {''.join(grid_lines)}
  {''.join(date_labels)}
  <path d="{area_path}" fill="#b6ff4a" opacity="0.08" shape-rendering="crispEdges"/>
  <path d="{line_path}" class="line" opacity="0.28" filter="url(#glow)"/>
  <path d="{line_path}" class="line"/>
  {''.join(markers)}
  <rect width="960" height="300" fill="url(#scanlines)" pointer-events="none"/>
  <text x="18" y="283" fill="#49651f" font-size="10">PUBLIC ACTIVITY // AUTO-REFRESH DAILY // SVG-30D</text>
</svg>
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=os.getenv("GITHUB_USERNAME", DEFAULT_USERNAME))
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--output", type=Path, default=Path("assets/contribution-graph.svg"))
    parser.add_argument("--data-file", type=Path, help="Use local JSON instead of calling GitHub (for previews/tests)")
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.days != 30:
        raise SystemExit("This design is intentionally fixed to exactly 30 days")

    try:
        if args.data_file:
            days = load_data_file(args.data_file, args.days)
        else:
            if not args.token:
                raise RuntimeError("Set GITHUB_TOKEN or pass --token to query GitHub")
            days = fetch_contributions(args.username, args.token, args.days)
        svg = render_svg(args.username, days)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8")
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Wrote {args.output} with {sum(item['contributionCount'] for item in days)} contributions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
