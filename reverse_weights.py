#!/usr/bin/env python3
"""
Reverse-engineer per-question faction weights for the IDRlabs
Warhammer 40k Factions Test.

For each question i in 1..40:
  - answer i with Agree (4 / Strongly Agree)
  - answer all other questions Neutral (2)
  - collect final faction percentages

Default mode injects answers via the same cookie the site uses, then
calls TEST.finish() — same scoring path as the UI, much more stable.

UI mode (--mode ui) walks the slider + NEXT buttons for visual debugging.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Page, async_playwright

TEST_URL = "https://www.idrlabs.com/warhammer-40k-factions/test.php"
NUM_QUESTIONS = 40

# Order of scores in graphic ?p= and friend share URLs
FACTION_ORDER = [
    "Imperium of Man",
    "Chaos",
    "Eldar",
    "Dark Eldar",
    "Orks",
    "Tyranids",
    "Necrons",
    "T’au Empire",
]

AGREE = "4"
NEUTRAL = "2"
GRAPHIC_P_RE = re.compile(r"[?&]p=([\d.,]+)")
SCORES_IN_PATH_RE = re.compile(r"/([\d.]+(?:-[\d.]+){7})/")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def log(msg: str) -> None:
    print(msg, flush=True)


def scores_to_dict(values: list[float]) -> dict[str, float]:
    if len(values) != len(FACTION_ORDER):
        raise ValueError(f"Expected {len(FACTION_ORDER)} scores, got {len(values)}: {values}")
    return {name: values[i] for i, name in enumerate(FACTION_ORDER)}


async def wait_for_test_ready(page: Page) -> None:
    await page.wait_for_function(
        """() => window.TEST
            && Array.isArray(TEST.questions)
            && TEST.questions.length === 40
            && typeof window.jQuery === 'function'
            && typeof TEST.finish === 'function'
            && typeof TEST.cookie === 'function'""",
        timeout=60000,
    )


async def get_questions(page: Page) -> list[dict[str, Any]]:
    return await page.evaluate(
        """() => TEST.questions.map((q, i) => ({
            position: i + 1,
            id: q.id,
            text: q.text,
        }))"""
    )


async def open_fresh_test(page: Page) -> list[dict[str, Any]]:
    await page.context.clear_cookies()
    await page.goto(TEST_URL, wait_until="domcontentloaded", timeout=60000)
    await wait_for_test_ready(page)
    # Drop any leftover answer cookies / in-memory answers
    await page.evaluate(
        """() => {
            try { localStorage.clear(); } catch (e) {}
            try { sessionStorage.clear(); } catch (e) {}
            document.cookie.split(';').forEach((c) => {
                const name = c.split('=')[0].trim();
                if (name.startsWith('answers-') || name.startsWith('qsort-')) {
                    document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:01 GMT; path=/; domain=www.idrlabs.com';
                    document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:01 GMT; path=/';
                }
            });
            TEST.answers = {};
        }"""
    )
    await page.goto(TEST_URL, wait_until="domcontentloaded", timeout=60000)
    await wait_for_test_ready(page)
    return await get_questions(page)


async def extract_results(page: Page) -> dict[str, float]:
    # Own results often stay on test.php — prefer chart querystring
    await page.wait_for_selector(
        "#test .graph img[src*='p='], #test img[src*='graphic/warhammer'][src*='p='], "
        "#test:has-text('Results')",
        timeout=30000,
    )

    for sel in (
        "#test .graph img[src*='p=']",
        "#test img[src*='graphic/warhammer'][src*='p=']",
        "img[src*='warhammer-40k-factions'][src*='p=']",
    ):
        loc = page.locator(sel).first
        if await loc.count():
            src = await loc.get_attribute("src") or ""
            m = GRAPHIC_P_RE.search(src)
            if m:
                return scores_to_dict([float(x) for x in m.group(1).split(",")])

    m = SCORES_IN_PATH_RE.search(page.url)
    if m:
        return scores_to_dict([float(x) for x in m.group(1).split("-")])

    text = await page.locator("#test").inner_text()
    raise RuntimeError(f"Could not parse scores from {page.url}\n{text[:500]}")


async def run_probe_fast(
    page: Page,
    *,
    probe_question_id: int | None,
    catalog: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, float]]:
    """
    Probe by stable question id (not shuffled UI position).
    probe_question_id=None → all-neutral baseline.
    """
    await open_fresh_test(page)

    await page.evaluate(
        """({probeId, agree, neutral, allIds}) => {
            const answers = {};
            for (const id of allIds) {
                answers[id] = (probeId !== null && Number(id) === Number(probeId)) ? agree : neutral;
            }
            TEST.answers = answers;
            TEST.cookie(
                'answers-' + TEST.test_main_id + TEST.locale + 'v1',
                JSON.stringify(answers),
                TEST.WEEK_IN_SECONDS
            );
        }""",
        {
            "probeId": probe_question_id,
            "agree": AGREE,
            "neutral": NEUTRAL,
            "allIds": [q["id"] for q in catalog],
        },
    )

    await page.goto(f"{TEST_URL}?finish", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_load_state("domcontentloaded")
    scores = await extract_results(page)

    probe_q = None
    if probe_question_id is not None:
        probe_q = next(q for q in catalog if q["id"] == probe_question_id)

    info = {
        "probe_question_id": probe_question_id,
        "probe_question_text": None if probe_q is None else probe_q["text"],
        "probe_position": None if probe_q is None else probe_q["position"],
        "questions": catalog,
        "result_url": page.url,
    }
    return info, scores


async def set_answer_ui(page: Page, value: str) -> None:
    await page.evaluate(
        """(value) => {
            const $ = jQuery;
            $('input[name=answer]').val(String(value)).trigger('change');
            TEST.answers[TEST.current_question.id] = String(value);
        }""",
        value,
    )


async def click_next_ui(page: Page) -> None:
    await page.wait_for_function(
        """() => {
            const el = document.querySelector('.qnav.next');
            return el && !el.classList.contains('disabled');
        }""",
        timeout=15000,
    )
    start_before = await page.evaluate("() => TEST.start_at")
    advanced = await page.evaluate(
        """() => {
            const $ = jQuery;
            const val = $('input[name=answer]').val();
            if (val === '' || val == null) return false;
            TEST.answers[TEST.current_question.id] = String(val);
            $('.qnav.next').trigger('click');
            return true;
        }"""
    )
    if not advanced:
        raise RuntimeError("NEXT click skipped: empty answer value")
    # Last question navigates away via TEST.finish(); earlier ones bump start_at
    try:
        await page.wait_for_function(
            """(prev) => !window.TEST || TEST.start_at > prev || location.search.includes('finish') || document.body.innerText.includes('Results')""",
            arg=start_before,
            timeout=15000,
        )
    except Exception:
        # Fallback: native click on the text label
        await page.locator(".qnav.next .text").click(force=True)
        await page.wait_for_function(
            """(prev) => !window.TEST || TEST.start_at > prev || document.body.innerText.includes('Results')""",
            arg=start_before,
            timeout=15000,
        )


async def run_probe_ui(
    page: Page,
    *,
    probe_question_id: int | None,
    catalog: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, float]]:
    """Walk UI; Agree only when current question id matches probe_question_id."""
    await open_fresh_test(page)
    await page.wait_for_selector(".qcont .qnum", timeout=30000)

    for pos in range(1, NUM_QUESTIONS + 1):
        await page.wait_for_function(
            """(n) => document.querySelector('.qcont .qnum')?.textContent.trim() === String(n)""",
            arg=pos,
            timeout=30000,
        )
        current_id = await page.evaluate("() => TEST.current_question.id")
        if probe_question_id is None:
            value = NEUTRAL
        else:
            value = AGREE if current_id == probe_question_id else NEUTRAL
        await set_answer_ui(page, value)

        if pos < NUM_QUESTIONS:
            await click_next_ui(page)
        else:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
                await click_next_ui(page)

    scores = await extract_results(page)
    probe_q = None
    if probe_question_id is not None:
        probe_q = next(q for q in catalog if q["id"] == probe_question_id)

    info = {
        "probe_question_id": probe_question_id,
        "probe_question_text": None if probe_q is None else probe_q["text"],
        "probe_position": None if probe_q is None else probe_q["position"],
        "questions": catalog,
        "result_url": page.url,
    }
    return info, scores


async def save_outputs(
    out_dir: Path,
    probes: dict[str, Any],
    baseline: dict[str, float] | None,
    *,
    all_probes_blob: dict[str, Any] | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    compact: dict[str, dict[str, float]] = {}
    for key, payload in probes.items():
        if key.startswith("question_") and isinstance(payload, dict) and "scores" in payload:
            compact[key] = payload["scores"]

    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps(compact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"[+] Wrote {results_path}")

    detailed = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_url": TEST_URL,
        "scale": {"disagree": 0, "neutral": int(NEUTRAL), "agree": int(AGREE)},
        "faction_order": FACTION_ORDER,
        "question_catalog": catalog,
        "baseline_all_neutral": baseline,
        "probes": all_probes_blob if all_probes_blob is not None else probes,
    }
    detailed_path = out_dir / "results_detailed.json"
    detailed_path.write_text(json.dumps(detailed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"[+] Wrote {detailed_path}")

    csv_path = out_dir / "weights.csv"
    fieldnames = ["question_key", "position", "question_id", "question_text", *FACTION_ORDER]
    if baseline:
        fieldnames.extend([f"delta_{f}" for f in FACTION_ORDER])

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for i in range(1, NUM_QUESTIONS + 1):
            key = f"question_{i}"
            payload = probes.get(key)
            if not payload or "scores" not in payload:
                continue
            row: dict[str, Any] = {
                "question_key": key,
                "position": payload.get("position", i),
                "question_id": payload.get("question_id"),
                "question_text": payload.get("question_text"),
            }
            for f in FACTION_ORDER:
                row[f] = payload["scores"][f]
            if baseline:
                for f in FACTION_ORDER:
                    row[f"delta_{f}"] = round(payload["scores"][f] - baseline[f], 4)
            writer.writerow(row)
    log(f"[+] Wrote {csv_path}")


async def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.output).resolve()
    start, end = args.start, args.end
    if not (1 <= start <= end <= NUM_QUESTIONS):
        log(f"[!] Invalid range: start={start} end={end} (question ids 1..{NUM_QUESTIONS})")
        return 1

    run_probe = run_probe_fast if args.mode == "fast" else run_probe_ui
    results: dict[str, Any] = {}
    detailed_path = out_dir / "results_detailed.json"
    baseline: dict[str, float] | None = None
    catalog: list[dict[str, Any]] | None = None

    if args.resume and detailed_path.exists():
        prior = json.loads(detailed_path.read_text(encoding="utf-8"))
        results = prior.get("probes", {})
        baseline = prior.get("baseline_all_neutral")
        catalog = prior.get("question_catalog")
        log(f"[+] Resumed {sum(1 for k in results if k.startswith('question_'))} probe(s)")

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=args.headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
            locale="en-US",
        )
        page = await context.new_page()
        page.set_default_timeout(30000)

        if catalog is None:
            log("[+] Loading question catalog...")
            raw = await open_fresh_test(page)
            # Normalize: stable catalog sorted by id; position = id for output keys
            catalog = sorted(
                [{"id": q["id"], "text": q["text"], "position": q["id"]} for q in raw],
                key=lambda q: q["id"],
            )
            if [q["id"] for q in catalog] != list(range(1, NUM_QUESTIONS + 1)):
                log(f"[!] Unexpected question ids: {[q['id'] for q in catalog]}")
            log(f"[+] Catalog loaded ({len(catalog)} questions)")

        if args.baseline and baseline is None:
            log("[+] Running all-neutral baseline...")
            info, scores = await run_probe(page, probe_question_id=None, catalog=catalog)
            baseline = scores
            results["baseline"] = {"scores": scores, "result_url": info["result_url"]}
            log(f"[+] Baseline done: {scores}")

        target_ids = [qid for qid in range(start, end + 1)]
        for qid in target_ids:
            key = f"question_{qid}"
            if args.resume and key in results and "scores" in results[key]:
                log(f"[+] Skipping {key} (already present)")
                continue

            qmeta = next(q for q in catalog if q["id"] == qid)
            log(f"[+] Processing question {qid}/{NUM_QUESTIONS}: {qmeta['text'][:60]}...")
            try:
                info, scores = await run_probe(page, probe_question_id=qid, catalog=catalog)
                results[key] = {
                    "position": qid,
                    "question_id": qid,
                    "question_text": qmeta["text"],
                    "scores": scores,
                    "result_url": info["result_url"],
                }
                await save_outputs(
                    out_dir,
                    {k: v for k, v in results.items() if k.startswith("question_")},
                    baseline,
                    all_probes_blob=results,
                    catalog=catalog,
                )
                top = max(scores, key=scores.get)
                log(f"[+] Done. top={top} ({scores[top]}%)")
            except Exception as exc:
                log(f"[!] FAILED: {exc}")
                results[key] = {"position": qid, "question_id": qid, "error": str(exc)}
                if args.stop_on_error:
                    await browser.close()
                    return 1

        await browser.close()

    probe_only = {k: v for k, v in results.items() if k.startswith("question_") and "scores" in v}
    await save_outputs(
        out_dir, probe_only, baseline, all_probes_blob=results, catalog=catalog
    )
    log(f"[+] Finished. Probes complete: {len(probe_only)}/{len(target_ids)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Headless browser (default: true). Use --no-headless to watch.",
    )
    p.add_argument(
        "--mode",
        choices=("fast", "ui"),
        default="fast",
        help="fast=cookie inject + ?finish (default); ui=click through every question",
    )
    p.add_argument("--start", type=int, default=1, help="First question id (1-40)")
    p.add_argument("--end", type=int, default=NUM_QUESTIONS, help="Last question id (1-40)")
    p.add_argument("-o", "--output", default=".", help="Output directory")
    p.add_argument(
        "--baseline",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run all-neutral baseline and write deltas to CSV (default: true)",
    )
    p.add_argument("--resume", action="store_true", help="Skip probes already in results_detailed.json")
    p.add_argument("--stop-on-error", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        log("\n[!] Interrupted")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
