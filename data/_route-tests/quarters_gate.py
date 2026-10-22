#!/usr/bin/env python3
"""The 30-text-bearing-reviews-per-quarter gate, measured without full walks.

Why not walk: Purple pools 210,143 reviews across its top products, and
exhausting every candidate would be hundreds of thousands of requests for a
question that is only "is there enough depth per quarter".

Two facts make a cheap exact-enough measurement possible:
  * Filter=IsRatingsOnly:eq:false counts TEXT-BEARING reviews directly, which
    is what the floor is about. Toro's top product: 5,878 of 10,270.
  * Reviews sort strictly by SubmissionTime desc (verified on the dates, never
    trusted from the Sort parameter), so offset is monotone in time. Sampling
    the date at a ladder of offsets gives the date-to-offset curve, and the
    review count between two offsets is just their difference.

Counts within one stride are apportioned linearly across the days it spans.
That is an approximation, and it is the reason this reports a FLOOR CHECK
rather than a quarterly series: the pipeline's own aggregate.py produces the
series from collected reviews, and nothing here substitutes for it.

Only the top N products per brand are sampled, so every number is a LOWER
BOUND on the company's pooled depth. A company that clears the floor here
clears it; one that does not may still clear it once more families are pooled.
"""
import datetime as dt, gzip, io, json, math, sys, time, urllib.error, urllib.parse, urllib.request
from collections import defaultdict

UA=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
FLOOR=30
TOPN=6
MAX_SAMPLES=26

def j(u, tries=3):
    for k in range(tries):
        try:
            r=urllib.request.Request(u,headers={"User-Agent":UA,"Accept-Encoding":"gzip"})
            with urllib.request.urlopen(r,timeout=45) as z:
                b=z.read()
                if z.headers.get("Content-Encoding")=="gzip":
                    b=gzip.GzipFile(fileobj=io.BytesIO(b)).read()
                return json.loads(b.decode("utf-8","replace"))
        except Exception:
            if k==tries-1: raise
            time.sleep(1.5*(k+1))

def revs(pk,pid,offset,limit=1):
    q=urllib.parse.urlencode({"apiversion":"5.4","passkey":pk,"Limit":limit,"Offset":offset,
        "Sort":"SubmissionTime:desc"},doseq=True)
    q+="&"+urllib.parse.urlencode({"Filter":"ProductId:%s"%pid})
    q+="&"+urllib.parse.urlencode({"Filter":"IsRatingsOnly:eq:false"})
    return j("https://api.bazaarvoice.com/data/reviews.json?"+q)

def qlabel(d): return "%04dQ%d"%(d.year,(d.month-1)//3+1)
def qstart(y,qq): return dt.date(y,3*(qq-1)+1,1)
def qnext(y,qq): return (y+1,1) if qq==4 else (y,qq+1)

def curve(pk,pid,total):
    """(offset, date) ladder over a product's text-bearing reviews."""
    if total<=0: return []
    stride=max(50,math.ceil(total/MAX_SAMPLES))
    offs=list(range(0,total,stride))
    if offs[-1]!=total-1: offs.append(total-1)
    out=[]
    for o in offs:
        try:
            d=revs(pk,pid,o)
        except urllib.error.HTTPError:
            break
        rs=d.get("Results") or []
        if not rs: break
        s=(rs[0].get("SubmissionTime") or "")[:10]
        if not s: break
        out.append((o,dt.date(*map(int,s.split("-")))))
        time.sleep(0.28)
    return out

def spread(cv, acc):
    """Apportion each stride's review count across the quarters it spans."""
    for (o1,d1),(o2,d2) in zip(cv,cv[1:]):
        n=o2-o1
        if n<=0: continue
        lo,hi=min(d1,d2),max(d1,d2)
        days=(hi-lo).days+1
        # walk the quarters the stride touches, weighting by days of overlap
        y,qq=lo.year,(lo.month-1)//3+1
        segs=[]
        while True:
            s=qstart(y,qq); y2,q2=qnext(y,qq); e=qstart(y2,q2)-dt.timedelta(days=1)
            ov=(min(hi,e)-max(lo,s)).days+1
            if ov>0: segs.append(("%04dQ%d"%(y,qq),ov))
            if e>=hi: break
            y,qq=y2,q2
        tot=sum(w for _,w in segs) or 1
        for lab,w in segs: acc[lab]+=n*w/tot

def run(name,pk,topn=TOPN):
    q=urllib.parse.urlencode({"apiversion":"5.4","passkey":pk,"Limit":100,
        "Sort":"TotalReviewCount:desc","Stats":"Reviews"})
    p=j("https://api.bazaarvoice.com/data/products.json?"+q)
    prods=sorted((((x.get("ReviewStatistics") or {}).get("TotalReviewCount") or 0),x.get("Id"))
                 for x in p.get("Results",[]))[-topn:]
    acc=defaultdict(float); used=[]
    for c,pid in reversed(prods):
        if not pid or c<FLOOR: continue
        try:
            d=revs(pk,pid,0)
        except Exception as e:
            continue
        txt=d.get("TotalResults") or 0
        if txt<FLOOR: continue
        cv=curve(pk,pid,min(txt,10000))
        if len(cv)<2: continue
        spread(cv,acc); used.append((pid,c,txt,cv[0][1].isoformat(),cv[-1][1].isoformat()))
        time.sleep(0.3)
    return acc,used

def summarise(acc):
    labs=sorted(acc)
    clear=[l for l in labs if acc[l]>=FLOOR]
    # longest consecutive run of clearing quarters
    best=run_=0; prev=None
    for l in labs:
        ok=acc[l]>=FLOOR
        y,qq=int(l[:4]),int(l[5])
        cont = prev is not None and (y,qq)==qnext(*prev)
        run_ = (run_+1) if (ok and cont) else (1 if ok else 0)
        best=max(best,run_); prev=(y,qq)
    return len(clear),best,labs

if __name__=="__main__":
    rows=[]
    print("%-26s %-7s %-7s %-9s %-11s %s"%("company","qtrs>=30","longest","products","span","floor verdict"))
    print("-"*92)
    for line in open(sys.argv[1]):
        line=line.strip()
        if not line or line.startswith("#"): continue
        name,client,pk=line.split("|")
        try:
            acc,used=run(name,pk)
        except Exception as e:
            print("%-26s %s"%(name[:26],"%s: %s"%(type(e).__name__,e)));continue
        nclear,longest,labs=summarise(acc)
        span="%s..%s"%(labs[0],labs[-1]) if labs else "-"
        v = "PASS" if longest>=12 else ("THIN" if longest>=8 else "FAIL")
        print("%-26s %-7s %-7s %-9s %-11s %s"%(name[:26],nclear,longest,len(used),span,v))
        rows.append({"company":name,"client":client,"quarters_clearing_floor":nclear,
          "longest_consecutive_run":longest,"products_sampled":len(used),"span":span,
          "verdict":v,"per_quarter_estimate":{k:round(v2,1) for k,v2 in sorted(acc.items())},
          "products":used})
        time.sleep(0.5)
    json.dump(rows,open(sys.argv[2],"w"),indent=1)
    print("\nwrote %s"%sys.argv[2])
