import json
import os
import re
import time
from datetime import date
from pathlib import Path

import requests
from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "site-data.js"
TODAY = date.today().isoformat()
MODEL = os.getenv("GAZETTE_MODEL", "gpt-5.6-luna")


def load():
    if not DATA_FILE.exists():
        return {}

    m = re.search(
        r"window\.GAZETTE_DATA\s*=\s*(\{.*\})\s*;\s*$",
        DATA_FILE.read_text(),
        re.S,
    )

    return json.loads(m.group(1)) if m else {}


def ask(client, prompt):
    r = client.responses.create(
        model=MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
        include=["web_search_call.action.sources"],
    )

    return json.loads(
        re.sub(
            r"^```json\s*|\s*```$",
            "",
            r.output_text.strip(),
            flags=re.I,
        )
    )


def commons(q, expected_subject=""):
    """Find a historically relevant Wikimedia Commons image.

    The search is deliberately conservative: a candidate must have metadata
    that identifies the requested person/event, or publication is aborted.
    """

    headers = {
        "User-Agent": (
            "EmpiricalGazetteBot/1.2 "
            "(https://github.com/PhaniKartik/empirical-gazette) "
            "Python-requests"
        ),
        "Api-User-Agent": (
            "EmpiricalGazetteBot/1.2 "
            "(https://github.com/PhaniKartik/empirical-gazette)"
        ),
    }

    raw = str(q or "").strip()
    subject = str(expected_subject or "").strip()

    subject_tokens = [
        t.lower()
        for t in re.findall(r"[A-Za-z]{3,}", subject)
    ]

    # Prefer the subject itself over event prose. Event/date terms are often
    # enough to retrieve books, diagrams, or unrelated historical pages.
    terms = []

    if subject:
        terms.extend(
            [
                f'intitle:"{subject}"',
                f'"{subject}"',
                subject,
            ]
        )

    if raw and raw.lower() not in {x.lower() for x in terms}:
        terms.append(raw)

    # Remove dates from broad fallback queries.
    if raw:
        broad = re.sub(
            r"\b(?:17|18|19|20)\d{2}\b",
            "",
            raw,
        )
        broad = re.sub(r"\s+", " ", broad).strip(" ,.-")

        if broad:
            terms.append(broad)

    session = requests.Session()
    session.headers.update(headers)

    for term in terms:
        search_params = {
            "action": "query",
            "list": "search",
            "srsearch": term,
            "srnamespace": 6,
            "srlimit": 30,
            "format": "json",
        }

        for attempt in range(3):
            r = session.get(
                "https://commons.wikimedia.org/w/api.php",
                params=search_params,
                timeout=30,
            )

            if (
                r.status_code in (429, 500, 502, 503, 504)
                and attempt < 2
            ):
                raw_delay = r.headers.get("Retry-After", "2")
                delay = (
                    int(raw_delay)
                    if str(raw_delay).isdigit()
                    else 2
                )
                time.sleep(min(delay, 10))
                continue

            r.raise_for_status()
            break

        titles = [
            x.get("title")
            for x in r.json()
            .get("query", {})
            .get("search", [])
            if x.get("title")
        ]

        if not titles:
            continue

        info_params = {
            "action": "query",
            "titles": "|".join(titles[:30]),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1200,
            "format": "json",
        }

        for attempt in range(3):
            r = session.get(
                "https://commons.wikimedia.org/w/api.php",
                params=info_params,
                timeout=30,
            )

            if (
                r.status_code in (429, 500, 502, 503, 504)
                and attempt < 2
            ):
                raw_delay = r.headers.get("Retry-After", "2")
                delay = (
                    int(raw_delay)
                    if str(raw_delay).isdigit()
                    else 2
                )
                time.sleep(min(delay, 10))
                continue

            r.raise_for_status()
            break

        found = []

        for x in (
            r.json()
            .get("query", {})
            .get("pages", {})
            .values()
        ):
            i = (x.get("imageinfo") or [{}])[0]

            u = i.get("thumburl") or i.get("url")
            title = x.get("title", "")
            meta = i.get("extmetadata") or {}

            description = " ".join(
                str(meta.get(k, {}).get("value", ""))
                for k in (
                    "ImageDescription",
                    "ObjectName",
                    "Categories",
                )
            )

            hay = (title + " " + description).lower()
            low = (title + " " + (u or "")).lower()

            if not u:
                continue

            if any(
                v in low
                for v in [
                    ".svg",
                    "logo",
                    "map",
                    "diagram",
                    "chart",
                ]
            ):
                continue

            # If we know the person/event subject, require a textual match.
            # This prevents an unrelated book page from becoming the portrait.
            if subject_tokens and not all(
                t in hay for t in subject_tokens
            ):
                continue

            score = 0

            if subject and subject.lower() in hay:
                score += 10

            if "portrait" in hay:
                score += 5

            if "photograph" in hay or "photo" in hay:
                score += 3

            if (
                "painting" in hay
                or "engraving" in hay
                or "etching" in hay
            ):
                score += 2

            if any(
                ext in low
                for ext in [
                    ".jpg",
                    ".jpeg",
                    ".png",
                ]
            ):
                score += 1

            # Penalize generic scans/pages when a person portrait is wanted.
            if any(
                v in hay
                for v in [
                    "book page",
                    "page from",
                    "title page",
                    "table of contents",
                ]
            ):
                score -= 8

            found.append((score, title, u))

        if found:
            found.sort(
                key=lambda x: (x[0], x[1]),
                reverse=True,
            )

            score, title, u = found[0]

            if score >= 10:
                return {
                    "title": title,
                    "url": u,
                }

    return None


