#!/usr/bin/env python
# -*- coding: utf-8 -*- ###########  T X   F L O W  ·  coinjoin.nl  ###########
#  Pull any Bitcoin transaction from mempool.space (or your own self-hosted    #
#  mempool) and animate its input -> output flow as ASCII.  Equal-value        #
#  "coinjoin" outputs are detected and highlighted.                            #
#     python txflow.py <txid> [--mempool URL] [--file tx.json] [--frames N]    #
###############################################################################
import sys, os, time, math, random, json, argparse, urllib.request
M = math
os.system("")
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

# ---- palette ----------------------------------------------------------------------
BG=(10,12,16); BRAND=(176,186,236); GREEN=(46,214,122); GLOW=(120,255,170)
ORANGE=(247,147,26); WHITE=(236,239,246); GREY=(110,120,134); DIM=(44,40,36)
BLUE=(96,156,236); RED=(232,92,104)
IN_COLS=[(86,150,240),(232,86,98),(240,158,48),(206,108,196),(74,200,200),(122,138,250),(232,120,150)]
def lerp(a,b,t): return (a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t)
def clamp8(c): return (max(0,min(255,int(c[0]))),max(0,min(255,int(c[1]))),max(0,min(255,int(c[2]))))
def smooth(u): u=0. if u<0 else 1. if u>1 else u; return u*u*(3-2*u)

def fmt(v):                                          # full value label
    return f"{v/1e8:.4f} BTC" if v >= 10_000_000 else f"{v:,} sats"
def cfmt(v):                                         # compact value label
    if v>=100_000_000: return f"{v/1e8:.2f}BTC"
    if v>=1_000_000:   return f"{v/1e6:.1f}M"
    if v>=1_000:       return f"{v//1000}k"
    return str(v)
def short(a):
    if not a: return "unknown"
    return a if len(a)<=15 else a[:7]+"…"+a[-4:]

# ---- data -------------------------------------------------------------------------
def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent":"txflow/1.0 (coinjoin.nl)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def parse_tx(tx):
    vin = tx.get("vin",[]) or []; vout = tx.get("vout",[]) or []
    coinbase = any(i.get("is_coinbase") for i in vin)
    ins=[(((i.get("prevout") or {}).get("value",0)), (i.get("prevout") or {}).get("scriptpubkey_address"), bool(i.get("is_coinbase"))) for i in vin]
    outs=[(o.get("value",0), o.get("scriptpubkey_address"), o.get("scriptpubkey_type","")) for o in vout]
    fee = tx.get("fee",0) or 0
    weight = tx.get("weight",0) or 0
    vsize = (weight+3)//4 if weight else (tx.get("size",0) or 0)
    total_out = sum(v for v,_,_ in outs)
    total_in = sum(v for v,_,_ in ins) if not coinbase else total_out+fee
    if coinbase: ins=[(total_in,"coinbase (newly minted)",True)]
    st = tx.get("status",{}) or {}
    return dict(txid=tx.get("txid",""), ins=ins, outs=outs, fee=fee, vsize=vsize,
                feerate=(fee/vsize if vsize else 0), confirmed=st.get("confirmed",False),
                height=st.get("block_height"), total_in=total_in, total_out=total_out, coinbase=coinbase)

def cap(items, maxn):                                # keep top-by-value, aggregate the rest
    items = sorted(items, key=lambda x: x[0], reverse=True)
    if len(items) <= maxn: return items, 0
    rest = items[maxn-1:]; agg=(sum(x[0] for x in rest), f"+{len(rest)} more", "agg")
    return items[:maxn-1]+[agg], len(rest)

# ---- layout -----------------------------------------------------------------------
W,H = 118, 40
X_IN, X_MIX, X_OUT = 26, 59, 92
TOP, BOT = 12, 37
CY = (TOP+BOT)/2
MAXN = 14

# CoinJoin shield: a "C" (top bar / left side / middle bars) flowing into a "J" point
LOGO = [
    "  ::::::::........",
    "  ::",
    "  ::",
    "  ::",
    "  -----:::::::::::",
    "  ::::::::::::....",
    "               -.",
    "    :=        *.",
    "     ::      ..",
    "      =::  ..:",
    "         ..",
]

def ypos(n):
    if n<=1: return [int(CY)]
    return [round(TOP+(BOT-TOP)*i/(n-1)) for i in range(n)]

