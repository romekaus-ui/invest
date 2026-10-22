#!/usr/bin/env python3
"""Catalogue population and date reach for a list of Bazaarvoice passkeys.

A published passkey is not a usable route. The three ways a route fails after
the key works were measured on 2026-10-15 and all three are checked here:
catalogue empty, catalogue full but abandoned (newest review years old), and
depth too thin to clear the 30-per-quarter floor. Dates are read off the
returned reviews, never trusted from the Sort parameter.
"""
import json, sys, time, urllib.parse, urllib.request, urllib.error, gzip, io, datetime as dt
UA=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
def j(u):
    r=urllib.request.Request(u,headers={"User-Agent":UA,"Accept-Encoding":"gzip"})
    with urllib.request.urlopen(r,timeout=40) as z:
        b=z.read()
        if z.headers.get("Content-Encoding")=="gzip":
            b=gzip.GzipFile(fileobj=io.BytesIO(b)).read()
        return json.loads(b.decode("utf-8","replace"))
def products(pk):
    q=urllib.parse.urlencode({"apiversion":"5.4","passkey":pk,"Limit":100,
                              "Sort":"TotalReviewCount:desc","Stats":"Reviews"})
    return j("https://api.bazaarvoice.com/data/products.json?"+q)
def revs(pk,pid,offset=0,limit=100):
    q=urllib.parse.urlencode({"apiversion":"5.4","passkey":pk,"Limit":limit,
        "Offset":offset,"Sort":"SubmissionTime:desc","Filter":"ProductId:%s"%pid})
    return j("https://api.bazaarvoice.com/data/reviews.json?"+q)
def date(r):
    s=(r.get("SubmissionTime") or "")[:10]
    return s
rows=[]
for line in open(sys.argv[1]):
    line=line.strip()
    if not line or line.startswith("#"): continue
    name,client,pk=line.split("|")
    rec={"name":name,"client":client}
    try:
        p=products(pk)
        rec["catalog"]=p.get("TotalResults")
        rs=sorted(((( x.get("ReviewStatistics") or {}).get("TotalReviewCount") or 0),
                   x.get("Id")) for x in p.get("Results",[]))
        top=rs[-1] if rs else (0,None)
        rec["top_reviews"],rec["top_id"]=top
        # top 5 products' pooled count, a crude read on whether pooling can clear the floor
        rec["top5_sum"]=sum(c for c,_ in rs[-5:])
        if top[0] >= 30 and top[1]:
            a=revs(pk,top[1],0,100); time.sleep(0.4)
            newest=date(a["Results"][0]) if a.get("Results") else None
            last=max(0,min(top[0],10000)-100)
            b=revs(pk,top[1],last,100) if last else a
            oldest=date(b["Results"][-1]) if b.get("Results") else None
            rec["newest"],rec["oldest"]=newest,oldest
            txt=sum(1 for x in a.get("Results",[]) if (x.get("ReviewText") or "").strip())
            rec["text_pct_recent"]=txt
            if newest:
                rec["age_days"]=(dt.date.today()-dt.date(*map(int,newest.split("-")))).days
            if newest and oldest:
                y=(dt.date(*map(int,newest.split("-")))-dt.date(*map(int,oldest.split("-")))).days/365.25
                rec["reach_years"]=round(y,1)
    except urllib.error.HTTPError as e:
        rec["error"]="HTTP %s"%e.code
    except Exception as e:
        rec["error"]="%s: %s"%(type(e).__name__,e)
    rows.append(rec)
    print("%-24s cat=%-7s top=%-6s top5=%-7s %s..%s  %sy  txt%%=%-4s age=%-5s %s"%(
        name[:24],rec.get("catalog","-"),rec.get("top_reviews","-"),rec.get("top5_sum","-"),
        rec.get("oldest","-"),rec.get("newest","-"),rec.get("reach_years","-"),
        rec.get("text_pct_recent","-"),rec.get("age_days","-"),rec.get("error","")),flush=True)
    time.sleep(0.8)
json.dump(rows,open(sys.argv[2],"w"),indent=1)
print("\nwrote %s"%sys.argv[2])
