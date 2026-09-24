"""Capture the README screenshots and GIFs from the running app.

Needs the backend on :8000 (seeded with samples/kb) and the frontend on :3000.

    pip install playwright pillow
    python docs/capture.py            # GIFs + screenshots (runs three real audits)
    python docs/capture.py --stills   # screenshots only, reusing the last run's jobs
"""
import io
import json
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "media"
FRAMES = Path(__file__).parent / ".capture"
OUT.mkdir(parents=True, exist_ok=True)
FRAMES.mkdir(exist_ok=True)
APP = "http://localhost:3000"
CONFLICT = ROOT / "samples" / "uploads" / "logging-standard-2026.md"
CLEAN = ROOT / "samples" / "uploads" / "onboarding-checklist.md"
DUP = ROOT / "samples" / "kb" / "expense-policy.md"
VIEW = {"width": 1200, "height": 760}


class Recorder:
    def __init__(self, page):
        self.page, self.frames = page, []

    def snap(self, ms: int):
        png = self.page.screenshot()
        self.frames.append((Image.open(io.BytesIO(png)).convert("RGB"), ms))

    def hold(self, ms: int):
        self.snap(ms)

    def save(self, name: str, width: int = 960):
        imgs, durs = [], []
        for img, ms in self.frames:
            h = round(img.height * width / img.width)
            imgs.append(img.resize((width, h), Image.LANCZOS))
            durs.append(ms)
        # Shared adaptive palette keeps the near-monochrome UI clean and the file small.
        base = imgs[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
        pal = [i.quantize(palette=base, dither=Image.Dither.NONE) for i in imgs]
        pal[0].save(OUT / name, save_all=True, append_images=pal[1:], duration=durs, loop=0, optimize=True)
        print(name, len(pal), "frames", round((OUT / name).stat().st_size / 1e6, 2), "MB")


def wait_verdict(page, rec: Recorder | None, every: float = 1.0):
    progress = []
    while not page.locator(".verdict-title").count():
        if rec:
            png = page.screenshot()
            progress.append(Image.open(io.BytesIO(png)).convert("RGB"))
        time.sleep(every)
    page.wait_for_timeout(500)  # let the resolve animation finish
    if rec and progress:
        # Compress a long CPU audit into ~3.5 s of progress.
        step = max(1, len(progress) // 12)
        for img in progress[::step]:
            rec.frames.append((img, 280))


def smooth_scroll(page, rec: Recorder, to: int, steps: int = 10, ms: int = 60):
    start = page.evaluate("window.scrollY")
    for i in range(1, steps + 1):
        page.evaluate(f"window.scrollTo(0, {start + (to - start) * i / steps})")
        rec.snap(ms)


def upload(page, path: Path):
    page.locator("input[type=file]").set_input_files(str(path))


def gif_run(browser) -> dict:
    ctx = browser.new_context(viewport=VIEW, device_scale_factor=1, color_scheme="light")
    page = ctx.new_page()
    jobs = {}

    # Hero: record -> check -> audit -> conflict verdict -> override reason
    rec = Recorder(page)
    page.goto(APP)
    page.wait_for_selector(".table")
    page.wait_for_timeout(600)
    rec.hold(1800)
    page.get_by_role("link", name="Check an upload").first.click()
    page.wait_for_selector(".dropzone")
    page.wait_for_timeout(400)
    rec.hold(1400)
    page.locator(".dropzone").hover()
    rec.hold(500)
    upload(page, CONFLICT)
    wait_verdict(page, rec)
    jobs["conflict"] = page.url.split("job=")[1]
    rec.hold(2200)
    full = page.evaluate("document.body.scrollHeight") - VIEW["height"]
    smooth_scroll(page, rec, min(560, full), steps=12)
    rec.hold(1600)
    smooth_scroll(page, rec, full, steps=12)
    rec.hold(1000)
    page.get_by_role("button", name="Upload anyway").click()
    page.wait_for_timeout(300)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    rec.snap(600)
    box = page.locator("#reason")
    box.click()
    for word in "The 2026 logging standard supersedes the retention policy.".split(" "):
        box.type(word + " ")
        rec.snap(90)
    rec.hold(2400)
    rec.save("concord-flow.gif")

    # Duplicate: short loop
    rec = Recorder(page)
    page.goto(APP + "/check")
    page.wait_for_selector(".dropzone")
    page.wait_for_timeout(400)
    rec.hold(1200)
    upload(page, DUP)
    wait_verdict(page, rec, every=0.3)
    jobs["duplicate"] = page.url.split("job=")[1]
    rec.hold(3200)
    rec.save("concord-duplicate.gif")

    # Clean upload (no GIF, just the job for screenshots)
    page.goto(APP + "/check")
    page.wait_for_selector(".dropzone")
    upload(page, CLEAN)
    wait_verdict(page, None)
    jobs["concord"] = page.url.split("job=")[1]

    ctx.close()
    return jobs


def stills(browser, jobs: dict):
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 1200, "height": 800}, device_scale_factor=2, color_scheme=scheme)
        page = ctx.new_page()
        sfx = "" if scheme == "light" else "-dark"

        page.goto(APP)
        page.wait_for_selector(".table")
        page.wait_for_timeout(800)
        page.screenshot(path=OUT / f"record{sfx}.png")

        page.goto(f"{APP}/check?job={jobs['conflict']}")
        page.wait_for_selector(".verdict-title")
        page.wait_for_timeout(800)
        page.screenshot(path=OUT / f"verdict-conflict{sfx}.png", full_page=True)
        if scheme == "light":
            href = page.locator(".provenance a").first.get_attribute("href")
            page.get_by_role("button", name="Upload anyway").click()
            page.wait_for_timeout(400)
            page.locator("#reason").fill("The 2026 logging standard supersedes the retention policy.")
            page.locator(".decision").screenshot(path=OUT / "override.png")
            page.locator(".finding").first.screenshot(path=OUT / "finding.png")

            page.goto(APP + href)
            page.wait_for_selector(".claim.target")
            page.wait_for_timeout(900)
            page.screenshot(path=OUT / "document.png")

            page.goto(f"{APP}/check?job={jobs['concord']}")
            page.wait_for_selector(".verdict-title")
            page.wait_for_timeout(800)
            page.screenshot(path=OUT / "verdict-concord.png")

            page.goto(f"{APP}/check?job={jobs['duplicate']}")
            page.wait_for_selector(".verdict-title")
            page.wait_for_timeout(800)
            page.screenshot(path=OUT / "verdict-duplicate.png")
        ctx.close()

    # Phone width
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, color_scheme="light")
    page = ctx.new_page()
    page.goto(f"{APP}/check?job={jobs['conflict']}")
    page.wait_for_selector(".verdict-title")
    page.wait_for_timeout(800)
    page.screenshot(path=OUT / "mobile.png")
    ctx.close()


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    if "--stills" in sys.argv:
        jobs = json.loads((FRAMES / "jobs.json").read_text())
    else:
        jobs = gif_run(browser)
        print(json.dumps(jobs))
        (FRAMES / "jobs.json").write_text(json.dumps(jobs))
    stills(browser, jobs)
    browser.close()