def build(meta):
    ins, ni = cap(meta["ins"], MAXN)
    outs, no = cap(meta["outs"], MAXN)
    iy, oy = ypos(len(ins)), ypos(len(outs))
    # coinjoin / equal-output detection: any value repeated >=3x is an "equal" cluster
    from collections import Counter
    vals = Counter(v for v,_,_ in meta["outs"])
    clusters = {v for v,c in vals.items() if c >= 3 and v > 0}
    equal = sum(vals[v] for v in clusters)
    cj = equal >= 5                                   # enough uniform outputs to be a mix
    denoms = sorted(((vals[v], v) for v in clusters), reverse=True)
    maxout = max((v for v,_,_ in meta["outs"]), default=1) or 1
    def ocol(v,typ):
        if v in clusters: return GREEN
        if typ=="op_return" or v==0: return GREY
        if v >= 0.25*maxout: return ORANGE
        return BLUE
    nodes_in  = [(iy[k], IN_COLS[k%len(IN_COLS)], ins[k]) for k in range(len(ins))]
    nodes_out = [(oy[k], ocol(outs[k][0], outs[k][2]), outs[k]) for k in range(len(outs))]
    return dict(nin=nodes_in, nout=nodes_out, more_in=ni, more_out=no,
                cj=cj, denoms=denoms, center=GLOW if cj else (228,232,244))

# ---- render ----------------------------------------------------------------------
def blank(): return [[" "]*W for _ in range(H)], [[BG]*W for _ in range(H)]
def put(ch,col,r,c,s,color):
    for i,k in enumerate(str(s)):
        if 0<=r<H and 0<=c+i<W: ch[r][c+i]=k; col[r][c+i]=color
def rput(ch,col,r,c_end,s,color): put(ch,col,r,c_end-len(str(s)),s,color)
def dot(ch,col,r,c,g,color):
    r=int(round(r)); c=int(round(c))
    if 0<=r<H and 0<=c<W: ch[r][c]=g; col[r][c]=color
def funnel(t,yi,yo,off):
    ym=CY+off
    return yi+(ym-yi)*smooth(t/0.5) if t<0.5 else ym+(yo-ym)*smooth((t-0.5)/0.5)

