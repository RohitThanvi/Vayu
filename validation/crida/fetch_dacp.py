#!/usr/bin/env python3
"""
fetch_dacp.py - download ICAR-CRIDA district contingency-plan PDFs. RUN ON YOUR MACHINE (the sandbox cannot reach icar-crida.res.in).

  python fetch_dacp.py --urls urls.txt --out dacp_pdfs [--limit 20]          one PDF URL per line
  python fetch_dacp.py --index <state-index-page-url> --out dacp_pdfs        collects every *.pdf link on that page

Polite: sequential, 1.5 s delay, skips files already downloaded, identifies itself, stops on repeated HTTP errors.
NOT TESTED against the live index pages (unknown template); if --index finds no links, open the page, copy the PDF links into urls.txt and use --urls.
Pilot first: --limit 20 spread over several states, then run parse_dacp.py on the folder and read the QA flags before scaling.
Please check the site's terms / robots.txt before bulk use.
"""
import argparse, re, time, urllib.parse, urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (research; Vayu pilot; contact: repo owner)"

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--urls"); ap.add_argument("--index"); ap.add_argument("--out", default="dacp_pdfs"); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(); out = Path(a.out); out.mkdir(exist_ok=True)
    urls = []
    if a.urls: urls += [u.strip() for u in Path(a.urls).read_text().splitlines() if u.strip() and not u.startswith("#")]
    if a.index:
        html = get(a.index).decode("utf-8", "replace")
        urls += [urllib.parse.urljoin(a.index, h) for h in re.findall(r'href=["\']([^"\']+\.pdf)["\']', html, re.I)]
    urls = list(dict.fromkeys(urls))[: a.limit or None]
    print(f"{len(urls)} PDFs"); errs = 0
    for u in urls:
        f = out / Path(urllib.parse.urlparse(u).path).name
        if f.exists() and f.stat().st_size > 1000: continue
        try: f.write_bytes(get(u)); print("ok  ", f.name); errs = 0
        except Exception as e:  # noqa: BLE001
            errs += 1; print("FAIL", u, e)
            if errs >= 5: print("5 consecutive failures - stopping"); break
        time.sleep(1.5)

if __name__ == "__main__":
    main()
