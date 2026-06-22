#!/usr/bin/env python
# -*- coding: utf-8 -*- ###########  T X   F L O W  ·  coinjoin.nl  ###########
#  Pull any Bitcoin transaction from mempool.space (or your own self-hosted    #
#  mempool) and animate its input -> output flow as ASCII.  Equal-value        #
#  "coinjoin" outputs are detected and highlighted.  Runs interactively:       #
#  a/d walk the chain back/forward, w/s scroll inputs/outputs by value, q quit  #
#     python txflow.py <txid> [--depth N] [--mempool URL] [--file tx.json]      #
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

def fetch_tx(txid, base): return fetch_json(f"{base}/api/tx/{txid}")

def is_cj(meta):                                     # equal-output (coinjoin) heuristic
    from collections import Counter
    vals = Counter(v for v,_,_ in meta["outs"])
    cl = {v for v,c in vals.items() if c >= 3 and v > 0}
    return sum(vals[v] for v in cl) >= 5

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
    cj = sum(vals[v] for v in clusters) >= 5          # enough uniform outputs to be a mix
    denoms = sorted(((vals[v], v) for v in clusters), reverse=True)
    maxout = max((v for v,_,_ in meta["outs"]), default=1) or 1
    def ocol(v,typ):
        if v in clusters: return GREEN
        if typ=="op_return" or v==0: return GREY
        if v >= 0.25*maxout: return ORANGE
        return BLUE
    nodes_in  = [(iy[k], IN_COLS[k%len(IN_COLS)], ins[k]) for k in range(len(ins))]
    nodes_out = [(oy[k], ocol(outs[k][0], outs[k][2]), outs[k]) for k in range(len(outs))]
    return dict(nin=nodes_in, nout=nodes_out, cj=cj, denoms=denoms,
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

def render_tx(ch, col, meta, viz, source, f, parts, hint=None):
    nin, nout = viz["nin"], viz["nout"]
    if not nin or not nout: return
    inw=[max(n[2][0],1) for n in nin]; outw=[max(n[2][0],1) for n in nout]
    center=viz["center"]; cj=viz["cj"]; label="COINJOIN" if cj else "TRANSACTION"
    pulse=0.5+0.5*M.sin(f*0.12)
    for (yi,_,_) in nin:                               # faint sankey ribbons
        for k in range(0,36):
            t=k/70.0; dot(ch,col,funnel(t,yi,CY,0),X_IN+(X_OUT-X_IN)*t,"·",DIM)
    for (yo,_,_) in nout:
        for k in range(35,71):
            t=k/70.0; dot(ch,col,funnel(t,CY,yo,0),X_IN+(X_OUT-X_IN)*t,"·",DIM)
    for _ in range(5):                                # spawn + advance coins
        i=random.choices(range(len(nin)),weights=inw)[0]
        j=random.choices(range(len(nout)),weights=outw)[0]
        parts.append([0.0,random.uniform(.012,.018),nin[i][0],nout[j][0],
                      random.uniform(-6,6),nin[i][1],nout[j][1]])
    parts[:]=[p for p in parts if (p.__setitem__(0,p[0]+p[1]) or p[0]<1.02)]
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
    for r in range(TOP,BOT+1):                        # mixing/tx bar + vertical label
        cg=clamp8(lerp((22,104,68) if cj else (40,46,78), center, 0.35+0.4*pulse))
        for cc in (X_MIX-1,X_MIX,X_MIX+1): ch[r][cc]="█"; col[r][cc]=cg
    for i,k in enumerate(label):
        put(ch,col,int(CY)-len(label)//2+i,X_MIX,k,WHITE)
    for (y,c,(v,addr,cb)) in nin:                     # input chips + labels
        put(ch,col,y,X_IN,"█",c)
        rput(ch,col,y,X_IN-2, f"{short(addr)} {cfmt(v)}", lerp(c,WHITE,.35))
    for (y,c,(v,addr,typ)) in nout:                   # output chips + labels
        put(ch,col,y,X_OUT,"██",c)
        put(ch,col,y,X_OUT+3, f"{cfmt(v)} {short(addr)}", lerp(c,WHITE,.3))
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
    txid = (meta["txid"][:10]+"…"+meta["txid"][-8:]) if meta["txid"] else "(local)"
    rput(ch,col,3,LABX,"txid",GREY);  put(ch,col,3,VALX,txid, lerp(BRAND,WHITE,.1))
    rput(ch,col,4,LABX,"value",GREY); put(ch,col,4,VALX,f"in {fmt(meta['total_in'])}    →    out {fmt(meta['total_out'])}", lerp(BRAND,WHITE,.25))
    rput(ch,col,5,LABX,"fee",GREY);   put(ch,col,5,VALX,f"{meta['fee']:,} sats   ·   {meta['feerate']:.1f} sat/vB   ·   {meta['vsize']:,} vB", lerp(BRAND,WHITE,.25))
    if cj:
        dn = "   ·   ".join(f"{c}× {fmt(v)}" for c,v in viz["denoms"][:3])
        rput(ch,col,6,LABX,"mix",GREY); put(ch,col,6,VALX,dn+"   — equal, unlinkable", GREEN)
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
            emit(o,ch,col); time.sleep(0.05); f+=1
    except KeyboardInterrupt:
        pass
    finally:
        o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")

# ---- multi-tx graph (--depth) -----------------------------------------------------
def build_graph(main_txid, base, depth, capn, log):
    raw, parsed = {}, {}
    def graw(t):
        if t not in raw: raw[t] = fetch_tx(t, base)
        return raw[t]
    def gp(t):
        if t not in parsed: parsed[t] = parse_tx(graw(t)); log(len(parsed))
        return parsed[t]
    gp(main_txid)
    placed = {main_txid: 0}; edges = set()
    frontier = [main_txid]                            # walk backward over funding txs
    for d in range(1, depth+1):
        cand = {}
        for t in frontier:
            for vin in graw(t).get("vin", []):
                if vin.get("is_coinbase"): continue
                p = vin.get("txid")
                if not p: continue
                edges.add((p, t))
                if p not in placed:
                    cand[p] = cand.get(p, 0) + ((vin.get("prevout") or {}).get("value", 0))
        keep = sorted(cand, key=lambda k: cand[k], reverse=True)[:capn]
        for p in keep: placed[p] = -d; gp(p)
        frontier = keep
        if not keep: break
    frontier = [main_txid]                            # walk forward over spending txs
    for d in range(1, depth+1):
        cand = {}
        for t in frontier:
            try: osp = fetch_json(f"{base}/api/tx/{t}/outspends")
            except Exception: osp = []
            outs = gp(t)["outs"]
            for i, o in enumerate(osp or []):
                if o and o.get("spent") and o.get("txid"):
                    s = o["txid"]; edges.add((t, s))
                    if s not in placed:
                        cand[s] = cand.get(s, 0) + (outs[i][0] if i < len(outs) else 0)
        keep = sorted(cand, key=lambda k: cand[k], reverse=True)[:capn]
        for s in keep: placed[s] = d; gp(s)
        frontier = keep
        if not keep: break
    edges = {(a, b) for (a, b) in edges if a in placed and b in placed}
    return placed, parsed, edges

def animate_graph(placed, parsed, edges, main_txid, source, depth, frames):
    GT, GB = 14, 35                                   # node band (leaves room for labels)
    def gy(n): return [(GT+GB)//2] if n<=1 else [round(GT+(GB-GT)*i/(n-1)) for i in range(n)]
    levels = {}
    for t, lv in placed.items(): levels.setdefault(lv, []).append(t)
    lvs = sorted(levels)
    XL, XR = 8, W-9
    xs = {lv: round(XL + (XR-XL)*(i/max(len(lvs)-1, 1))) for i, lv in enumerate(lvs)}
    pos, cjset = {}, set()
    for lv in lvs:
        ts = sorted(levels[lv], key=lambda t: parsed[t]["total_out"], reverse=True)
        for t, y in zip(ts, gy(len(ts))):
            pos[t] = (xs[lv], y)
            if is_cj(parsed[t]): cjset.add(t)
    elist = [(a, b) for (a, b) in edges if a in pos and b in pos]
    ew = [max(parsed[a]["total_out"], 1) for (a, b) in elist] or [1]
    m = parsed[main_txid]
    parts = []; o = sys.stdout.write
    o("\x1b[?1049h\x1b[?25l\x1b[2J")
    try:
        f = 0
        while frames == 0 or f < frames:
            ch, col = blank(); sweep = (f*0.7) % 28 - 4
            for (a, b) in elist:                       # faint connecting ribbons
                ax, ay = pos[a]; bx, by = pos[b]; n = max(abs(bx-ax), abs(by-ay)) or 1
                for s in range(n+1):
                    tt = s/n; dot(ch, col, ay+(by-ay)*tt, ax+(bx-ax)*tt, "·", DIM)
            if elist:                                  # coins flowing left -> right
                for _ in range(10):
                    a, b = elist[random.choices(range(len(elist)), weights=ew)[0]]
                    parts.append([0.0, random.uniform(.02,.035), pos[a], pos[b],
                                  GREEN if (a in cjset or b in cjset) else BRAND])
            parts = [p for p in parts if (p.__setitem__(0, p[0]+p[1]) or p[0] < 1.05)]
            for t, sp, (ax, ay), (bx, by), pc in parts:
                for k in range(3):
                    tt = t - k*0.05
                    if tt <= 0 or tt >= 1: continue
                    dot(ch, col, ay+(by-ay)*tt, ax+(bx-ax)*tt, "●" if k==0 else "·", clamp8(lerp(BG, pc, 1.0-k*0.33)))
            for t, (x, y) in pos.items():              # tx nodes
                cjn = t in cjset; main = (t == main_txid)
                base = lerp(GREEN if cjn else BRAND, WHITE, .35 if main else 0)
                h = 2 if main else 1
                for dy in range(-h, h+1):
                    if 0 <= y+dy < H: ch[y+dy][x] = "█"; col[y+dy][x] = clamp8(base)
                lab = cfmt(parsed[t]["total_out"])
                put(ch, col, y+h+1, x-len(lab)//2, lab, lerp(base, WHITE, .3))
                if cjn and not main: put(ch, col, y-h-1, x-1, "cj", GREEN)
                if main:
                    tg = "» " + ("COINJOIN" if cjn else "TRANSACTION") + " «"
                    put(ch, col, y-h-1, x-len(tg)//2, tg, ORANGE)
            for r, row in enumerate(LOGO):             # shimmering shield + header
                bs = lerp(BRAND, WHITE, 0.45 - 0.42*(r/(len(LOGO)-1)))
                for c, k in enumerate(row):
                    if k != " ":
                        sh = M.exp(-((c + r*0.6 - sweep)**2)/8.0)
                        ch[r][c] = k; col[r][c] = clamp8(lerp(bs, (255,255,255), 0.65*sh))
            put(ch,col,0,20,"CoinJoin",lerp(BRAND,WHITE,.45)); put(ch,col,0,29,"tx flow",GREY)
            rput(ch,col,0,W-2,"via "+source,GREY)
            put(ch,col,1,20,f"transaction graph  ·  depth {depth}  ·  {len(pos)} txs",lerp(BRAND,WHITE,.2))
            rput(ch,col,1,W-2,"← earlier        later →",GREY)
            put(ch,col,3,20,"main  "+(main_txid[:10]+"…"+main_txid[-8:]),lerp(BRAND,WHITE,.1))
            put(ch,col,4,20,f"{fmt(m['total_in'])} in  ·  {fmt(m['total_out'])} out  ·  fee {m['fee']:,} sats ({m['feerate']:.1f} sat/vB)",lerp(BRAND,WHITE,.25))
            tag = "coinjoin.nl    ·    privacy for cheap mining fees"
            put(ch,col,H-1,(W-len(tag))//2,tag,lerp(BRAND,WHITE,.25))
            out = ["\x1b[?2026h\x1b[H"]
            for r in range(H):
                last = None; line = []
                for c in range(W):
                    g = ch[r][c]
                    if g == " ": line.append(" "); continue
                    cc = col[r][c]
                    if cc != last: line.append("\x1b[38;2;%d;%d;%dm" % cc); last = cc
                    line.append(g)
                out.append("".join(line)+"\x1b[0m")
            o("\n".join(out)+"\x1b[?2026l"); sys.stdout.flush()
            time.sleep(0.05); f += 1
    except KeyboardInterrupt:
        pass
    finally:
        o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")

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
            "q":"QUIT","Q":"QUIT","\x1b":"QUIT"}
    if os.name == "nt":
        import msvcrt
        def get():
            if not msvcrt.kbhit(): return None
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):               # arrow/function key: consume code, ignore
                msvcrt.getwch(); return None
            return KEYS.get(ch)
        return get, (lambda: None)
    try:
        import termios, tty, select
        fd = sys.stdin.fileno(); old = termios.tcgetattr(fd); tty.setcbreak(fd)
        def get():
            if select.select([sys.stdin], [], [], 0)[0]:
                return KEYS.get(sys.stdin.read(1))
            return None
        return get, (lambda: termios.tcsetattr(fd, termios.TCSADRAIN, old))
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
    cur, io_off = txid, 0
    meta = gmeta(cur)                                 # initial fetch (may raise -> caught in main)
    getkey, restore = make_keyreader()
    HINT = "a / d  walk back / forward      w / s  higher / lower value      q  quit"
    o = sys.stdout.write; o("\x1b[?1049h\x1b[?25l\x1b[2J")
    parts = []; viz = build(meta, io_off); flash, flasht = "", 0
    try:
        f = 0
        while True:
            k = getkey()
            if k == "QUIT": break
            elif k in ("UP", "DOWN"):
                mx = max(0, max(len(meta["ins"]), len(meta["outs"])) - MAXN)
                no = max(0, min(mx, io_off + (-1 if k == "UP" else 1)))
                if no != io_off: io_off = no; viz = build(meta, io_off); parts.clear()
            elif k == "LEFT":
                p = parent_of(graw(cur))
                try: p and gmeta(p)
                except Exception: p = None
                if p: cur, io_off = p, 0; meta = gmeta(cur); viz = build(meta, io_off); parts.clear(); flash, flasht = "◀  walked back to a funding tx", 30
                else: flash, flasht = "no parent here (coinbase or unavailable)", 24
            elif k == "RIGHT":
                try: c = child_of(cur, gosp, gmeta); c and gmeta(c)
                except Exception: c = None
                if c: cur, io_off = c, 0; meta = gmeta(cur); viz = build(meta, io_off); parts.clear(); flash, flasht = "walked forward to a spending tx  ▶", 30
                else: flash, flasht = "this output isn't spent yet (tip of the chain)", 24
            ch, col = blank()
            render_tx(ch, col, meta, viz, source, f, parts, hint=(flash if flasht > 0 else HINT))
            emit(o, ch, col)
            if flasht > 0: flasht -= 1
            time.sleep(0.05); f += 1
    except KeyboardInterrupt:
        pass
    finally:
        restore(); o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")

# ---- main -------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Animate a Bitcoin tx flow from mempool.space.")
    ap.add_argument("txid", nargs="?", help="transaction id")
    ap.add_argument("--mempool", default="https://mempool.space", help="mempool base URL (self-hosted ok)")
    ap.add_argument("--file", help="load tx JSON from a file instead of fetching")
    ap.add_argument("--frames", type=int, default=0, help="frames to run (0 = forever)")
    ap.add_argument("--depth", type=int, default=0, help="also load N levels of connected txs (graph mode)")
    a = ap.parse_args()
    base = a.mempool.rstrip("/")
    if a.depth > 0:                                   # multi-tx graph mode
        if a.file: ap.error("--depth needs live data; drop --file")
        if not a.txid or len(a.txid)!=64 or any(c not in "0123456789abcdefABCDEF" for c in a.txid):
            ap.error("provide a valid 64-hex-char txid for --depth")
        depth = min(a.depth, 5)
        print(f"building tx graph (depth {depth}) from {base} - this fetches many txs ...", file=sys.stderr)
        try:
            placed, parsed, edges = build_graph(a.txid, base, depth, 5,
                lambda n: print(f"  fetched {n} txs...", file=sys.stderr))
        except urllib.error.HTTPError as e:
            sys.exit(f"mempool returned HTTP {e.code} - is the txid correct / known to {base}?")
        except Exception as e:
            sys.exit(f"could not build graph: {e}")
        animate_graph(placed, parsed, edges, a.txid, base.split("//")[-1], depth, a.frames)
        return
    if a.file:                                        # offline playback
        try: meta = parse_tx(json.load(open(a.file, encoding="utf-8")))
        except Exception as e: sys.exit(f"could not load file: {e}")
        if not meta["ins"] or not meta["outs"]: sys.exit("transaction has no inputs/outputs to draw.")
        animate(meta, build(meta), "file", a.frames); return
    if not a.txid: ap.error("provide a txid (or --file)")
    if len(a.txid)!=64 or any(c not in "0123456789abcdefABCDEF" for c in a.txid):
        ap.error("that doesn't look like a 64-hex-char txid")
    source = base.split("//")[-1]
    try:
        if a.frames == 0 and sys.stdin.isatty():      # interactive explorer (arrow keys)
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
