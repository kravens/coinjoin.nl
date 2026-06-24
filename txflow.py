22#!/usr/bin/env python
# -*- coding: utf-8 -*- ###########  T X   F L O W  ·  coinjoin.nl  ###########
#  Pull any Bitcoin transaction from mempool.space (or your own self-hosted    #
#  mempool) and animate its input -> output flow as ASCII.  Equal-value        #
#  "coinjoin" outputs are detected & highlighted.  No txid -> live mempool.   #
#  Interactive: a/d walk chain, w/s scroll, 1-9/Tab pick branch, q quit.      #
#   txflow.py [txid] [--watch] [--depth N] [--export out.gif] [--mempool URL]  #
###############################################################################
import sys, os, time, math, random, json, argparse, shutil, urllib.request
M = math
FRAME = 1/21                                         # 21 FPS - one for every million bitcoin
os.system("")
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

# ---- palette ----------------------------------------------------------------------
BG=(10,12,16); BRAND=(176,186,236); GREEN=(46,214,122); GLOW=(120,255,170)
ORANGE=(247,147,26); WHITE=(236,239,246); GREY=(110,120,134); DIM=(44,40,36)
BLUE=(96,156,236); RED=(232,92,104); AMBER=(255,176,32)
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

def fetch_text(url, timeout=20):                     # endpoints returning a bare string (e.g. block hash)
    req = urllib.request.Request(url, headers={"User-Agent":"txflow/1.0 (coinjoin.nl)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode().strip()

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

def fetch_tx(txid, base): return fetch_json(f"{base}/api/tx/{txid}")

WHIRLPOOL_POOLS = {100_000, 1_000_000, 5_000_000, 50_000_000}   # 0.001/0.01/0.05/0.5 BTC

def _wabisabi_denoms(maxv=10**14):                   # WabiSabi standard denominations
    s = set()                                        # powers of 2, powers of 3, and 1/2/5 x 10^n
    for b in (2, 3):
        v = 1
        while v <= maxv: s.add(v); v *= b
    k = 1
    while k <= maxv: s.update((k, 2*k, 5*k)); k *= 10
    return frozenset(s)
WABISABI_DENOMS = _wabisabi_denoms()

def classify_cj(meta):                               # name the coinjoin protocol (or None)
    from collections import Counter
    outs = [v for v,_,_ in meta["outs"] if v > 0]; nin = len(meta["ins"]); nout = len(outs)
    if not outs: return (None, 0.0)
    if nin < 2: return (None, 0.0)                    # a coinjoin needs >=2 participants (inputs)
    vals = Counter(outs)
    clusters = sorted(((c, v) for v, c in vals.items() if c >= 2), reverse=True)
    eqcnt, eqval = (clusters[0] if clusters else (0, 0))
    if nin == 5 and nout == 5 and len(set(outs)) == 1 and outs[0] in WHIRLPOOL_POOLS:
        return ("Whirlpool 5x5", 0.99)                # Samourai Whirlpool: fixed-denom 5-in/5-out
    std_out = sum(1 for v in outs if v in WABISABI_DENOMS)        # outputs on the WabiSabi denom set
    std_denoms = len({v for v in outs if v in WABISABI_DENOMS})   # distinct standard denominations
    if nout >= 8 and eqcnt >= 2 and std_denoms >= 3 and std_out >= 0.75*nout:
        return ("Wasabi/WabiSabi", min(0.98, 0.6 + 0.02*nout))    # outputs match the standard denom set
    if eqcnt < 3: return (None, 0.0)
    if eqcnt >= 5 and 9_000_000 <= eqval <= 12_000_000:
        return ("Wasabi 1.x", 0.9)                    # ~0.1 BTC equal outputs
    if 2 <= eqcnt <= 20 and abs((nout - eqcnt) - eqcnt) <= 2 and nin <= 30:
        return ("JoinMarket", 0.8)                    # k equal outputs + ~k change outputs
    if eqcnt >= 5:
        return ("equal-output mix", min(0.85, 0.4 + 0.05*eqcnt))
    return (None, 0.0)

def is_cj(meta): return classify_cj(meta)[0] is not None

def is_batched(meta):                                # one input fanning out to many outputs
    return len(meta["ins"]) == 1 and sum(1 for v,_,_ in meta["outs"] if v > 0) >= 3

def is_payjoin(meta):                                # heuristic candidate: receiver co-funds (BIP78)
    ins = [v for v,_,cb in meta["ins"] if not cb]
    outs = [(v,t) for v,_,t in meta["outs"] if v > 0]
    if len(ins) < 2 or len(outs) != 2 or is_cj(meta): return False
    (v0,t0),(v1,t1) = outs
    if not (v0 != v1 and bool(t0) and t0 == t1): return False   # 2 unequal same-type outs (pay + change)
    return max(v0, v1) > max(ins)                    # an output exceeds any single input (inputs combined)

def coin_anon(fm, value):                            # anon-set of a `value` coin from its funding tx fm
    if not fm: return None                           # N equal outputs if coinjoin, else 1 (non-private)
    if is_cj(fm):
        from collections import Counter
        return Counter(v for v,_,_ in fm["outs"] if v > 0).get(value, 1)
    return 1

def ndenoms(meta):                                   # distinct equal-output denominations (>=2)
    from collections import Counter
    vc = Counter(v for v,_,_ in meta["outs"] if v > 0)
    return sum(1 for c in vc.values() if c >= 2)

SHORT_CJ = {"Whirlpool 5x5":"Whirlpool", "Wasabi/WabiSabi":"Wasabi", "Wasabi 1.x":"Wasabi1",
            "JoinMarket":"JoinMkt", "equal-output mix":"mix"}

# ---- layout (canvas scales to the terminal; sensible 118x40 default) ---------------
W = H = X_IN = X_MIX = X_OUT = TOP = BOT = MAXN = 0; CY = 0.0
def apply_canvas(w, h):
    global W, H, X_IN, X_MIX, X_OUT, TOP, BOT, CY, MAXN
    W, H = w, h
    X_IN = 26; X_OUT = W - 26; X_MIX = (X_IN + X_OUT) // 2
    TOP, BOT = 12, H - 3; CY = (TOP + BOT) / 2
    MAXN = max(8, (H - 14) * 9 // 16)                # inputs/outputs shown; grows with height
def term_canvas(interactive=True):                   # clamped terminal size, or the default
    if not interactive: return 118, 40
    try: c, l = shutil.get_terminal_size((118, 40))
    except Exception: c, l = 118, 40
    # reserve the terminal's bottom row: filling the last row (and the bottom-right cell)
    # makes some terminals (e.g. VS Code) scroll up by one line -> 1-row flicker.
    # caps sized to fill a 6K terminal (~760x216); ~15ms/frame, well inside the 21fps budget.
    return max(100, min(c, 760)), max(30, min(l - 1, 216))
apply_canvas(118, 40)                                # default until an interactive view sizes it

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

def window(items, maxn, off):                        # value-sorted slice (for up/down scroll)
    items = sorted(items, key=lambda x: x[0], reverse=True)
    n = len(items); off = max(0, min(off, max(0, n-maxn)))
    return items[off:off+maxn], off, max(0, n-off-maxn)   # shown, hidden-above, hidden-below

def build(meta, io_off=0):
    ins, ai, bi = window(meta["ins"], MAXN, io_off)
    outs, ao, bo = window(meta["outs"], MAXN, io_off)
    iy, oy = ypos(len(ins)), ypos(len(outs))
    # coinjoin / equal-output detection: any value repeated >=3x is an "equal" cluster
    from collections import Counter
    vals = Counter(v for v,_,_ in meta["outs"])
    clusters = {v for v,c in vals.items() if c >= 3 and v > 0}
    cjlabel, cjconf = classify_cj(meta)               # protocol name + confidence (or None)
    cj = cjlabel is not None
    denoms = sorted(((vals[v], v) for v in clusters), reverse=True)
    maxout = max((v for v,_,_ in meta["outs"]), default=1) or 1
    def ocol(v,typ):
        if v in clusters: return GREEN
        if typ=="op_return" or v==0: return GREY
        if v >= 0.25*maxout: return ORANGE
        return BLUE
    nodes_in  = [(iy[k], IN_COLS[k%len(IN_COLS)], ins[k]) for k in range(len(ins))]
    nodes_out = [(oy[k], ocol(outs[k][0], outs[k][2]), outs[k]) for k in range(len(outs))]
    return dict(nin=nodes_in, nout=nodes_out, cj=cj, denoms=denoms, cjlabel=cjlabel, cjconf=cjconf,
                center=GLOW if cj else (228,232,244), ai=ai, bi=bi, ao=ao, bo=bo)

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

def emit(o, ch, col):                                # paint a frame (RLE, synchronized)
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

def render_tx(ch, col, meta, viz, source, f, parts, hint=None, isel=None, osel=None, den_off=0):
    nin, nout = viz["nin"], viz["nout"]
    if not nin or not nout: return
    inw=[max(n[2][0],1) for n in nin]; outw=[max(n[2][0],1) for n in nout]
    center=viz["center"]; cj=viz["cj"]
    batched=(not cj) and is_batched(meta); payjoin=(not cj) and (not batched) and is_payjoin(meta)
    label="COINJOIN" if cj else ("BATCHED" if batched else "TRANSACTION")
    pulse=0.5+0.5*M.sin(f*0.12)
    for (yi,_,_) in nin:                               # faint sankey ribbons
        for k in range(0,36):
            t=k/70.0; dot(ch,col,funnel(t,yi,CY,0),X_IN+(X_OUT-X_IN)*t,"·",DIM)
    for (yo,_,_) in nout:
        for k in range(35,71):
            t=k/70.0; dot(ch,col,funnel(t,CY,yo,0),X_IN+(X_OUT-X_IN)*t,"·",DIM)
    for _ in range(5):                                # spawn + advance coins  (kind 0)
        i=random.choices(range(len(nin)),weights=inw)[0]
        j=random.choices(range(len(nout)),weights=outw)[0]
        parts.append([0.0,random.uniform(.012,.018),nin[i][0],nout[j][0],
                      random.uniform(-6,6),nin[i][1],nout[j][1],0])
    if meta["fee"] > 0:                               # amber fee stream -> the flame  (kind 1)
        nfee = max(5.0*meta["fee"]/max(meta["total_in"],1), 0.10)   # sized to the fee's share of value
        while nfee > 0:
            if (nfee >= 1 or random.random() < nfee):
                parts.append([0.0,random.uniform(.014,.020),random.choice(nin)[0],float(BOT),
                              0.0,AMBER,AMBER,1])
            nfee -= 1
    parts[:]=[p for p in parts if (p.__setitem__(0,p[0]+p[1]) or p[0]<1.02)]
    for t,sp,yi,yo,off,icl,ocl,kind in parts:
        for k in range(4):
            tt=t-k*0.016
            if tt<=0 or tt>=1: continue
            if kind:                                  # fee particle: input -> flame at the bar base
                x=X_IN+(X_MIX-X_IN)*tt; y=yi+(yo-yi)*smooth(tt); c=AMBER
            else:
                x=X_IN+(X_OUT-X_IN)*tt; y=funnel(tt,yi,yo,off)
                if   tt<0.44: c=icl
                elif tt<0.50: c=lerp(icl,center,(tt-0.44)/0.06)
                elif tt<0.57: c=center
                else:         c=ocl
            dot(ch,col,y,x,"●" if k==0 else "·",clamp8(lerp(BG,c,1.0-k*0.30)))
    for r in range(TOP,BOT+1):                        # mixing/tx bar + vertical label
        cg=clamp8(lerp((22,104,68) if cj else (40,46,78), center, 0.35+0.4*pulse))
        for cc in (X_MIX-1,X_MIX,X_MIX+1): ch[r][cc]="█"; col[r][cc]=cg
    for i,k in enumerate(label):
        put(ch,col,int(CY)-len(label)//2+i,X_MIX,k,WHITE)
    if meta["fee"] > 0:                               # mining fee = a slowly flickering flame at the bar base
        flick = 0.5 + 0.3*M.sin(f*0.22) + 0.1*M.sin(f*0.5)
        em_base = clamp8(lerp((214,55,18), (247,140,30), flick))               # glowing coals
        em_top  = clamp8(lerp((255,200,70), (255,240,150), flick))             # flame tongues
        FLAMES  = ["ʌ^▴", "▲ʌΛ", "Λ^ʌ", "^▴ʌ", "▴▲^"]                          # one rotating flame row
        PLAYFUL = ["_*_", "∞/₿", "*_*", "$_$", "~_~", "T_T", "^.^", ">_<", "Y.Y"]   # occasional spark / face
        period = f // 7
        glyphs = PLAYFUL[(period//6) % len(PLAYFUL)] if period % 6 == 5 else FLAMES[period % len(FLAMES)]
        for j, cc in enumerate((X_MIX-1, X_MIX, X_MIX+1)):
            ch[BOT][cc] = "█"; col[BOT][cc] = em_base
            if glyphs[j] != " ": ch[BOT-1][cc] = glyphs[j]; col[BOT-1][cc] = em_top
        dest = (f"→ block #{meta['height']:,}" if meta["confirmed"] and meta.get("height") else "→ next block")
        put(ch,col, BOT, X_MIX+3, "fee "+dest, clamp8(lerp(AMBER, WHITE, .25)))
    for k,(y,c,(v,addr,cb)) in enumerate(nin):        # input chips (◂ = selected branch)
        s = (k==isel); cc = clamp8(lerp(c,WHITE,.75)) if s else c
        put(ch,col,y,X_IN,"█",cc)
        rput(ch,col,y,X_IN-2, f"{short(addr)} {cfmt(v)}", lerp(cc,WHITE,.35))
        if s: put(ch,col,y,X_IN+1,"◂",WHITE)
    for k,(y,c,(v,addr,typ)) in enumerate(nout):      # output chips (▸ = selected branch)
        s = (k==osel); cc = clamp8(lerp(c,WHITE,.75)) if s else c
        put(ch,col,y,X_OUT,"██",cc)
        put(ch,col,y,X_OUT+3, f"{cfmt(v)} {short(addr)}", lerp(cc,WHITE,.3))
        if s: put(ch,col,y,X_OUT-1,"▸",WHITE)
    sweep = (f*0.7) % 28 - 4                           # shimmering shield + data card
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
    rput(ch,col,3,LABX,"txid",GREY);  put(ch,col,3,VALX, meta["txid"] or "(local)", lerp(BRAND,WHITE,.1))
    rput(ch,col,4,LABX,"value",GREY); put(ch,col,4,VALX,f"in {fmt(meta['total_in'])}    →    out {fmt(meta['total_out'])}", lerp(BRAND,WHITE,.25))
    rput(ch,col,5,LABX,"fee",GREY);   put(ch,col,5,VALX,f"{meta['fee']:,} sats   ·   {meta['feerate']:.1f} sat/vB   ·   {meta['vsize']:,} vB", lerp(BRAND,WHITE,.25))
    if cj:
        from collections import Counter
        vc = Counter(v for v,_,_ in meta["outs"] if v > 0)
        denoms = sorted(((c, v) for v, c in vc.items() if c >= 2), reverse=True)
        teq = sum(c for c, _ in denoms); nd = len(denoms)
        doff = max(0, min(den_off, max(0, nd-6))); win = denoms[doff:doff+6]   # w/s scroll window
        maxc = max((c for c, _ in denoms), default=1)         # two columns x 3 rows = 6 denoms
        rng = f"   [{doff+1}-{doff+len(win)}/{nd}]" if nd > 6 else ""
        rput(ch,col,6,LABX,"goggles",GREY)
        put(ch,col,6,VALX, f"◆ {viz['cjlabel']}  ({viz['cjconf']*100:.0f}%)   "
            f"{teq} equal outputs in {nd} denomination{'s' if nd!=1 else ''}  — unlinkable" + rng, GREEN)
        COLS = [(VALX, VALX+22), (VALX+44, VALX+66)]           # (label x, bar x) left / right
        track = clamp8(lerp(BG, GREEN, 0.18))
        def denbar(r, x, n):
            for j in range(16): put(ch,col,r,x+j,"·",track)   # faint gauge track
            w = max(1, round(16*n/maxc))
            for j in range(w): put(ch,col,r,x+j,"█", lerp(GLOW, GREEN, j/max(w-1,1)))
        for idx, (c, v) in enumerate(win):
            lx, bx = COLS[idx//3]; r = 7 + idx % 3
            put(ch,col,r,lx, f"{c:>3}× {fmt(v)}", lerp(GREEN,WHITE,.30))
            denbar(r, bx, c)
        if nd > 6:
            put(ch,col,10,VALX, f"▲ {doff} above    ▼ {nd-doff-len(win)} below    w/s scroll",
                lerp(GREEN,GREY,.4))
    elif batched:
        nbo = sum(1 for v,_,_ in meta["outs"] if v > 0)
        rput(ch,col,6,LABX,"type",GREY)
        put(ch,col,6,VALX, f"batched payment  —  1 input fans out to {nbo} outputs  (not a coinjoin)",
            lerp(ORANGE,WHITE,.25))
    elif payjoin:                                     # subtle hint only; PayJoin looks like a normal payment
        rput(ch,col,6,LABX,"hint",GREY)
        put(ch,col,6,VALX, "possibly a PayJoin (co-funded inputs?)  —  indistinguishable from a normal payment",
            lerp(GREY,WHITE,.25))
    ilab=f"{len(meta['ins'])} INPUTS"                  # counts + scroll window
    olab=f"{len(meta['outs'])} OUTPUTS"
    if viz["ai"] or viz["bi"]: ilab+=f"  [{viz['ai']+1}-{viz['ai']+len(nin)} by value]"
    if viz["ao"] or viz["bo"]: olab+=f"  [{viz['ao']+1}-{viz['ao']+len(nout)} by value]"
    put(ch,col,TOP-1,X_IN-4,ilab,GREY); rput(ch,col,TOP-1,W-2,olab,GREY)
    if hint: put(ch,col,H-2,(W-len(hint))//2,hint,lerp(BRAND,WHITE,.4))
    tag = "coinjoin.nl    ·    great privacy for cheap mining fees"
    put(ch,col,H-1,(W-len(tag))//2,tag,lerp(BRAND,WHITE,.25))

def animate(meta, viz, source, frames):              # non-interactive playback
    parts=[]; o=sys.stdout.write
    o("\x1b[?1049h\x1b[?25l\x1b[2J")
    try:
        f=0
        while frames==0 or f<frames:
            ch,col=blank(); render_tx(ch,col,meta,viz,source,f,parts)
            emit(o,ch,col); time.sleep(FRAME); f+=1
    except KeyboardInterrupt:
        pass
    finally:
        o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")

# ---- multi-tx graph (--depth; e/f expand/contract; per-column scroll-to-reveal) ----
def _g_raw(G, t):
    if t not in G["raw"]: G["raw"][t] = fetch_tx(t, G["base"])
    return G["raw"][t]
def _g_parsed(G, t):
    if t not in G["parsed"]:
        G["parsed"][t] = parse_tx(_g_raw(G, t))
        if G.get("log"): G["log"](len(G["parsed"]))
    return G["parsed"][t]
def _g_osp(G, t):
    if t not in G["osp"]:
        try: G["osp"][t] = fetch_json(f"{G['base']}/api/tx/{t}/outspends")
        except Exception: G["osp"][t] = []
    return G["osp"][t]
def _g_cj(G, t):
    if t not in G["cj"]: G["cj"][t] = is_cj(G["parsed"][t])
    return G["cj"][t]

def _g_expand_back(G):                               # one more ancestor level from the frontier
    d = G["bdepth"] + 1; cand = {}
    try:                                             # gathering candidates touches the network
        for t in [t for t, lv in G["placed"].items() if lv == -(d-1)]:
            for vin in _g_raw(G, t).get("vin", []):
                if vin.get("is_coinbase"): continue
                p = vin.get("txid")
                if not p: continue
                G["edges"].add((p, t))
                if p not in G["placed"]: cand[p] = cand.get(p, 0) + ((vin.get("prevout") or {}).get("value", 0))
    except Exception: return False
    ranked = sorted(cand, key=lambda k: cand[k], reverse=True); keep = 0
    for p in ranked[:G["capn"]]:
        try: _g_parsed(G, p)                         # parse BEFORE placing -> placed stays in sync
        except Exception: break
        G["placed"][p] = -d; keep += 1
    G["overflow"][-d] = ranked[G["capn"]:]
    if keep: G["bdepth"] = d
    return keep > 0

def _g_expand_fwd(G):                                # one more descendant level from the frontier
    d = G["fdepth"] + 1; cand = {}
    try:
        for t in [t for t, lv in G["placed"].items() if lv == (d-1)]:
            outs = _g_parsed(G, t)["outs"]
            for i, o in enumerate(_g_osp(G, t) or []):
                if o and o.get("spent") and o.get("txid"):
                    s = o["txid"]; G["edges"].add((t, s))
                    if s not in G["placed"]: cand[s] = cand.get(s, 0) + (outs[i][0] if i < len(outs) else 0)
    except Exception: return False
    ranked = sorted(cand, key=lambda k: cand[k], reverse=True); keep = 0
    for s in ranked[:G["capn"]]:
        try: _g_parsed(G, s)
        except Exception: break
        G["placed"][s] = d; keep += 1
    G["overflow"][d] = ranked[G["capn"]:]
    if keep: G["fdepth"] = d
    return keep > 0

def _g_expand(G):  return _g_expand_back(G) | _g_expand_fwd(G)     # 'e'
def _g_contract(G):                                  # 'f' : drop the outermost level both ways
    d = max(G["bdepth"], G["fdepth"])
    if d <= 0: return False
    for t in [t for t, lv in G["placed"].items() if abs(lv) == d]: del G["placed"][t]
    G["overflow"].pop(d, None); G["overflow"].pop(-d, None)
    G["bdepth"] = max((-lv for lv in G["placed"].values() if lv < 0), default=0)
    G["fdepth"] = max((lv for lv in G["placed"].values() if lv > 0), default=0)
    return True
def _g_reveal(G, lv):                                # scroll past a column edge -> fetch one overflow tx
    of = G["overflow"].get(lv) or []
    if not of: return None
    t = of[0]
    try: _g_parsed(G, t)                             # parse first; only place if the fetch succeeded
    except Exception: return None
    of.pop(0); G["placed"][t] = lv; return t

def build_graph(main_txid, base, depth, capn, log):
    G = dict(base=base, main=main_txid, capn=capn, raw={}, parsed={}, osp={}, cj={},
             placed={main_txid: 0}, edges=set(), overflow={}, bdepth=0, fdepth=0, log=log)
    _g_parsed(G, main_txid)
    for _ in range(depth): _g_expand_back(G)
    for _ in range(depth): _g_expand_fwd(G)
    return G

def animate_graph(G, source, frames):
    main_txid = G["main"]; base = G["base"]
    from collections import Counter, defaultdict, deque
    loff = defaultdict(int); sel = [main_txid]
    interactive = (frames == 0 and sys.stdin.isatty())
    apply_canvas(*term_canvas(interactive))
    getkey, restore = make_keyreader() if interactive else ((lambda: None), (lambda: None))
    HINT = "w/a/s/d move    Tab/S-Tab page    e/f expand/contract    space open tx    q quit"
    parts = []; o = sys.stdout.write; flash, flasht = "", 0
    o("\x1b[?1049h\x1b[?25l\x1b[2J")
    def layout():                                      # band + value-order per level + scroll window
        GT = 16; GB = H - 6; band = GB - GT          # node band; packs denser as it grows
        SP = 2.0 if band < 40 else max(1.0, 2.0 - (band-40)/50.0)   # ~2-row spacing -> ~1-row on big screens
        FITS = max(4, int(band / SP) + 1)
        def gy(n): return [(GT+GB)//2] if n<=1 else [round(GT+(GB-GT)*i/(n-1)) for i in range(n)]
        placed, parsed = G["placed"], G["parsed"]
        if sel[0] not in placed: sel[0] = main_txid
        levels = defaultdict(list)
        for t, lv in placed.items(): levels[lv].append(t)
        lvs = sorted(levels)
        ov = {lv: sorted(levels[lv], key=lambda t: parsed[t]["total_out"], reverse=True) for lv in lvs}
        XL, XR = 8, W-9
        xs = {lv: round(XL + (XR-XL)*(i/max(len(lvs)-1, 1))) for i, lv in enumerate(lvs)}
        for lv in lvs: loff[lv] = max(0, min(loff[lv], max(0, len(ov[lv])-FITS)))
        slv = placed[sel[0]]; sr = ov[slv].index(sel[0])      # keep selection inside its window
        if sr < loff[slv]: loff[slv] = sr
        elif sr >= loff[slv]+FITS: loff[slv] = sr-FITS+1
        pos, vis = {}, {}
        for lv in lvs:
            win = ov[lv][loff[lv]:loff[lv]+FITS]; vis[lv] = win
            for t, y in zip(win, gy(len(win))): pos[t] = (xs[lv], y)
        cjset = {t for t in placed if _g_cj(G, t)}
        return placed, parsed, lvs, ov, xs, pos, vis, cjset, GT, GB
    try:
        f = 0
        while frames == 0 or f < frames:
            nw, nh = term_canvas(interactive)          # adapt to a terminal resize
            if (nw, nh) != (W, H): apply_canvas(nw, nh); o("\x1b[2J")
            placed, parsed, lvs, ov, xs, pos, vis, cjset, GT, GB = layout()
            k = getkey()
            if k == "QUIT": break
            elif k == "SPACE":                         # open the selected tx in the explorer
                restore()
                try: explore(sel[0], base, source)
                except Exception as e: flash, flasht = f"could not open {sel[0][:10]}...: {e}", 40
                getkey, restore = make_keyreader() if interactive else ((lambda: None), (lambda: None))
                o("\x1b[?1049h\x1b[?25l\x1b[2J"); parts.clear()
            elif k == "EXPAND":
                if len(placed) > 160: flash, flasht = "graph is large - contract (f) before expanding further", 36
                elif _g_expand(G): flash, flasht = "expanded one level each direction", 24
                else: flash, flasht = "no more connected transactions to load", 24
            elif k == "CONTRACT":
                if _g_contract(G): flash, flasht = "contracted the outermost level", 24
                else: flash, flasht = "nothing to contract - only the central tx", 24
            elif k in ("LEFT", "RIGHT"):
                back = (k == "LEFT"); lv = placed[sel[0]]
                side = [L for L in lvs if (L < lv if back else L > lv)]
                if side:                                   # move to the adjacent level
                    L = max(side) if back else min(side)
                    sr = ov[lv].index(sel[0]); sel[0] = ov[L][min(sr, len(ov[L])-1)]
                elif len(placed) > 160:
                    flash, flasht = "graph is large - contract (f) before expanding further", 36
                elif (_g_expand_back(G) if back else _g_expand_fwd(G)):   # at the edge -> expand this way
                    nlv = -G["bdepth"] if back else G["fdepth"]
                    new = sorted([t for t, l in G["placed"].items() if l == nlv],
                                 key=lambda t: G["parsed"][t]["total_out"], reverse=True)
                    if new:
                        sr = ov[lv].index(sel[0]); sel[0] = new[min(sr, len(new)-1)]
                    flash, flasht = ("expanded one level earlier  ←" if back else "expanded one level later  →"), 24
                else:
                    flash, flasht = ("no earlier funding txs to load" if back else "no later spending txs to load"), 18
            elif k in ("UP", "DOWN"):
                lv = placed[sel[0]]; lst = ov[lv]; i = lst.index(sel[0])
                if k == "DOWN" and i == len(lst)-1:                 # scroll past the column edge
                    rv = _g_reveal(G, lv)
                    if rv: sel[0] = rv; flash, flasht = "revealed another tx at this level", 20
                    else: flash, flasht = "no more transactions at this level", 18
                else:
                    sel[0] = lst[max(0, min(len(lst)-1, i + (-1 if k == "UP" else 1)))]
            elif k in ("TAB", "STAB"):                 # page through the column (reveal more going down)
                lv = placed[sel[0]]; step = max(1, len(vis[lv])); cur = ov[lv]; i = cur.index(sel[0])
                if k == "STAB":
                    sel[0] = cur[max(0, i - step)]
                else:
                    target = i + step; cnt = len(cur); rv = 0
                    while target >= cnt and rv < 20:   # reveal overflow to land a page lower
                        if not _g_reveal(G, lv): break
                        cnt += 1; rv += 1
                    lst2 = sorted([t for t, l in G["placed"].items() if l == lv],
                                  key=lambda t: G["parsed"][t]["total_out"], reverse=True)
                    sel[0] = lst2[min(target, len(lst2)-1)]
                    if rv: flash, flasht = "revealed %d more tx at this level" % rv, 20
            placed, parsed, lvs, ov, xs, pos, vis, cjset, GT, GB = layout()   # reflect any mutation now
            elist = [(a, b) for (a, b) in G["edges"] if a in pos and b in pos]
            adj = defaultdict(list)
            for (a, b) in elist: adj[a].append(b); adj[b].append(a)
            def lineage(node):
                if node == main_txid or node not in adj: return {node}, set()
                prev = {node: None}; q = deque([node])
                while q:
                    u = q.popleft()
                    if u == main_txid: break
                    for v in adj[u]:
                        if v not in prev: prev[v] = u; q.append(v)
                if main_txid not in prev: return {node}, set()
                nodes, pe, u = set(), set(), main_txid
                while u is not None:
                    nodes.add(u); p = prev[u]
                    if p is not None: pe.add(frozenset((u, p)))
                    u = p
                return nodes, pe
            anon = defaultdict(int); arounds = defaultdict(int); aper = defaultdict(list)
            for t in cjset:
                for v, c in Counter(vv for vv,_,_ in parsed[t]["outs"] if vv > 0).items():
                    if c >= 2: anon[v] += c; arounds[v] += 1; aper[v].append(c)
            anon_rows = sorted(anon.items(), key=lambda kv: kv[1], reverse=True)[:5]
            maxan = max((a for _, a in anon_rows), default=1)
            m = parsed[main_txid]
            ch, col = blank(); sweep = (f*0.7) % 28 - 4
            pnodes, pedges = lineage(sel[0])           # the selected node's path back to the central tx
            hops = max(0, len(pnodes)-1); cjhops = sum(1 for t in pnodes if t in cjset)
            for (a, b) in elist:                       # ribbons: lineage glows green, rest dimmed
                on = frozenset((a, b)) in pedges
                ec = clamp8(lerp(BG, GREEN, .55)) if on else clamp8(lerp(BG, BRAND, .12))
                ax, ay = pos[a]; bx, by = pos[b]; n = max(abs(bx-ax), abs(by-ay)) or 1
                for s in range(n+1):
                    tt = s/n; dot(ch, col, ay+(by-ay)*tt, ax+(bx-ax)*tt, "·", ec)
            flowset = [e for e in elist if frozenset(e) in pedges] or elist
            fw = [max(parsed[a]["total_out"], 1) for (a, b) in flowset]
            for _ in range(8):                         # coins flow along the lineage path
                a, b = flowset[random.choices(range(len(flowset)), weights=fw)[0]]
                parts.append([0.0, random.uniform(.02,.035), pos[a], pos[b], GREEN])
            parts = [p for p in parts if (p.__setitem__(0, p[0]+p[1]) or p[0] < 1.05)]
            for t, sp, (ax, ay), (bx, by), pc in parts:
                for k2 in range(3):
                    tt = t - k2*0.05
                    if tt <= 0 or tt >= 1: continue
                    dot(ch, col, ay+(by-ay)*tt, ax+(bx-ax)*tt, "●" if k2==0 else "·", clamp8(lerp(BG, pc, 1.0-k2*0.33)))
            selh = 1 if (GB - GT) < 40 else 0          # selected node 3-tall when sparse, 1-tall when dense
            for t, (x, y) in pos.items():              # tx nodes (green = coinjoin; labels on side)
                cjn = t in cjset; selnode = (t == sel[0]); mainnode = (t == main_txid)
                c = GREEN if cjn else BRAND
                if selnode:
                    bc = clamp8(lerp(c, WHITE, .8))
                    for dy in range(-selh, selh+1):
                        if 0 <= y+dy < H: ch[y+dy][x] = "█"; col[y+dy][x] = bc
                    kind = "COINJOIN" if cjn else ("BATCH" if is_batched(parsed[t]) else "TX")
                    rput(ch, col, y, x-3, "» " + kind + " «", ORANGE)
                    put(ch, col, y, x+3, cfmt(parsed[t]["total_out"]), WHITE)
                else:
                    bb = .4 if mainnode else (.32 if t in pnodes else 0.0)
                    ch[y][x] = "█"; col[y][x] = clamp8(lerp(c, WHITE, bb))
                    if mainnode: put(ch, col, y, x+2, "main", GREY)
            for lv in lvs:                             # per-column scroll cues (hidden + revealable txs)
                x = xs[lv]; above = loff[lv]
                below = (len(ov[lv]) - loff[lv] - len(vis[lv])) + len(G["overflow"].get(lv, []))
                if above > 0: put(ch, col, GT-1, x-1, f"▲{above}", GREY)
                if below > 0: put(ch, col, GB+1, x-1, f"▼{below}", GREY)
            for r, row in enumerate(LOGO):             # shimmering shield + header
                bs = lerp(BRAND, WHITE, 0.45 - 0.42*(r/(len(LOGO)-1)))
                for c, kk in enumerate(row):
                    if kk != " ":
                        sh = M.exp(-((c + r*0.6 - sweep)**2)/8.0)
                        ch[r][c] = kk; col[r][c] = clamp8(lerp(bs, (255,255,255), 0.65*sh))
            put(ch,col,0,20,"CoinJoin",lerp(BRAND,WHITE,.45)); put(ch,col,0,29,"tx flow",GREY)
            rput(ch,col,0,W-2,"via "+source,GREY)
            put(ch,col,1,20,f"transaction graph  ·  depth -{G['bdepth']}/+{G['fdepth']}  ·  {len(placed)} txs",lerp(BRAND,WHITE,.2))
            rput(ch,col,1,W-2,"← earlier        later →",GREY)
            ms = parsed.get(sel[0], m); cjl = classify_cj(ms)[0]
            put(ch,col,3,20,"selected  "+sel[0],lerp(BRAND,WHITE,.1))
            info = f"{fmt(ms['total_in'])} in  ·  {fmt(ms['total_out'])} out  ·  fee {ms['fee']:,} sats ({ms['feerate']:.1f} sat/vB)"
            put(ch,col,4,20,info,lerp(BRAND,WHITE,.25))
            if cjl: put(ch,col,4,20+len(info)+3,"◆ "+cjl,GREEN)
            if sel[0] == main_txid:
                put(ch,col,5,20,"this is the central transaction",lerp(GREEN,WHITE,.35))
            else:
                put(ch,col,5,20,f"lineage  {hops} hop{'s' if hops!=1 else ''} to central tx  ·  "
                    f"{cjhops} coinjoin round{'s' if cjhops!=1 else ''} on this path",lerp(GREEN,WHITE,.35))
            if anon_rows:                              # cumulative anonymity-set across all rounds
                nr = len(cjset)
                put(ch,col,6,20,"CUMULATIVE ANONYMITY SET",lerp(GREEN,WHITE,.4))
                put(ch,col,6,46,f"·  {nr} coinjoin round{'s' if nr!=1 else ''} in view  ·  green nodes = coinjoins",GREY)
                put(ch,col,7,20,"denomination",GREY); put(ch,col,7,38,"rounds",GREY)
                put(ch,col,7,52,"per-round = total",GREY); put(ch,col,7,74,"combined anon-set",GREY)
                sel_vc = Counter(vv for vv,_,_ in ms["outs"] if vv > 0) if sel[0] in cjset else {}
                for i, (v, a) in enumerate(anon_rows):
                    y = 8+i
                    put(ch,col,y,20, fmt(v), lerp(GREEN,WHITE,.3))
                    put(ch,col,y,38, f"{arounds[v]}x", GREY)
                    bd = "+".join(str(x) for x in sorted(aper[v], reverse=True)[:4]) + ("+…" if len(aper[v])>4 else "")
                    put(ch,col,y,52, f"{bd} = {a}", lerp(GREEN,WHITE,.35))
                    bx = 74; w = max(1, round(28*a/maxan))
                    for j in range(w): put(ch,col,y,bx+j,"█", lerp(GLOW,GREEN, j/max(w-1,1)))
                    cc = sel_vc.get(v, 0)
                    if cc: put(ch,col,y,bx+w+1, f"+{cc} this round", WHITE)
            if interactive:
                hb = flash if flasht > 0 else HINT
                put(ch,col,H-2,(W-len(hb))//2,hb,lerp(BRAND,WHITE,.4))
                if flasht > 0: flasht -= 1
            tag = "coinjoin.nl    ·    great privacy for cheap mining fees"
            put(ch,col,H-1,(W-len(tag))//2,tag,lerp(BRAND,WHITE,.25))
            emit(o, ch, col)
            time.sleep(FRAME); f += 1
    except KeyboardInterrupt:
        pass
    finally:
        restore(); o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")

# ---- interactive explorer (w/a/s/d) -----------------------------------------------
def parent_of(raw):                                  # funding tx of the largest input
    best, bv = None, -1
    for vin in raw.get("vin", []):
        if vin.get("is_coinbase"): continue
        p = vin.get("txid"); v = (vin.get("prevout") or {}).get("value", 0)
        if p and v > bv: bv, best = v, p
    return best

def child_of(txid, gosp, gmeta):                     # spending tx of the largest output
    osp = gosp(txid); outs = gmeta(txid)["outs"]; best, bv = None, -1
    for i, o in enumerate(osp or []):
        if o and o.get("spent") and o.get("txid"):
            v = outs[i][0] if i < len(outs) else 0
            if v > bv: bv, best = v, o["txid"]
    return best

def make_keyreader():                                # non-blocking w/a/s/d controls
    KEYS = {"w":"UP","a":"LEFT","s":"DOWN","d":"RIGHT",
            "W":"UP","A":"LEFT","S":"DOWN","D":"RIGHT",
            "e":"EXPAND","E":"EXPAND","f":"CONTRACT","F":"CONTRACT","c":"COINBASE","C":"COINBASE",
            "\t":"TAB"," ":"SPACE","q":"QUIT","Q":"QUIT","\x1b":"QUIT"}
    for _d in "123456789": KEYS[_d] = _d
    def consume(nextc):                              # we just read ESC; swallow the whole sequence
        a = nextc()
        if a == "": return "QUIT"                    # bare Esc
        if a != "[":
            if a == "O": nextc()                     # SS3 function key
            return None
        seq = ""                                     # CSI: read params until a final byte @..~
        while True:
            c = nextc()
            if c == "": break
            seq += c
            if "\x40" <= c <= "\x7e": break
        if seq == "Z": return "STAB"                 # Shift+Tab (back-tab)
        if seq == "M": nextc(); nextc(); nextc()     # X10 mouse: 3 coordinate bytes follow
        if seq == "200~":                            # bracketed paste: swallow until 201~
            buf = ""
            while True:
                c = nextc()
                if c == "": break
                buf += c
                if buf.endswith("\x1b[201~"): break
        return None                                  # arrows / mouse / pastes -> ignore
    def first_key(chars):                            # first real key in a small (non-paste) burst
        i = [0]
        def nx():
            if i[0] < len(chars): i[0] += 1; return chars[i[0]-1]
            return ""
        while i[0] < len(chars):
            ch = nx()
            if ch in ("\x00", "\xe0"):               # extended key: scancode follows
                if nx() == "\x0f": return "STAB"     # Shift+Tab = back-tab
                continue
            if ch == "\x1b":
                t = consume(nx)
                if t: return t
                continue
            k = KEYS.get(ch)
            if k: return k
        return None
    PASTE = 8                                        # >8 chars buffered in one tick = a paste, ignore
    sys.stdout.write("\x1b[?2004h"); sys.stdout.flush()   # bracketed paste on (belt-and-suspenders)
    if os.name == "nt":
        import msvcrt
        hin = mode0 = _k32 = None                    # turn off console mouse + quick-edit so the
        try:                                         # scroll wheel / clicks don't stall getwch()
            import ctypes
            _k32 = ctypes.windll.kernel32; hin = _k32.GetStdHandle(-10)
            m = ctypes.c_uint(); _k32.GetConsoleMode(hin, ctypes.byref(m)); mode0 = m.value
            _k32.SetConsoleMode(hin, (mode0 | 0x0080) & ~0x0010 & ~0x0040)   # EXTENDED, ~MOUSE, ~QUICK_EDIT
        except Exception:
            hin = mode0 = _k32 = None
        def get():
            if not msvcrt.kbhit(): return None
            chars = []                               # drain everything buffered this tick
            while msvcrt.kbhit() and len(chars) < 256:
                try: chars.append(msvcrt.getwch())
                except Exception: break
            return None if len(chars) > PASTE else first_key(chars)
        def restore():
            sys.stdout.write("\x1b[?2004l"); sys.stdout.flush()
            if _k32 is not None and hin is not None and mode0 is not None:
                try: _k32.SetConsoleMode(hin, mode0)
                except Exception: pass
        return get, restore
    try:
        import termios, tty, select
        fd = sys.stdin.fileno(); old = termios.tcgetattr(fd); tty.setcbreak(fd)
        def get():
            chars = []
            while select.select([sys.stdin], [], [], 0)[0] and len(chars) < 256:
                chars.append(sys.stdin.read(1))
            if not chars: return None
            return None if len(chars) > PASTE else first_key(chars)
        def restore():
            sys.stdout.write("\x1b[?2004l"); sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return get, restore
    except Exception:
        return (lambda: None), (lambda: None)

def explore(txid, base, source):
    rawc, metac, ospc = {}, {}, {}
    def graw(t):
        if t not in rawc: rawc[t] = fetch_tx(t, base)
        return rawc[t]
    def gmeta(t):
        if t not in metac: metac[t] = parse_tx(graw(t))
        return metac[t]
    def gosp(t):
        if t not in ospc:
            try: ospc[t] = fetch_json(f"{base}/api/tx/{t}/outspends")
            except Exception: ospc[t] = []
        return ospc[t]
    cur, io_off, sel = txid, 0, 0
    meta = gmeta(cur)                                 # initial fetch (may raise -> caught in main)
    interactive = sys.stdin.isatty(); apply_canvas(*term_canvas(interactive))
    getkey, restore = make_keyreader()
    HINT = "a/d walk    w/s move ◂▸    Tab page    e graph    space address    c fee-block    q quit"
    o = sys.stdout.write; o("\x1b[?1049h\x1b[?25l\x1b[2J")
    parts = []; viz = build(meta, io_off); flash, flasht = "", 0
    def back_targets():                               # funding txids, largest input first
        items = [(((vin.get("prevout") or {}).get("value",0)), vin.get("txid"))
                 for vin in graw(cur).get("vin", []) if not vin.get("is_coinbase") and vin.get("txid")]
        return [t for _, t in sorted(items, reverse=True)]
    def fwd_targets():                                # spending txids, largest output first
        outs = gmeta(cur)["outs"]; osp = gosp(cur)
        order = sorted(range(len(outs)), key=lambda i: outs[i][0], reverse=True)
        res = []
        for i in order:
            oo = osp[i] if i < len(osp) else None
            res.append(oo["txid"] if (oo and oo.get("spent") and oo.get("txid")) else None)
        return res
    def follow():                                     # scroll the window to keep the cursor visible
        nonlocal io_off, viz
        no = io_off
        if sel < io_off: no = sel
        elif sel >= io_off + MAXN: no = sel - MAXN + 1
        no = max(0, min(no, max(0, max(len(meta["ins"]), len(meta["outs"])) - MAXN, ndenoms(meta) - 6)))
        if no != io_off: io_off = no; viz = build(meta, io_off); parts.clear()
    try:
        f = 0
        while True:
            k = getkey()
            if k == "QUIT": break
            elif k in ("UP", "DOWN"):                     # move the ◂▸ cursor; window follows it
                maxtotal = max(len(meta["ins"]), len(meta["outs"]))
                sel = max(0, min(maxtotal-1, sel + (-1 if k == "UP" else 1))); follow()
            elif k == "TAB":                              # page forward through inputs/outputs
                maxtotal = max(len(meta["ins"]), len(meta["outs"]))
                if maxtotal > MAXN: sel = (sel + MAXN) % maxtotal; follow()
            elif k == "STAB":                             # Shift+Tab: page backward
                maxtotal = max(len(meta["ins"]), len(meta["outs"]))
                if maxtotal > MAXN: sel = (sel - MAXN) % maxtotal; follow()
            elif k == "LEFT":
                tg = back_targets(); i = sel
                p = tg[i] if 0 <= i < len(tg) else (tg[0] if tg else None)
                try: p and gmeta(p)
                except Exception: p = None
                if p: cur, io_off, sel = p, 0, 0; meta = gmeta(cur); viz = build(meta, io_off); parts.clear(); flash, flasht = "◀  walked back through input #%d" % (i+1), 30
                else: flash, flasht = "no input at this row (coinbase / out of range)", 24
            elif k == "RIGHT":
                tg = fwd_targets(); i = sel
                c = tg[i] if 0 <= i < len(tg) else None
                try: c and gmeta(c)
                except Exception: c = None
                if c: cur, io_off, sel = c, 0, 0; meta = gmeta(cur); viz = build(meta, io_off); parts.clear(); flash, flasht = "walked forward through output #%d  ▶" % (i+1), 30
                else: flash, flasht = "that output isn't spent yet (tip of the chain)", 24
            elif k == "SPACE":                            # dive into the selected address view
                addr = None
                if sel < len(viz["nout"]): addr = viz["nout"][sel][2][1]
                if not addr and sel < len(viz["nin"]): addr = viz["nin"][sel][2][1]
                if addr and addr[:3] in ("bc1","tb1","bcr") or (addr and addr[:1] in ("1","3")):
                    restore()
                    try: explore_address(addr, base, source)
                    except Exception as e: flash, flasht = f"address view failed: {e}", 40
                    getkey, restore = make_keyreader(); o("\x1b[?1049h\x1b[?25l\x1b[2J"); parts.clear()
                else:
                    flash, flasht = "no address on this output (op_return / coinbase?)", 24
            elif k == "EXPAND":                           # zoom out to the connected-tx graph
                restore()
                o("\x1b[2J\x1b[H\x1b[38;2;176;186;236mbuilding transaction graph from this tx ...\x1b[0m"); sys.stdout.flush()
                try: animate_graph(build_graph(cur, base, 1, 8, lambda n: None), source, 0)
                except Exception as e: flash, flasht = f"graph view failed: {e}", 40
                getkey, restore = make_keyreader(); o("\x1b[?1049h\x1b[?25l\x1b[2J"); parts.clear()
            elif k == "COINBASE":                         # follow the fee to its block's coinbase tx
                if meta["confirmed"] and meta.get("height"):
                    bheight = meta["height"]; cb = cbm = None
                    try:
                        bh = fetch_text(f"{base}/api/block-height/{bheight}")
                        cb = ((fetch_json(f"{base}/api/block/{bh}/txs/0") or [{}])[0]).get("txid")
                        if cb: cbm = gmeta(cb)        # fetch the coinbase tx inside the guard
                    except Exception as e: cbm = None; flash, flasht = f"could not load coinbase: {e}", 40
                    if cbm:
                        cur, io_off, sel = cb, 0, 0; meta = cbm; viz = build(meta, io_off)
                        parts.clear(); flash, flasht = "▲  followed the fee to the block #%d coinbase" % bheight, 30
                else:
                    flash, flasht = "fee goes to the next block (this tx is still unconfirmed)", 24
            nw, nh = term_canvas(interactive)             # terminal resized -> resize the canvas
            if (nw, nh) != (W, H): apply_canvas(nw, nh); o("\x1b[2J"); viz = build(meta, io_off); parts.clear()
            ch, col = blank()
            vsel = sel - io_off                            # cursor row inside the visible window
            isel = vsel if 0 <= vsel < len(viz["nin"]) else None
            osel = vsel if 0 <= vsel < len(viz["nout"]) else None
            render_tx(ch, col, meta, viz, source, f, parts,
                      hint=(flash if flasht > 0 else HINT), isel=isel, osel=osel, den_off=io_off)
            emit(o, ch, col)
            if flasht > 0: flasht -= 1
            time.sleep(FRAME); f += 1
    except KeyboardInterrupt:
        pass
    finally:
        restore(); o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")

# ---- address privacy view (space-dive from a tx output) ---------------------------
WARN = (255, 92, 92)                                 # vivid red for privacy warnings

def address_report(addr, base):
    info = fetch_json(f"{base}/api/address/{addr}")
    cs = info.get("chain_stats", {}) or {}; mp = info.get("mempool_stats", {}) or {}
    def s(k): return cs.get(k, 0) + mp.get(k, 0)
    funded, spent, txcount = s("funded_txo_count"), s("spent_txo_count"), s("tx_count")
    balance = s("funded_txo_sum") - s("spent_txo_sum")
    try: utxos = sorted(fetch_json(f"{base}/api/address/{addr}/utxo") or [], key=lambda u: u.get("value",0), reverse=True)
    except Exception: utxos = []
    try: atxs = fetch_json(f"{base}/api/address/{addr}/txs") or []
    except Exception: atxs = []
    provmeta = {}                                    # txid -> parsed funding tx (for provenance + anon-set)
    def fmeta(txid):
        if txid not in provmeta:
            try: provmeta[txid] = parse_tx(fetch_json(f"{base}/api/tx/{txid}"))
            except Exception: provmeta[txid] = None
        return provmeta[txid]
    npriv = nnon = 0
    for u in utxos[:12]:                             # bounded provenance classification (anon-set)
        a = coin_anon(fmeta(u.get("txid")), u.get("value", 0))
        if a is None: continue
        npriv += a > 1; nnon += a == 1
    consol = 0                                        # spent-together merges, EXCLUDING coinjoin rounds
    for tx in atxs[:12]:
        if len(tx.get("vin", [])) >= 3 and any(((vin.get("prevout") or {}).get("scriptpubkey_address") == addr) for vin in tx.get("vin", [])):
            if not is_cj(parse_tx(tx)): consol += 1   # entering a coinjoin is a mix, not a consolidation
    warns = []
    if funded > 1: warns.append(f"ADDRESS REUSE - received {funded} times to one address; links those payments")
    if npriv > 0 and nnon > 0: warns.append(f"MIXED COINS - holds {npriv} private (coinjoin) + {nnon} non-private UTXOs; consolidating delinks your mix")
    if consol > 0: warns.append(f"CONSOLIDATION - {consol} non-coinjoin tx merged this address with other inputs; ties identities together")
    if len(utxos) > 1 and funded <= 1: warns.append(f"{len(utxos)} UTXOs on one address - spending them together links them")
    txrows = []                                      # recent history (already fetched; ~50 max, no extra calls)
    for tx in atxs:
        m = parse_tx(tx)
        recv = sum(v for v, a, _ in m["outs"] if a == addr)
        sent = sum(v for v, a, _ in m["ins"] if a == addr)
        rv = [v for v, a, _ in m["outs"] if a == addr]
        anon = coin_anon(m, max(rv)) if (recv > sent and rv) else None    # anon-set of the received coin
        st = tx.get("status", {}) or {}
        txrows.append(dict(txid=m["txid"], net=recv-sent, cj=classify_cj(m)[0], anon=anon,
                           height=st.get("block_height"), confirmed=st.get("confirmed", False)))
    return dict(addr=addr, balance=balance, funded=funded, spent=spent, txcount=txcount,
                utxos=utxos, prov=provmeta, npriv=npriv, nnon=nnon, warns=warns, txrows=txrows)

def explore_address(addr, base, source):
    print(f"analyzing privacy of {addr[:18]}... ...", file=sys.stderr)
    r = address_report(addr, base)                   # one-shot fetch + analysis (may raise -> caller)
    utxos = r["utxos"]; prov = r["prov"]; warns = r["warns"]; txrows = r["txrows"]
    mode = "utxo" if utxos else "tx"                  # empty address -> browse its tx history instead
    items = utxos if utxos else txrows
    interactive = sys.stdin.isatty(); apply_canvas(*term_canvas(interactive))
    getkey, restore = make_keyreader()
    o = sys.stdout.write; o("\x1b[?1049h\x1b[?25l\x1b[2J")
    LIST = 12; sel = 0; off = 0
    try:
        f = 0
        while True:
            k = getkey()
            if k == "QUIT": break
            elif k == "DOWN" and items: sel = min(len(items)-1, sel+1)
            elif k == "UP" and items: sel = max(0, sel-1)
            elif k == "SPACE" and items:
                tid = items[sel].get("txid")
                if tid:
                    restore()
                    try: explore(tid, base, source)
                    except Exception: pass
                    getkey, restore = make_keyreader(); o("\x1b[?1049h\x1b[?25l\x1b[2J")
            nw, nh = term_canvas(interactive)             # resize -> grow the visible list
            if (nw, nh) != (W, H): apply_canvas(nw, nh); o("\x1b[2J")
            VIS = H - 3 - LIST                            # rows shown; scales with terminal height
            off = max(0, min(off, sel)) if sel < off+VIS else sel-VIS+1
            if sel < off: off = sel
            ch, col = blank(); sweep = (f*0.7) % 28 - 4
            for rr, row in enumerate(LOGO):              # shimmering shield
                bs = lerp(BRAND, WHITE, 0.45 - 0.42*(rr/(len(LOGO)-1)))
                for c, kk in enumerate(row):
                    if kk != " ":
                        sh = M.exp(-((c + rr*0.6 - sweep)**2)/8.0)
                        ch[rr][c] = kk; col[rr][c] = clamp8(lerp(bs, (255,255,255), 0.65*sh))
            put(ch,col,0,20,"CoinJoin",lerp(BRAND,WHITE,.45)); put(ch,col,0,29,"address privacy",GREY)
            rput(ch,col,0,W-2,"via "+source,GREY)
            put(ch,col,1,20,r["addr"],lerp(BRAND,WHITE,.15))
            put(ch,col,3,20,f"balance {fmt(r['balance'])}  ·  {r['txcount']} txs  ·  {len(utxos)} UTXOs  "
                            f"·  received {r['funded']}x  ·  spent {r['spent']}x",lerp(BRAND,WHITE,.25))
            if warns:                                    # RED privacy warnings
                put(ch,col,5,20,"PRIVACY WARNINGS",WARN)
                for i, wn in enumerate(warns[:4]): put(ch,col,6+i,20,"!  "+wn,WARN)
            else:
                put(ch,col,5,20,"OK   no obvious privacy leaks - single-use, no mixed-coin consolidation",GREEN)
            if mode == "utxo":
                put(ch,col,LIST-1,20,f"UTXOS ({len(utxos)})   ·   w/s select   ·   space open funding tx",GREY)
                for i, u in enumerate(utxos[off:off+VIS]):
                    y = LIST + i; gi = off + i; selrow = (gi == sel)
                    a = coin_anon(prov.get(u.get("txid")), u.get("value", 0))
                    if   a is None: plab, pcol = "anon ?", GREY
                    elif a > 1:     plab, pcol = f"anon {a} · coinjoin", GREEN
                    else:           plab, pcol = "anon 1 · non-private", ORANGE
                    stt = u.get("status", {}) or {}
                    conf = f"block {stt.get('block_height'):,}" if stt.get("confirmed") else "unconfirmed"
                    bc = WHITE if selrow else lerp(BRAND, WHITE, .15)
                    put(ch,col,y,20, ("▸ " if selrow else "  ") + f"{fmt(u.get('value',0)):<16}", bc)
                    put(ch,col,y,42, plab, pcol)
                    put(ch,col,y,64, short(u.get("txid",""))+f":{u.get('vout',0)}", lerp(bc,GREY,.3))
                    rput(ch,col,y,W-2, conf, GREY)
            else:                                        # empty address: scroll its tx history
                cap = f" of {r['txcount']:,}" if r["txcount"] > len(txrows) else ""
                put(ch,col,LIST-1,20,f"RECENT TXS ({len(txrows)}{cap})   ·   w/s select   ·   space open tx",GREY)
                if not txrows: put(ch,col,LIST,20,"no transactions found for this address",GREY)
                for i, t in enumerate(txrows[off:off+VIS]):
                    y = LIST + i; gi = off + i; selrow = (gi == sel); net = t["net"]
                    dlab, dcol = (("+ "+fmt(net)+" received"), GREEN) if net > 0 else \
                                 ((("- "+fmt(-net)+" sent"), ORANGE) if net < 0 else ("self-transfer", GREY))
                    conf = f"block {t['height']:,}" if (t.get("confirmed") and t.get("height")) else "unconfirmed"
                    bc = WHITE if selrow else lerp(BRAND, WHITE, .15)
                    put(ch,col,y,20, ("▸ " if selrow else "  ") + dlab, WHITE if selrow else dcol)
                    if t["cj"]: put(ch,col,y,46, "◆ "+SHORT_CJ.get(t["cj"], t["cj"]), GREEN)
                    if t.get("anon") is not None:             # anon-set of the received coin
                        put(ch,col,y,58, f"anon {t['anon']}", GREEN if t["anon"] > 1 else GREY)
                    put(ch,col,y,70, short(t["txid"]), lerp(bc,GREY,.3))
                    rput(ch,col,y,W-2, conf, GREY)
            hint = ("w/s select UTXO    space open funding tx    q back to tx" if mode == "utxo"
                    else "w/s select tx    space open tx    q back to tx")
            put(ch,col,H-2,(W-len(hint))//2,hint,lerp(BRAND,WHITE,.4))
            tag = "coinjoin.nl    ·    one address, one use    ·    never mix private + non-private"
            put(ch,col,H-1,(W-len(tag))//2,tag,lerp(BRAND,WHITE,.25))
            emit(o, ch, col); time.sleep(FRAME); f += 1
    except KeyboardInterrupt:
        pass
    finally:
        restore(); o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")

# ---- live mempool dashboard (--watch / default when no txid) ----------------------
def feecol(fr):                                      # colour a feerate (sat/vB)
    if fr <= 1: return BLUE
    if fr < 8:  return lerp(BLUE, GREEN, (fr-1)/7)
    if fr < 30: return lerp(GREEN, ORANGE, (fr-8)/22)
    return lerp(ORANGE, RED, min((fr-30)/70, 1.0))

def project_blocks(hist, maxn=6, cap_vsize=1_000_000):
    # fold the fee histogram ([[feerate, vsize], ...], high->low) into 1 MvB blocks,
    # splitting a bucket across the boundary so each block is exactly ~1 MvB
    blocks = []; pairs = []; cvs = 0; fee = 0.0
    for item in (hist or []):
        fr, vs = item[0], item[1]
        while vs > 0:
            take = min(vs, cap_vsize - cvs)
            pairs.append((fr, take)); cvs += take; fee += fr*take; vs -= take
            if cvs >= cap_vsize:
                blocks.append(_mk_block(pairs, cvs, fee)); pairs = []; cvs = 0; fee = 0.0
                if len(blocks) >= maxn: return blocks
    if pairs and len(blocks) < maxn: blocks.append(_mk_block(pairs, cvs, fee))
    return blocks

def _mk_block(pairs, cvs, fee):
    half = cvs/2; acc = 0; med = pairs[-1][0]            # vsize-weighted median feerate
    for fr, vs in pairs:
        acc += vs
        if acc >= half: med = fr; break
    frs = [fr for fr, _ in pairs]
    return dict(vsize=cvs, fee=fee, med=med, lo=min(frs), hi=max(frs), ntx=None, size=None)

def fetch_projected(base, hist):                     # next blocks, mempool.space-style if available
    try:
        mb = fetch_json(f"{base}/api/v1/fees/mempool-blocks")   # has real size, nTx, fees
        out = []
        for b in (mb or [])[:3]:
            fr = b.get("feeRange") or [b.get("medianFee", 0)]
            out.append(dict(med=b.get("medianFee", 0), lo=fr[0], hi=fr[-1],
                            fee=b.get("totalFees", 0), ntx=b.get("nTx"),
                            size=b.get("blockSize"), vsize=b.get("blockVSize", 0)))
        if out: return out
    except Exception:
        pass
    return project_blocks(hist, maxn=3)               # fallback: project from the histogram

def ff(x): return (f"{x:.2f}".rstrip("0").rstrip(".")) if x < 10 else f"{x:.0f}"

def draw_block(ch, col, x, y, w, h, lines, base, bright, caption, capcol=None, xmin=0):
    ring = clamp8(lerp(base, WHITE, 0.15 + 0.45*bright))         # chunky colour cube
    def cell(yy, xx, g, cc):                                     # plot, clipped left of xmin
        if xx >= xmin and 0 <= yy < H and 0 <= xx < W: ch[yy][xx] = g; col[yy][xx] = cc
    for c in range(w):
        cell(y, x+c, "█", ring); cell(y+h-1, x+c, "█", ring)
    for r in range(1, h-1):
        cell(y+r, x, "█", ring); cell(y+r, x+w-1, "█", ring)
        s = lines[r-1] if r-1 < len(lines) else ""
        if s:
            sx = x+1+max(0,(w-2-len(s))//2)
            for i, k in enumerate(s[:w-2]): cell(y+r, sx+i, k, WHITE)
    if caption:
        cx = x+max(0,(w-len(caption))//2)
        for i, k in enumerate(caption[:w]): cell(y+h, cx+i, k, capcol or GREY)

def watch(base, source, frames):                     # returns a txid to inspect, or None to quit
    import threading
    stop = threading.Event(); seen = set()
    state = {"recent": [], "stats": None, "new": [], "err": "connecting...",
             "ver": 0, "height": None, "projected": []}
    def poll():
        while not stop.is_set():
            try:
                rec = fetch_json(f"{base}/api/mempool/recent")
                st  = fetch_json(f"{base}/api/mempool")
                try: h = fetch_json(f"{base}/api/blocks/tip/height")
                except Exception: h = state.get("height")
                proj = fetch_projected(base, st.get("fee_histogram") or [])
                new = [t for t in rec if t.get("txid") not in seen]
                for t in rec: seen.add(t.get("txid"))
                state.update(recent=rec, stats=st, new=new, height=h, projected=proj,
                             err=None, ver=state["ver"]+1)
                cjmap = state.get("cjmeta", {}); done = 0   # lazy coinjoin goggles on the feed
                for t in sorted(rec, key=lambda t: t.get("fee",0)/max(t.get("vsize",1),1), reverse=True)[:26]:
                    tid = t.get("txid")
                    if tid in cjmap: continue
                    try: cjmap[tid] = classify_cj(parse_tx(fetch_json(f"{base}/api/tx/{tid}")))[0]
                    except Exception: cjmap[tid] = None
                    done += 1
                    if done >= 10: break          # bound fetches per cycle to keep polling snappy
                state["cjmeta"] = cjmap
            except Exception as e:
                state["err"] = str(e)
            stop.wait(3)
    threading.Thread(target=poll, daemon=True).start()
    interactive = sys.stdin.isatty(); apply_canvas(*term_canvas(interactive))
    getkey, restore = make_keyreader()
    o = sys.stdout.write; o("\x1b[?1049h\x1b[?25l\x1b[2J")
    parts = []; chosen = None; lastver = -1; lastheight = None; nbflash = 0; slide = 0.0
    def fr_of(t): return t.get("fee",0)/max(t.get("vsize",1),1)
    try:
        f = 0
        while frames == 0 or f < frames:
            k = getkey()
            if k == "QUIT": break
            if k and k.isdigit():
                i = int(k) - 1
                shown = sorted(state["recent"], key=fr_of, reverse=True)
                if i < len(shown): chosen = shown[i]["txid"]; break
            nw, nh = term_canvas(interactive)             # adapt to a terminal resize
            if (nw, nh) != (W, H): apply_canvas(nw, nh); o("\x1b[2J")
            BARX = X_MIX                                   # mempool bar tracks the (resizable) centre
            ch, col = blank(); pulse = 0.5 + 0.5*M.sin(f*0.12)
            height = state.get("height"); projected = state.get("projected") or []
            if lastheight is not None and height is not None and height != lastheight:
                nbflash = 40; slide = -22.0          # new block -> shove the lane one pitch right
            if height is not None: lastheight = height
            if slide < 0:                            # ease the slide back to rest
                slide = 0.0 if slide > -0.5 else slide*0.82
            if state["ver"] != lastver:                  # spawn coins for newly arrived txs
                for t in state.get("new", [])[:40]:
                    parts.append([0.0, random.uniform(.010,.018), random.uniform(TOP,BOT),
                                  random.uniform(-7,7), clamp8(feecol(fr_of(t)))])
                lastver = state["ver"]
            if state["recent"] and random.random() < 0.5:  # keep the stream lively
                t = random.choice(state["recent"])
                parts.append([0.0, random.uniform(.010,.018), random.uniform(TOP,BOT),
                              random.uniform(-7,7), clamp8(feecol(fr_of(t)))])
            for yi in range(TOP, BOT+1, 2):              # faint inflow ribbons
                for kk in range(0, 34):
                    t = kk/34.0; dot(ch, col, yi+(CY-yi)*smooth(t), X_IN+(BARX-X_IN)*t, "·", DIM)
            parts[:] = [p for p in parts if (p.__setitem__(0, p[0]+p[1]) or p[0] < 1.04)]
            for t, sp, yi, off, pc in parts:
                for kk in range(4):
                    tt = t - kk*0.018
                    if tt <= 0 or tt >= 1: continue
                    x = X_IN + (BARX-X_IN)*tt; y = yi + (CY+off - yi)*smooth(tt)
                    dot(ch, col, y, x, "●" if kk==0 else "·", clamp8(lerp(BG, pc, 1.0-kk*0.30)))
            for r in range(TOP, BOT+1):                  # the mempool bar
                cg = clamp8(lerp((40,46,78), GLOW, 0.30+0.4*pulse))
                for cc in (BARX-1, BARX, BARX+1): ch[r][cc] = "█"; col[r][cc] = cg
            for i, kk in enumerate("MEMPOOL"): put(ch, col, int(CY)-3+i, BARX, kk, WHITE)
            put(ch, col, TOP-1, X_IN-4, "INCOMING TXS", GREY)
            cjmap = state.get("cjmeta", {})                          # live feed (numbered, goggled)
            rec = sorted(state["recent"], key=fr_of, reverse=True)
            shown = rec[:BOT-TOP+1]
            ncj = sum(1 for t in shown if cjmap.get(t.get("txid")))
            hdr = (f"LIVE FEED  ({ncj} coinjoin{'s' if ncj!=1 else ''} ◆  ·  1-9 inspect)" if ncj
                   else "LIVE FEED  (press 1-9 to inspect)")
            put(ch, col, TOP-1, BARX+6, hdr, lerp(GREEN,WHITE,.2) if ncj else GREY)
            for i, t in enumerate(shown):
                y = TOP + i; pc = clamp8(feecol(fr_of(t)))
                num = f"{i+1}." if i < 9 else "  ·"
                txid = t.get("txid",""); lab = (txid[:8]+"…"+txid[-6:]) if txid else "?"
                cjl = cjmap.get(txid)
                row = f"{num:>3} {fr_of(t):5.1f} sat/vB  {lab}  {fmt(t.get('value',0))}"
                put(ch, col, y, BARX+6, row, lerp(GREEN,WHITE,.25) if cjl else lerp(pc, WHITE, .25))
                if cjl:
                    put(ch, col, y, BARX+4, "◆", GREEN)
                    s = SHORT_CJ.get(cjl, cjl)
                    put(ch, col, y, min(W-len(s)-1, BARX+8+len(row)), s, GREEN)
            sweep = (f*0.7) % 28 - 4                      # shimmering shield + live stats
            for r, row in enumerate(LOGO):
                bs = lerp(BRAND, WHITE, 0.45 - 0.42*(r/(len(LOGO)-1)))
                for c, kk in enumerate(row):
                    if kk != " ":
                        sh = M.exp(-((c + r*0.6 - sweep)**2)/8.0)
                        ch[r][c] = kk; col[r][c] = clamp8(lerp(bs, (255,255,255), 0.65*sh))
            put(ch,col,0,20,"CoinJoin",lerp(BRAND,WHITE,.45)); put(ch,col,0,29,"live mempool",GREY)
            rput(ch,col,0,W-2,"via "+source,GREY)
            st = state["stats"]
            if st:
                cnt = st.get("count",0); vb = st.get("vsize",0); tf = st.get("total_fee",0)
                hist = st.get("fee_histogram") or []
                top = hist[0][0] if hist else 0; flo = hist[-1][0] if hist else 0
                rput(ch,col,1,W-2, f"{cnt:,} txs waiting", lerp(BRAND,WHITE,.25))
                put(ch,col,1,20, f"backlog {vb/1e6:.1f} vMB  ·  {tf/1e8:.3f} BTC in fees", lerp(BRAND,WHITE,.2))
                put(ch,col,2,20, (f"chain tip  #{height:,}" if height else "chain tip  (loading)"), lerp(BRAND,WHITE,.3))
                rput(ch,col,2,W-2, f"top {top:.0f}  ·  floor {flo:.1f} sat/vB", lerp(BRAND,WHITE,.2))
                put(ch,col,3,20, "UPCOMING BLOCKS", GREY)
            else:
                put(ch,col,1,20, state.get("err") or "loading live mempool ...", ORANGE)
            # -- upcoming-blocks lane: next 3 blocks (real size/tx/fees) + chain tip ---------
            BW, PITCH, YB, BH = 20, 22, 4, 6; xo = round(slide); x_tip = W-2-BW
            if projected:
                tcap, tbr, tcc = "just mined", 0.5+0.5*pulse, lerp(ORANGE,WHITE,.3)
                if nbflash > 0:
                    if (nbflash//4) % 2 == 0: tcap, tbr, tcc = "** JUST MINED **", 1.0, clamp8(lerp(ORANGE,WHITE,.6))
                    nbflash -= 1
                draw_block(ch,col,x_tip+xo,YB,BW,BH, ["", (f"#{height:,}" if height else "tip"), "chain tip", ""],
                           ORANGE, tbr, tcap, tcc, xmin=19)
                for bi in range(min(len(projected), 3)):
                    x = x_tip - (bi+1)*PITCH + xo
                    pb = projected[bi]
                    if pb.get("ntx") and pb.get("size"):
                        n = pb["ntx"]; ktx = f"{n/1000:.1f}k" if n >= 1000 else str(n)
                        l4 = f"{ktx} tx · {pb['size']/1e6:.2f} MB"
                    elif pb.get("size"): l4 = f"{pb['size']/1e6:.2f} MB"
                    else:                l4 = f"{pb['vsize']/1e6:.2f} MvB"
                    lines = [f"~{pb['med']:.0f} sat/vB", f"{ff(pb['lo'])} - {ff(pb['hi'])} sat/vB",
                             f"{pb['fee']/1e8:.3f} BTC", l4]
                    cap = "next block" if bi==0 else f"in ~{(bi+1)*10} min"
                    draw_block(ch,col,x,YB,BW,BH, lines, feecol(pb['med']),
                               (0.45+0.45*pulse) if bi==0 else 0.18, cap, GREY, xmin=19)
            tag = "coinjoin.nl    ·    mix to break the link    ·    press q to quit"
            put(ch,col,H-1,(W-len(tag))//2,tag,lerp(BRAND,WHITE,.25))
            emit(o, ch, col); time.sleep(FRAME); f += 1
    except KeyboardInterrupt:
        pass
    finally:
        stop.set(); restore(); o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")
    return chosen

# ---- export to gif / png / svg (--export) -----------------------------------------
def _xescape(g): return g.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def export_svg(meta, viz, source, out, frame=36):
    parts = []
    for f in range(frame+1):
        ch, col = blank(); render_tx(ch, col, meta, viz, source, f, parts)
    cw, chh = 10, 19; br, bg, bb = BG
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W*cw}" height="{H*chh}" '
           f'font-family="Consolas,Menlo,monospace" font-size="16">',
           f'<rect width="100%" height="100%" fill="rgb({br},{bg},{bb})"/>']
    for r in range(H):
        for c in range(W):
            g = ch[r][c]
            if g == " ": continue
            cr, cg2, cb = clamp8(col[r][c])
            svg.append(f'<text x="{c*cw}" y="{r*chh+14}" fill="rgb({cr},{cg2},{cb})" '
                       f'xml:space="preserve">{_xescape(g)}</text>')
    svg.append("</svg>")
    open(out, "w", encoding="utf-8").write("\n".join(svg))
    print(f"wrote {out}  ({W*cw}x{H*chh} svg, 1 frame)", file=sys.stderr)

def _xfont(size):
    from PIL import ImageFont
    for p in ("C:/Windows/Fonts/consola.ttf","C:/Windows/Fonts/cour.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf","/Library/Fonts/Menlo.ttc"):
        try: return ImageFont.truetype(p, size)
        except Exception: pass
    return ImageFont.load_default()

def _raster(ch, col, font, cw, chh):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W*cw, H*chh), BG); d = ImageDraw.Draw(img)
    for r in range(H):
        for c in range(W):
            g = ch[r][c]
            if g == " ": continue
            d.text((c*cw, r*chh), g, fill=clamp8(col[r][c]), font=font)
    return img

def export_raster(meta, viz, source, out, nframes, gif):
    try:
        from PIL import Image
    except Exception:
        sys.exit("PNG/GIF export needs Pillow:  pip install pillow   (or export a .svg)")
    size = 18; font = _xfont(size)
    try: cw = max(1, int(font.getlength("█")))
    except Exception: cw = 0
    if cw < 6: cw = int(size*0.6)
    chh = int(size*1.3)
    if not gif:
        parts = []
        for f in range(nframes):
            ch, col = blank(); render_tx(ch, col, meta, viz, source, f, parts)
        _raster(ch, col, font, cw, chh).save(out)
        print(f"wrote {out}  ({W*cw}x{H*chh} png)", file=sys.stderr); return
    frs = []; parts = []
    for f in range(nframes):
        ch, col = blank(); render_tx(ch, col, meta, viz, source, f, parts)
        frs.append(_raster(ch, col, font, cw, chh).convert("P", palette=Image.ADAPTIVE, colors=256))
    frs[0].save(out, save_all=True, append_images=frs[1:], duration=50, loop=0, optimize=True, disposal=2)
    print(f"wrote {out}  ({W*cw}x{H*chh}, {nframes} frames gif)", file=sys.stderr)

def export(meta, viz, source, out, nframes):
    ext = out.lower().rsplit(".", 1)[-1] if "." in out else ""
    if   ext == "svg": export_svg(meta, viz, source, out)
    elif ext == "png": export_raster(meta, viz, source, out, nframes, gif=False)
    elif ext == "gif": export_raster(meta, viz, source, out, nframes, gif=True)
    else: sys.exit("--export needs a .gif, .png, or .svg filename")

# ---- main -------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Animate a Bitcoin tx flow from mempool.space.")
    ap.add_argument("txid", nargs="?", help="transaction id")
    ap.add_argument("--mempool", default="https://mempool.space", help="mempool base URL (self-hosted ok)")
    ap.add_argument("--file", help="load tx JSON from a file instead of fetching")
    ap.add_argument("--frames", type=int, default=0, help="frames to run (0 = forever)")
    ap.add_argument("--depth", type=int, default=0, help="also load N levels of connected txs (graph mode)")
    ap.add_argument("--width", type=int, default=8, help="max txs plotted per graph level (4-10)")
    ap.add_argument("--watch", action="store_true", help="live mempool dashboard (default when no txid)")
    ap.add_argument("--export", metavar="FILE", help="render to FILE (.gif/.png/.svg) instead of live view")
    a = ap.parse_args()
    base = a.mempool.rstrip("/")
    if a.export:                                      # export mode (gif/png/svg)
        nframes = a.frames if a.frames > 0 else 90
        if a.file:
            try: meta = parse_tx(json.load(open(a.file, encoding="utf-8")))
            except Exception as e: sys.exit(f"could not load file: {e}")
            src = "file"
        else:
            if not a.txid or len(a.txid)!=64 or any(c not in "0123456789abcdefABCDEF" for c in a.txid):
                ap.error("--export needs a valid txid (or --file)")
            try: meta = parse_tx(fetch_json(f"{base}/api/tx/{a.txid}"))
            except Exception as e: sys.exit(f"could not load transaction: {e}")
            src = base.split("//")[-1]
        if not meta["ins"] or not meta["outs"]: sys.exit("transaction has no inputs/outputs to draw.")
        export(meta, build(meta), src, a.export, nframes); return
    if a.depth > 0:                                   # multi-tx graph mode
        if a.file: ap.error("--depth needs live data; drop --file")
        if not a.txid or len(a.txid)!=64 or any(c not in "0123456789abcdefABCDEF" for c in a.txid):
            ap.error("provide a valid 64-hex-char txid for --depth")
        depth = min(a.depth, 5); width = min(max(a.width, 4), 10)
        print(f"building tx graph (depth {depth}, width {width}) from {base} - this fetches many txs ...", file=sys.stderr)
        try:
            G = build_graph(a.txid, base, depth, width,
                lambda n: print(f"  fetched {n} txs...", file=sys.stderr))
        except urllib.error.HTTPError as e:
            sys.exit(f"mempool returned HTTP {e.code} - is the txid correct / known to {base}?")
        except Exception as e:
            sys.exit(f"could not build graph: {e}")
        try: animate_graph(G, base.split("//")[-1], a.frames)
        except Exception as e: sys.exit(f"graph view error: {e}")
        return
    if a.file:                                        # offline playback
        try: meta = parse_tx(json.load(open(a.file, encoding="utf-8")))
        except Exception as e: sys.exit(f"could not load file: {e}")
        if not meta["ins"] or not meta["outs"]: sys.exit("transaction has no inputs/outputs to draw.")
        animate(meta, build(meta), "file", a.frames); return
    if a.watch or not a.txid:                         # live mempool dashboard (default, no txid)
        src = base.split("//")[-1]
        if not sys.stdin.isatty() and a.frames == 0:
            print("live mempool needs a terminal; pass a txid or --frames N for a fixed run.", file=sys.stderr)
        while True:
            print(f"connecting to {base} live mempool ... (1-9 inspect, q quit)", file=sys.stderr)
            txid = watch(base, src, a.frames)
            if not txid: break
            try: explore(txid, base, src)
            except urllib.error.HTTPError as e: print(f"HTTP {e.code} for {txid[:12]}", file=sys.stderr); time.sleep(1)
            except Exception as e: print(f"could not load {txid[:12]}: {e}", file=sys.stderr); time.sleep(1)
        return
    if len(a.txid)!=64 or any(c not in "0123456789abcdefABCDEF" for c in a.txid):
        ap.error("that doesn't look like a 64-hex-char txid")
    source = base.split("//")[-1]
    try:
        if a.frames == 0 and sys.stdin.isatty():      # interactive explorer (w/a/s/d)
            print(f"loading {a.txid[:12]}... (a/d walk the chain, w/s scroll value, q quit)", file=sys.stderr)
            explore(a.txid, base, source); return
        print(f"fetching {a.txid[:12]}... from {base} ...", file=sys.stderr)
        meta = parse_tx(fetch_json(f"{base}/api/tx/{a.txid}"))
    except urllib.error.HTTPError as e:
        sys.exit(f"mempool returned HTTP {e.code} - is the txid correct / known to {base}?")
    except Exception as e:
        sys.exit(f"could not load transaction: {e}")
    if not meta["ins"] or not meta["outs"]:
        sys.exit("transaction has no inputs/outputs to draw.")
    animate(meta, build(meta), source, a.frames)

if __name__ == "__main__":
    main()
