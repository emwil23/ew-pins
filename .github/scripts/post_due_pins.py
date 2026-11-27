"""Post due pins from a plan CSV to Pinterest (API v5). Run by .github/workflows/post-pins.yml.

  python .github/scripts/post_due_pins.py command-center-2027/plan.csv            # post up to 2 due pins
  python .github/scripts/post_due_pins.py command-center-2027/plan.csv --dry-run  # GET calls only

Plan columns: id, Publish date (UTC), Media URL, Title, Description, Alt text, Board, Link.
Posted pins are appended to posted-log.csv next to the plan, so a row is never posted twice.
Token comes only from env PINTEREST_ACCESS_TOKEN and is never printed.
"""
import argparse, csv, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

API = "https://api.pinterest.com/v5"
MAX_PER_RUN = 2
TOKEN = os.environ.get("PINTEREST_ACCESS_TOKEN", "").strip()


def fail(msg):
    print("ERROR: " + msg)
    sys.exit(1)


def api(method, path, params=None, body=None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + TOKEN, "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            if e.code == 401:
                fail("Pinterest token expired or invalid (HTTP 401). Update the PINTEREST_ACCESS_TOKEN repo secret.")
            if e.code in (429, 500, 502, 503) and attempt < 2 and method == "GET":
                time.sleep(10 * (attempt + 1)); continue
            try: msg = json.loads(raw).get("message", raw[:300])
            except ValueError: msg = raw[:300]
            fail(f"{method} {path} -> HTTP {e.code}: {msg}")
        except urllib.error.URLError as e:
            if attempt < 2: time.sleep(10); continue
            fail(f"{method} {path} -> network error: {e.reason}")


def all_pages(path):
    items, bookmark = [], None
    while True:
        js = api("GET", path, dict(page_size=100, **({"bookmark": bookmark} if bookmark else {})))
        items += js.get("items", [])
        bookmark = js.get("bookmark")
        if not bookmark:
            return items


def norm(s):
    return " ".join((s or "").lower().split())


def parse_utc(s):
    d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not TOKEN:
        fail("PINTEREST_ACCESS_TOKEN is not set.")
    log_path = os.path.join(os.path.dirname(a.plan), "posted-log.csv")
    posted = set()
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", newline="") as f:
            posted = {r["id"] for r in csv.DictReader(f)}
    with open(a.plan, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    now = datetime.now(timezone.utc)

    problems = []
    for r in rows:
        for col in ("id", "Publish date", "Media URL", "Title", "Board", "Link"):
            if not (r.get(col) or "").strip(): problems.append(f"{r.get('id')}: missing {col}")
        if len(r["Title"]) > 100: problems.append(f"{r['id']}: title over 100 chars")
        if len(r["Description"]) > 800: problems.append(f"{r['id']}: description over 800 chars")
        if len(r.get("Alt text", "")) > 500: problems.append(f"{r['id']}: alt text over 500 chars")
        r["_when"] = parse_utc(r["Publish date"])
    if problems:
        fail("plan problems: " + "; ".join(problems))

    me = api("GET", "/user_account")
    if me.get("username") != "ewstudioz":
        fail(f"token is for '{me.get('username')}', not ewstudioz")
    boards = {norm(b["name"]): b["id"] for b in all_pages("/boards")}
    live_titles = {norm(p.get("title")) for p in all_pages("/pins") if p.get("title")}
    missing = sorted({r["Board"] for r in rows if norm(r["Board"]) not in boards})
    if missing:
        fail("boards not found: " + ", ".join(missing))

    due = sorted((r for r in rows if r["id"] not in posted and r["_when"] <= now), key=lambda r: r["_when"])
    later = [r for r in rows if r["id"] not in posted and r["_when"] > now]
    print(f"now {now:%Y-%m-%d %H:%M} UTC | rows {len(rows)} | posted {len(posted)} | due {len(due)} | later {len(later)}")
    if later:
        print(f"next not-yet-due: {min(r['_when'] for r in later):%Y-%m-%d %H:%M} UTC")

    done = 0
    for r in due:
        if done >= MAX_PER_RUN:
            print(f"cap of {MAX_PER_RUN} per run reached; the rest post next run"); break
        if norm(r["Title"]) in live_titles:
            print(f"skip {r['id']}: a pin with this title is already live"); continue
        if a.dry_run:
            print(f"[dry-run] would post {r['id']}: {r['Title'][:70]}"); done += 1; continue
        pin = api("POST", "/pins", body={
            "board_id": boards[norm(r["Board"])], "title": r["Title"], "description": r["Description"],
            "link": r["Link"], "alt_text": r.get("Alt text") or r["Title"][:500],
            "media_source": {"source_type": "image_url", "url": r["Media URL"]},
            "ai_disclosures": {"values": ["AI_MODIFIED"]}})
        new = not os.path.exists(log_path)
        with open(log_path, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new: w.writerow(["id", "pin_id", "posted_at_utc"])
            w.writerow([r["id"], pin.get("id"), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")])
        live_titles.add(norm(r["Title"]))
        print(f"posted {r['id']} -> pin {pin.get('id')}")
        done += 1
    if not due:
        print("nothing due")


if __name__ == "__main__":
    main()
