#!/usr/bin/env python3
"""Probe Bazaarvoice directly for brands the HTML scan missed.

The scan misses two ways that are not absence: the site refuses a plain GET
(403/308/TLS), or it renders its widget from JS the scan never executes. Both
leave the deployment itself reachable, because Bazaarvoice publishes it on its
own CDN. A 200 with a passkey is positive evidence; a 404 is evidence of
nothing more than a wrong client guess, and is reported as such.
"""
import gzip, io, re, sys, time, urllib.error, urllib.request
UA=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
def get(u):
    r=urllib.request.Request(u,headers={"User-Agent":UA,"Accept-Encoding":"gzip"})
    with urllib.request.urlopen(r,timeout=25) as z:
        b=z.read()
        if z.headers.get("Content-Encoding")=="gzip":
            b=gzip.GzipFile(fileobj=io.BytesIO(b)).read()
        return b.decode("utf-8","replace")
def probe(client,site="main_site",loc="en_US"):
    try:
        js=get("https://apps.bazaarvoice.com/deployments/%s/%s/production/%s/bv.js"%(client,site,loc))
    except urllib.error.HTTPError as e:
        return None,"HTTP %s"%e.code
    except Exception as e:
        return None,type(e).__name__
    dc=re.search(r'displayCode:"(\d+)"',js); sc=re.search(r'legacyScoutUrl:"([^"]+)"',js)
    if not sc: return None,"stub (displayCode %s, serves no reviews)"%(dc.group(1) if dc else "-")
    try: js2=get(sc.group(1))
    except Exception as e: return None,"scout %s"%type(e).__name__
    ks=[k for k in sorted(set(re.findall(r'passkey:"([A-Za-z0-9]{24,})',js2)))
        if k!="b8d57893b7754e2d390348dc172b7c76"]
    return (ks[0] if ks else None),("displayCode %s"%(dc.group(1) if dc else "-"))
GUESSES={
 "Freshpet (FRPT)":["freshpet"],
 "BARK (BARK)":["bark","barkbox"],
 "Cricut (CRCT)":["cricut"],
 "Purple (PRPL)":["purple","purpleinnovation"],
 "Sleep Number (SNBR)":["sleepnumber","selectcomfort"],
 "Tempur Sealy (TPX)":["tempurpedic","tempursealy","tempur"],
 "Peloton (PTON)":["peloton","onepeloton"],
 "BowFlex (BFX)":["bowflex","nautilus"],
 "e.l.f. Beauty (ELF)":["elfcosmetics","elf"],
 "Honest Co (HNST)":["honest","thehonestcompany"],
 "Crocs (CROX)":["crocs"],
 "Allbirds (BIRD)":["allbirds"],
 "Deckers/Hoka (DECK)":["hoka","hokaoneone","ugg","deckers"],
 "Skechers (SKX)":["skechers"],
 "On Holding (ONON)":["onrunning","on"],
 "Solo Brands (DTC)":["solostove","solobrands"],
 "Johnson Outdoors (JOUT)":["minnkota","johnsonoutdoors","humminbird"],
 "GoPro (GPRO)":["gopro"],
 "Garmin (GRMN)":["garmin"],
 "Arlo (ARLO)":["arlo"],
 "iRobot (IRBT)":["irobot","roomba"],
 "SharkNinja (SN)":["sharkclean","shark","ninjakitchen","sharkninja"],
 "Hamilton Beach (HBB)":["hamiltonbeach"],
 "Lifetime Brands (LCUT)":["farberware","lifetimebrands"],
 "WD-40 (WDFC)":["wd40","wd40company"],
 "Energizer (ENR)":["energizer"],
 "A.O. Smith (AOS)":["aosmith"],
 "Stanley B&D (SWK)":["dewalt","stanleyblackanddecker","craftsman","blackanddecker"],
 "Central Garden (CENT)":["centralgardenpet","pennington"],
}
hits=[]
for name,cs in GUESSES.items():
    got=False
    for c in cs:
        pk,note=probe(c)
        if pk:
            print("%-24s %-22s PASSKEY  %s  (%s)"%(name,c,pk[:20]+"...",note),flush=True)
            hits.append((name,c,pk)); got=True; break
        time.sleep(0.5)
    if not got:
        print("%-24s %-22s no deployment found on these guesses"%(name,"/".join(cs)),flush=True)
print("\n%d brands reached by direct probe"%len(hits))
for h in hits: print("  %s|%s|%s"%h)
