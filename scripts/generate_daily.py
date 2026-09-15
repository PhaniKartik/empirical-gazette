import json
import os
import re
from datetime import date
from pathlib import Path

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
        print("Today's Gazette has already been published.")
        return

    old = data.get("dispatches", [])

    archive = "\n".join(
        f"- {x.get('headline', '')}"
        for x in old[-100:]
    ) or "(none)"

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"]
    )

    # ------------------------------------------------------------
    # STEP 1: RESEARCH
    # ------------------------------------------------------------

    research = ask(
        client,
        f"""Research ONE documented historical event in science for {TODAY}.

Prefer topics in:
- physics
- astronomy
- mathematics
- chemistry
- earth science
- computing
- scientific instruments
- history of scientific institutions

Medical or biological history is allowed only as high-level historical or
biographical context.

Do not provide experimental protocols, recipes, procedures, quantities,
operational steps, or other actionable technical detail.

Avoid these previous headlines:
{archive}

Return JSON with exactly these fields:

event
people
date
location
why_significant
key_facts
prevailing_belief
evidence
primary_sources
quote_candidate
quote_attribution
on_this_day_title
on_this_day_copy
instrument_title
instrument_copy

Use reliable sources.

Never invent facts or quotations.

For prevailing_belief, identify only a specific historical belief,
expectation, uncertainty, or assumption that is explicitly documented by
the sources. Do not infer a broad scientific or engineering consensus.

For evidence, explain only what the documented evidence actually showed.
Clearly distinguish between what an event demonstrated and what it did not
demonstrate.

Keep all evidence descriptions non-procedural and suitable for a general
historical newspaper."""
    )

    # ------------------------------------------------------------
    # STEP 2: ARTICLE
    # ------------------------------------------------------------

    article = ask(
        client,
        f"""Write one Empirical Gazette dispatch using ONLY this research
dossier:

{json.dumps(research, ensure_ascii=False)}

Return JSON with exactly these fields:

scientist
exactHistoricalDate
headline
deck
caption
lore
body_html
wrongBelief
evidence
primarySource
quote
quoteAuthor
onThisDayTitle
onThisDayCopy
instrumentTitle
instrumentCopy

Use 3-5 <p> paragraphs.

Use at most one pull quote.

Use no Markdown.

Do not invent facts, dates, people, locations, quotations, or historical
interpretations that are not supported by the dossier.

For wrongBelief, state only a specific belief, expectation, or uncertainty
that the dossier explicitly documents.

Do not invent a broad "prevailing belief," "engineering consensus," or
"scientific consensus."

For evidence, distinguish clearly between what the event demonstrated and
what it did not demonstrate.

Do not describe an uncrewed test as proof of crewed capability unless the
dossier explicitly supports that conclusion.

When describing historical significance, prefer cautious wording such as
"demonstrated," "tested," "provided evidence for," or "helped influence"
rather than claiming that an event definitively caused a later decision.

Do not turn historical uncertainty into certainty.

Keep medical/biological material historical and non-procedural.

Do not add instructions, protocols, recipes, quantities, or operational
technical detail.

There is intentionally NO IMAGE for this edition."""
    )

    # ------------------------------------------------------------
    # STEP 3: FINAL FACT CHECK
    # ------------------------------------------------------------

    check = ask(
        client,
        f"""Perform the final editorial fact-check for The Empirical Gazette.

Compare the DOSSIER and ARTICLE for historical accuracy.

DOSSIER:
{json.dumps(research, ensure_ascii=False)}

ARTICLE:
{json.dumps(article, ensure_ascii=False)}

There is intentionally NO IMAGE. Do not perform an image check.

Do not expand or introduce procedural medical or biological information.

Return JSON with exactly these fields:

pass
score
errors
warnings
verified_claims
quote_ok
primary_source_ok

Use a score from 0 to 1.

1.0 means fully supported.
0.90 is the minimum acceptable publication threshold.

Fail for any material unsupported or contradicted claim.

Fail for:
- wrong date
- wrong person
- wrong location
- unsupported quotation
- unsupported source
- materially exaggerated historical significance
- invented consensus
- unsupported causal claim
- unsupported capability claim

Be especially careful with statements about what an experiment,
mission, discovery, or invention proved.

Distinguish what was actually demonstrated from later interpretations.

A cautious historical article with documented limitations should pass.

There is intentionally NO IMAGE, so do not fail the article because an
image is absent."""
    )

    # ------------------------------------------------------------
    # STEP 4: NORMALIZE VALIDATION SCORE
    # ------------------------------------------------------------

    score = check.get("score", 0)

    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0

    # Accept either:
    # 0.96  -> 96
    # 96    -> 96
    if 0 <= score <= 1:
        score *= 100

    check["score"] = score

    # ------------------------------------------------------------
    # STEP 5: FAIL CLOSED
    # ------------------------------------------------------------

    if (
        not check.get("pass", False)
        or score < 90
        or not check.get("quote_ok", False)
        or not check.get("primary_source_ok", False)
    ):
        raise RuntimeError(
            f"Validation failed; nothing published: {check}"
        )

    # ------------------------------------------------------------
    # STEP 6: PUBLISH
    # ------------------------------------------------------------

    old.append(
        {
            "scientist": article["scientist"],
            "exactHistoricalDate": article[
                "exactHistoricalDate"
            ],
            "headline": article["headline"],
            "deck": article["deck"],

            # Images are intentionally disabled for now.
            "wikiImage": None,
            "fallbackSvg": None,

            "caption": article["caption"],
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
                "imageSource": None,
                "imageUrl": None,
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
