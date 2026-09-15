import json, os, re
from datetime import date
from pathlib import Path
import requests
from openai import OpenAI

ROOT=Path(__file__).resolve().parents[1]
DATA_FILE=ROOT/"site-data.js"
TODAY=date.today().isoformat()
MODEL=os.getenv("GAZETTE_MODEL","gpt-5.6-luna")

def load():
    if not DATA_FILE.exists(): return {}
    m=re.search(r"window\.GAZETTE_DATA\s*=\s*(\{.*\})\s*;\s*$",DATA_FILE.read_text(),re.S)
    return json.loads(m.group(1)) if m else {}

def ask(client,prompt):
    r=client.responses.create(model=MODEL,tools=[{"type":"web_search"}],
                              input=prompt,include=["web_search_call.action.sources"])
    return json.loads(re.sub(r"^```json\s*|\s*```$","",r.output_text.strip(),flags=re.I))

def commons(q):
    p={"action":"query","generator":"search","gsrsearch":q,"gsrnamespace":6,
       "gsrlimit":10,"prop":"imageinfo","iiprop":"url|extmetadata",
       "iiurlwidth":1000,"format":"json"}
    r=requests.get("https://commons.wikimedia.org/w/api.php",params=p,timeout=30)
    r.raise_for_status()
    for x in r.json().get("query",{}).get("pages",{}).values():
        i=(x.get("imageinfo") or [{}])[0]; u=i.get("thumburl") or i.get("url")
        t=x.get("title",""); low=(t+" "+(u or "")).lower()
        if u and not any(v in low for v in [".svg","logo","map","diagram","chart"]):
            return {"title":t,"url":u}
    return None


def write_rss(data):
    items=[]
    for x in reversed(data.get("dispatches", [])[-30:]):
        title=x.get("headline", "The Empirical Gazette")
        desc=x.get("deck", "")
        dt=x.get("exactHistoricalDate", "")
        items.append(f"<item><title>{esc(title)}</title><description>{esc(desc)}</description><pubDate>{esc(dt)}</pubDate><guid>{esc(title)}</guid></item>")
    rss='<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>The Empirical Gazette</title><description>A daily newspaper of science history</description>'+''.join(items)+'</channel></rss>'
    (ROOT/"feed.xml").write_text(rss)

def esc(v):
    return (str(v).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;'))

def main():
    data=load()
    if data.get("updated")==TODAY: return
    old=data.get("dispatches",[])
    archive="\n".join(f"- {x.get('headline','')}" for x in old[-100:]) or "(none)"
    c=OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    research=ask(c,f"""Research ONE documented historical science story for {TODAY}.
Avoid these previous headlines:
{archive}
Return JSON with event,people,date,location,why_significant,key_facts,
prevailing_belief,evidence,primary_sources,image_subject,search_queries,
quote_candidate,quote_attribution,on_this_day_title,on_this_day_copy,
instrument_title,instrument_copy.
Use reliable sources. Never invent facts or quotations.""")

    article=ask(c,f"""Write one Empirical Gazette dispatch using ONLY this dossier:
{json.dumps(research,ensure_ascii=False)}
Return JSON with scientist,exactHistoricalDate,headline,deck,caption,lore,
body_html,wrongBelief,evidence,primarySource,imageSearchQuery,quote,quoteAuthor,
onThisDayTitle,onThisDayCopy,instrumentTitle,instrumentCopy.
Use 3-5 <p> paragraphs, at most one pull quote, no Markdown, and no facts/quotes
not supported by the dossier.""")

    image=None
    for q in research.get("search_queries",[])[:5]:
        image=commons(q)
        if image: break
    if not image: image=commons(article.get("imageSearchQuery",article["scientist"]))
    if not image: raise RuntimeError("No historical image; publication aborted.")

    check=ask(c,f"""Final fact-check. Compare DOSSIER and ARTICLE and IMAGE.
DOSSIER:{json.dumps(research,ensure_ascii=False)}
ARTICLE:{json.dumps(article,ensure_ascii=False)}
IMAGE:{json.dumps(image,ensure_ascii=False)}
Return JSON with pass,score,errors,warnings,verified_claims,image_ok,quote_ok,
primary_source_ok. Fail for any material unsupported/contradicted claim,
wrong date/person/location, unsupported quote/source, or unrelated image.""")

    if not check.get("pass") or check.get("score",0)<90:
        raise RuntimeError(f"Validation failed; nothing published: {check}")

    old.append({
        "scientist":article["scientist"],"exactHistoricalDate":article["exactHistoricalDate"],
        "headline":article["headline"],"deck":article["deck"],"wikiImage":image["url"],
        "fallbackSvg":None,"caption":article["caption"]+f" Source image: {image['title']}.",
        "lore":article["lore"],"body":article["body_html"],
        "wrongBelief":article["wrongBelief"],"evidence":article["evidence"],
        "primarySource":article["primarySource"],
        "editorialRecord":{"publishedDate":TODAY,"validationScore":check["score"],
          "verifiedClaims":check.get("verified_claims",[]),"warnings":check.get("warnings",[]),
          "researchSources":research.get("primary_sources",[]),
          "imageSource":image["title"],"imageUrl":image["url"]}})
    data={"dispatches":old,
          "dailyQuote":{"text":article["quote"],"author":article["quoteAuthor"]},
          "onThisDay":{"title":article["onThisDayTitle"],"copy":article["onThisDayCopy"]},
          "instrument":{"title":article["instrumentTitle"],"copy":article["instrumentCopy"]},
          "updated":TODAY}
    DATA_FILE.write_text("window.GAZETTE_DATA = "+json.dumps(data,ensure_ascii=False,indent=2)+";\n")
    write_rss(data)
    print(f"Published Dispatch No. {len(old):03d}; validation {check['score']}/100.")

if __name__=="__main__": main()