def animate(meta, viz, source, frames):
    nin, nout = viz["nin"], viz["nout"]
    inw=[max(n[2][0],1) for n in nin]; outw=[max(n[2][0],1) for n in nout]
    center=viz["center"]; cj=viz["cj"]
    label = ("COINJOIN" if cj else "TRANSACTION")
    parts=[]; o=sys.stdout.write
    o("\x1b[?1049h\x1b[?25l\x1b[2J")
    try:
        f=0
        while frames==0 or f<frames:
            ch,col=blank(); pulse=0.5+0.5*M.sin(f*0.12)
            # faint sankey ribbons
            for (yi,_,_) in nin:
                for k in range(0,36):
                    t=k/70.0; dot(ch,col,funnel(t,yi,CY,0),X_IN+(X_OUT-X_IN)*t,"·",DIM)
            for (yo,_,_) in nout:
                for k in range(35,71):
                    t=k/70.0; dot(ch,col,funnel(t,CY,yo,0),X_IN+(X_OUT-X_IN)*t,"·",DIM)
            # spawn + advance coins
            for _ in range(5):
                i=random.choices(range(len(nin)),weights=inw)[0]
                j=random.choices(range(len(nout)),weights=outw)[0]
                parts.append([0.0,random.uniform(.012,.018),nin[i][0],nout[j][0],
                              random.uniform(-6,6),nin[i][1],nout[j][1]])
            parts=[p for p in parts if (p.__setitem__(0,p[0]+p[1]) or p[0]<1.02)]
            for t,sp,yi,yo,off,icl,ocl in parts:
                for k in range(4):
                    tt=t-k*0.016
                    if tt<=0 or tt>=1: continue
                    x=X_IN+(X_OUT-X_IN)*tt; y=funnel(tt,yi,yo,off)
                    if   tt<0.44: c=icl
                    elif tt<0.50: c=lerp(icl,center,(tt-0.44)/0.06)
                    elif tt<0.57: c=center
                    else:         c=ocl
                    dot(ch,col,y,x,"●" if k==0 else "·",clamp8(lerp(BG,c,1.0-k*0.30)))
            # mixing/tx bar + vertical label
            for r in range(TOP,BOT+1):
                cg=clamp8(lerp((22,104,68) if cj else (40,46,78), center, 0.35+0.4*pulse))
                for cc in (X_MIX-1,X_MIX,X_MIX+1): ch[r][cc]="█"; col[r][cc]=cg
            for i,k in enumerate(label):
                put(ch,col,int(CY)-len(label)//2+i,X_MIX,k,WHITE)
            # input chips + labels
            for (y,c,(v,addr,cb)) in nin:
                put(ch,col,y,X_IN,"█",c)
                rput(ch,col,y,X_IN-2, f"{short(addr)} {cfmt(v)}", lerp(c,WHITE,.35))
            # output chips + labels
            for (y,c,(v,addr,typ)) in nout:
                put(ch,col,y,X_OUT,"██",c)
                put(ch,col,y,X_OUT+3, f"{cfmt(v)} {short(addr)}", lerp(c,WHITE,.3))
            # header: shimmering CoinJoin shield (left) + a tidy data card (right)
            sweep = (f*0.7) % 28 - 4
            for r, row in enumerate(LOGO):
                base = lerp(BRAND, WHITE, 0.45 - 0.42*(r/(len(LOGO)-1)))
                for c, k in enumerate(row):
                    if k != " ":
                        sh = M.exp(-((c + r*0.6 - sweep)**2)/8.0)
                        ch[r][c] = k; col[r][c] = clamp8(lerp(base, (255,255,255), 0.65*sh))
            LABX, VALX = 28, 30
            put(ch,col,0,20,"CoinJoin",lerp(BRAND,WHITE,.45)); put(ch,col,0,29,"tx flow",GREY)
            rput(ch,col,0,W-2,"via "+source,GREY)
            stat = f"confirmed · block {meta['height']:,}" if meta["confirmed"] else "unconfirmed · in mempool"
            rput(ch,col,1,W-2,stat, GREEN if meta["confirmed"] else ORANGE)
            txid = (meta["txid"][:10]+"…"+meta["txid"][-8:]) if meta["txid"] else "(local)"
            rput(ch,col,3,LABX,"txid",GREY);  put(ch,col,3,VALX,txid, lerp(BRAND,WHITE,.1))
            rput(ch,col,4,LABX,"value",GREY); put(ch,col,4,VALX,f"in {fmt(meta['total_in'])}    →    out {fmt(meta['total_out'])}", lerp(BRAND,WHITE,.25))
            rput(ch,col,5,LABX,"fee",GREY);   put(ch,col,5,VALX,f"{meta['fee']:,} sats   ·   {meta['feerate']:.1f} sat/vB   ·   {meta['vsize']:,} vB", lerp(BRAND,WHITE,.25))
            if cj:
                denoms = "   ·   ".join(f"{c}× {fmt(v)}" for c,v in viz["denoms"][:3])
                rput(ch,col,6,LABX,"mix",GREY); put(ch,col,6,VALX,denoms+"   — equal, unlinkable", GREEN)
            put(ch,col,TOP-1,X_IN-4,f"{len(meta['ins'])} INPUTS",GREY)
            rput(ch,col,TOP-1,W-2,f"{len(meta['outs'])} OUTPUTS",GREY)
            # footer: brand tagline, centred
            tag = "coinjoin.nl    ·    great privacy for cheap mining fees"
            put(ch,col,H-1,(W-len(tag))//2,tag,lerp(BRAND,WHITE,.25))
            # emit
            out=["\x1b[?2026h\x1b[H"]
            for r in range(H):
                last=None; line=[]
                for c in range(W):
                    g=ch[r][c]
                    if g==" ": line.append(" "); continue
                    cc=col[r][c]
                    if cc!=last: line.append("\x1b[38;2;%d;%d;%dm"%cc); last=cc
                    line.append(g)
                out.append("".join(line)+"\x1b[0m")
            o("\n".join(out)+"\x1b[?2026l"); sys.stdout.flush()
            time.sleep(0.05); f+=1
    except KeyboardInterrupt:
        pass
    finally:
        o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")

# ---- main -------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Animate a Bitcoin tx flow from mempool.space.")
    ap.add_argument("txid", nargs="?", help="transaction id")
    ap.add_argument("--mempool", default="https://mempool.space", help="mempool base URL (self-hosted ok)")
    ap.add_argument("--file", help="load tx JSON from a file instead of fetching")
    ap.add_argument("--frames", type=int, default=0, help="frames to run (0 = forever)")
    a = ap.parse_args()
    base = a.mempool.rstrip("/")
    try:
        if a.file:
            tx = json.load(open(a.file, encoding="utf-8")); source = "file"
        else:
            if not a.txid: ap.error("provide a txid (or --file)")
            if len(a.txid)!=64 or any(c not in "0123456789abcdefABCDEF" for c in a.txid):
                ap.error("that doesn't look like a 64-hex-char txid")
            print(f"fetching {a.txid[:12]}... from {base} ...", file=sys.stderr)
            tx = fetch_json(f"{base}/api/tx/{a.txid}")
            source = base.split("//")[-1]
    except urllib.error.HTTPError as e:
        sys.exit(f"mempool returned HTTP {e.code} - is the txid correct / known to {base}?")
    except Exception as e:
        sys.exit(f"could not load transaction: {e}")
    meta = parse_tx(tx)
    if not meta["ins"] or not meta["outs"]:
        sys.exit("transaction has no inputs/outputs to draw.")
    animate(meta, build(meta), source, a.frames)

if __name__ == "__main__":
    main()
