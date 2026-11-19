import os, sys
os.environ["STAGE_DELAY"]="0"
import engine; engine.DELAY=0
hist=[]
for i,f in enumerate(sorted(os.listdir("samples")),1):
    last=None
    for e in engine.process("samples/"+f,hist): last=e
    r=last["data"]; d=r["fields"]
    hist.append(dict(run_id=i,vendor_key=r["vendor_key"],invoice_no=d["invoice_no"],po=r["po"],total=d["total"],decision=r["decision"],date=d["date"]))
    print(f"{f:40s} {r['decision']:13s} | {d['invoice_no']} {d['date']} {d['total']} {r['po']}\n      ", "; ".join(r["reasons"])[:260])