def esc(v):
    return (
        str(v)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_rss(data):
    items = []

    for x in reversed(data.get("dispatches", [])[-30:]):
        title = x.get(
            "headline",
            "The Empirical Gazette",
        )

        desc = x.get("deck", "")
        dt = x.get("exactHistoricalDate", "")

        items.append(
            f"<item>"
            f"<title>{esc(title)}</title>"
            f"<description>{esc(desc)}</description>"
            f"<pubDate>{esc(dt)}</pubDate>"
            f"<guid>{esc(title)}</guid>"
            f"</item>"
        )

    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0">'
        '<channel>'
        '<title>The Empirical Gazette</title>'
        '<description>'
        'A daily newspaper of science history'
        '</description>'
        + "".join(items)
        + "</channel>"
        "</rss>"
    )

    (ROOT / "feed.xml").write_text(rss)


def main():
    data = load()

    if data.get("updated") == TODAY:
        return

    old = data.get("dispatches", [])

    archive = "\n".join(
        f"- {x.get('headline', '')}"
        for x in old[-100:]
    ) or "(none)"

    c = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"]
    )

    research = ask(
        c,
        f"""Research ONE documented historical event in science for {TODAY}.

Prefer topics in physics, astronomy, mathematics, chemistry, earth science,
computing, scientific instruments, or the history of scientific institutions.

Medical or biological history is allowed only as high-level historical or
biographical context.

Do not provide experimental protocols, recipes, procedures, quantities,
operational steps, or other actionable technical detail.

Avoid these previous headlines:
{archive}

Return JSON with:
event,
people,
date,
location,
why_significant,
key_facts,
prevailing_belief,
evidence,
primary_sources,
image_subject,
search_queries,
quote_candidate,
quote_attribution,
on_this_day_title,
on_this_day_copy,
instrument_title,
instrument_copy.

Use reliable sources. Never invent facts or quotations.

For prevailing_belief, identify only a specific historical belief,
expectation, uncertainty, or assumption that is explicitly documented by
the sources. Do not infer a broad scientific or engineering consensus.

For evidence, explain only what the documented evidence actually showed.
Clearly distinguish between what an event demonstrated and what it did not
demonstrate.

Keep all evidence descriptions non-procedural and suitable for a general
historical newspaper."""
    )

    article = ask(
        c,
        f"""Write one Empirical Gazette dispatch using ONLY this dossier:

{json.dumps(research, ensure_ascii=False)}

Return JSON with:
scientist,
exactHistoricalDate,
headline,
deck,
caption,
lore,
body_html,
wrongBelief,
evidence,
primarySource,
imageSearchQuery,
quote,
quoteAuthor,
onThisDayTitle,
onThisDayCopy,
instrumentTitle,
instrumentCopy.

Use 3-5 <p> paragraphs, at most one pull quote, no Markdown, and no
facts/quotes not supported by the dossier.

For wrongBelief, state only a specific belief, expectation, or uncertainty
that the dossier explicitly documents. Do not infer or invent a broad
"prevailing belief," "engineering consensus," or "scientific consensus."

For evidence, distinguish clearly between what the event demonstrated and
what it did not demonstrate. Do not describe an uncrewed test as proof of
crewed capability unless the dossier explicitly supports that conclusion.

When describing historical significance, prefer cautious wording such as
"demonstrated," "tested," "provided evidence for," or "helped influence"
rather than claiming that an event definitively caused a later decision.

Do not turn historical uncertainty into certainty.

Keep medical/biological material historical and non-procedural; do not add
instructions, protocols, recipes, quantities, or operational technical detail."""
    )

    image = None

    for q in research.get("search_queries", [])[:5]:
        image = commons(
            q,
            article.get(
                "scientist",
                research.get("image_subject", ""),
            ),
        )

        if image:
            break

    if not image:
        image = commons(
            article.get(
                "imageSearchQuery",
                article["scientist"],
            ),
            article.get(
                "scientist",
                research.get("image_subject", ""),
            ),
        )

    if not image:
        image = commons(
            article.get("scientist", ""),
            article.get(
                "scientist",
                research.get("image_subject", ""),
            ),
        )

    if not image:
        raise RuntimeError(
            "No historical image; publication aborted."
        )

    check = ask(
        c,
        f"""Final editorial fact-check. Compare DOSSIER, ARTICLE, and IMAGE
for historical accuracy only.

DOSSIER:
{json.dumps(research, ensure_ascii=False)}

ARTICLE:
{json.dumps(article, ensure_ascii=False)}

IMAGE:
{json.dumps(image, ensure_ascii=False)}

Do not expand or introduce procedural medical or biological information.

Return JSON with:
pass,
score,
errors,
warnings,
verified_claims,
image_ok,
quote_ok,
primary_source_ok.

Use a score from 0 to 1, where 1.0 means fully supported and 0.90 is the
minimum acceptable publication threshold.

Fail for any material unsupported or contradicted claim, wrong date,
wrong person, wrong location, unsupported quotation or source, or unrelated
image.

Also fail when the article presents an inference, consensus, causal claim,
or capability claim more strongly than the supplied evidence supports."""
    )

    # Accept either a 0-1 score (e.g. 0.96) or a 0-100 score (e.g. 96).
    score = check.get("score", 0)

    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0

    if 0 <= score <= 1:
        score *= 100

    check["score"] = score

    if (
        not check.get("pass", False)
        or score < 90
        or not check.get("image_ok", False)
        or not check.get("quote_ok", False)
        or not check.get("primary_source_ok", False)
    ):
        raise RuntimeError(
            f"Validation failed; nothing published: {check}"
        )

    old.append(
        {
            "scientist": article["scientist"],
            "exactHistoricalDate": article[
                "exactHistoricalDate"
            ],
            "headline": article["headline"],
            "deck": article["deck"],
            "wikiImage": image["url"],
            "fallbackSvg": None,
            "caption": (
                article["caption"]
                + f" Source image: {image['title']}."
            ),
            "lore": article["lore"],
            "body": article["body_html"],
            "wrongBelief": article["wrongBelief"],
            "evidence": article["evidence"],
            "primarySource": article["primarySource"],
            "editorialRecord": {
                "publishedDate": TODAY,
                "validationScore": check["score"],
                "verifiedClaims": check.get(
                    "verified_claims",
                    [],
                ),
                "warnings": check.get(
                    "warnings",
                    [],
                ),
                "researchSources": research.get(
                    "primary_sources",
                    [],
                ),
                "imageSource": image["title"],
                "imageUrl": image["url"],
            },
        }
    )

    data = {
        "dispatches": old,
        "dailyQuote": {
            "text": article["quote"],
            "author": article["quoteAuthor"],
        },
        "onThisDay": {
            "title": article["onThisDayTitle"],
            "copy": article["onThisDayCopy"],
        },
        "instrument": {
            "title": article["instrumentTitle"],
            "copy": article["instrumentCopy"],
        },
        "updated": TODAY,
    }

    DATA_FILE.write_text(
        "window.GAZETTE_DATA = "
        + json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
        + ";\n"
    )

    write_rss(data)

    print(
        f"Published Dispatch No. {len(old):03d}; "
        f"validation {check['score']}/100."
    )


if __name__ == "__main__":
    main()
