#!/usr/bin/env python3
# -*- coding: utf-8 -*- ############  S A B I  ·  coinjoin.nl  ################
#  A terminal interface (TUI) for the headless Wasabi Wallet daemon.           #
#  Wallets · balances · history · coinjoin (single round / auto / sweep) ·     #
#  payments inside coinjoin · privacy-aware batched sends.                     #
#                                                                              #
#  Setup:  in Wasabi's Config.json set  "JsonRpcServerEnabled": true           #
#          then run the daemon:   wassabee daemon  (or wassabeed)              #
#  Run:    python3 sabi.py [--rpc http://127.0.0.1:37128] [--wallet NAME]      #
#          [--user U --pass P]   ·   python3 sabi.py --demo   (no daemon)      #
#  Keys:   1-7 tabs · WASD/arrows navigate · enter act · ? help · q quit        #
#          mouse: click tabs/rows, wheel scrolls (Linux/macOS terminals)       #
################################################################################
import sys, os, time, math, random, json, argparse, shutil, re, tempfile, urllib.request
M = math
FRAME = 1/21                                          # 21 FPS - the bitcoin frame rate
os.system("")
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

# ---- palette ----------------------------------------------------------------------
BG=(10,12,16); BRAND=(176,186,236); GREEN=(46,214,122); GLOW=(120,255,170)
ORANGE=(247,147,26); WHITE=(236,239,246); GREY=(110,120,134); DIM=(44,40,36)
BLUE=(96,156,236); RED=(232,92,104); AMBER=(255,176,32); WARN=(255,92,92)
NORI=(70,118,70); RICE=(240,242,248)                 # sushi nori wrap + rice (visible on dark bg)
TAKO=(190,120,190)                                    # octopus purple
def qpulse(f, w, step=6):                             # breathing, quantized to STEP frames:
    return 0.5 + 0.5*M.sin((f - f % step) * w)        # rows stay identical between steps, so
                                                      # the frame diff sends nothing (remote!)

def lerp(a,b,t): return (a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t)
def clamp8(c): return (max(0,min(255,int(c[0]))),max(0,min(255,int(c[1]))),max(0,min(255,int(c[2]))))

DISCREET = {"on": False}                              # privacy mode: obfuscate amounts + addresses
def _mask_digits(s): return re.sub(r"\d", "*", s)     # keeps shape: "*.**** **** BTC"
def _mask_alnum(s):  return re.sub(r"[0-9A-Za-z]", "*", s)

def btc(v):                                           # sats -> "0.1234 5678 BTC" (wasabi-style groups)
    s = f"{abs(int(v))/1e8:.8f}"; a, b = s.split(".")
    out = ("-" if v < 0 else "") + a + "." + b[:4] + " " + b[4:] + " BTC"
    return _mask_digits(out) if DISCREET["on"] else out
def cbtc(v):                                          # compact: for rows
    av = abs(int(v))
    if av >= 100_000_000: out = f"{v/1e8:.4f} BTC"
    elif av >= 100_000:   out = f"{v/1e8:.5f} BTC"
    else:                 out = f"{int(v):,} sats"
    return _mask_digits(out) if DISCREET["on"] else out
def short(a, n=15):
    if not a: return "?"
    a = str(a)
    out = a if len(a) <= n else a[:n-5] + "…" + a[-4:]
    return _mask_alnum(out) if DISCREET["on"] else out
def maskaddr(a):                                      # for places that print full addresses
    return _mask_alnum(str(a)) if DISCREET["on"] else str(a)

_B58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
_BECH = set("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
def is_btc_address(s):
    if not s: return False
    low = s.lower()
    if low.startswith(("bc1", "tb1", "bcrt1")):
        body = low[low.rindex("1")+1:]
        return 6 <= len(body) <= 87 and all(c in _BECH for c in body)
    if s[0] in "123mn" and 25 <= len(s) <= 35: return all(c in _B58 for c in s)
    return False

def ding():                                           # terminal bell (events while unattended)
    try: sys.stdout.write("\a"); sys.stdout.flush()
    except Exception: pass

# ---- QR code (byte mode, EC level L, versions 1-5, mask 0 - pure stdlib) -----------
_QR_CAP = {1: 17, 2: 32, 3: 53, 4: 78, 5: 106}        # byte-mode capacity at level L
_QR_DCW = {1: 19, 2: 34, 3: 55, 4: 80, 5: 108}        # data codewords
_QR_ECW = {1: 7, 2: 10, 3: 15, 4: 20, 5: 26}          # ecc codewords
_QR_FMT0 = 0b111011111000100                          # format info: level L, mask 0
_GF_EXP = [0]*512; _GF_LOG = [0]*256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x; _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100: _x ^= 0x11d
for _i in range(255, 512): _GF_EXP[_i] = _GF_EXP[_i-255]

def _rs_ecc(data, ecw):                               # Reed-Solomon ecc codewords
    gen = [1]
    for i in range(ecw):                              # gen *= (x - a^i)
        ng = [0]*(len(gen)+1)
        for j, g in enumerate(gen):
            ng[j] ^= g                                # g * x
            if g:
                ng[j+1] ^= _GF_EXP[(_GF_LOG[g] + i) % 255]   # g * a^i
        gen = ng
    rem = list(data) + [0]*ecw
    for i in range(len(data)):
        f = rem[i]
        if f:
            for j in range(1, len(gen)):
                if gen[j]:
                    rem[i+j] ^= _GF_EXP[(_GF_LOG[gen[j]] + _GF_LOG[f]) % 255]
        rem[i] = 0
    return rem[len(data):]

def qr_matrix(text):                                  # -> n x n of 0/1, or None if too long
    data = text.encode("utf-8")
    v = next((v for v in range(1, 6) if len(data) <= _QR_CAP[v]), None)
    if v is None: return None
    n = 17 + 4*v
    M = [[None]*n for _ in range(n)]
    def finder(r0, c0):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0+r, c0+c
                if 0 <= rr < n and 0 <= cc < n:
                    pat = 0 <= r <= 6 and 0 <= c <= 6 and (r in (0, 6) or c in (0, 6)
                          or (2 <= r <= 4 and 2 <= c <= 4))
                    M[rr][cc] = 1 if pat else 0
    finder(0, 0); finder(0, n-7); finder(n-7, 0)
    for i in range(8, n-8):                           # timing
        if M[6][i] is None: M[6][i] = (i+1) % 2
        if M[i][6] is None: M[i][6] = (i+1) % 2
    if v >= 2:                                        # one alignment pattern at (k,k)
        k = 4*v + 10
        for r in range(k-2, k+3):
            for c in range(k-2, k+3):
                M[r][c] = 1 if (r in (k-2, k+2) or c in (k-2, k+2) or (r == k and c == k)) else 0
    M[n-8][8] = 1                                     # dark module
    fmt1 = [(8,0),(8,1),(8,2),(8,3),(8,4),(8,5),(8,7),(8,8),(7,8),
            (5,8),(4,8),(3,8),(2,8),(1,8),(0,8)]
    fmt2 = [(n-1,8),(n-2,8),(n-3,8),(n-4,8),(n-5,8),(n-6,8),(n-7,8),
            (8,n-8),(8,n-7),(8,n-6),(8,n-5),(8,n-4),(8,n-3),(8,n-2),(8,n-1)]
    for (r, c) in fmt1 + fmt2:
        if M[r][c] is None: M[r][c] = 0               # reserve format areas
    bits = []
    def push(val, cnt):
        for k2 in range(cnt-1, -1, -1): bits.append((val >> k2) & 1)
    push(4, 4); push(len(data), 8)
    for b in data: push(b, 8)
    push(0, min(4, _QR_DCW[v]*8 - len(bits)))         # terminator
    while len(bits) % 8: bits.append(0)
    pi = 0
    while len(bits) < _QR_DCW[v]*8:
        push((0xEC, 0x11)[pi % 2], 8); pi += 1
    cw = [int("".join(map(str, bits[i:i+8])), 2) for i in range(0, len(bits), 8)]
    allcw = cw + _rs_ecc(cw, _QR_ECW[v])
    seq = []
    for c8 in allcw:
        for k2 in range(7, -1, -1): seq.append((c8 >> k2) & 1)
    idx = 0; col = n-1; up = True
    while col > 0:                                    # zigzag placement, mask 0
        if col == 6: col -= 1
        rng = range(n-1, -1, -1) if up else range(n)
        for r in rng:
            for cc in (col, col-1):
                if M[r][cc] is None:
                    b = seq[idx] if idx < len(seq) else 0
                    idx += 1
                    if (r + cc) % 2 == 0: b ^= 1
                    M[r][cc] = b
        up = not up; col -= 2
    fbits = [(_QR_FMT0 >> (14-i)) & 1 for i in range(15)]
    for (rc, b) in zip(fmt1, fbits): M[rc[0]][rc[1]] = b
    for (rc, b) in zip(fmt2, fbits): M[rc[0]][rc[1]] = b
    return M

def qr_lines(text, quiet=2):                          # half-block rows: light modules = white blocks
    M = qr_matrix(text)
    if M is None: return []
    n = len(M); w = n + 2*quiet
    grid = [[0]*w for _ in range(w)]                  # 0 = light (drawn), 1 = dark (background)
    for r in range(n):
        for c in range(n): grid[r+quiet][c+quiet] = M[r][c]
    out = []
    for r in range(0, w, 2):
        row = []
        for c in range(w):
            t = grid[r][c] == 0
            b = grid[r+1][c] == 0 if r+1 < w else True
            row.append("█" if t and b else ("▀" if t else ("▄" if b else " ")))
        out.append("".join(row))
    return out

def clip_copy(text):                                  # best-effort OS clipboard
    import subprocess
    if os.name == "nt": cands = [["clip"]]
    elif sys.platform == "darwin": cands = [["pbcopy"]]
    else: cands = [["wl-copy"], ["xclip", "-selection", "clipboard"],   # Wayland first, then X11
                   ["xsel", "--clipboard", "--input"]]
    for cmd in cands:
        try:
            subprocess.run(cmd, input=text, text=True, check=True,
                           stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue
    return False

# ---- the official Wasabi logo (SVG paths -> polygons -> half-block raster) --------
_W_PATHS = [
 "M53.4227 26.9681C46.331 16.2741 43.7272 5.86066 42.672 0C38.4243 2.58412 34.172 5.16824 "
 "29.9198 7.75689C31.1379 12.8527 33.4384 20.4286 37.9352 28.7059H24.6984C24.2954 28.1402 "
 "23.8969 27.5655 23.5029 26.9681C16.4113 16.2741 13.8074 5.86066 12.7522 0C8.49999 2.58412 "
 "4.25226 5.16824 0 7.75689C1.56686 14.3281 4.93153 25.0221 12.4941 36.0329C19.9843 46.9351 "
 "28.3801 53.47 33.7192 57C37.2242 53.6556 40.7248 50.3112 44.2298 46.9622C41.7935 45.4959 "
 "38.4243 43.2331 34.7472 39.9566H45.294C51.9826 48.5371 58.9791 53.9135 63.639 56.9955C67.144 "
 "53.6511 70.6445 50.3066 74.1496 46.9577C71.7133 45.4914 68.3441 43.2286 64.6669 39.952H74.1496"
 "V28.6969H54.6182C54.2152 28.1312 53.8167 27.5564 53.4227 26.959V26.9681Z",
 "M74.3622 0.0454102H59.957V14.4414H74.3622V0.0454102Z"]
_W_VB = (75.0, 57.0)                                  # viewBox width/height

def _svg_polys(d, samples=14):                        # flatten M/L/H/V/C/Z (absolute) to polygons
    seq = re.findall(r"[MLHVCZ]|-?\d*\.?\d+(?:e-?\d+)?", d)
    i = 0; cur = (0.0, 0.0); start = cur; poly = []; polys = []; cmd = None
    def f():
        nonlocal i
        v = float(seq[i]); i += 1; return v
    while i < len(seq):
        t = seq[i]
        if t in "MLHVCZ": cmd = t; i += 1
        if cmd == "M":
            if len(poly) > 2: polys.append(poly)
            cur = (f(), f()); start = cur; poly = [cur]; cmd = "L"; continue
        if cmd == "L": cur = (f(), f()); poly.append(cur)
        elif cmd == "H": cur = (f(), cur[1]); poly.append(cur)
        elif cmd == "V": cur = (cur[0], f()); poly.append(cur)
        elif cmd == "C":
            p1 = (f(), f()); p2 = (f(), f()); p3 = (f(), f()); p0 = cur
            for k in range(1, samples+1):
                u = k/samples; m_ = 1-u
                poly.append((m_**3*p0[0]+3*m_*m_*u*p1[0]+3*m_*u*u*p2[0]+u**3*p3[0],
                             m_**3*p0[1]+3*m_*m_*u*p1[1]+3*m_*u*u*p2[1]+u**3*p3[1]))
            cur = p3
        elif cmd == "Z":
            poly.append(start)
            if len(poly) > 2: polys.append(poly)
            poly = []
    if len(poly) > 2: polys.append(poly)
    return polys

_W_POLYS = [p for d in _W_PATHS for p in _svg_polys(d)]

def _inside(x, y):                                    # even-odd fill test over all subpaths
    c = False
    for poly in _W_POLYS:
        for j in range(len(poly)-1):
            x1, y1 = poly[j]; x2, y2 = poly[j+1]
            if (y1 > y) != (y2 > y) and x < x1 + (y-y1)*(x2-x1)/(y2-y1): c = not c
    return c

_LOGO_CACHE = {}
def logo_cells(rows):                                 # -> [(r, c, glyph, shade 0..1)], cols
    rows = max(4, rows)
    if rows in _LOGO_CACHE: return _LOGO_CACHE[rows]
    vw, vh = _W_VB
    cols = max(8, round(2*rows*vw/vh))                # cell aspect 1:2 -> px grid cols x 2*rows
    pxh = 2*rows
    def cov(px, py):                                  # 2x2 supersample coverage of one pixel
        hits = 0
        for dx in (0.25, 0.75):
            for dy in (0.25, 0.75):
                if _inside((px+dx)*vw/cols, (py+dy)*vh/pxh): hits += 1
        return hits/4.0
    cells = []
    for r in range(rows):
        for c in range(cols):
            t, b = cov(c, 2*r), cov(c, 2*r+1)
            if t >= .45 and b >= .45: cells.append((r, c, "█", (t+b)/2))
            elif t >= .45:            cells.append((r, c, "▀", t))
            elif b >= .45:            cells.append((r, c, "▄", b))
    _LOGO_CACHE[rows] = (cells, cols)
    return _LOGO_CACHE[rows]

def draw_logo(ch, col, y, x, rows, color, dimf=0.35, turn=0.0, bob=0, pitch=0.0, depth=1):
    cells, cols = logo_cells(rows)
    cxc = (cols - 1) / 2.0                            # yaw: rotate about the vertical centre line
    scale = 1.0 - 0.34 * abs(turn)                    # face foreshortens as it turns to a side
    side = clamp8(lerp(BG, color, 0.16))              # extruded 3D side wall (dark)
    edge = clamp8(lerp(BG, color, 0.30))              # front of the wall, a touch lighter
    exd = -1 if turn >= 0 else 1                      # turning one way reveals the far side's thickness
    side_steps = int(round(3.0 * abs(turn)))          # more side wall the more it faces away
    def place(r, c, g, cc, ox, oy):
        yy = y + r + bob + oy; xx = x + int(round(cxc + (c - cxc) * scale)) + ox
        if 0 <= yy < H and 0 <= xx < W: ch[yy][xx] = g; col[yy][xx] = cc
    for step in range(depth, 0, -1):                  # block thickness; pitch tips it forward (rises up)
        oy = int(round(step * (1 - 1.7 * pitch)))
        wall = side if step > 1 else edge
        for r, c, g, s in cells: place(r, c, g, wall, step, oy)
    for step in range(side_steps, 0, -1):             # the turned-away side wall
        for r, c, g, s in cells: place(r, c, g, side if step > 1 else edge, exd*step, 0)
    for r, c, g, s in cells:                          # lit face on top
        place(r, c, g, clamp8(lerp(lerp(BG, color, dimf), color, s)), 0, 0)
    return cols

def logo_anim(f):                                     # -> (turn, bob, pitch, snack, kind); rest (0,0,0,None,-1)
    # a brief action every ~30s, deterministic from the frame counter (cheap, remote-friendly).
    CYCLE, DUR = 660, 74
    t = f % CYCLE
    if t >= DUR: return 0.0, 0, 0.0, None, -1
    cyc = f // CYCLE
    kind = cyc % 3
    p = t / DUR
    if kind == 0:                                     # turn to look left or right, then face front again
        side = 1 if (cyc // 3) % 2 == 0 else -1       # alternate direction each time
        return side * M.sin(p * M.pi), 0, 0.0, None, 0
    if kind == 1:                                     # a slow, subtle forward tip (face stays put)
        return 0.0, 0, 0.5 * M.sin(p * M.pi), None, 1
    # kind 2: nibble - faces slightly toward the mouth sushi, then it's gone ("eaten")
    if t < 40: return (0.3 * M.sin(p * M.pi)), (1 if 0.55 < p < 0.68 else 0), 0.0, True, 2
    return 0.0, 0, 0.0, None, 2

# halfwidth katakana (single terminal cell each) - the W talks
def logo_says(kind, S):
    if kind == 2: return "ﾓｸﾞﾓｸﾞ"                     # *munch munch* (eating the sushi)
    st = S.get("status") or {}
    try: fleft = int(st.get("filtersLeft") or 0)
    except Exception: fleft = 0
    if S.get("cj_on"): return "ﾏｾﾞﾏｾﾞ"                # *mixing* (coinjoin running)
    if fleft > 0: return "ﾄﾞｳｷﾁｭｳ"                    # *syncing* (filters catching up)
    if len(st.get("peers") or []): return "ｾﾂｿﾞｸ"     # *connected* (p2p peers)
    return "ｲﾗｯｼｬｲ"                                   # *welcome!* (sushi-chef greeting)

# ---- layout / canvas (scales with the terminal; min one row spared) ---------------
# Small terminals: the canvas keeps a comfortable virtual size (>=100x31) and emit()
# shows a TWxTH viewport of it (VOFF = vertical scroll) - no line wrapping, no garbage.
W = H = 0                                             # virtual canvas size
TW = TH = 0                                           # real terminal size (viewport)
VOFF = 0                                              # vertical viewport offset (thin mode)
HOFF = 0                                              # horizontal viewport offset (thin mode)
def apply_canvas(tw, th):
    global W, H, TW, TH, VOFF, HOFF
    TW, TH = tw, th
    W = max(100, min(tw, 760)); H = max(31, min(th, 216))
    if TH >= H: VOFF = 0
    if TW >= W: HOFF = 0
def term_canvas(interactive=True):                    # -> the REAL terminal size
    if not interactive: return 118, 40
    try: c, l = shutil.get_terminal_size((118, 40))
    except Exception: c, l = 118, 40
    return max(20, min(c, 760)), max(8, min(l - 1, 216))
apply_canvas(118, 40)

def blank(): return [[" "]*W for _ in range(H)], [[BG]*W for _ in range(H)]
def put(ch, col, r, c, s, color):
    for i, k in enumerate(str(s)):
        if 0 <= r < H and 0 <= c+i < W: ch[r][c+i] = k; col[r][c+i] = color
def rput(ch, col, r, c_end, s, color): put(ch, col, r, c_end-len(str(s)), s, color)

_PREV = {"rows": None, "meta": None}                  # last emitted frame, for row diffing
def repaint():                                        # force a full clear + repaint on next emit
    _PREV["rows"] = None

def emit(o, ch, col):                                 # RLE truecolor frame, synchronized output.
    # Only rows that CHANGED since the last frame are sent (absolute cursor addressing):
    # a full 21fps repaint is megabytes/s over ssh and tears into glitches on slow links.
    meta = (W, H, TW, TH, VOFF, HOFF)
    full = _PREV["meta"] != meta or _PREV["rows"] is None
    prev = None if full else _PREV["rows"]
    out = ["\x1b[?2026h"]
    if full: out.append("\x1b[2J")
    rows = []
    rng = range(VOFF, min(H, VOFF + TH))
    for i, r in enumerate(rng):
        # Never print the bottom-right cell: buggy emulators (Termius iOS et al) scroll a
        # line when it is written, leaving ghost rows above. Trailing blanks are erase-to-
        # EOL instead of spaces, so the last column is only touched by real content.
        cend = HOFF + TW - (1 if i == len(rng) - 1 else 0)
        last = None; line = []
        for c in range(HOFF, min(W, cend)):
            g = ch[r][c]
            if g == " ": line.append(" "); continue
            cc = col[r][c]
            if cc != last: line.append("\x1b[38;2;%d;%d;%dm" % cc); last = cc
            line.append(g)
        s = "".join(line).rstrip() + "\x1b[0m\x1b[K"
        rows.append(s)
        if prev is not None and i < len(prev) and prev[i] == s: continue
        out.append("\x1b[%d;1H%s" % (i + 2, s))       # +2: frames have always started on line 2
    _PREV["rows"] = rows; _PREV["meta"] = meta
    if len(out) > (2 if full else 1):                 # nothing changed -> nothing sent
        o("".join(out) + "\x1b[?2026l"); sys.stdout.flush()

def draw_box(ch, col, y, x, w, h, color):
    for c in range(x+1, x+w-1):
        put(ch, col, y, c, "─", color); put(ch, col, y+h-1, c, "─", color)
    for r in range(y+1, y+h-1):
        put(ch, col, r, x, "│", color); put(ch, col, r, x+w-1, "│", color)
    put(ch, col, y, x, "┌", color); put(ch, col, y, x+w-1, "┐", color)
    put(ch, col, y+h-1, x, "└", color); put(ch, col, y+h-1, x+w-1, "┘", color)

def draw_overlay(ch, col, title, lines, tcol=WHITE):  # lines: str or (str, color)
    def txt(l): return l[0] if isinstance(l, tuple) else l
    w = max([len(title)] + [len(txt(l)) for l in lines]) + 6; h = len(lines) + 4
    vw, vh = min(W, TW), min(H, TH)                   # center inside the visible viewport
    x0 = HOFF + max(0, (vw-w)//2); y0 = VOFF + max(0, (vh-h)//2)
    for yy in range(y0, min(y0+h, H)):
        for xx in range(x0, min(x0+w, W)):
            edge = yy in (y0, y0+h-1) or xx in (x0, x0+w-1)
            ch[yy][xx] = "█" if edge else " "
            col[yy][xx] = clamp8(lerp(BRAND, WHITE, .25)) if edge else (16, 18, 26)
    put(ch, col, y0+1, x0+(w-len(title))//2, title, tcol)
    for i, l in enumerate(lines):
        c = l[1] if isinstance(l, tuple) else lerp(BRAND, WHITE, .45)
        put(ch, col, y0+3+i, x0+3, txt(l), c)

# ---- AFK screensaver: the sushi belt (also a privacy screen - balances hidden) -----
AFK_SECS = 600                                        # 10 min without input
# full block pixel-art sushi (letter-art originals by Daniel Au, 1995, retired for blocks)
_NORI = (46, 68, 44); _RICE = (240, 242, 248); _ROE = (255, 118, 56); _TAMA = (255, 204, 64)

def _nigiri(name, top, stripe, pattern=(2, 2, 3, 2, 3)):   # striped topping over a rice ball
    segs = []
    for i, n in enumerate(pattern):
        segs.append(("█" * n, top if i % 2 == 0 else stripe))
    return (name, top, [
        [("  ", None), ("▄▄▄▄▄▄▄▄▄▄", top)],
        [(" ", None)] + segs,
        [(" ", None), ("████████████", _RICE)],
        [("  ", None), ("▀▀▀▀▀▀▀▀▀▀", _RICE)]])

def _gunkan(name, top, toprow):                       # nori boat, topping mound, rice inside
    return (name, top, [
        [("  ", None), (toprow, top)],
        [(" ", None), ("▐████████▌", _NORI)],
        [(" ", None), ("▐████████▌", _NORI)],
        [("  ", None), ("▀▀▀▀▀▀▀▀", _NORI)]])

def _maki(name, fill):                                # roll cross-section: nori ring, rice, core
    return (name, fill, [
        [("  ", None), ("▄████████▄", _NORI)],
        [(" ", None), ("██", _NORI), ("██", _RICE), ("████", fill), ("██", _RICE), ("██", _NORI)],
        [(" ", None), ("██", _NORI), ("██", _RICE), ("████", fill), ("██", _RICE), ("██", _NORI)],
        [("  ", None), ("▀████████▀", _NORI)]])

SUSHI = [
 _nigiri("maguro",  (210, 58, 68),  (210, 58, 68)),         # akami: solid deep red
 _nigiri("toro",    (240, 120, 128), (255, 236, 232),       # fatty tuna, heavy marbling
         pattern=(1, 2, 2, 2, 1, 2, 2)),
 _nigiri("sake",    (250, 120, 80), (255, 214, 192)),       # salmon + fat lines
 _nigiri("hamachi", (255, 176, 32), (255, 228, 150)),       # yellowtail
 _nigiri("saba",    (126, 156, 204), (224, 232, 244)),      # mackerel, silver skin stripes
 _nigiri("ika",     (232, 238, 248), (204, 214, 232)),      # pearly squid
 _nigiri("tako",    (190, 120, 190), (238, 206, 238),       # octopus, sucker dots
         pattern=(2, 1, 3, 1, 5)),
 ("ebi", ORANGE, [
   [("  ", None), ("▄▄▚▚▄▄▚▚▄▄", ORANGE)],
   [(" ", None), ("██▚▚██▚▚████", ORANGE)],
   [(" ", None), ("████████████", _RICE)],
   [("  ", None), ("▀▀▀▀▀▀▀▀▀▀", _RICE)]]),
 ("tamago", _TAMA, [
   [("  ", None), ("▄▄▄▄▄▄▄▄▄▄", _TAMA)],
   [(" ", None), ("████", _TAMA), ("███", _NORI), ("█████", _TAMA)],
   [(" ", None), ("████", _RICE), ("███", _NORI), ("█████", _RICE)],
   [("  ", None), ("▀▀▀▀▀▀▀▀▀▀", _RICE)]]),
 _gunkan("uni",   (255, 176, 32), "▄▄▄▄▄▄▄▄"),              # uni mound
 _gunkan("ikura", _ROE,           "●●●●●●●●"),              # roe pearls
 _maki("tekka maki", (210, 58, 68)),
 _maki("kappa maki", GREEN),
 _maki("california", ORANGE),
 ("uramaki", _RICE, [
   [("  ", None), ("▄████████▄", _RICE)],
   [(" ", None), ("███", _RICE), ("██████", ORANGE), ("███", _RICE)],
   [(" ", None), ("███", _RICE), ("██", GREEN), ("████", ORANGE), ("███", _RICE)],
   [("  ", None), ("▀████████▀", _RICE)]]),
 ("dumpling", (250, 220, 185), [
   [("    ", None), ("▄▟▙▄", (238, 160, 100))],
   [("  ", None), ("▄██", (250, 220, 185)), ("██", (238, 160, 100)), ("███▄", (250, 220, 185))],
   [(" ", None), ("████████████", (250, 220, 185))],
   [("  ", None), ("▀▀▀▀▀▀▀▀▀▀", (214, 178, 140))]]),
]

# sashimi (no rice): slanted slices, marbling drawn with colour segments
_SAL, _SALF, _SALD = (250, 120, 80), (255, 214, 192), (204, 88, 58)   # salmon + fat lines
_AKA, _AKAH, _AKAD = (198, 38, 54), (238, 96, 106), (150, 26, 40)     # tuna akami
_WGY, _MARB, _WGYD = (232, 108, 118), (255, 238, 234), (188, 74, 86)  # wagyu + marbling
SUSHI += [
 ("salmon sashimi", _SAL, [
   [("   ", None), ("▄▄▄▄▄▄▄▄▄▄", _SAL)],
   [("  ", None), ("██", _SAL), ("██", _SALF), ("███", _SAL), ("██", _SALF), ("██", _SAL)],
   [(" ", None), ("██", _SAL), ("██", _SALF), ("███", _SAL), ("██", _SALF), ("██", _SAL)],
   [(" ", None), ("▀▀▀▀▀▀▀▀▀▀", _SALD)]]),
 ("tuna sashimi", _AKA, [
   [("   ", None), ("▄▄▄▄▄▄▄▄▄▄", _AKAH)],
   [("  ", None), ("████", _AKA), ("█", _AKAH), ("██████", _AKA)],
   [(" ", None), ("███████████", _AKA)],
   [(" ", None), ("▀▀▀▀▀▀▀▀▀▀", _AKAD)]]),
 ("wagyu", _WGY, [
   [("   ", None), ("▄▄▄▄▄▄▄▄▄▄", _WGY)],
   [("  ", None), ("█", _WGY), ("█", _MARB), ("██", _WGY), ("██", _MARB), ("█", _WGY), ("█", _MARB), ("██", _WGY)],
   [(" ", None), ("█", _WGY), ("██", _MARB), ("█", _WGY), ("█", _MARB), ("██", _WGY), ("██", _MARB), ("█", _WGY)],
   [(" ", None), ("▀▀▀▀▀▀▀▀▀▀", _WGYD)]]),
]

# tiny 3x2 nigiri for the logo's snack (topping with a stripe over a rice base)
MINI_SUSHI = {
    "salmon": ("mini", _SAL, [[("▄", _SAL), ("▄", _SALF), ("▄", _SAL)], [("▀▀▀", RICE)]]),
    "wagyu":  ("mini", _WGY, [[("▄", _WGY), ("▄", _MARB), ("▄", _WGY)], [("▀▀▀", RICE)]]),
}

def _piece_width(rows):
    return max(len(r) if isinstance(r, str) else sum(len(t) for t, _ in r) for r in rows)

# platter-only props (not on the belts)
_ONIGIRI = ("onigiri", _RICE, [
   [("   ", None), ("▄██▄", _RICE)],
   [("  ", None), ("██████", _RICE)],
   [(" ", None), ("████████", _RICE)],
   [(" ", None), ("████████", _NORI)]])
_SOY = ("soy", (64, 40, 24), [
   [(" ", None), ("▄▄▄▄▄▄", (208, 210, 218))],
   [("▐", (208, 210, 218)), ("██████", (64, 40, 24)), ("▌", (208, 210, 218))],
   [(" ", None), ("▀▀▀▀▀▀", (208, 210, 218))]])
_WASABI = ("wasabi", GREEN, [
   [("▄▟▙▄", GREEN)],
   [("▀▀▀▀", (88, 158, 88))]])

def draw_too_small(ch, col, f):                       # btop says 'terminal too small'; we say it with maki
    makis = [p for p in SUSHI if p[0] in ("tekka maki", "kappa maki", "california")]
    p = makis[(f // 63) % len(makis)]                 # a fresh maki every ~3 s
    vw, vh = min(W, TW), min(H, TH)
    py = max(0, vh//2 - 4)
    _draw_piece(ch, col, max(0, (vw - _piece_width(p[2]))//2), py, p)
    msg = "terminal too small"
    sub = (f"need 44x10 (now {TW}x{TH})" if (f // 84) % 2 == 0
           else "itamae needs a bigger counter")
    put(ch, col, min(vh-2, py+5), max(0, (vw-len(msg))//2), msg, WARN)
    put(ch, col, min(vh-1, py+6), max(0, (vw-len(sub))//2), sub, GREY)

def draw_saver_platter(ch, col, f):                   # omakase still life
    _saver_chrome(ch, col, "o m a k a s e")
    def g(name): return next(p for p in SUSHI if p[0] == name)
    cx, cy = min(W, TW)//2, H//2 - 4
    plate_w = 86
    for x in range(cx - plate_w//2, cx + plate_w//2):     # the plate, back rim first
        put(ch, col, cy+9, x, "▂", lerp(BG, GREY, .5))
    layout = [(-40, 1, g("ikura")), (-26, 0, _ONIGIRI), (-13, 0, _ONIGIRI),
              (0, 1, g("dumpling")), (14, 1, g("uramaki")), (28, 1, g("ikura")),
              (-36, 5, g("sake")), (-22, 5, g("ebi")), (-8, 5, g("tamago")),
              (6, 5, g("tekka maki")), (17, 5, g("kappa maki")),
              (28, 4, _SOY), (29, 7, _WASABI)]
    for dx, dy, p in layout:                          # back row first, front overlaps
        _draw_piece(ch, col, cx+dx, cy+dy, p)
    rnd = random.Random(7)
    for _ in range(8):                                # a little sparkle over the spread
        x, y = rnd.randint(cx-44, cx+44), rnd.randint(max(3, cy-4), cy-1)
        if (x*13 + y*7 + f//8) % 5 == 0: put(ch, col, y, x, "·", lerp(BRAND, GREY, .5))

# bouncing sushi: pieces under gravity, elastic-ish floor, the physics finale
_BOUNCE = {}
def draw_saver_bounce(ch, col, f):
    _saver_chrome(ch, col, "s u s h i   p h y s i c s")
    vw = min(W, TW); top = VOFF + 3; floor = min(H, VOFF + TH) - 3
    B = _BOUNCE
    if B.get("box") != (vw, floor) or f - B.get("lf", f) > 30:   # fresh spawn per session/resize
        rnd = random.Random()
        B["box"] = (vw, floor)
        pcs = [SUSHI[rnd.randrange(len(SUSHI))] for _ in range(6)]
        B["balls"] = [dict(p=p, w=_piece_width(p[2]), h=len(p[2]),
                           x=rnd.uniform(2, max(3, vw-20)), y=rnd.uniform(top, top+5),
                           vx=rnd.uniform(-1.3, 1.3), vy=rnd.uniform(-0.3, 0.7))
                      for p in pcs]
    B["lf"] = f
    for x in range(1, vw-1):
        put(ch, col, floor+1, x, "▁", lerp(BG, GREY, .5))
    balls = B["balls"]
    for b in balls:                                   # integrate + walls
        b["vy"] += 0.06                               # gravity
        b["x"] += b["vx"]; b["y"] += b["vy"]
        if b["x"] < 1: b["x"] = 1; b["vx"] = abs(b["vx"])
        if b["x"] + b["w"] > vw-1: b["x"] = vw-1-b["w"]; b["vx"] = -abs(b["vx"])
        if b["y"] + b["h"] > floor + 1:
            b["y"] = floor + 1 - b["h"]; b["vy"] = -abs(b["vy"]) * 0.82
            if abs(b["vy"]) < 0.4: b["vy"] = -random.uniform(1.0, 1.7)   # keep it lively
        if b["y"] < top: b["y"] = top; b["vy"] = abs(b["vy"]) * 0.5
    for i in range(len(balls)):                       # sushi-on-sushi: AABB, elastic (equal mass
        for j in range(i+1, len(balls)):              # -> velocity swap on the least-overlap axis)
            a, b = balls[i], balls[j]
            ox = min(a["x"]+a["w"], b["x"]+b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"]+a["h"], b["y"]+b["h"]) - max(a["y"], b["y"])
            if ox <= 0 or oy <= 0: continue
            if ox < oy * 2.1:                         # cells are ~2:1, compare in physical units
                d = 1 if a["x"] < b["x"] else -1
                a["x"] -= d*ox/2; b["x"] += d*ox/2
                if (b["vx"] - a["vx"]) * d < 0:
                    a["vx"], b["vx"] = b["vx"]*0.95, a["vx"]*0.95
            else:
                d = 1 if a["y"] < b["y"] else -1
                a["y"] -= d*oy/2; b["y"] += d*oy/2
                if (b["vy"] - a["vy"]) * d < 0:
                    a["vy"], b["vy"] = b["vy"]*0.9, a["vy"]*0.9
    for b in balls:
        _draw_piece(ch, col, int(b["x"]), int(b["y"]), b["p"])
        if b.get("tag"):                              # blocks that fell in carry their height
            put(ch, col, int(b["y"]) + b["h"], int(b["x"]) + 1, b["tag"], lerp(GREEN, GREY, .35))

def draw_saver_wasabi(ch, col, f):                    # grating fresh wasabi, the sabi way
    _saver_chrome(ch, col, "f r e s h   w a s a b i")
    vw = min(W, TW); cx, cy = vw//2, H//2 - 1
    STEEL, STEEL_D, TOOTH = (176, 182, 192), (134, 140, 150), (118, 124, 134)
    gw, gh = 42, 12
    for r in range(gh):                               # rounded steel plate, soft row gradient
        for c in range(gw):
            nx = (c - gw/2) / (gw/2); ny = (r - gh/2) / (gh/2)
            if nx*nx + ny*ny*0.85 <= 1.0:
                put(ch, col, cy - gh//2 + r, cx - gw//2 + c, "█",
                    clamp8(lerp(STEEL_D, STEEL, r/gh)))
    for r in range(cy - gh//2 + 2, cy + gh//2 - 1, 2):    # grating teeth: darker dimples, no holes
        for c in range(cx - gw//2 + 5, cx + 2, 3):
            put(ch, col, r, c + (r % 2), "█", TOOTH)
    cyc = f % 2100                                    # paste grows, then the plate is cleared
    amount = min(1.0, cyc / 1500.0)
    if cyc > 1800:
        amount = 0.0
        tag = "◆ itadakimasu"
        put(ch, col, cy - gh//2 - 2, max(0, (vw-len(tag))//2), tag, clamp8(lerp(GREEN, WHITE, .3)))
    if amount > 0.05:                                 # one cohesive pile, not scattered flecks
        pw = max(2, int(9 * amount)); ph = max(1, int(3.4 * amount))
        px0, py0 = cx - 9, cy - 2
        W1, W2, W3 = (150, 202, 62), (126, 174, 52), (100, 146, 46)
        for dy in range(-ph, ph + 1):
            for dx in range(-pw, pw + 1):
                if (dx/pw)**2 + (dy/ph)**2 <= 1.0:
                    h = (dx*7 + dy*13) % 5            # deterministic texture, no sparkle churn
                    put(ch, col, py0 + dy, px0 + dx, "█", W1 if h == 0 else (W3 if h == 3 else W2))
    def tri(t, m): p = t % (2*m); return p if p < m else 2*m - p
    sh = tri(int(f*0.7), 5)                           # the rubbing stroke
    rx, ry = cx + 3 - sh//2, cy + 2 - (sh+1)//2
    ROOT, ROOT_D, TIP = (128, 158, 84), (104, 132, 66), (96, 78, 52)
    for i in range(9):                                # the root: a thick clean diagonal, brown tip
        x, y = rx + i, ry + (i+1)//2
        c_ = ROOT if i < 6 else TIP
        put(ch, col, y, x, "██", c_)
        put(ch, col, y + 1, x + 1, "██", ROOT_D if i < 6 else clamp8(lerp(TIP, BG, .25)))
    for k in range(3):                                # fresh flecks at the contact point
        if (f//2 + k*5) % 4 == 0:
            put(ch, col, ry + (k % 2), rx - 2 - k, "█", (150, 202, 62))


_GALAXY_CACHE = {}
def _galaxy_particles():                              # two-arm spiral, deterministic jitter
    if "p" not in _GALAXY_CACHE:
        rnd = random.Random(21); pts = []
        for arm in (0.0, M.pi):
            for i in range(130):
                t = i / 130.0
                pts.append((1.5 + t*16.0 + rnd.uniform(-0.5, 0.5),
                            arm + t*3.4 + rnd.uniform(-0.09, 0.09), t))
        for _ in range(45):                           # loose halo stars
            pts.append((rnd.uniform(3.0, 19.0), rnd.uniform(0, 2*M.pi), 1.0))
        _GALAXY_CACHE["p"] = pts
    return _GALAXY_CACHE["p"]

_INFALL = {}                                          # a freshly mined block, captured by gravity
def draw_galaxy_infall(ch, col, f, angf):
    if not _INFALL: return
    a = _INFALL
    cx, cy = W/2, H/2 - 1
    a["t"] -= 0.008                                   # inward along the arm, ~7 s to the core
    if a["t"] <= 0.05:
        if a["t"] > -0.3:                             # absorbed: a brief glint at the core
            put(ch, col, int(cy), int(cx) - 1, "✦", clamp8(lerp(GREEN, WHITE, .5)))
        else:
            _INFALL.clear()
        return
    sy = min((H-8)/40.0, 1.0); sx = sy * 2.1
    t = a["t"]; r = 1.5 + t*16.0; th = a["arm"] + t*3.4 + f*angf
    p = SUSHI[a["h"] % len(SUSHI)]
    w, h = _piece_width(p[2]), len(p[2])
    px, py = int(cx + r*M.cos(th)*sx - w/2), int(cy + r*M.sin(th)*sy - h/2)
    _draw_piece(ch, col, px, py, p)
    put(ch, col, py + h, px + 1, f"#{a['h']:,}", clamp8(lerp(GREEN, WHITE, .2)))

def draw_saver_galaxy(ch, col, f, rainbow=False):     # while a coinjoin runs, the mix spins
    import colorsys
    cx, cy = W/2, H/2 - 1
    ang = (f - f % 2) * 0.03
    sy = min((H-8)/40.0, 1.0); sx = sy * 2.1          # cell aspect + fit
    for i, (r, th, t) in enumerate(_galaxy_particles()):
        if rainbow and (i*31 + f//6) % 11 == 0: continue     # twinkle
        x = int(cx + r*M.cos(th+ang)*sx); y = int(cy + r*M.sin(th+ang)*sy)
        g = "@#0Ooc*+!;:^"[min(11, int(t*12))]
        if rainbow:                                   # pastel hue sweeps along the arms
            rr, gg, bb = colorsys.hsv_to_rgb((0.28 + 0.75*t) % 1.0, 0.45, 1.0)
            c_ = (int(rr*255), int(gg*255), int(bb*255))
        else:
            c_ = clamp8(lerp((168, 214, 255), (66, 112, 190), t))
        put(ch, col, y, x, g, c_)
    put(ch, col, int(cy), int(cx), "0", WHITE)
    draw_galaxy_infall(ch, col, f, 0.03)
    pulse = qpulse(f, 0.09)
    put(ch, col, 2, max(0, (W-24)//2), "◆ coinjoin in progress", clamp8(lerp(GREEN, WHITE, .35*pulse)))
    put(ch, col, H-2, max(0, (W-46)//2), "amounts hidden while away  ·  any key returns", lerp(BRAND, GREY, .35))
    rput(ch, col, 2, W-3, time.strftime("%H:%M"), lerp(BRAND, GREY, .5))

def draw_saver_galaxy_sushi(ch, col, f):              # spiral of rice grains, sashimi in the arms
    cx, cy = W/2, H/2 - 1
    ang = (f - f % 2) * 0.02                          # a heavier galaxy turns a little slower
    sy = min((H-8)/40.0, 1.0); sx = sy * 2.1
    for i, (r, th, t) in enumerate(_galaxy_particles()):
        if (i*17 + f//8) % 13 == 0: continue          # grains glint in and out
        x = int(cx + r*M.cos(th+ang)*sx); y = int(cy + r*M.sin(th+ang)*sy)
        put(ch, col, y, x, "•" if i % 3 else "·",
            clamp8(lerp((250, 251, 255), (214, 206, 188), t)))   # rice, warming outward
    slices = [p for p in SUSHI if p[0] in ("salmon sashimi", "tuna sashimi", "wagyu")]
    for k, (arm, t) in enumerate(((0.0, .30), (0.0, .62), (0.0, .92),
                                  (M.pi, .30), (M.pi, .62), (M.pi, .92))):
        p = slices[k % len(slices)]
        r = 1.5 + t*16.0; th = arm + t*3.4 + ang
        w, h = _piece_width(p[2]), len(p[2])
        _draw_piece(ch, col, int(cx + r*M.cos(th)*sx - w/2), int(cy + r*M.sin(th)*sy - h/2), p)
    _draw_piece(ch, col, int(cx)-2, int(cy)-1, _WASABI)          # a wasabi core holds it together
    draw_galaxy_infall(ch, col, f, 0.02)
    pulse = qpulse(f, 0.09)
    put(ch, col, 2, max(0, (W-24)//2), "◆ coinjoin in progress", clamp8(lerp(GREEN, WHITE, .35*pulse)))
    put(ch, col, H-2, max(0, (W-46)//2), "amounts hidden while away  ·  any key returns", lerp(BRAND, GREY, .35))
    rput(ch, col, 2, W-3, time.strftime("%H:%M"), lerp(BRAND, GREY, .5))

def _draw_piece(ch, col, px, py, piece, tint=None):   # one sushi at top-left (px, py)
    name, accent, rows = piece
    h = len(rows)
    for i, row in enumerate(rows):
        y = py + i
        if isinstance(row, str):                      # classic ascii: topping tinted, rice bright
            c_ = accent if i < h-2 else (238, 240, 246)
            put(ch, col, y, px, row, tint(c_) if tint else c_)
        else:                                         # pixel piece: explicit per-segment colors
            x = px
            for txt, c_ in row:
                if c_ is not None: put(ch, col, y, x, txt, tint(c_) if tint else c_)
                x += len(txt)

def _saver_chrome(ch, col, title):
    vw, vb = min(W, TW), min(H, VOFF + TH)            # pin to the VISIBLE window
    put(ch, col, VOFF+2, HOFF + max(0, (vw-len(title))//2), title, lerp(BRAND, GREY, .4))
    put(ch, col, vb-2, HOFF + max(0, (vw-46)//2),
        "amounts hidden while away  ·  any key returns", lerp(BRAND, GREY, .35))
    rput(ch, col, VOFF+2, HOFF + vw-3, time.strftime("%H:%M"), lerp(BRAND, GREY, .5))

def draw_saver_belt(ch, col, f):                      # horizontal conveyor
    _saver_chrome(ch, col, "s u s h i   b r e a k")
    yb = H//2 + 2                                     # belt surface (pieces sit on it)
    rail = lerp(BG, GREY, .55)
    for x in range(W):
        put(ch, col, yb+1, x, "─", rail)
    for x in range(-(int(f*0.5) % 8), W, 8):          # rollers move with the belt
        put(ch, col, yb+2, x, "o", lerp(BG, GREY, .4))
    gap = 7
    train = []                                        # (cum_x, piece); duplicate to cover the screen
    cum = 0
    while cum < W + 200:
        for p in SUSHI:
            train.append((cum, p)); cum += _piece_width(p[2]) + gap
    total = cum
    off = int(f*0.5) % total
    for cx, p in train:
        px = (cx - off) % total                       # seamless wrap around the loop
        if px > W: px -= total
        if px + _piece_width(p[2]) < 0 or px > W: continue
        _draw_piece(ch, col, px, yb - len(p[2]) + 1, p)
        put(ch, col, yb+3, px+2, p[0], lerp(BRAND, GREY, .55))

def draw_saver_belt_v(ch, col, f):                    # vertical conveyor, drifting upward
    _saver_chrome(ch, col, "s u s h i   b r e a k")
    xb = W//2
    rail = lerp(BG, GREY, .55)
    for y in range(4, H-3):
        put(ch, col, y, xb-13, "│", rail); put(ch, col, y, xb+13, "│", rail)
    for y in range(4 + (int(f*0.35) % 4), H-3, 4):    # rollers move with the belt
        put(ch, col, y, xb-15, "o", lerp(BG, GREY, .4)); put(ch, col, y, xb+15, "o", lerp(BG, GREY, .4))
    gap = 3
    train = []; cum = 0
    while cum < H + 80:
        for p in SUSHI:
            train.append((cum, p)); cum += len(p[2]) + gap
    total = cum
    off = int(f*0.35) % total
    for cy, p in train:
        py = (cy - off) % total                       # seamless wrap around the loop
        if py > H: py -= total
        if py + len(p[2]) < 4 or py > H-4: continue
        _draw_piece(ch, col, xb - _piece_width(p[2])//2, py, p)
        put(ch, col, py + len(p[2])//2, xb+18, p[0], lerp(BRAND, GREY, .55))

def draw_saver_belt_r(ch, col, f):                    # round kaiten belt, slowly turning
    _saver_chrome(ch, col, "k a i t e n   s u s h i")
    cx, cy = W/2, (H-2)/2 + 0.5
    rx, ry = max(20, W//2 - 16), max(5, H//2 - 8)
    ring = lerp(BG, GREY, .5)
    for k in range(72):                               # the track
        a = k * M.pi / 36
        put(ch, col, int(cy + ry*M.sin(a)), int(cx + rx*M.cos(a)), "·", ring)
    n = 8
    front = (None, -2.0)
    for k in range(n):
        a = 2*M.pi*k/n - f*0.008
        p = SUSHI[(k*2) % len(SUSHI)]
        w, h = _piece_width(p[2]), len(p[2])
        _draw_piece(ch, col, int(cx + rx*M.cos(a) - w/2), int(cy + ry*M.sin(a) - h/2), p)
        if M.sin(a) > front[1]: front = (p[0], M.sin(a))   # piece nearest the viewer
    if front[0]:
        tag = "now serving  ·  " + front[0]
        put(ch, col, H-4, max(0, (W-len(tag))//2), tag, lerp(GREEN, GREY, .35))

def draw_saver_logo(ch, col, f):                      # the wasabi logo, bouncing (DVD style)
    def tri(t, m): p = t % max(2*m, 1); return p if p < m else 2*m - p
    rows = max(6, min(12, H//3))
    _cells, cols = logo_cells(rows)
    fq = f - f % 3                                    # 7fps motion: 1/3 the remote traffic
    x = tri(int(fq*0.55), max(1, W - cols - 2)) + 1
    y = tri(int(fq*0.3), max(1, H - rows - 6)) + 1
    pulse = qpulse(f, 0.05)
    draw_logo(ch, col, y, x, rows, clamp8(lerp(GREEN, BRAND, pulse)), dimf=0.4)
    put(ch, col, y+rows+1, x + max(0, cols//2 - 2), "sabi", lerp(BRAND, GREY, .4))
    put(ch, col, H-2, max(0, (W-46)//2),
        "amounts hidden while away  ·  any key returns", lerp(BRAND, GREY, .35))
    rput(ch, col, 2, W-3, time.strftime("%H:%M"), lerp(BRAND, GREY, .5))

# ---- non-blocking key/mouse reader (raw fd; split-escape safe; Ctrl+C = exit) ------
def make_keyreader(mouse=True):
    KEYS = {"w":"UP","a":"LEFT","s":"DOWN","d":"RIGHT",   # WASD = arrow keys (nav only)
            "\t":"TAB"," ":"SPACE","q":"QUIT","Q":"QUIT",
            "\r":"ENTER","\n":"ENTER","\x7f":"BACK","\x08":"BACK","?":"HELP",
            "\x05":"SAVER"}                           # Ctrl+E: sushi break, right now
    for _d in "123456789": KEYS[_d] = _d
    ARROW = {"A":"UP","B":"DOWN","C":"RIGHT","D":"LEFT"}
    SCAN  = {"H":"UP","P":"DOWN","K":"LEFT","M":"RIGHT","\x0f":"STAB"}
    def parse(chars):                                 # -> ((name, raw) | None, leftover)
        n = len(chars); i = 0
        while i < n:
            c0 = chars[i]
            if c0 in ("\x00", "\xe0"):                # windows extended scancode
                if i+1 >= n: return (None, chars[i:])
                sc = chars[i+1]; i += 2
                k = SCAN.get(sc)
                if k: return ((k, None), [])
                continue
            if c0 == "\x1b":
                if i+1 >= n: return (None, chars[i:])
                a = chars[i+1]
                if a != "[":
                    if a == "O":
                        if i+2 >= n: return (None, chars[i:])
                        if chars[i+2] == "Z": return (("STAB", None), [])
                        i += 3; continue
                    i += 2; continue
                j = i+2; seq = ""
                while j < n:
                    c = chars[j]; seq += c; j += 1
                    if "\x40" <= c <= "\x7e": break
                else:
                    return (None, chars[i:])          # incomplete CSI - retry next tick
                if seq == "Z": return (("STAB", None), [])
                if seq in ARROW: return ((ARROW[seq], None), [])
                if seq == "M":                        # X10 mouse (non-SGR fallback): 3 coord bytes follow
                    if n - j < 3: return (None, chars[i:])
                    i = j + 3; continue
                if seq.startswith("<") and seq[-1] in "Mm":   # SGR mouse: <b;x;y M/m
                    try:
                        b, mx, my = (int(v) for v in seq[1:-1].split(";"))
                        # my-2, not my-1: emit() joins home-escape + rows with "\n", so canvas
                        # row 0 sits on terminal line 2 (SGR y is 1-based on the terminal)
                        if b == 64: return (("WHEELUP", (mx-1, my-2)), chars[j:] if j < n else [])
                        if b == 65: return (("WHEELDN", (mx-1, my-2)), chars[j:] if j < n else [])
                        if seq[-1] == "M" and b in (0, 1, 2):
                            return (("CLICK", (mx-1, my-2)), chars[j:] if j < n else [])
                    except Exception: pass
                    i = j; continue
                if seq == "200~":                     # bracketed paste: swallow through 201~
                    end = "".join(chars[j:]).find("\x1b[201~")
                    if end < 0: return (None, chars[i:])
                    j += end + 6
                i = j; continue
            k = KEYS.get(c0)
            if k: return ((k, c0), [])
            if c0.isprintable(): return ((None, c0), [])
            i += 1
        return (None, [])
    PASTE = 24                                        # generous: modal paste of an address is fine
    pend = []
    def feed(new):
        if "\x03" in new: raise KeyboardInterrupt     # Ctrl+C anywhere = immediate exit
        if not new:
            if pend == ["\x1b"]: pend.clear(); return ("QUIT", "\x1b")
            pend.clear(); return None
        chars = pend + new; pend.clear()
        if len(chars) > PASTE:                        # long burst = paste -> pass to modal as text
            s = re.sub(r"\x1b?\[20[01]~", "", "".join(chars))   # strip bracketed-paste markers
            txt = "".join(c for c in s if c.isprintable() or c == "\n")
            return ("PASTE", txt.strip("\n")) if txt.strip() else None
        ev, leftover = parse(chars); pend[:] = leftover
        return ev
    sys.stdout.write("\x1b[?2004h"); sys.stdout.flush()
    mouse_on = mouse and os.name != "nt"
    if mouse_on: sys.stdout.write("\x1b[?1000;1006h"); sys.stdout.flush()
    if os.name == "nt":
        import msvcrt
        hin = mode0 = _k32 = None; vt = False
        try:
            import ctypes
            _k32 = ctypes.windll.kernel32; hin = _k32.GetStdHandle(-10)
            m = ctypes.c_uint(); _k32.GetConsoleMode(hin, ctypes.byref(m)); mode0 = m.value
            # +EXTENDED +VIRTUAL_TERMINAL_INPUT  -MOUSE -QUICK_EDIT.  VT input makes special keys
            # arrive as escape sequences (Shift+Tab = ESC[Z) - getwch alone drops the Shift modifier.
            _k32.SetConsoleMode(hin, (mode0 | 0x0080 | 0x0200) & ~0x0010 & ~0x0040)
            m2 = ctypes.c_uint(); _k32.GetConsoleMode(hin, ctypes.byref(m2)); vt = bool(m2.value & 0x0200)
        except Exception:
            hin = mode0 = _k32 = None
        if vt and mouse:                              # VT input on -> Windows Terminal can do SGR mouse
            sys.stdout.write("\x1b[?1000;1006h"); sys.stdout.flush()
        def get():
            chars = []
            while msvcrt.kbhit() and len(chars) < 4096:
                try: chars.append(msvcrt.getwch())
                except Exception: break
            return feed(chars)
        def restore():
            sys.stdout.write("\x1b[?2004l"); sys.stdout.flush()
            if vt and mouse: sys.stdout.write("\x1b[?1000;1006l"); sys.stdout.flush()
            if _k32 is not None and hin is not None and mode0 is not None:
                try: _k32.SetConsoleMode(hin, mode0)
                except Exception: pass
        return get, restore
    try:
        import termios, tty, select
        fd = sys.stdin.fileno(); old = termios.tcgetattr(fd); tty.setcbreak(fd)
        def get():
            data = b""                                # raw fd read - buffered stdin hides seq tails
            while select.select([fd], [], [], 0)[0] and len(data) < 8192:
                try: b_ = os.read(fd, 1024)
                except (BlockingIOError, OSError): break
                if not b_: break
                data += b_
                if len(b_) < 1024: break
            return feed(list(data.decode("utf-8", "replace")))
        def restore():
            sys.stdout.write("\x1b[?2004l"); sys.stdout.flush()
            if mouse_on: sys.stdout.write("\x1b[?1000;1006l"); sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return get, restore
    except Exception:
        return (lambda: None), (lambda: None)

# ---- Wasabi daemon JSON-RPC client -------------------------------------------------
class RpcError(Exception): pass

class WasabiRpc:
    def __init__(self, url, user=None, password=None):
        self.url = url.rstrip("/"); self.user = user; self.password = password
    def call(self, method, params=None, wallet=None, timeout=15):
        target = self.url + ("/" + urllib.request.quote(wallet) + "/" if wallet else "")   # wcli.sh shape
        body = json.dumps({"jsonrpc": "2.0", "id": "1", "method": method,
                           "params": params if params is not None else []}).encode()
        req = urllib.request.Request(target, data=body,                                    # text/plain like wcli.sh
              headers={"Content-Type": "text/plain;", "User-Agent": "sabi/1.0 (coinjoin.nl)"})
        if self.user:
            import base64
            tok = base64.b64encode(f"{self.user}:{self.password or ''}".encode()).decode()
            req.add_header("Authorization", "Basic " + tok)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 401: raise RpcError("HTTP 401 (authentication required)")
            try: resp = json.loads(e.read().decode())
            except Exception: raise RpcError(f"HTTP {e.code}")
        except Exception as e:
            raise RpcError(str(e) or type(e).__name__)
        if isinstance(resp, dict) and resp.get("error"):
            raise RpcError(str(resp["error"].get("message", resp["error"])))
        return resp.get("result") if isinstance(resp, dict) else resp

class BitcoindRpc:                                    # the user's own node, creds from Wasabi's config
    def __init__(self, endpoint, cred, torport=37150):   # 37150 = wasabi's own Tor SOCKS port
        from urllib.parse import urlparse
        u = urlparse(endpoint if "://" in str(endpoint) else "http://" + str(endpoint))
        self.host = u.hostname or "127.0.0.1"; self.port = u.port or 8332
        self.user, _, self.password = str(cred or "").partition(":")
        self.torport = torport
    def _tor_sock(self, timeout):                     # minimal SOCKS5 CONNECT for .onion endpoints
        import socket
        s = socket.create_connection(("127.0.0.1", self.torport), timeout=timeout)
        def rx(n):
            b = b""
            while len(b) < n:
                c = s.recv(n - len(b))
                if not c: raise OSError("socks: connection closed")
                b += c
            return b
        try:
            s.sendall(b"\x05\x01\x00")                # no-auth
            if rx(2) != b"\x05\x00": raise OSError("tor socks refused (is the wasabi daemon running?)")
            host = self.host.encode()
            s.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + self.port.to_bytes(2, "big"))
            r = rx(4)
            if r[1] != 0: raise OSError(f"tor could not reach {self.host} (rep {r[1]})")
            if r[3] == 1: rx(4 + 2)                   # swallow BND.ADDR + BND.PORT
            elif r[3] == 4: rx(16 + 2)
            elif r[3] == 3: rx(rx(1)[0] + 2)
            return s
        except Exception:
            s.close(); raise
    def call(self, method, params=None, timeout=30):
        import http.client, base64
        body = json.dumps({"jsonrpc": "1.0", "id": "sabi", "method": method, "params": params or []})
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        if self.host.endswith(".onion"): conn.sock = self._tor_sock(timeout)
        tok = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        try:
            conn.request("POST", "/", body, {"Authorization": "Basic " + tok,
                                             "Content-Type": "application/json"})
            resp = json.loads(conn.getresponse().read().decode() or "{}")
        finally:
            conn.close()
        if isinstance(resp, dict) and resp.get("error"):
            raise RpcError(str(resp["error"].get("message", resp["error"])))
        return resp.get("result") if isinstance(resp, dict) else resp

# ---- wasabi release discovery + verification (mirrors the wallet's own updater) ----
# Wasabi announces releases as a nostr kind-1 event from the team key; tags carry the
# version and per-asset download URLs. SHA256SUMS.wasabisig is a secp256k1 ECDSA (DER,
# base64) over SHA256(SHA256SUMS.asc); the installer's sha256 must match its line in
# SHA256SUMS.asc. Trust roots below are pinned from WalletWasabi/Helpers/Constants.cs.
WASABI_NOSTR_NPUB = "npub129hpcwy3h7uhpzwzts6utkt2p5st7lf4qpzp3d2j0p6z56lvkpgspngzeq"
WASABI_RELEASE_PUBKEY = "02c8ab8eea76c83788e246a1baee10c04a134ec11be6553946f6ae65e47ae9a608"
NOSTR_RELAYS = ["wss://relay.primal.net", "wss://nos.lol", "wss://nostr.mom"]

# secp256k1, verification only (no key material ever touches this code)
_P  = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N  = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8

def _pt_add(a, b):
    if a is None: return b
    if b is None: return a
    if a[0] == b[0] and (a[1] + b[1]) % _P == 0: return None
    if a == b:
        l = (3*a[0]*a[0]) * pow(2*a[1], -1, _P) % _P
    else:
        l = (b[1]-a[1]) * pow(b[0]-a[0], -1, _P) % _P
    x = (l*l - a[0] - b[0]) % _P
    return (x, (l*(a[0]-x) - a[1]) % _P)

def _pt_mul(k, pt):
    r = None
    while k:
        if k & 1: r = _pt_add(r, pt)
        pt = _pt_add(pt, pt); k >>= 1
    return r

def _lift_x(x):                                       # BIP340: point with even y, or None
    if not (0 < x < _P): return None
    y2 = (pow(x, 3, _P) + 7) % _P
    y = pow(y2, (_P+1)//4, _P)
    if y*y % _P != y2: return None
    return (x, y if y % 2 == 0 else _P - y)

def _pub_decode(pub33):                               # compressed pubkey bytes -> point
    x = int.from_bytes(pub33[1:33], "big")
    pt = _lift_x(x)
    if pt is None: raise ValueError("bad pubkey")
    return pt if (pub33[0] == 2) == (pt[1] % 2 == 0) else (pt[0], _P - pt[1])

def schnorr_verify(pub_x32, msg32, sig64):            # BIP340 (nostr event signatures)
    import hashlib
    P_ = _lift_x(int.from_bytes(pub_x32, "big"))
    r = int.from_bytes(sig64[:32], "big"); s = int.from_bytes(sig64[32:], "big")
    if P_ is None or r >= _P or s >= _N: return False
    tag = hashlib.sha256(b"BIP0340/challenge").digest()
    e = int.from_bytes(hashlib.sha256(tag + tag + sig64[:32] + pub_x32 + msg32).digest(), "big") % _N
    R = _pt_add(_pt_mul(s, (_GX, _GY)), _pt_mul(_N - e, P_))
    return R is not None and R[1] % 2 == 0 and R[0] == r

def ecdsa_verify_der(pub33, msg32, der):              # wasabisig over sha256(SHA256SUMS.asc)
    try:
        i = 0
        if der[i] != 0x30: return False
        i += 2
        if der[i] != 0x02: return False
        lr = der[i+1]; r = int.from_bytes(der[i+2:i+2+lr], "big"); i += 2 + lr
        if der[i] != 0x02: return False
        ls = der[i+1]; s = int.from_bytes(der[i+2:i+2+ls], "big")
        if not (0 < r < _N and 0 < s < _N): return False
        e = int.from_bytes(msg32, "big")
        w = pow(s, -1, _N)
        R = _pt_add(_pt_mul(e*w % _N, (_GX, _GY)), _pt_mul(r*w % _N, _pub_decode(pub33)))
        return R is not None and R[0] % _N == r
    except Exception:
        return False

def npub_to_hex(npub):                                # bech32 (BIP173) npub -> 32-byte hex
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    data = [charset.index(c) for c in npub.lower().split("1", 1)[1]][:-6]   # drop checksum
    acc = bits = 0; out = bytearray()
    for v in data:
        acc = (acc << 5) | v; bits += 5
        if bits >= 8:
            bits -= 8; out.append((acc >> bits) & 0xFF)
    return out.hex()

def _ws_recv_frame(sock):                             # -> (opcode, payload) one RFC6455 frame
    def rx(n):
        b = b""
        while len(b) < n:
            c = sock.recv(n - len(b))
            if not c: raise OSError("websocket closed")
            b += c
        return b
    h = rx(2)
    op = h[0] & 0x0F; ln = h[1] & 0x7F
    if ln == 126: ln = int.from_bytes(rx(2), "big")
    elif ln == 127: ln = int.from_bytes(rx(8), "big")
    mask = rx(4) if h[1] & 0x80 else None
    pl = rx(ln) if ln else b""
    if mask: pl = bytes(b ^ mask[i % 4] for i, b in enumerate(pl))
    return op, pl

def _ws_send(sock, payload, op=1):                    # client frames are masked
    import secrets
    mask = secrets.token_bytes(4)
    ln = len(payload)
    hd = bytes([0x80 | op]) + (bytes([0x80 | ln]) if ln < 126 else
         (b"\xfe" + ln.to_bytes(2, "big") if ln < 65536 else b"\xff" + ln.to_bytes(8, "big")))
    sock.sendall(hd + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

def nostr_fetch_release(relay, author_hex, timeout=15):
    import socket, ssl, base64, secrets, hashlib
    from urllib.parse import urlparse
    u = urlparse(relay); host = u.hostname; port = u.port or 443
    raw = socket.create_connection((host, port), timeout=timeout)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    try:
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        sock.sendall((f"GET / HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                      f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        hdr = b""
        while b"\r\n\r\n" not in hdr:
            c = sock.recv(1024)
            if not c: raise OSError("handshake failed")
            hdr += c
        if b" 101" not in hdr.split(b"\r\n", 1)[0]: raise OSError("upgrade refused")
        sub = json.dumps(["REQ", "sabi", {"kinds": [1], "authors": [author_hex], "limit": 1}])
        _ws_send(sock, sub.encode())
        deadline = time.time() + timeout; best = None
        while time.time() < deadline:
            op, pl = _ws_recv_frame(sock)
            if op == 9: _ws_send(sock, pl, op=10); continue      # ping -> pong
            if op == 8: break                                    # close
            if op != 1: continue
            try: msg = json.loads(pl.decode())
            except Exception: continue
            if msg[0] == "EOSE": break
            if msg[0] == "EVENT" and len(msg) >= 3:
                ev = msg[2]
                if ev.get("pubkey") != author_hex or ev.get("kind") != 1: continue
                ser = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"],
                                  ev["tags"], ev["content"]],
                                 separators=(",", ":"), ensure_ascii=False).encode()
                if hashlib.sha256(ser).hexdigest() != ev.get("id"): continue     # forged id
                if not schnorr_verify(bytes.fromhex(ev["pubkey"]),
                                      bytes.fromhex(ev["id"]), bytes.fromhex(ev["sig"])):
                    continue                                     # forged signature
                if best is None or ev["created_at"] > best["created_at"]: best = ev
        _ws_send(sock, b"", op=8)
        return best
    finally:
        try: sock.close()
        except Exception: pass

def wasabi_release_info(timeout=15):                  # -> dict(version=str, assets={name: url})
    author = npub_to_hex(WASABI_NOSTR_NPUB)
    err = None
    for relay in NOSTR_RELAYS:
        try:
            ev = nostr_fetch_release(relay, author, timeout)
        except Exception as e:
            err = e; continue
        if not ev: continue
        tags = {t[0]: t[1] for t in ev.get("tags", []) if len(t) >= 2}
        ver = tags.pop("version", None)
        if not ver: continue
        assets = {n: u for n, u in tags.items() if str(u).startswith("https://")}
        return dict(version=ver, assets=assets, relay=relay, content=ev.get("content", ""))
    raise RpcError(f"no verified release event from any relay ({err})")

def pick_release_asset(version):                      # portable archive with wassabeed for this OS
    import platform
    arm = platform.machine().lower() in ("arm64", "aarch64", "armv7l", "armv6l")
    if os.name == "nt": return f"Wasabi-{version}-win-x64.zip"
    if sys.platform == "darwin": return f"Wasabi-{version}-macOS-{'arm64' if arm else 'x64'}.zip"
    return f"Wasabi-{version}-linux-{'arm64' if arm else 'x64'}.tar.gz"   # tar keeps +x bits

def resolve_asset_url(assets, name):
    # The nostr event tags only SOME asset URLs (e.g. linux-x64 but not linux-arm64), yet the
    # file is on the release and in the signed SHA256SUMS. Derive its URL from any sibling in
    # the same release directory; integrity still comes from the signed hash, not the URL.
    if name in assets: return assets[name]
    for u in assets.values():
        s = str(u)
        if s.startswith("https://") and "/" in s:
            return s.rsplit("/", 1)[0] + "/" + name
    return None

def _download(url, path, progress=None, timeout=60):
    if not str(url).startswith("https://"): raise RpcError("refusing non-https download")
    req = urllib.request.Request(url, headers={"User-Agent": "sabi/1.0 (coinjoin.nl)"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0); done = 0
        while True:
            chunk = r.read(262144)
            if not chunk: break
            f.write(chunk); done += len(chunk)
            if progress: progress(done, total)
    return path

def wasabi_install(info, progress=lambda s: None):    # -> (wassabeed path, archive sha256 hex)
    # verify EVERYTHING before anything is extracted or run - same chain as wasabi itself
    import hashlib, base64, tempfile, zipfile, tarfile
    ver = info["version"]; assets = info["assets"]
    asset = pick_release_asset(ver)
    for need in ("SHA256SUMS.asc", "SHA256SUMS.wasabisig"):
        if need not in assets: raise RpcError(f"release event lacks '{need}'")
    asset_url = resolve_asset_url(assets, asset)      # tagged, or derived from a sibling URL
    if not asset_url: raise RpcError(f"cannot resolve a download URL for {asset}")
    tmp = tempfile.mkdtemp(prefix=f"sabi-wasabi-{ver}-")
    progress("fetching signature files ...")
    asc = open(_download(assets["SHA256SUMS.asc"], os.path.join(tmp, "SHA256SUMS.asc")), "rb").read()
    was = open(_download(assets["SHA256SUMS.wasabisig"], os.path.join(tmp, "s.wasabisig")), "rb").read()
    progress("verifying release signature ...")
    if not ecdsa_verify_der(bytes.fromhex(WASABI_RELEASE_PUBKEY),
                            hashlib.sha256(asc).digest(), base64.b64decode(was)):
        raise RpcError("SIGNATURE INVALID - SHA256SUMS.asc is not signed by the wasabi team key")
    expected = None
    for line in asc.decode("utf-8", "replace").splitlines():
        parts = [p for p in line.split("  ./") if p]
        if len(parts) == 2 and parts[1].strip() == asset: expected = parts[0].strip().lower()
    if not expected: raise RpcError(f"{asset} not listed in the signed SHA256SUMS")
    apath = os.path.join(tmp, asset)
    def pcb(done, total):
        progress(f"downloading {asset}  {done//1048576} / {total//1048576} MB"
                 if total else f"downloading {asset}  {done//1048576} MB")
    _download(asset_url, apath, pcb)
    progress("checking sha256 ...")
    h = hashlib.sha256()
    with open(apath, "rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""): h.update(chunk)
    got = h.hexdigest()
    if got != expected:
        os.remove(apath)
        raise RpcError(f"HASH MISMATCH - expected {expected[:16]}..., got {got[:16]}... (download deleted)")
    dest = os.path.join(os.path.expanduser("~"), f"wasabi-{ver}")
    progress(f"extracting to {dest} ...")
    if asset.endswith(".zip"):
        with zipfile.ZipFile(apath) as zf:
            zf.extractall(dest)
            if os.name != "nt":                       # restore unix modes (zip loses them)
                for zi in zf.infolist():
                    m = (zi.external_attr >> 16) & 0o7777
                    if m: os.chmod(os.path.join(dest, zi.filename), m)
    else:
        with tarfile.open(apath) as tf: tf.extractall(dest)
    exe = None
    want = "wassabeed.exe" if os.name == "nt" else "wassabeed"
    for root, _dirs, files in os.walk(dest):
        if want in files: exe = os.path.join(root, want); break
    if not exe: raise RpcError(f"extracted, but {want} not found under {dest}")
    if os.name != "nt":                               # Windows: PE runs without an exec bit - nothing to do
        # +x every native binary, not just wassabeed: the daemon launches BUNDLED Tor/HWI
        # under BundledApps/Binaries, and a zip (or a stripped tar) leaves them non-exec ->
        # "Permission denied" starting Tor. Detect executables by magic so data files (geoip,
        # dylibs, etc.) are left alone. Covers linux ELF and every macOS Mach-O / universal form.
        EXEC_MAGIC = {
            b"\x7fELF",                                # ELF (linux)
            b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit  (LE / BE)
            b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",  # Mach-O 32-bit  (LE / BE)
            b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",  # Mach-O universal (FAT)
        }
        progress("making bundled binaries executable ...")
        made = 0
        for root, _dirs, files in os.walk(dest):
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    if os.path.islink(fp): continue
                    with open(fp, "rb") as fh: magic = fh.read(4)
                    if magic in EXEC_MAGIC:
                        st = os.stat(fp).st_mode
                        os.chmod(fp, st | ((st & 0o444) >> 2))  # +x wherever there is a matching +r
                        made += 1
                except Exception:
                    pass
        try: os.chmod(exe, os.stat(exe).st_mode | 0o755)  # wassabeed itself, regardless
        except Exception: pass
    try:                                              # remember it so find_daemon works next start
        open(os.path.join(os.path.expanduser("~"), ".sabi-daemon"), "w", encoding="utf-8").write(exe)
    except Exception: pass
    return exe, got

class DemoRpc:                                        # --demo : plausible fake daemon, no network
    def __init__(self):
        rnd = random.Random(21)
        self.cj = False; self.pays = []; self.t0 = time.time()
        self.coins = []
        for i in range(14):
            anon = rnd.choice([1, 1, 1, 2, 3, 5, 8, 21, 34, 55, 90])
            self.coins.append(dict(
                txid="".join(rnd.choice("0123456789abcdef") for _ in range(64)), index=i % 3,
                amount=rnd.choice([50_000, 262_144, 1_048_576, 2_000_000, 5_000_000, 13_421_772]),
                anonymityScore=anon, confirmed=True, confirmations=rnd.randint(1, 900),
                address="bc1q" + "".join(rnd.choice("qpzry9x8gf2tvdw0s3jn54khce6mua7l") for _ in range(38)),
                label=rnd.choice(["zonda", "kraken", "salary", "coinjoin", "", ""]),
                excludedFromCoinjoin=False))                    # 2.8.0 field set (no coinJoinInProgress)
        self.coins[2]["confirmed"] = False                      # one pending-incoming coin
        self.hist = []
        for i in range(24):
            cjrow = rnd.random() < 0.4
            self.hist.append(dict(datetime=f"2026-06-{28-i:02d}T1{i%10}:2{i%6}:00+00:00",
                height=str(902_000-13*i),                       # 2.8.0: height is a string
                amount=rnd.choice([1, -1])*rnd.randint(40_000, 9_000_000),
                label="coinjoin" if cjrow else rnd.choice(["zonda", "kraken", "pizza", "rent", ""]),
                tx="".join(rnd.choice("0123456789abcdef") for _ in range(64)), islikelycoinjoin=cjrow))
        self.hist[0]["height"] = "Mempool"            # one unconfirmed row (speed-up / cancel demo)
    def call(self, method, params=None, wallet=None, timeout=15):
        p = params or []
        if method == "getstatus":                     # 2.8.0 shape: p2p sync, no backendStatus
            fleft = max(0, 420 - int((time.time()-self.t0)*30))   # filters catch up in ~14s
            return dict(torStatus="Running", network="Main",
                        bestBlockchainHeight=str(902_213 + int((time.time()-self.t0)//600)),
                        filtersCount=902_213, filtersLeft=fleft,
                        exchangeRate=101_842.0 + 500*M.sin(time.time()/60), peers=[{}]*8)
        if method == "listwallets": return [{"walletName": "SavingsWallet"}, {"walletName": "DailyWallet"}]
        if method in ("loadwallet", "selectwallet"): return None
        if method == "getwalletinfo":
            return dict(walletName=wallet or "SavingsWallet", loaded=True, anonScoreTarget=5,
                        isWatchOnly=(wallet == "DailyWallet"),  # demo: 2nd wallet is watch-only
                        isAutoCoinjoin=False, masterKeyFingerprint="8f2a1c3d",
                        balance=sum(c["amount"] for c in self.coins),
                        coinjoinStatus="In progress" if self.cj else "Idle")   # 2.8.0 field
        if method == "listkeys":
            rnd = random.Random(7)
            out = []
            for i in range(40):
                out.append(dict(fullKeyPath=f"m/84'/0'/0'/{i%2}/{i}", internal=(i % 3 == 2),
                    keyState=rnd.choice([0, 0, 2]), label=rnd.choice(["zonda", "kraken", "rent", ""]),
                    address="bc1q" + "".join(rnd.choice("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
                                             for _ in range(38))))
            return out
        if method in ("listunspentcoins", "listcoins"): return [dict(c) for c in self.coins]
        if method == "gethistory": return [dict(h) for h in self.hist]
        if method == "getnewaddress":
            rnd = random.Random()
            hrp = "bc1p" if (len(p) > 1 and p[1]) else "bc1q"    # taproot flag (2.8.0)
            return dict(address=hrp + "".join(rnd.choice("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
                        for _ in range(38 if hrp == "bc1q" else 58)),
                        keyPath="m/84'/0'/0'/0/23", label=(p[0] if p else ""))
        if method == "getfeerates": return {"2": 11, "6": 7, "18": 3, "144": 1}
        if method == "startcoinjoin":
            self.cj = True; return None
        if method == "startcoinjoinsweep":                       # 2.8.0: needs a DIFFERENT output wallet
            outw = p[1] if len(p) > 1 else None
            if not outw or outw == (wallet or "SavingsWallet"):
                raise RpcError("Output wallet name is invalid.")
            self.cj = True; return None
        if method == "stopcoinjoin":
            self.cj = False; return None
        if method == "payincoinjoin":
            pid = f"c0ffee{len(self.pays)+1:02d}-aaaa-bbbb-cccc-000000000000"
            self.pays.append(dict(id=pid, amount=p[1] if len(p) > 1 else 0,
                destination="0014" + "ab"*20,                    # 2.8.0: scriptPubKey hex, not address
                state=[dict(status="Pending")]))
            return pid
        if method == "listpaymentsincoinjoin": return [dict(x) for x in self.pays]
        if method == "cancelpaymentincoinjoin":
            self.pays = [x for x in self.pays if x.get("id") != (p[0] if p else "")]
            return None
        if method == "excludefromcoinjoin":
            ex = p[2] if len(p) > 2 else True
            for c in self.coins:
                if c["txid"] == (p[0] if p else "") and c["index"] == (p[1] if len(p) > 1 else -1):
                    c["excludedFromCoinjoin"] = bool(ex)
            return None
        if method == "createwallet":
            words = ("abandon ability able about above absent absorb abstract absurd abuse access accident").split()
            random.Random().shuffle(words)
            return " ".join(words)
        if method == "recoverwallet": return None
        if method in ("canceltransaction", "speeduptransaction"): return "02000000" + "ab"*60
        if method == "broadcast": return dict(txid="e"*64)
        if method == "query":                          # fake Scheme eval - plausible cross-wallet output
            src = (p[0] if p else "")
            wallets = ["SavingsWallet", "DailyWallet", "ColdVault"]
            wo = {"ColdVault": True}
            if "wallet-info" in src:
                return [[["name", w], ["loaded", True], ["readOnly", wo.get(w, False)],
                         ["path", f"~/.walletwasabi/.../{w}.json"]] for w in wallets]
            if "wallet-loaded?" in src:
                return [[w, True] for w in wallets]
            if "open-wallet" in src: return wallets
            if "wallet-watch-only?" in src:
                return [[w, wo.get(w, False)] for w in wallets]
            if "wallet-auto-coinjoin?" in src and "isolation" not in src.lower():
                return [[w, w == "SavingsWallet"] for w in wallets]
            if "wallet-hardware-wallet?" in src:
                return [[w, w == "ColdVault"] for w in wallets]
            if "fingerprint" in src.lower():
                return [[w, f"{8+i:08x}"] for i, w in enumerate(wallets)]
            if "length (wallets)" in src.replace(" ", " "): return len(wallets)
            if "isolation" in src.lower() or "non-private-coin" in src:
                return [[w, wo.get(w, False), w == "SavingsWallet", False] for w in wallets]
            if "network" in src:
                return [["network", "Main"], ["localTip", 902213], ["remoteTip", 902213], ["headersLeft", 0]]
            return "(demo) query evaluated - connect a real daemon with scripting enabled for live data"
        if method in ("send", "build"):
            pay = (params or {}).get("payments", []) if isinstance(params, dict) else []
            tot = sum(x.get("amount", 0) for x in pay)
            self.hist.insert(0, dict(datetime="2026-07-02T12:00:00+00:00", height=None,
                amount=-tot, label="batched send", tx="f"*64, islikelycoinjoin=False))
            return dict(txid="f" * 64, tx="0200000001" + "ab"*90)
        raise RpcError(f"method not found: {method}")

# ---- shared state + background poller ----------------------------------------------
def anon_of(c): return c.get("anonymityScore") or c.get("anonymitySet") or 1
def is_cj_row(h):
    return bool(h.get("islikelycoinjoin") or h.get("isLikelyCoinJoin")
                or str(h.get("label", "")).lower() == "coinjoin")

_BLK_RE = re.compile(r"Block \((\d+)\) downloaded")
def _tail_log(S):                                     # local daemon Logs.txt -> wallet-scan progress
    path = S.get("logpath")                           # (RPC exposes no per-wallet sync progress)
    if not path: return
    try:
        size = os.path.getsize(path)
        pos = S.get("_log_pos")
        if pos is None or pos > size:                 # first read / log rotated: start near the end
            pos = max(0, size - 65536)
        if size == pos: return
        with open(path, "rb") as fh:
            fh.seek(pos); chunk = fh.read(min(size - pos, 262144))
        S["_log_pos"] = pos + len(chunk)
        heights = [int(m) for m in _BLK_RE.findall(chunk.decode("utf-8", "replace"))]
        if not heights: return
        now = time.monotonic()
        S["sync_h"] = heights[-1]
        if not S.get("sync_h0"): S["sync_h0"] = heights[0]
        hist = S.setdefault("sync_hist", [])
        hist.extend((now, 1) for _ in heights)
        hist[:] = [(t, n) for (t, n) in hist if now - t <= 120]
        span = max(10.0, now - hist[0][0]) if hist else 10.0
        S["sync_rate"] = sum(n for _, n in hist) * 60.0 / span
        S["sync_t"] = now
    except Exception:
        pass

def poller(rpc, S, stop):
    import threading
    last_w = last_b = 0.0
    while not stop.is_set():
        try:
            S["status"] = rpc.call("getstatus"); S["err"] = None
        except Exception as e:
            S["err"] = str(e)
        if time.monotonic() - last_w >= 20:
            try:
                ws = rpc.call("listwallets") or []
                S["wallets"] = [w.get("walletName", w) if isinstance(w, dict) else str(w) for w in ws]
            except Exception: pass
            last_w = time.monotonic()
        wname = S.get("wallet")
        if wname:
            try:
                S["winfo"] = rpc.call("getwalletinfo", [], wallet=wname)
                S["no_coord"] = False                 # 2.8.0: getwalletinfo throws without a coordinator
                if S["winfo"] and S["winfo"].get("loaded") is False:
                    S["wloading"] = True; S["werr"] = None    # async load: filters still processing -
                else:                                         # coins/history would throw until loaded
                    S["wloading"] = False
                    S["coins"] = rpc.call("listunspentcoins", [], wallet=wname) or []
                    hist = rpc.call("gethistory", [], wallet=wname) or []
                    if isinstance(hist, dict): hist = hist.get("transactions") or []
                    def _hkey(h_):                    # newest on top: unconfirmed first, then height,
                        try: hh = int(h_.get("height"))    # datetime as tiebreak (ISO sorts lexically)
                        except (TypeError, ValueError): hh = 1 << 31
                        return (hh, str(h_.get("datetime", "")))
                    hist.sort(key=_hkey, reverse=True)
                    S["history"] = hist
                    S["werr"] = None
            except Exception as e:
                S["werr"] = str(e)
                if "no coordinator" in str(e).lower(): S["no_coord"] = True
                if "no wallet loaded" in str(e).lower(): S["wloading"] = True; S["werr"] = None
            try: S["pays"] = rpc.call("listpaymentsincoinjoin", [], wallet=wname) or []
            except Exception: S["pays"] = S.get("pays") or []
            try: S["fees"] = rpc.call("getfeerates", [], wallet=wname) or S.get("fees")
            except Exception: pass
            cjs = str((S.get("winfo") or {}).get("coinjoinStatus") or "")   # 2.8.0: authoritative
            mixing = sum(1 for c in S["coins"] if c.get("coinJoinInProgress"))  # pre-2.8 fallback
            S["cj_coins"] = mixing; S["cj_status"] = cjs
            if cjs: S["cj_on"] = (cjs.lower() != "idle")
            elif mixing: S["cj_on"] = True
            cjtx = sum(1 for h_ in S["history"] if is_cj_row(h_))
            if S.get("single") and cjtx > S.get("single_base", 0):
                S["single"] = False
                try: rpc.call("stopcoinjoin", [], wallet=wname); S["cj_on"] = False
                except Exception: pass
                ding(); S["flash"], S["flasht"] = "single coinjoin round complete - stopped ✓", 90
            if S.get("_rules_w") != wname:            # wallet changed -> load its saved rules
                S["rules"] = load_rules(wname); S["_rules_w"] = wname
            eval_rules(rpc, S, wname)                 # armed automation rules (thresholds/triggers)
        if time.monotonic() - last_b >= 15:           # coinjoin.nl coordinator banner (best effort)
            try:
                req = urllib.request.Request("https://coinjoin.nl/wabisabi/human-monitor",
                      headers={"User-Agent": "sabi/1.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    rs = (json.loads(r.read().decode()).get("RoundStates") or [])
                S["banner"] = rs[0] if rs else None; S["banner_t"] = time.monotonic()
            except Exception: pass
            last_b = time.monotonic()
        try:                                          # a new block? drop a numbered sushi on the belt
            _hh = int((S.get("status") or {}).get("bestBlockchainHeight"))
        except (TypeError, ValueError):
            _hh = None
        if _hh:
            if S.get("_tiph") and _hh > S["_tiph"]:
                S["blockanim"] = dict(h=_hh, t0=time.monotonic(), y=None, vy=0.0,
                                      bounced=False, settled=False)
            S["_tiph"] = _hh
        S["ver"] = S.get("ver", 0) + 1; S["t_poll"] = time.monotonic()
        for _ in range(8):                            # 4s in slices; 'r' kicks an early refresh
            if S.pop("kick", False): break
            if stop.wait(0.5): break
            if S.get("wloading"): _tail_log(S)        # live scan progress from the local daemon log

def fmt_dt(v):                                        # 2.8.0 gethistory: epoch seconds; older: ISO string
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(float(v))))
    except Exception:
        pass
    return str(v or "")[:16].replace("T", " ")

def tgt_of(S):
    wi = S.get("winfo") or {}
    try: return int(wi.get("anonScoreTarget") or 5)
    except Exception: return 5

def balances(S):
    T = tgt_of(S); pr = se = np_ = 0
    for c in S.get("coins") or []:
        a = anon_of(c); v = c.get("amount", 0)
        if a >= T: pr += v
        elif a > 1: se += v
        else: np_ += v
    return pr, se, np_

def _rem_secs(s):
    tot = 0
    for tok in (s or "").split():
        if len(tok) >= 2 and tok[-1] in "dhms" and tok[:-1].isdigit():
            tot += int(tok[:-1]) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[tok[-1]]
    return tot
def _fmt_secs(t):
    t = int(max(0, t)); d, t = divmod(t, 86400); h, t = divmod(t, 3600); m, s = divmod(t, 60)
    if d: return f"{d}d {h}h {m:02d}m"
    if h: return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"

# ---- coin selection for sends (privacy first) ---------------------------------------
def pick_coins(coins, need, T):
    avail = [c for c in coins if c.get("confirmed", True) and not c.get("coinJoinInProgress")]
    pr = [c for c in avail if anon_of(c) >= T]
    se = [c for c in avail if 1 < anon_of(c) < T]
    np_ = [c for c in avail if anon_of(c) <= 1]
    sel = []; tot = 0
    for grp in (pr, se, np_):
        for c in sorted(grp, key=lambda c: -c.get("amount", 0)):
            if tot >= need: break
            sel.append(c); tot += c.get("amount", 0)
        if tot >= need: break
    toxic = any(anon_of(c) >= T for c in sel) and any(anon_of(c) <= 1 for c in sel)
    return sel, tot, toxic

# ---- automation rules (visually programmable coinjoin triggers) --------------------
RULES_FILE = os.path.join(os.path.expanduser("~"), ".sabi-rules.json")
TRIGN = {"np": "non-private", "pr": "private", "tot": "total", "always": "always"}
ACTN  = {"start": "start coinjoin", "single": "join ONE round", "sweep": "sweep -> wallet",
         "stop": "stop coinjoin"}

def load_rules(wname):
    try:
        allr = json.load(open(RULES_FILE, encoding="utf-8"))
        return allr.get(wname) or []
    except Exception:
        return []

def save_rules(wname, rules):
    try:
        try: allr = json.load(open(RULES_FILE, encoding="utf-8"))
        except Exception: allr = {}
        allr[wname] = rules
        json.dump(allr, open(RULES_FILE, "w", encoding="utf-8"), indent=1)
    except Exception:
        pass

def rule_window(s):                                   # "23:00-06:00" -> ("23:00","06:00") or None
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", s or "")
    if not m: return None
    h0, m0, h1, m1 = (int(g) for g in m.groups())
    if not (h0 < 24 and h1 < 24 and m0 < 60 and m1 < 60): return None
    return (f"{h0:02d}:{m0:02d}", f"{h1:02d}:{m1:02d}")

def in_window(rl):                                    # local time inside the rule's window (wraps midnight)
    t0, t1 = rl.get("t0"), rl.get("t1")
    if not t0 or not t1: return True
    now = time.localtime(); cur = now.tm_hour*60 + now.tm_min
    a = int(t0[:2])*60 + int(t0[3:]); b = int(t1[:2])*60 + int(t1[3:])
    return (a <= cur < b) if a <= b else (cur >= a or cur < b)

def rule_text(rl):
    t = TRIGN.get(rl.get("trig"), "?"); a = ACTN.get(rl.get("act"), "?")
    cond = "always" if rl.get("trig") == "always" else f"when {t} ≥ {rl.get('amt', 0)/1e8:.4f} BTC"
    tail = f" {rl.get('outw', '')}" if rl.get("act") == "sweep" else ""
    win = f"  · {rl['t0']}-{rl['t1']}" if rl.get("t0") else ""
    return f"{cond}  →  {a}{tail}{win}"

def eval_rules(rpc, S, wname):                       # poller hook: fire armed rules (10 min cooldown)
    pw = S.get("autopw")
    if not (S.get("armed") and pw is not None and wname) or S.get("werr"): return
    prv, sev, npv = balances(S); tot = prv + sev + npv
    now = time.time()
    for rl in S.get("rules") or []:
        if not rl.get("on") or now - rl.get("last", 0) < 600: continue
        if not in_window(rl): continue
        amt = rl.get("amt", 0)
        ok = {"np": npv >= amt, "pr": prv >= amt, "tot": tot >= amt, "always": True}.get(rl.get("trig"))
        if not ok: continue
        a = rl.get("act")
        try:
            if a == "start" and not S.get("cj_on"):
                rpc.call("startcoinjoin", [pw, True, True], wallet=wname); S["cj_on"] = True
            elif a == "single" and not S.get("cj_on"):
                S["single_base"] = sum(1 for h_ in S.get("history") or [] if is_cj_row(h_))
                rpc.call("startcoinjoin", [pw, False, True], wallet=wname)
                S["cj_on"] = True; S["single"] = True
            elif a == "sweep" and not S.get("cj_on"):
                rpc.call("startcoinjoinsweep", [pw, rl.get("outw", "")], wallet=wname); S["cj_on"] = True
            elif a == "stop" and S.get("cj_on"):
                rpc.call("stopcoinjoin", [], wallet=wname); S["cj_on"] = False; S["single"] = False
            else:
                continue
            rl["last"] = now; save_rules(wname, S["rules"]); ding()
            S["flash"], S["flasht"] = f"◆ rule fired: {rule_text(rl)}", 110
        except Exception as e:
            rl["last"] = now
            S["flash"], S["flasht"] = f"✗ rule failed ({rule_text(rl)}): {e}", 110

# ---- Scheme console: curated cross-wallet scripts (Wasabi 2.8.0 'query' RPC) --------
# IMPORTANT: Wasabi's Scheme interpreter is EXPERIMENTAL and NOT tail-recursive. Its list ops
# (length/filter/map/fold) recurse once per element on a 1 MB .NET stack, and a StackOverflow in
# .NET is UNCATCHABLE - it kills the whole daemon. So iterating a wallet's coins or transactions
# can crash Wasabi on any non-trivial wallet. These curated snippets therefore only touch
# WALLET-LEVEL metadata (a handful of wallets) - never coin/transaction lists. Coin-level scripts
# are possible via 'e' (edit) but are flagged at-your-own-risk in the UI.
SCHEME_SNIPPETS = [
 ("wallets overview",
  "name · loaded · read-only · path for every wallet on the daemon",
  "(map wallet-info (wallets))"),
 ("loaded state",
  "which wallets are currently loaded (started) in the daemon",
  "(map (lambda (w) (list (wallet-name w) (wallet-loaded? w))) (wallets))"),
 ("open ALL wallets",
  "start every wallet so other cross-wallet queries can see them",
  "(map (lambda (w) (wallet-name (open-wallet w))) (wallets))"),
 ("watch-only wallets",
  "flag each wallet: watch-only (cold/read-only) or spendable",
  "(map (lambda (w) (list (wallet-name w) (wallet-watch-only? w))) (wallets))"),
 ("auto-coinjoin flags",
  "which loaded wallets have auto-coinjoin enabled",
  "(map (lambda (w) (list (wallet-name w) (wallet-auto-coinjoin? w)))\n"
  "     (get-opened-wallets))"),
 ("hardware wallets",
  "flag each wallet as hardware-backed or software",
  "(map (lambda (w) (list (wallet-name w) (wallet-hardware-wallet? w)))\n"
  "     (get-opened-wallets))"),
 ("master fingerprints",
  "master key fingerprint per loaded wallet (identify duplicates)",
  "(map (lambda (w) (list (wallet-name w) (wallet-master-key-fingerprint w)))\n"
  "     (get-opened-wallets))"),
 ("wallet count",
  "how many wallets exist on this daemon",
  "(length (wallets))"),
 ("full wallet info",
  "detailed info incl. accounts/xpubs per wallet (metadata only)",
  "(map (lambda (w)\n"
  "       (list (wallet-name w) (wallet-watch-only? w)\n"
  "             (wallet-auto-coinjoin? w) (wallet-non-private-coin-isolation? w)))\n"
  "     (get-opened-wallets))"),
 ("sync status",
  "network + local/remote tip height + block filters remaining",
  "(list (list \"network\"     (native->string network))\n"
  "      (list \"localTip\"    (local-tip-height))\n"
  "      (list \"remoteTip\"   (remote-tip-height))\n"
  "      (list \"headersLeft\" (headers-left)))"),
]

def _write_config_atomic(path, text):
    # Never truncate the live Config.json in place: a crash mid-write loses the user's RPC
    # password + all settings. Back it up once, write a temp file, then atomically replace.
    bak = path + ".sabi.bak"
    if not os.path.exists(bak) and os.path.exists(path):
        try: shutil.copy2(path, bak)                  # one-time safety copy of the original
        except Exception: pass
    tmp = path + ".sabi.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)                         # atomic on the same filesystem
    except Exception:
        try: os.remove(tmp)                           # leave no half-written temp behind
        except OSError: pass
        raise

def _edit_config(path, mutate):                       # load -> mutate dict -> atomic write; preserves
    cfg = json.load(open(path, encoding="utf-8-sig")) # every other key, value and order
    mutate(cfg)
    _write_config_atomic(path, json.dumps(cfg, indent=2))

def enable_scripting_in_config(path):                 # add "scripting" to ExperimentalFeatures
    try:
        def m(cfg):
            ef = cfg.get("ExperimentalFeatures")
            if not isinstance(ef, list): ef = []
            if not any(str(x).lower() == "scripting" for x in ef): ef.append("scripting")
            cfg["ExperimentalFeatures"] = ef
        _edit_config(path, m)
        return True
    except Exception as e:
        print(f"could not edit {path}: {e}", file=sys.stderr)
        return False

# ---- coinjoin coordinator (Config.json 'CoordinatorUri') ----------------------------
# Wasabi ships WITHOUT a coordinator - the user picks one they trust. Without it the
# daemon has no CoinJoinManager and even getwalletinfo fails: "No coordinator configured."
# A coordinator batches the rounds; it sees coinjoin activity and sets the coordination
# fee, but it can never steal funds (WabiSabi is trustless for custody).
KNOWN_COORDINATORS = [
    ("coinjoin.nl", "https://coinjoin.nl/",      "this project's coordinator"),
    ("kruw.io",     "https://coinjoin.kruw.io/", "well-known, long-running"),
]
LIQUISABI_API = "http://liquisabi.com/api"            # public round aggregator (same as txflow.py)

def coord_host(url):                                  # short display name from a coordinator URL
    from urllib.parse import urlparse
    h = (urlparse(str(url)).netloc or str(url)).lower()
    for pre in ("www.", "api.", "btcpay.", "coordinator.", "coinjoin.", "wabisabi."):
        if h.startswith(pre) and "." in h[len(pre):]: h = h[len(pre):]   # keep e.g. coinjoin.nl intact
    return h or str(url)

def fetch_live_coordinators(api=LIQUISABI_API, days=14, n=100, timeout=10):
    import datetime                                   # coordinators with recent PUBLIC rounds
    now = datetime.datetime.now(datetime.timezone.utc)
    body = json.dumps({"jsonrpc": "2.0", "id": "1", "method": "dashboard", "params": {
        "since": (now - datetime.timedelta(days=days)).isoformat(), "until": now.isoformat(),
        "page": 1, "pageSize": n, "orderBy": "RoundEndTime", "descending": True, "searchTerm": ""}})
    req = urllib.request.Request(api, data=body.encode("utf-8"),
        headers={"Content-Type": "text/plain;charset=UTF-8", "User-Agent": "sabi/1.0 (coinjoin.nl)",
                 "Origin": "http://liquisabi.com", "Referer": "http://liquisabi.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    rounds = ((resp.get("result") or resp).get("PaginatedRounds") or {}).get("Rounds") or []
    seen = {}
    for rd in rounds:
        ep = str(rd.get("CoordinatorEndpoint") or "").strip()
        if ep: seen[ep] = seen.get(ep, 0) + 1
    return sorted(seen.items(), key=lambda kv: -kv[1])   # [(url, rounds seen in window)]

def norm_coord_uri(s):                                # user text -> URI for Config.json (None = invalid)
    from urllib.parse import urlparse
    s = (s or "").strip()
    if s.lower() in ("none", "off", "clear"): return ""      # explicit: run without a coordinator
    if not s: return None
    if "://" not in s: s = "https://" + s
    try:
        u = urlparse(s); host, _ = u.hostname, u.port  # .port raises on a malformed port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not host: return None
    if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", host): return None
    if host.endswith("wasabiwallet.io"): return None   # daemon rejects the old zkSNACKs host
    return s if s.endswith("/") else s + "/"

def set_coordinator_in_config(path, uri):
    # edit ONLY the CoordinatorUri value: v2.8.0's strict loader deletes and re-defaults a
    # Config.json it cannot fully decode, so never write a fresh/partial file here.
    try:
        _edit_config(path, lambda cfg: cfg.__setitem__("CoordinatorUri", uri))
        return True
    except Exception as e:
        print(f"could not edit {path}: {e}", file=sys.stderr)
        return False

# ---- the TUI ------------------------------------------------------------------------
TABS = ["dashboard", "wallet", "history", "coinjoin", "send", "auto", "scheme"]

HELP = ["WASD / ↑↓←→      w up · s down (rows) · a/d = ←→ switch tab · Tab too",
        "enter            primary action      y           copy (address / txid)",
        "r                refresh now         ?           this help",
        ".                privacy mode: hide amounts + addresses (receive stays visible)",
        "q                quit                Ctrl+C      quit immediately",
        "Ctrl+E           sushi break now (screensaver; also auto after 10 min idle)",
        "small screens    [ / ] pan left/right · w/s scroll the page (on list-less tabs)",
        "",
        "any tab    g RECEIVE - label -> fresh address + scannable QR code",
        "dashboard  space/enter load wallet · n create wallet · v recover wallet",
        "           i install wasabi: nostr release + signature/sha256 verified download",
        "           p connect to a running daemon on other RPC address/user/pass (RaspiBlitz)",
        "           t import a TREZOR (coinjoin signs on the device via the bridge)",
        "wallet     k address book · x exclude coin from coinjoin · y copy address",
        "           click anon/amount/confs headers to sort (newest confirmed on top)",
        "history    u speed up (fee bump) · c cancel unconfirmed tx · y copy txid",
        "           r copy raw hex (fetched from YOUR node; also works after send)",
        "coinjoin   space start/stop · o single round · b sweep to other wallet",
        "           p pay inside coinjoin · x cancel selected payment · e trezor acct",
        "           c choose coordinator (edits Config.json - daemon restart needed)",
        "send       n add payment · i import pasted list · e edit · x remove",
        "           + / - apply no-change round-up/down (exact coin match, no change output)",
        "           u subtract-fee · enter send (live fee estimate in the confirm)",
        "auto       n new rule · e edit · space on/off · x delete · m arm/disarm",
        "           rules: when np/pr/tot ≥ threshold -> start/single/sweep/stop",
        "",
        "mouse: click tabs/rows · click the selected row again (or double-click)",
        "       to open/run it · click addresses & txids to copy them · wheel scrolls",
        "       also clickable: sort headers, coordinator, coinjoin status, [on]/[off],",
        "       no-change round-up/down, modal fields & numbered menu lines"]

def tui(rpc, args, frames=0):
    global VOFF, HOFF
    import threading
    interactive = sys.stdin.isatty() and frames == 0
    apply_canvas(*term_canvas(interactive or frames > 0))
    S = dict(err=None, werr=None, status=None, wallets=[], wallet=args.wallet, winfo=None,
             coins=[], history=[], pays=[], fees=None, cj_on=False, cj_coins=0, cj_status="",
             armed=False, autopw=None, single=False, single_base=0, banner=None, banner_t=0,
             rules=[], _rules_w=None, notice=None, pager=None, busy=None,
             wloading=False, logpath=getattr(args, "logpath", None),
             cfgpath=getattr(args, "cfgpath", None), loaded=set(),
             no_coord=False, coord_uri=getattr(args, "coord", None), coord_menu=None,
             hw_auth=None,
             last_send=None, coin_sort=("confs", False),
             sc_out=None, sc_custom=None, sc_running=False, sc_needs_enable=False, sc_hist=[],
             sync_h=None, sync_h0=None, sync_rate=0.0, sync_t=0.0,
             t_poll=0.0, flash="", flasht=0, ver=0)
    if args.wallet: S["loaded"].add(args.wallet)
    bc = getattr(args, "btcrpc", None) or {}
    btcrpc = BitcoindRpc(bc["url"], bc.get("cred", "")) if bc.get("url") else None
    stop = threading.Event()
    threading.Thread(target=poller, args=(rpc, S, stop), daemon=True).start()
    getkey, restore = make_keyreader() if interactive else ((lambda: None), (lambda: None))
    o = sys.stdout.write
    o("\x1b]0;sabi · wasabi daemon\x07\x1b[?1049h\x1b[?25l\x1b[2J")
    tab = 0; sel = {t: 0 for t in range(7)}; off = {t: 0 for t in range(7)}
    helpon = False; modal = None; queue = []; sub_fee = False; regions = []
    sug_lock = {}                                     # applied no-change match: {coins, subset, total}
    def flash(msg, t=60): S["flash"], S["flasht"] = msg, t

    def act(label, fn):                               # RPC actions never block the render loop
        def w():
            S["busy"] = label
            try:
                r = fn()
                flash(label + (f"  {r}" if isinstance(r, str) else "") + "  ✓")
            except Exception as e:
                flash(f"✗ {label}: {e}", 90)
            finally:
                S["busy"] = None
        threading.Thread(target=w, daemon=True).start()

    def open_modal(title, fields, cb, warn=None, info=None, lines=None):
        nonlocal modal                                # lines: list or callable -> menu above the fields
        modal = dict(title=title, fields=fields, i=0, cb=cb, warn=warn, info=info, lines=lines)

    def modal_key(name, raw):
        nonlocal modal
        fl = modal["fields"]; i = modal["i"]
        if name == "QUIT": modal = None; return
        if name == "ENTER":
            if i < len(fl)-1: modal["i"] += 1; return
            vals = {f["k"]: f["v"] for f in fl}; cb = modal["cb"]; modal = None; cb(vals); return
        if name == "TAB":  modal["i"] = (i+1) % len(fl); return
        if name == "STAB": modal["i"] = (i-1) % len(fl); return
        if name == "BACK": fl[i]["v"] = fl[i]["v"][:-1]; return
        if name in ("CLICK", "WHEELUP", "WHEELDN"): return   # mouse: raw is (x, y), not text
        if name == "PASTE": fl[i]["v"] += raw; return
        if isinstance(raw, str) and raw.isprintable(): fl[i]["v"] += raw

    def parse_amount(v):                              # BTC ("0.001") or sats ("150000s"/"150000 sats")
        v = v.strip().lower().replace("btc", "").strip()
        if v.endswith("sats"): return int(float(v[:-4].strip()))
        if v.endswith("s") and v[:-1].strip().replace(".", "").isdigit(): return int(float(v[:-1]))
        return int(round(float(v) * 1e8))

    def usd(sats):                                    # fiat tag from the daemon's exchange rate
        if DISCREET["on"]: return "≈ $***" if sats else ""
        xr = (S.get("status") or {}).get("exchangeRate")
        try: return f"≈ ${sats/1e8*float(xr):,.0f}" if xr and sats else ""
        except Exception: return ""

    def wo():                                         # pure watch-only can't sign; hardware can
        wi = S.get("winfo") or {}
        if wi.get("isWatchOnly") and not wi.get("isHardwareWallet"):
            flash("◇ watch-only wallet - it can't sign; open its hot counterpart", 80)
            return True
        return False

    def hw():
        return bool((S.get("winfo") or {}).get("isHardwareWallet"))

    def hw_auth_watch():                              # the device wants a hold-to-confirm NOW
        S["hw_auth"] = time.monotonic()
        flash("◆ CONFIRM ON YOUR TREZOR - hold to approve the coinjoin batch", 170)
        ding()
        def w():
            deadline = time.monotonic() + 200         # matches the daemon's 3-minute device window
            while time.monotonic() < deadline:
                if not S.get("hw_auth"): return       # stopped / cancelled meanwhile
                if (S.get("cj_status") or "").lower() not in ("", "idle"):
                    S["hw_auth"] = None
                    ding()
                    S["flash"], S["flasht"] = "✓ trezor authorized - mixing unattended from here on", 150
                    return
                time.sleep(1.5)
            if S.get("hw_auth"):
                S["hw_auth"] = None
                S["flash"], S["flasht"] = "✗ no authorization seen - device prompt timed out? start coinjoin again", 160
        threading.Thread(target=w, daemon=True).start()

    def parse_batch(txt):                             # "addr amount [label]" per line/; -> payments
        out = []
        for line in re.split(r"[\n;]+", txt or ""):
            toks = [t for t in re.split(r"[,\s]+", line.strip()) if t]
            if len(toks) < 2 or not is_btc_address(toks[0]): continue
            try: amt = parse_amount(toks[1])
            except Exception: continue
            if amt > 0: out.append(dict(sendto=toks[0], amount=amt, label=" ".join(toks[2:]) or "sabi"))
        return out

    # ---------- actions --------------------------------------------------------------
    def do_load_wallet():
        wl = S.get("wallets") or []
        if not wl: flash("no wallets found on the daemon", 60); return
        name = wl[sel[0] % len(wl)]
        def fn():
            try: rpc.call("loadwallet", [name])
            except Exception:
                try: rpc.call("selectwallet", [name])
                except Exception: pass
            S.setdefault("loaded", set()).add(name)   # loaded wallets stay loaded in the daemon
            # switch the ACTIVE wallet now; drop its data (never show stale coins)
            S["wallet"] = name; S["winfo"] = None
            S["coins"] = []; S["history"] = []; S["pays"] = []; S["wloading"] = True
            S["kick"] = True
            return name
        act("loading wallet", fn)

    def do_load_all():                                # load every wallet -> cross-wallet Scheme queries
        wl = S.get("wallets") or []
        if not wl: flash("no wallets found on the daemon", 60); return
        def fn():
            done = 0
            for name in wl:
                try: rpc.call("loadwallet", [name]); S.setdefault("loaded", set()).add(name); done += 1
                except Exception: pass
            if not S.get("wallet") and wl: S["wallet"] = wl[0]
            S["kick"] = True
            return f"{done}/{len(wl)} loaded - filters sync in the background"
        act("loading all wallets", fn)

    def do_receive():
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        def cb(v):
            lab = (v.get("label") or "").strip()
            if not lab: flash("✗ wasabi requires a label - who is paying you?", 80); return
            tap = v.get("taproot", "").strip().lower() in ("y", "yes", "true", "1")
            def fn():
                r = rpc.call("getnewaddress", [lab, True] if tap else [lab], wallet=S["wallet"])
                addr = (r or {}).get("address") if isinstance(r, dict) else str(r)
                kp = (r or {}).get("keyPath", "") if isinstance(r, dict) else ""
                copied = clip_copy(addr or "")
                qr = [("  " + q, WHITE) for q in qr_lines(addr or "")] if H >= 34 else []
                S["notice"] = dict(title="RECEIVE  ·  give this address to the payer", lines=[
                    "", ("  " + (addr or "?"), clamp8(lerp(GREEN, WHITE, .35))), "",
                    f"  label    {lab}", f"  type     {'taproot' if tap else 'segwit'}"
                    + (f"      path {kp}" if kp else ""),
                    "  " + ("copied to clipboard ✓" if copied else "clipboard unavailable"), ""]
                    + qr + ["" if qr else "  (terminal too small for the QR - resize and retry)",
                    "  one address, one use - never reuse it", "", "  press any key to close"])
                return "address ready"
            act("receive", fn)
        open_modal("RECEIVE INTO " + str(S.get("wallet", "")).upper(),
                   [dict(k="label", label="label", v="", mask=False,
                         hint="who/what is this payment for? (required)"),
                    dict(k="taproot", label="taproot? (y/n)", v="n", mask=False,
                         hint="n = segwit bc1q (default) · y = taproot bc1p")], cb)

    def coins_view():                                 # wallet tab order: sortable columns
        key, rev = S.get("coin_sort") or ("confs", False)
        coins = list(S.get("coins") or [])
        if key == "anon": kf = anon_of
        elif key == "amount": kf = lambda c: c.get("amount", 0)
        else: kf = lambda c: c.get("confirmations", 0) if c.get("confirmed", True) else -1
        coins.sort(key=kf, reverse=rev)               # default: lowest confs first = newest on top
        return coins

    def do_toggle_exclude():
        coins = coins_view()
        if not coins: return
        c = coins[sel[1] % len(coins)]
        ex = not c.get("excludedFromCoinjoin")
        act("exclude" if ex else "include",
            lambda: rpc.call("excludefromcoinjoin", [c.get("txid"), c.get("index"), bool(ex)],
                             wallet=S["wallet"]))

    def do_cj_toggle():
        if wo(): return
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        if S.get("cj_on"):
            def fn():
                rpc.call("stopcoinjoin", [], wallet=S["wallet"])
                S["cj_on"] = False; S["single"] = False; S["hw_auth"] = None
            act("coinjoin stopped", fn); return
        def cb(v):
            def fn():
                rpc.call("startcoinjoin", [v.get("password", ""), True, True], wallet=S["wallet"])
                S["cj_on"] = True
                if hw(): hw_auth_watch()
            act("coinjoin started ◆", fn)
        open_modal("START COINJOIN", [dict(k="password", label="wallet password", v="", mask=True,
                   hint=("leave empty - authorize ON THE DEVICE"
                         if hw() else "mixes until everything is private, then stops"))], cb)

    def do_cj_single():
        if wo(): return
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        def cb(v):
            def fn():
                base = sum(1 for h_ in S.get("history") or [] if is_cj_row(h_))
                rpc.call("startcoinjoin", [v.get("password", ""), False, True], wallet=S["wallet"])
                S["cj_on"] = True; S["single"] = True; S["single_base"] = base
                if hw(): hw_auth_watch()
                return "watching for the round to confirm"
            act("single round joined ◆", fn)
        open_modal("JOIN ONE COINJOIN ROUND",
                   [dict(k="password", label="wallet password", v="", mask=True,
                         hint="joins now; sabi auto-stops after one completed round")], cb)


    def do_cj_sweep():
        if wo(): return
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        others = [w for w in (S.get("wallets") or []) if w != S.get("wallet")]
        def cb(v):
            outw = v.get("output", "").strip()
            if not outw or outw == S.get("wallet"):
                flash("✗ sweep needs a DIFFERENT output wallet name", 80); return
            def fn():
                rpc.call("startcoinjoinsweep", [v.get("password", ""), outw], wallet=S["wallet"])
                if hw(): hw_auth_watch()
            act("coinjoin sweep started ◆", fn)
        open_modal("COINJOIN SWEEP  ·  mix everything to another wallet",
                   [dict(k="password", label="wallet password", v="", mask=True, hint=""),
                    dict(k="output", label="output wallet name", v=(others[0] if others else ""),
                         mask=False, hint="must be a different wallet on this daemon")], cb)

    def do_coordinator():                             # choose the coinjoin coordinator (Config.json)
        path = S.get("cfgpath")
        if not path:
            flash("✗ Config.json not found - set \"CoordinatorUri\" in it yourself", 90); return
        if S.get("coord_menu") is None:               # live list, fetched once per session (best effort)
            S["coord_menu"] = []
            def w():
                try: S["coord_menu"] = fetch_live_coordinators()
                except Exception: pass
            threading.Thread(target=w, daemon=True).start()
        def menu():                                   # known examples first, then live extras
            out = list(KNOWN_COORDINATORS)
            for url, cnt in S.get("coord_menu") or []:
                if not any(url.rstrip("/") == u.rstrip("/") for _, u, _ in out):
                    out.append((coord_host(url), url, f"{cnt} public rounds/14d"))
            return out[:9]
        def lines():
            cur = S.get("coord_uri")
            ls = ["current: " + (cur if cur else "(none - coinjoin is disabled)"),
                  "",
                  "the coordinator batches your coinjoin rounds. it can see coinjoin",
                  "activity and sets the coordination fee - it can NEVER steal funds.",
                  "wasabi ships without one on purpose: pick whoever YOU trust.", ""]
            for i, (name, url, note) in enumerate(menu()):
                ls.append(f" {i+1}  {name:<15} {url:<40} {note}")
            if not S.get("coord_menu"):
                ls.append("    ... fetching live coordinators from liquisabi.com ...")
            return ls
        def cb(v):
            raw = v.get("uri", "").strip()
            m = menu()
            if raw.isdigit() and 1 <= int(raw) <= len(m):
                uri = m[int(raw)-1][1]
            else:
                uri = norm_coord_uri(raw)
                if uri is None:
                    flash("✗ give a number from the list, an http(s) URL, or 'none'", 90); return
            if not set_coordinator_in_config(path, uri):
                flash("✗ could not edit " + path, 90); return
            S["coord_uri"] = uri
            S["notice"] = dict(title="COORDINATOR SET", lines=[
                "", "  \"CoordinatorUri\" in " + path,
                "  is now:  " + (uri or "(none)"), "",
                "  ⟳ RESTART the Wasabi daemon for it to take effect", "",
                "  (a running daemon only reads its config at startup; a",
                "   WASABI_COORDINATORURI env var would override the file)", "",
                "  press any key"])
        open_modal("SET COINJOIN COORDINATOR",
                   [dict(k="uri", label="coordinator (number, URL, or 'none')", v="", mask=False,
                         hint="e.g. 1 · or https://your.coordinator/ (.onion ok via the daemon's Tor)")],
                   cb, lines=lines)

    def start_daemon_assist(exe):                     # secure the RPC, start, wait, onboard
        try:                                          # already reachable? never start a 2nd instance
            rpc.call("getstatus", timeout=3)          # nor change the working credentials
            S["err"] = None; S["kick"] = True
            flash("◆ daemon already running and reachable ✓ - nothing to do", 110); return
        except RpcError as e:
            if "401" in str(e):                       # up, but wants auth sabi doesn't have
                flash("◆ daemon is up but needs RPC auth - restart sabi with --user/--pass", 150); return
        except Exception:
            pass                                      # genuinely not answering -> start it
        cfg0 = wasabi_config()
        if cfg0 and cfg0.get("user") and cfg0.get("password"):
            _start_daemon(exe, None, None)            # config already carries credentials
            return
        def cb(v):                                    # sabi never starts an auth-less RPC
            u = (v.get("user") or "").strip() or "sabi"
            p = v.get("password") or ""
            if not p:
                flash("✗ an RPC password is required before starting the daemon", 100); return
            _start_daemon(exe, u, p)
        open_modal("SECURE THE DAEMON RPC",
                   [dict(k="user", label="RPC username", v="sabi", mask=False,
                         hint="default: sabi - edit if you like"),
                    dict(k="password", label="RPC password (required)", v="", mask=True,
                         hint="protects the local wallet RPC - pick a fresh one")],
                   cb, lines=lambda: [
                       "the daemon's JSON-RPC can spend from your wallets, so it must",
                       "not run without authentication. sabi starts wassabeed with",
                       "these credentials and stores them in wasabi's Config.json."])

    def _start_daemon(exe, u, p):                     # launch + wait for RPC + adopt config
        proc = launch_daemon(exe, u, p)
        if not proc:
            S["notice"] = dict(title="COULD NOT START WASSABEED", lines=[
                "", "  " + exe, ""] + ["  " + l for l in daemon_log_tail(6)] + [
                "", "  press any key"])
            return
        if u:
            rpc.user, rpc.password = u, p             # sabi authenticates the same way
        flash("◆ wassabeed starting - waiting for its RPC (tor bootstrap takes a bit) ...", 120)
        def w():
            deadline = time.monotonic() + RPC_WAIT_SECS
            while time.monotonic() < deadline:
                try:
                    rpc.call("getstatus", timeout=3); break
                except Exception:
                    pass
                if proc.poll() is not None:           # process died - show WHY (captured output)
                    tail = daemon_log_tail(12) or ["(the daemon produced no output)"]
                    S["notice"] = dict(title=f"WASSABEED EXITED (code {proc.returncode})", lines=[
                        "", "  it started but stopped right away. last output:", ""]
                        + ["  " + l[:72] for l in tail] + [
                        "", "  full log:  " + DAEMON_LOG, "", "  press any key"])
                    return
                time.sleep(2)
            else:
                S["flash"], S["flasht"] = (f"✗ no RPC answer after {RPC_WAIT_SECS}s - still bootstrapping? "
                                           "sabi keeps retrying. tail: " + DAEMON_LOG, 200)
                return
            S["err"] = None; S["kick"] = True
            cfg = wasabi_config()                     # a first run just created its Config.json
            if cfg:
                S["cfgpath"] = cfg["path"]; S["logpath"] = cfg["log"]
                if S.get("coord_uri") is None: S["coord_uri"] = cfg.get("coordinator")
                if u or not cfg["enabled"]:           # persist enabled + creds for MANUAL starts
                    apply_rpc_config(cfg["path"], u, p)
            if not (S.get("coord_uri") or "").strip():
                S["flash"], S["flasht"] = ("◆ daemon is up ✓ - next: press c on [4] coinjoin "
                                           "to pick a coordinator", 180)
            else:
                S["flash"], S["flasht"] = ("◆ daemon is up ✓ - create (n) or recover (v) "
                                           "a wallet on [1] dashboard", 180)
        threading.Thread(target=w, daemon=True).start()

    def do_rpc_connect():                             # point sabi at a RUNNING daemon (never starts one)
        def cb(v):
            url = (v.get("url") or "").strip()
            if url:
                if "://" not in url: url = "http://" + url
                rpc.url = url.rstrip("/")
            rpc.user = (v.get("user") or "").strip() or None
            rpc.password = v.get("password") or None
            S["kick"] = True
            flash("reconnecting to " + rpc.url + " ...", 80)
        open_modal("CONNECT TO A RUNNING DAEMON",
                   [dict(k="url", label="RPC address", v=getattr(rpc, "url", "http://127.0.0.1:37128"),
                         mask=False, hint="e.g. http://127.0.0.1:37128"),
                    dict(k="user", label="RPC username", v=getattr(rpc, "user", "") or "", mask=False,
                         hint="blank if the daemon has no auth"),
                    dict(k="password", label="RPC password", v="", mask=True, hint="")],
                   cb, lines=lambda: [
                       "use this when a daemon is already running with settings sabi",
                       "didn't auto-detect - e.g. RaspiBlitz sets the RPC user/pass and",
                       "port through WASABI_* env vars, not Config.json.", "",
                       "this only changes how sabi CONNECTS; it starts nothing and",
                       "edits no files. (or launch sabi with --rpc/--user/--pass.)"])

    def do_install_wasabi():                          # find existing wassabeed, else verified install
        running = not S.get("err")                    # sabi is already talking to a daemon
        if running:                                   # don't touch a working daemon
            def cb0(v):
                if v.get("ok", "").strip().lower() == "i": _install_wizard()
                else: flash("daemon already running - left as is")
            open_modal("WASABI DAEMON ALREADY RUNNING",
                       [dict(k="ok", label="type i to install the latest release, or esc", v="",
                             mask=False, hint="the running daemon is fine - nothing needs starting")],
                       cb0, lines=lambda: [
                           "sabi is connected to a running wasabi daemon already.",
                           "starting another one, or changing the RPC password now,",
                           "would only break this working connection.", "",
                           "press i only if you want to download + verify a newer release."])
            return
        exe0 = find_daemon(getattr(args, "daemon", None))
        if exe0:
            def cb0(v):
                a = v.get("ok", "").strip().lower()
                if a == "i": _install_wizard(); return
                if a != "y": flash("cancelled"); return
                start_daemon_assist(exe0)
            open_modal("WASABI DAEMON FOUND",
                       [dict(k="ok", label="start it? (y/n · i = install latest instead)", v="y",
                             mask=False, hint="no download needed")],
                       cb0, lines=lambda: [
                           "found an existing wassabeed on this machine:", "  " + exe0, "",
                           "sabi starts it with RPC enabled, waits for it to answer,",
                           "then guides you to a coordinator and your first wallet."])
            return
        _install_wizard()

    def _install_wizard():                            # guided, verified wasabi install (nostr)
        def stage3(exe, sha, ver):                    # everything verified - offer to start it
            def cb(v):
                if v.get("ok", "").strip().lower() != "y":
                    flash(f"not started - wassabeed is ready at {exe}", 110); return
                start_daemon_assist(exe)
            open_modal(f"WASABI {ver} VERIFIED ✓",
                       [dict(k="ok", label="start wassabeed now? (y/n)", v="y", mask=False,
                             hint="sabi starts it with RPC on and waits for it to answer")],
                       cb, lines=lambda: [
                           "signature   VALID - signed by the wasabi team key",
                           "sha256      " + sha[:32], "            " + sha[32:],
                           "wassabeed   " + exe, "",
                           "sabi remembered this path (~/.sabi-daemon) for future starts."])
        def stage2(info):
            ver = info["version"]; asset = pick_release_asset(ver)
            from urllib.parse import urlparse
            host = urlparse(str(resolve_asset_url(info["assets"], asset) or "")).netloc or "github.com"
            def cb(v):
                if v.get("ok", "").strip().lower() != "y": flash("cancelled - nothing downloaded"); return
                def fn():
                    try:
                        exe, sha = wasabi_install(info, progress=lambda s: S.update(busy=str(s)))
                    except Exception as e:            # full reason in a card, not the clipped flash
                        S["notice"] = dict(title="WASABI INSTALL FAILED", lines=[
                            "", "  " + str(e), "",
                            "  nothing was installed. common causes: no disk space in",
                            "  the temp dir, a broken download (retry), or no route to",
                            "  the release host over Tor.", "", "  press any key"])
                        return "install failed"
                    stage3(exe, sha, ver)
                    return "verified ✓"
                act("installing wasabi", fn)
            open_modal("DOWNLOAD WASABI " + ver + "?",
                       [dict(k="ok", label="download + verify now? (y/n)", v="", mask=False,
                             hint="~100 MB - progress shows in the header")],
                       cb, lines=lambda: [
                           f"release      Wasabi {ver}   (nostr event verified: schnorr sig by team npub)",
                           f"this machine {asset}",
                           f"host         {host}", "",
                           "before anything is extracted or run, sabi verifies:",
                           "  1. the wasabi team's secp256k1 signature over SHA256SUMS.asc",
                           "  2. the archive's sha256 against that signed list",
                           "a failed check deletes the download and stops."])
        def cb1(v):
            if v.get("ok", "").strip().lower() != "y": flash("cancelled"); return
            def fn():
                info = wasabi_release_info()
                stage2(info)
                return f"found Wasabi {info['version']} (event signature valid)"
            act("checking nostr relays for the latest release", fn)
        open_modal("INSTALL WASABI DAEMON",
                   [dict(k="ok", label="fetch latest release info? (y/n)", v="", mask=False,
                         hint="read-only step - you approve again before any download")],
                   cb1, lines=lambda: [
                       "sabi installs the official wasabi daemon (wassabeed) for you:", "",
                       " 1  fetch the release announcement over nostr",
                       "    relays: " + ", ".join(r.split("//")[1] for r in NOSTR_RELAYS),
                       " 2  verify the announcement (schnorr, wasabi team npub)",
                       " 3  download - verify team ECDSA signature + sha256 (pinned keys)",
                       " 4  extract - and only with your approval, start wassabeed", "",
                       "nothing runs before every check passes."])

    def do_import_trezor():                           # trezor coinjoin wallet via the daemon bridge
        def cb(v):
            name = (v.get("name") or "").strip()
            if not name: flash("✗ wallet needs a name"); return
            cj = v.get("coinjoin", "y").strip().lower() not in ("n", "no", "false", "0")
            def fn():
                # reading the SLIP-25 account asks for confirmation ON THE DEVICE (3 min window)
                r = rpc.call("importtrezorwallet", [name, cj], timeout=200) or {}
                S["kick"] = True
                S["notice"] = dict(title="TREZOR WALLET IMPORTED", lines=[
                    "", f"  wallet       {r.get('walletName', name)}",
                    f"  fingerprint  {r.get('masterKeyFingerprint', '?')}",
                    f"  coinjoin     {'enabled (SLIP-25 account)' if r.get('coinjoinEnabled') else 'off'}", "",
                    "  load it on [1] dashboard - coinjoin authorization happens",
                    "  on the device when you start mixing.", "", "  press any key"])
                return r.get("walletName", name)
            act("importing from trezor - CONFIRM ON THE DEVICE", fn)
        open_modal("IMPORT TREZOR WALLET",
                   [dict(k="name", label="wallet name", v="", mask=False, hint=""),
                    dict(k="coinjoin", label="enable coinjoin account? (y/n)", v="y", mask=False,
                         hint="adds the SLIP-25 coinjoin account - confirm on the device")],
                   cb, lines=lambda: [
                       "imports the connected trezor through the daemon's bridge.",
                       "the device will ask you to confirm exporting the accounts -",
                       "keep it connected and unlocked. nothing leaves the device",
                       "except public keys; coinjoins are signed on the trezor."])

    def do_enable_cj_account():                       # add the SLIP-25 account to a loaded trezor wallet
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        if not hw(): flash("this wallet is not a hardware wallet", 70); return
        def cb(v):
            if v.get("ok", "").strip().lower() != "y": flash("cancelled"); return
            def fn():
                r = rpc.call("enablecoinjoin", [], wallet=S["wallet"], timeout=200) or {}
                lines = ["", "  coinjoin account  " + str(r.get("coinjoinAccountKeyPath", "?")), ""]
                if r.get("restartRequired"):
                    lines += ["  ⟳ RESTART the daemon - a loaded wallet only reads its",
                              "  accounts at startup, then start coinjoin as usual.", ""]
                S["notice"] = dict(title="TREZOR COINJOIN ENABLED", lines=lines + ["  press any key"])
                return "enabled"
            act("enabling coinjoin account - CONFIRM ON THE DEVICE", fn)
        open_modal("ENABLE TREZOR COINJOIN",
                   [dict(k="ok", label="type y to continue (device will prompt)", v="", mask=False,
                         hint="3 minute window for the on-device confirmation")], cb)

    def do_create_wallet():
        def cb(v):
            name = v.get("name", "").strip(); pw = v.get("password", "")
            if not name: flash("✗ wallet needs a name"); return
            if pw != v.get("confirm", ""): flash("✗ passwords don't match", 80); return
            def fn():
                mn = rpc.call("createwallet", [name, pw])
                words = str(mn).split()
                rows = ["  " + " ".join(f"{w:<10}" for w in words[i:i+4]) for i in range(0, len(words), 4)]
                S["notice"] = dict(title=f"NEW WALLET '{name}'  ·  RECOVERY WORDS", lines=[
                    "", "  ⚠ WRITE THESE DOWN ON PAPER - THEY ARE SHOWN ONLY ONCE", ""] + rows + [
                    "", "  anyone with these words can take your coins", "",
                    "  press any key when they are safely written down"])
                return name
            act("wallet created", fn)
        open_modal("CREATE WALLET",
                   [dict(k="name", label="wallet name", v="", mask=False, hint=""),
                    dict(k="password", label="password", v="", mask=True, hint=""),
                    dict(k="confirm", label="confirm password", v="", mask=True, hint="")], cb)

    def do_recover_wallet():
        def cb(v):
            name = v.get("name", "").strip(); mn = " ".join(v.get("mnemonic", "").split())
            if not name: flash("✗ wallet needs a name"); return
            if len(mn.split()) not in (12, 15, 18, 21, 24): flash("✗ mnemonic must be 12/15/18/21/24 words", 90); return
            act("wallet recovered - loading filters may take a while",
                lambda: rpc.call("recoverwallet", [name, mn, v.get("password", "")]))
        open_modal("RECOVER WALLET",
                   [dict(k="name", label="wallet name", v="", mask=False, hint=""),
                    dict(k="mnemonic", label="recovery words", v="", mask=False,
                         hint="12-24 words, space separated (paste ok)"),
                    dict(k="password", label="password", v="", mask=True, hint="")], cb)

    def _hist_tx(unconfirmed_only=True):
        hist = S.get("history") or []
        if not hist: return None
        h_ = hist[sel[2] % len(hist)]
        try: conf = int(h_.get("height"))
        except (TypeError, ValueError): conf = None
        if unconfirmed_only and conf: flash("that tx is already confirmed"); return None
        return h_.get("tx")

    def do_speedup():
        if wo(): return
        tid = _hist_tx()
        if not tid: return
        def cb(v):
            def fn():
                hx = rpc.call("speeduptransaction", [tid, v.get("password", "")], wallet=S["wallet"])
                r = rpc.call("broadcast", [hx])       # 2.8.0: build returns hex; broadcast it
                nt = (r or {}).get("txid") if isinstance(r, dict) else str(r)
                return f"replacement {short(str(nt), 20)}"
            act("fee bumped (RBF/CPFP)", fn)
        open_modal("SPEED UP TRANSACTION  ·  " + short(tid, 24),
                   [dict(k="password", label="wallet password", v="", mask=True,
                         hint="builds + broadcasts a higher-fee replacement")], cb)

    def do_cancel_tx():
        if wo(): return
        tid = _hist_tx()
        if not tid: return
        def cb(v):
            def fn():
                hx = rpc.call("canceltransaction", [tid, v.get("password", "")], wallet=S["wallet"])
                r = rpc.call("broadcast", [hx])
                nt = (r or {}).get("txid") if isinstance(r, dict) else str(r)
                return f"cancel tx {short(str(nt), 20)}"
            act("transaction cancelled (double-spend to self)", fn)
        open_modal("CANCEL TRANSACTION  ·  " + short(tid, 24),
                   [dict(k="password", label="wallet password", v="", mask=True,
                         hint="spends the funds back to yourself with a higher fee")], cb)

    def rule_edit(idx=None):
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        d = (S.get("rules") or [])[idx] if idx is not None else {}
        others = [w for w in (S.get("wallets") or []) if w != S.get("wallet")]
        def cb(v):
            trig = v.get("when", "").strip().lower()
            if trig not in TRIGN: flash("✗ when: np / pr / tot / always", 80); return
            a = v.get("action", "").strip().lower()
            if a not in ACTN: flash("✗ action: start / single / sweep / stop", 80); return
            amt = 0
            if trig != "always":
                try: amt = parse_amount(v.get("amount", "0") or "0")
                except Exception: flash("✗ bad amount - BTC like 0.05", 80); return
            outw = v.get("output", "").strip()
            if a == "sweep" and (not outw or outw == S.get("wallet")):
                flash("✗ sweep needs a DIFFERENT output wallet (cold/read-only ok)", 90); return
            wtxt = v.get("window", "").strip(); t0 = t1 = ""
            if wtxt:
                wn = rule_window(wtxt)
                if not wn: flash("✗ window like 23:00-06:00 (or leave empty)", 80); return
                t0, t1 = wn
            rl = dict(on=True, trig=trig, amt=amt, act=a, outw=outw, t0=t0, t1=t1, last=0)
            if idx is not None: rl["on"] = d.get("on", True); S["rules"][idx] = rl
            else: S["rules"].append(rl)
            save_rules(S["wallet"], S["rules"])
            flash("rule saved: " + rule_text(rl))
        open_modal("AUTOMATION RULE" + (f"  ·  edit #{idx+1}" if idx is not None else ""),
                   [dict(k="when", label="when (np/pr/tot/always)", v=d.get("trig", "np"), mask=False,
                         hint="np = non-private balance · pr = private · tot = total"),
                    dict(k="amount", label="threshold (BTC)",
                         v=(f"{d['amt']/1e8:.8f}".rstrip("0").rstrip(".") if d.get("amt") else "0.01"),
                         mask=False, hint="rule fires while balance ≥ this"),
                    dict(k="action", label="action (start/single/sweep/stop)", v=d.get("act", "start"),
                         mask=False, hint="sweep = coinjoin everything to another wallet"),
                    dict(k="output", label="output wallet (for sweep)",
                         v=d.get("outw", others[0] if others else ""), mask=False,
                         hint="e.g. your cold / watch-only wallet"),
                    dict(k="window", label="time window (optional)",
                         v=(f"{d['t0']}-{d['t1']}" if d.get("t0") else ""), mask=False,
                         hint="e.g. 23:00-06:00 = only mix at night (cheap fees)")], cb)

    def do_arm():
        if wo(): return
        if S.get("armed"):
            S["armed"] = False; S["autopw"] = None; flash("automation disarmed"); return
        if not (S.get("rules") or []): flash("add a rule first (n)"); return
        def cb(v):
            S["autopw"] = v.get("password", ""); S["armed"] = True
            flash("◆ automation ARMED - rules run while sabi is open (10 min cooldown each)", 110)
        open_modal("ARM AUTOMATION", [dict(k="password", label="wallet password", v="", mask=True,
                   hint="kept in memory only - rules fire without asking again")], cb)

    def do_pay_in_cj():
        if wo(): return
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        def cb(v):
            addr = v.get("address", "").strip()
            if not is_btc_address(addr): flash("✗ that doesn't look like a bitcoin address", 70); return
            try: amt = parse_amount(v.get("amount", ""))
            except Exception: flash("✗ bad amount - use BTC like 0.001 (or 150000s)", 70); return
            act("payment queued inside coinjoin",
                lambda: rpc.call("payincoinjoin", [addr, amt], wallet=S["wallet"]))
        open_modal("PAY INSIDE A COINJOIN",
                   [dict(k="address", label="pay to address", v="", mask=False,
                         hint="receiver gets a coinjoin output - maximum privacy"),
                    dict(k="amount", label="amount (BTC)", v="", mask=False, hint="e.g. 0.001")], cb)

    def do_cancel_pay():
        pays = S.get("pays") or []
        if not pays: return
        p = pays[sel[3] % len(pays)]
        pid = p.get("id") or p.get("paymentId")
        if pid is None: flash("payment has no id to cancel"); return
        act("payment cancelled", lambda: rpc.call("cancelpaymentincoinjoin", [pid], wallet=S["wallet"]))

    def q_add(prefill=None):
        d = prefill or {}
        def cb(v):
            addr = v.get("address", "").strip()
            if not is_btc_address(addr): flash("✗ that doesn't look like a bitcoin address", 70); return
            try: amt = parse_amount(v.get("amount", ""))
            except Exception: flash("✗ bad amount - use BTC like 0.001 (or 150000s)", 70); return
            if amt <= 0: flash("✗ amount must be positive"); return
            item = dict(sendto=addr, amount=amt, label=v.get("label") or "sabi")
            if d.get("_edit") is not None: queue[d["_edit"]] = item
            else: queue.append(item)
            sug_lock.clear()
            flash(f"queued {cbtc(amt)} -> {short(addr, 20)}")
        open_modal("ADD PAYMENT" if d.get("_edit") is None else "EDIT PAYMENT",
                   [dict(k="address", label="pay to address", v=d.get("sendto", ""), mask=False, hint=""),
                    dict(k="amount", label="amount (BTC)", v=(f"{d['amount']/1e8:.8f}".rstrip("0").rstrip(".")
                         if d.get("amount") else ""), mask=False, hint="e.g. 0.001"),
                    dict(k="label", label="label", v=d.get("label", ""), mask=False,
                         hint="required by wasabi - who receives this?")], cb)

    def _fee_rate6():                                 # ~6-block sat/vB from the daemon's estimates
        f = S.get("fees")
        try:
            ks = sorted(int(k) for k in f.keys())
            k = min(ks, key=lambda k_: abs(k_-6))
            return max(0.1, float(f.get(str(k), f.get(k))))   # 0.1 sat/vB = core 30 minrelay floor
        except Exception:
            return 5.0

    def changeless_search(total):                     # find coin subsets that pay `total` EXACTLY
        coins = [c for c in S.get("coins") or []      # (minus fee) -> transaction with NO change output
                 if c.get("confirmed", True) and not c.get("coinJoinInProgress")]
        if not coins or total <= 0: return {}
        rate = _fee_rate6(); nout = max(1, len(queue))
        def fee(n): return int(rate * (11 + 68*n + 31*nout))
        vals = sorted(((c.get("amount", 0), i) for i, c in enumerate(coins)), reverse=True)[:40]
        UP = int(total*0.02) + 2_000                  # round-up tolerance:  +2%
        DN = int(total*0.05) + 2_000                  # round-down tolerance: -5%
        best = {"up": None, "dn": None}; budget = [80_000]
        def dfs(idx, cur, picked, rem):
            if budget[0] <= 0: return
            budget[0] -= 1
            n = len(picked)
            if n:
                d = cur - fee(n) - total              # + => receiver gets a bit more, - => a bit less
                if 0 <= d <= UP and (best["up"] is None or d < best["up"][0]):
                    best["up"] = (d, tuple(picked), cur)
                if -DN <= d < 0 and (best["dn"] is None or d > best["dn"][0]):
                    best["dn"] = (d, tuple(picked), cur)
            if idx >= len(vals) or n >= 25: return
            if cur + rem < total - DN: return         # even taking everything left can't reach
            if n and cur - fee(n) - total > UP: return  # already past the window; adding only grows it
            v, ci = vals[idx]
            dfs(idx+1, cur+v, picked+[ci], rem-v)
            dfs(idx+1, cur, picked, rem-v)
        dfs(0, 0, [], sum(v for v, _ in vals))
        out = {}
        for k, hit in best.items():
            if hit:
                out[k] = dict(delta=hit[0], coins=[coins[i] for i in hit[1]], subset=hit[2])
        return out

    def get_sug(total):                               # cached per (total, coins, rate)
        key = (total, len(queue), len(S.get("coins") or []),
               sum(c.get("amount", 0) for c in S.get("coins") or []), int(_fee_rate6()*10))
        cache = S.get("_sug")
        if cache and cache[0] == key: return cache[1]
        res = changeless_search(total)
        S["_sug"] = (key, res)
        return res

    def apply_sug(which):
        if not queue: return
        total = sum(p["amount"] for p in queue)
        sug = (get_sug(total) or {}).get(which)
        if not sug: flash("no exact-match coin combo in that direction"); return
        queue[0]["amount"] += sug["delta"]
        sug_lock.clear()
        sug_lock.update(coins=sug["coins"], subset=sug["subset"],
                        total=sum(p["amount"] for p in queue))
        flash(f"◆ no-change match locked  ({'+' if sug['delta'] >= 0 else ''}{sug['delta']:,} sats) "
              "- this spend produces NO change output", 90)

    def do_send():
        if wo(): return
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        if not queue: flash("queue a payment first (n)"); return
        total = sum(p["amount"] for p in queue)
        T = tgt_of(S)
        # applied no-change match? spend EXACTLY that subset; daemon fee comes out via subtractFee
        have_now = {(c.get("txid"), c.get("index")) for c in S.get("coins") or []}
        locked = (sug_lock.get("coins") and sug_lock.get("total") == total
                  and all((c.get("txid"), c.get("index")) in have_now for c in sug_lock["coins"]))
        if sug_lock and not locked: sug_lock.clear()
        if locked:
            coins = sug_lock["coins"]; have = sug_lock["subset"]
            toxic = (any(anon_of(c) >= T for c in coins) and any(anon_of(c) <= 1 for c in coins))
        else:
            need = total + max(5_000, int(total*0.003))
            coins, have, toxic = pick_coins(S.get("coins") or [], need, T)
            if have < need and not sub_fee:
                flash(f"✗ not enough confirmed funds: need ~{cbtc(need)}, have {cbtc(have)}", 90); return
        warn = ("⚠ TOXIC MERGE: this spend joins private + non-private coins and undoes their mix"
                if toxic else None)
        def cb(v):
            try: ft = max(1, min(1008, int(v.get("feeTarget") or "6")))
            except Exception: ft = 6
            pays = [dict(p) for p in queue]
            if locked:                                # consume the subset to zero -> no change output
                others = sum(p["amount"] for p in pays[1:])
                pays[0]["amount"] = sug_lock["subset"] - others
                pays[0]["subtractFee"] = True
            elif sub_fee and pays:
                pays[0]["subtractFee"] = True
            params = dict(payments=pays,
                          coins=[dict(transactionid=c.get("txid"), index=c.get("index")) for c in coins],
                          feeTarget=ft, password=v.get("password", ""))
            def fn():
                r = rpc.call("send", params, wallet=S["wallet"])
                txid = (r or {}).get("txid") if isinstance(r, dict) else str(r)
                if isinstance(r, dict) and r.get("tx"):   # keep the raw hex: 'r' copies it later
                    S["last_send"] = dict(txid=str(txid), hex=str(r["tx"]))
                queue.clear(); sug_lock.clear(); clip_copy(txid or "")
                return f"txid {short(txid or '?', 24)} (copied · r = raw hex)"
            act(f"sent {len(pays)} payment(s), {cbtc(total)}", fn)
        nc, np_ = len(coins), len(queue)
        def fee_info(vals):                           # live estimate as the fee target is typed
            try: ft = max(1, min(1008, int(vals.get("feeTarget") or "6")))
            except Exception: return ""
            f = S.get("fees")
            if not isinstance(f, dict) or not f: return "fee rates unavailable yet"
            try: ks = sorted(int(k) for k in f.keys())
            except Exception: return ""
            k = min(ks, key=lambda k_: abs(k_-ft))
            rate = float(f.get(str(k), f.get(k, 0)))
            vs = 58 + 68*nc + 31*(np_+1)              # P2WPKH heuristic: ins + outs + change
            fee = int(rate*vs)
            return f"fee ≈ {rate:.1f} sat/vB × ~{vs} vB = {fee:,} sats  {usd(fee)}   ({k}-block rate)"
        fields = [dict(k="feeTarget", label="fee target (blocks)", v="6", mask=False,
                       hint=fees_hint(S)),
                  dict(k="password", label="wallet password", v="", mask=True, hint="")]
        title = f"CONFIRM SEND · {len(queue)} payment(s) · {cbtc(total)} {usd(total)} · {len(coins)} coins"
        if locked: title += " · ◆ NO CHANGE"
        open_modal(title, fields, cb, warn=warn, info=fee_info)

    def do_copy_rawtx():                              # history: raw hex of the selected tx
        hist = S.get("history") or []
        if not hist: return
        tid = str(hist[sel[2] % len(hist)].get("tx") or "")
        ls = S.get("last_send") or {}
        if tid and tid == ls.get("txid"):             # sent this session: hex already in hand
            ok = clip_copy(ls["hex"])
            flash("raw tx hex copied ✓ (from the send result)" if ok else "clipboard unavailable"); return
        if not btcrpc:
            flash("✗ no BitcoinRpcEndPoint in wasabi's Config.json - can't fetch raw hex", 90); return
        def fn():
            hx = btcrpc.call("getrawtransaction", [tid])   # mempool, or confirmed via txindex
            ok = clip_copy(str(hx))
            if not ok: raise RpcError("clipboard unavailable")
            return f"{len(str(hx))//2} bytes (from your node)"
        act("raw tx hex copied", fn)

    def do_copy_last_send():                          # send: raw hex of the last tx sent this session
        ls = S.get("last_send")
        if not ls: flash("nothing sent this session yet - hex is kept from each send", 70); return
        ok = clip_copy(ls["hex"])
        flash(f"raw hex of {short(ls['txid'], 20)} copied ✓" if ok else "clipboard unavailable")

    def do_import():
        def cb(v):
            items = parse_batch(v.get("lines", ""))
            if not items: flash("✗ nothing usable - lines like: bc1q... 0.01 rent", 90); return
            queue.extend(items); sug_lock.clear()
            flash(f"imported {len(items)} payment(s), {cbtc(sum(p['amount'] for p in items))} total")
        open_modal("IMPORT PAYMENTS  ·  paste a list",
                   [dict(k="lines", label="address amount [label]  (one per line)", v="", mask=False,
                         hint="paste multiple lines at once - commas or spaces both fine")], cb)

    def do_list_keys():
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        def fn():
            keys = rpc.call("listkeys", [], wallet=S["wallet"]) or []
            ext = [k for k in keys if not k.get("internal")]
            used = lambda k: str(k.get("keyState", "")) in ("2", "Used", "used")
            lines = [f"  {'ADDRESS':<44} {'STATE':<6} LABEL", ""]
            for k in sorted(ext, key=lambda k: (not used(k),))[:300]:
                lines.append(f"  {maskaddr(k.get('address', '?')):<44} {'used' if used(k) else 'clean':<6} "
                             + str(k.get("label", "") or ""))
            S["pager"] = dict(title=f"ADDRESS BOOK · {S['wallet']} · {len(ext)} receive keys",
                              lines=lines, off=0)
            return f"{len(ext)} addresses"
        act("address book", fn)

    def _json_lines(obj, ind=0, out=None):            # pretty-print query result (nested lists/dicts)
        out = [] if out is None else out
        pad = "  " * ind
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)): out.append(f"{pad}{k}:"); _json_lines(v, ind+1, out)
                else: out.append(f"{pad}{k}: {v}")
        elif isinstance(obj, list):
            for v in obj:
                if isinstance(v, (dict, list)): _json_lines(v, ind, out); out.append("")
                else: out.append(f"{pad}• {v}")
        else:
            out.append(f"{pad}{obj}")
        return out

    def do_scheme_run(script):
        if not script.strip(): return
        S["sc_running"] = True; S["sc_out"] = ["running ..."]
        def w():
            try:
                r = rpc.call("query", [script], timeout=30)   # 'query' is not wallet-scoped
                if isinstance(r, str) and "scripting" in r.lower() and "not enabled" in r.lower():
                    S["sc_out"] = ["⚠ the daemon's experimental 'scripting' feature is OFF.",
                                   "", "press x to enable it in Config.json (asks first), then",
                                   "restart the daemon."]
                    S["sc_needs_enable"] = True
                else:
                    S["sc_out"] = _json_lines(r) or ["(empty result)"]
                    S["sc_hist"] = ([script] + [h for h in S.get("sc_hist", []) if h != script])[:20]
            except Exception as e:
                S["sc_out"] = [f"✗ {e}"]
            finally:
                S["sc_running"] = False
        threading.Thread(target=w, daemon=True).start()

    # native cross-wallet reports: same insights as the removed coin-level Scheme scripts, but
    # computed from the ordinary per-wallet RPC (daemon sums coins in C#) - exact and crash-safe.
    NATIVE_REPORTS = [
        ("total", "total across all wallets", "adds up every loaded wallet's native balance"),
        ("balances", "balance per wallet", "per-wallet balance from getwalletinfo (native sum)"),
        ("privacy", "privacy % per wallet", "private share of each wallet vs its anon target"),
        ("nonpriv", "non-private per wallet", "unmixed sats still needing coinjoin, per wallet"),
        ("counts", "coin count per wallet", "UTXO count per wallet (consolidation exposure)"),
        ("largest", "largest coin per wallet", "the single biggest UTXO in each wallet"),
        ("toxic", "toxic coins (anon <= 1)", "non-private coins across ALL wallets, largest first"),
        ("cjs", "coinjoins per wallet", "coinjoin transactions per wallet, from history"),
        ("labels", "label audit", "largest coins with their labels + addresses"),
    ]

    def report_lines(key):
        names = S.get("wallets") or []
        rows = []; skipped = []
        def winfo(n): return rpc.call("getwalletinfo", [], wallet=n) or {}
        def coins(n): return rpc.call("listunspentcoins", [], wallet=n) or []
        def hist(n):
            h = rpc.call("gethistory", [], wallet=n) or []
            return h.get("transactions") or [] if isinstance(h, dict) else h
        for name in names:
            try:
                wi = winfo(name)
                if wi.get("loaded") is False: skipped.append(name); continue
                T = int(wi.get("anonScoreTarget") or 5)
                if key == "total" or key == "balances":
                    b = wi.get("balance")
                    if b is None:
                        b = sum(c.get("amount", 0) for c in coins(name))
                    rows.append((name, int(b)))
                elif key in ("privacy", "nonpriv", "counts", "largest", "toxic", "labels"):
                    cs = coins(name)
                    if key == "privacy":
                        tot = sum(c.get("amount", 0) for c in cs)
                        pr = sum(c.get("amount", 0) for c in cs if anon_of(c) >= T)
                        rows.append((name, (100.0*pr/tot) if tot else 0.0, tot))
                    elif key == "nonpriv":
                        rows.append((name, sum(c.get("amount", 0) for c in cs if anon_of(c) <= 1)))
                    elif key == "counts":
                        rows.append((name, len(cs)))
                    elif key == "largest":
                        rows.append((name, max((c.get("amount", 0) for c in cs), default=0)))
                    elif key == "toxic":
                        rows += [(name, c.get("amount", 0), str(c.get("label", "") or "-"))
                                 for c in cs if anon_of(c) <= 1]
                    elif key == "labels":
                        rows += [(name, c.get("amount", 0), str(c.get("label", "") or "-"),
                                  c.get("address", "")) for c in cs]
                elif key == "cjs":
                    rows.append((name, sum(1 for h_ in hist(name) if is_cj_row(h_))))
            except Exception as e:
                skipped.append(f"{name} ({short(str(e), 30)})")
        out = []
        if key == "total":
            tot = sum(v for _, v in rows)
            out = [f"TOTAL   {btc(tot)}   {usd(tot)}", f"across {len(rows)} loaded wallet(s)"]
        elif key == "balances":
            out = [f"{n:<20} {btc(v):>20}   {usd(v)}" for n, v in rows]
        elif key == "privacy":
            out = [f"{n:<20} {p:5.1f}% private   of {btc(t)}" for n, p, t in rows]
        elif key == "nonpriv":
            out = [f"{n:<20} {btc(v):>20}   {usd(v)}" for n, v in rows]
            out.append(""); out.append(f"to mix in total: {btc(sum(v for _, v in rows))}")
        elif key == "counts":
            out = [f"{n:<20} {v:>4} coins" for n, v in rows]
        elif key == "largest":
            out = [f"{n:<20} {btc(v):>20}" for n, v in rows]
        elif key == "toxic":
            rows.sort(key=lambda r: -r[1])
            out = [f"{n:<18} {cbtc(v):>14}   {short(lb, 18)}" for n, v, lb in rows[:24]]
            if not rows: out = ["no non-private coins anywhere ◆ fully mixed"]
        elif key == "cjs":
            out = [f"{n:<20} {v:>4} coinjoins" for n, v in rows]
        elif key == "labels":
            rows.sort(key=lambda r: -r[1])
            out = [f"{short(lb, 16):<17} {cbtc(v):>14}  {short(a, 22)}  {n}"
                   for n, v, lb, a in rows[:24]]
        if skipped:
            out += ["", "not loaded (press l to load all): " + ", ".join(skipped[:6])]
        return out or ["(no data yet)"]

    def do_native_report(key):
        S["sc_running"] = True; S["sc_out"] = ["running (native RPC, crash-safe) ..."]
        def w():
            try: S["sc_out"] = report_lines(key)
            except Exception as e: S["sc_out"] = [f"✗ {e}"]
            finally: S["sc_running"] = False
        threading.Thread(target=w, daemon=True).start()

    SCHEME_ITEMS = ([("rpc", t, d, k) for k, t, d in NATIVE_REPORTS]
                    + [("scm", t, d, c) for t, d, c in SCHEME_SNIPPETS])

    def do_enable_scripting():
        path = S.get("cfgpath")
        if not path: flash("✗ Config.json not found - enable 'scripting' in ExperimentalFeatures yourself", 90); return
        def cb(_v):
            if enable_scripting_in_config(path):
                S["sc_needs_enable"] = False
                S["notice"] = dict(title="SCRIPTING ENABLED", lines=[
                    "", "  added \"scripting\" to ExperimentalFeatures in:", "  " + path, "",
                    "  ⟳ RESTART the Wasabi daemon for it to take effect", "",
                    "  (a running daemon only reads its config at startup)", "",
                    "  press any key"])
            else:
                flash("✗ could not edit the config", 80)
        open_modal("ENABLE EXPERIMENTAL SCRIPTING?",
                   [dict(k="ok", label="type y to confirm editing Config.json", v="", mask=False,
                         hint="adds ExperimentalFeatures: [scripting] - needs a daemon restart")],
                   lambda v: cb(v) if v.get("ok", "").strip().lower() == "y" else flash("cancelled"))

    def do_scheme_edit(prefill):
        def cb(v):
            code = v.get("code", "")
            S["sc_custom"] = code; do_scheme_run(code)
        open_modal("EDIT / PASTE SCHEME SCRIPT   ·   ⚠ coin/tx loops can crash the daemon",
                   [dict(k="code", label="scheme expression (paste multi-line ok)", v=prefill, mask=False,
                         hint="prefer wallet-level ops; iterating coins can overflow the interpreter")], cb)

    def fees_hint(S):
        f = S.get("fees")
        if isinstance(f, dict) and f:
            try:
                items = sorted(((int(k), v) for k, v in f.items()))[:4]
                return "current: " + "  ".join(f"{k}blk={float(v):.1f}s/vB" for k, v in items)
            except Exception: pass
        return "2 = fast · 6 = normal · 144 = eco"

    # ---------- render helpers -------------------------------------------------------
    def lists_for_tab(t):
        if t == 0: return S.get("wallets") or []
        if t == 1: return S.get("coins") or []
        if t == 2: return S.get("history") or []
        if t == 3: return S.get("pays") or []
        if t == 4: return queue
        if t == 5: return S.get("rules") or []
        if t == 6: return SCHEME_ITEMS
        return []

    def draw_header(ch, col, f):
        pulse = qpulse(f, 0.09)                       # slow breathing (~3.3 s)
        on = S.get("cj_on")
        if TH < 24 or TW < 70:                        # thin terminal: 2-line header, no logo
            put(ch, col, 0, 2, "S A B I", WHITE)
            wname = S.get("wallet")
            if wname:
                pr, se, np_ = balances(S)
                put(ch, col, 0, 12, f"{wname}  {btc(pr+se+np_)}", lerp(BRAND, WHITE, .3))
            if on: rput(ch, col, 0, min(W, TW)-2, "◆ CJ", clamp8(lerp(GREEN, WHITE, .3*pulse)))
            if S.get("err"):
                t = "● offline - i install/find daemon"
                put(ch, col, 1, 2, t, WARN)
                regions.append((1, 2, 2+len(t)-1, ("ACT", do_install_wasabi)))
            else:
                st = S.get("status") or {}
                seg = f"● #{st.get('bestBlockchainHeight', '?')} · {len(st.get('peers') or [])} peers"
                cu = S.get("coord_uri")
                if cu: seg += " · " + coord_host(cu)
                elif S.get("no_coord") or cu == "": seg += " · no coordinator (c)"
                put(ch, col, 1, 2, seg, GREY)
            if S.get("busy"):
                sp = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.time()*10) % 10]
                rput(ch, col, 1, min(W, TW)-2, sp + " " + str(S["busy"])[:24], AMBER)
            elif S.get("hw_auth"):
                rput(ch, col, 1, min(W, TW)-2, "◆ CONFIRM ON TREZOR", clamp8(lerp(AMBER, WHITE, .5*pulse)))
            return 3
        lcol = lerp(GREEN, GLOW, pulse) if on else lerp(BRAND, GREY, .45)
        rows = 7 if H >= 34 else 5
        turn, bob, pitch, snack, kind = logo_anim(f)  # the corner W has moods, and talks
        cols = draw_logo(ch, col, 1, 2, rows, lcol, dimf=0.5 if on else 0.3,
                         turn=turn, bob=bob, pitch=pitch, depth=2)
        if snack:                                     # a miniature nigiri at the mouth, about to be eaten
            mini = MINI_SUSHI["salmon" if (f // 660 // 3) % 2 == 0 else "wagyu"]
            _draw_piece(ch, col, 2 + cols, max(0, 1 + rows//2 - 1 + bob), mini)
        x0 = cols + 6
        if kind >= 0:                                 # a katakana speech blip above S A B I
            say = "｢" + logo_says(kind, S) + "｣"
            put(ch, col, 0, x0, say, clamp8(lerp(GLOW if on else BRAND, WHITE, .3)))
        put(ch, col, 1, x0, "S A B I", WHITE)
        put(ch, col, 1, x0+9, "· wasabi daemon terminal", GREY)
        st = S.get("status") or {}
        if S.get("err"):
            e = str(S["err"]).lower()
            reachable = ("401" in e or "auth" in e)   # answering, just rejecting our credentials
            if reachable:
                put(ch, col, 3, x0, "● daemon is running but rejected the RPC login", AMBER)
                cta = "p  set the RPC address / user / password - or click here"
            else:
                msg = ("daemon offline - wasabi is not running (or its RPC is disabled)"
                       if ("refused" in e or "10061" in e or "111" in e or "urlopen" in e)
                       else "daemon unreachable: " + short(S["err"], 44))
                put(ch, col, 3, x0, "● " + msg, WARN)
                cta = "i  find/install daemon · p  set RPC address/login · or click"
            put(ch, col, 4, x0, cta, clamp8(lerp(GREEN, WHITE, .2)))
            regions.append((4, x0, x0+len(cta)-1, ("ACT", do_rpc_connect if reachable else do_install_wasabi)))
        else:
            def dot_(okk): return ("●", GREEN if okk else WARN)
            g, c_ = dot_(str(st.get("torStatus", "")).lower().startswith(("running", "turned off")))
            put(ch, col, 3, x0, g, c_); put(ch, col, 3, x0+2, "tor", GREY)
            npeers = len(st.get("peers") or []); x = x0 + 7
            if "backendStatus" in st:                 # pre-2.8 daemons: central backend
                g, c_ = dot_(str(st.get("backendStatus", "")).lower().startswith("connected"))
                put(ch, col, 3, x, g, c_); put(ch, col, 3, x+2, "backend", GREY); x += 11
            else:                                     # 2.8+: P2P block-filter sync, no backend
                g, c_ = dot_(npeers > 0)
                put(ch, col, 3, x, g, c_); put(ch, col, 3, x+2, "p2p", GREY); x += 7
            put(ch, col, 3, x, f"height {st.get('bestBlockchainHeight', '?')}", lerp(BRAND, WHITE, .2))
            x += 8 + len(str(st.get("bestBlockchainHeight", "?"))) + 3
            put(ch, col, 3, x, f"{npeers} peers", GREEN if npeers else WARN); x += 9
            try: fleft = int(st.get("filtersLeft") or 0)
            except Exception: fleft = 0
            if fleft > 0:                             # daemon still downloading block filters (p2p)
                put(ch, col, 3, x, f"⟳ filters: {fleft:,} left", AMBER)
            else:
                put(ch, col, 3, x, "filters ✓", lerp(GREEN, GREY, .35))
            net = st.get("network", "")
            if net and net != "Main": rput(ch, col, 3, W-2, str(net).upper(), AMBER)
            cu = S.get("coord_uri")
            if S.get("no_coord") or cu == "":
                txt = "no coordinator - press c on [4] coinjoin (or click here)"
                put(ch, col, 4, x0, txt, WARN)
                regions.append((4, x0, x0+len(txt)-1, ("ACT", do_coordinator)))
            elif cu:
                txt = "coordinator " + coord_host(cu)
                put(ch, col, 4, x0, txt,
                    lerp(GREEN, GREY, .45) if S.get("cj_on") else lerp(BRAND, GREY, .35))
                regions.append((4, x0, x0+len(txt)-1, ("ACT", do_coordinator)))
        wname = S.get("wallet")
        pr, se, np_ = balances(S)
        tot = pr + se + np_
        if wname:
            put(ch, col, 5, x0, wname, lerp(BRAND, WHITE, .4))
            if S.get("wloading"):
                sp = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.time()*8) % 10]
                h_ = S.get("sync_h"); h0 = S.get("sync_h0")
                try: tip = int((S.get("status") or {}).get("bestBlockchainHeight") or 0)
                except Exception: tip = 0
                fresh = time.monotonic() - S.get("sync_t", 0) < 30
                if h_ and h0 and tip > h0 and fresh:   # live scan position from the daemon's local log
                    pct = max(0.0, min(100.0, 100.0*(h_-h0)/(tip-h0)))
                    txt = f"{sp} scanning block {h_:,} / {tip:,}  ·  ~{pct:.0f}%  ·  {S.get('sync_rate', 0):.0f} blk/min"
                else:
                    txt = sp + " synchronizing (block filters) ..."
                put(ch, col, 5, x0+len(wname)+2, txt, AMBER)
            else:
                put(ch, col, 5, x0+len(wname)+2, btc(tot), WHITE)
                fx = usd(tot)
                if fx: put(ch, col, 5, x0+len(wname)+2+len(btc(tot))+2, fx, GREY)
            wi_ = S.get("winfo") or {}
            if wi_.get("isHardwareWallet"):
                rput(ch, col, 5, W-2, "◇ HARDWARE · signs on device", lerp(GREEN, GREY, .3))
            elif wi_.get("isWatchOnly"):
                rput(ch, col, 5, W-2, "◇ WATCH-ONLY", AMBER)
        else:
            put(ch, col, 5, x0, "no wallet loaded - pick one on [1] dashboard", ORANGE)
        if S.get("busy"):                             # action in flight: spinner, never a frozen UI
            sp = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.time()*10) % 10]
            put(ch, col, 2, x0, f"{sp} {S['busy']} ...", AMBER)
        elif S.get("hw_auth"):
            put(ch, col, 2, x0, "◆ CONFIRM COINJOIN ON YOUR TREZOR - it is waiting for you",
                clamp8(lerp(AMBER, WHITE, .5*pulse)))
        nload = len(S.get("loaded") or ())
        if nload > 1: put(ch, col, 1, x0+34, f"· {nload} wallets loaded", lerp(GREEN, GREY, .3))
        if DISCREET["on"]:
            rput(ch, col, 0, W-2, "●  privacy mode  ·  '.' to reveal", clamp8(lerp(AMBER, WHITE, .2)))
        if on:
            tagx = W-24
            put(ch, col, 1, tagx, "◆ COINJOIN ACTIVE", clamp8(lerp(GREEN, WHITE, .3*pulse)))
            if S.get("single"): put(ch, col, 2, tagx, "single round mode", GREY)
            elif S.get("armed"): put(ch, col, 2, tagx, "rules armed", GREY)
        elif S.get("armed"):
            put(ch, col, 1, W-24, "◆ automation armed", AMBER)
        return rows + 3

    def draw_tabs(ch, col, y):
        for j in range(1, W-1): put(ch, col, y-1, j, "─", lerp(BG, BRAND, .22))   # header divider
        if TW < 96:                                   # thin: only the active tab + ◂ ▸ pagers
            put(ch, col, y, 2, "◂", lerp(BRAND, WHITE, .2))
            regions.append((y, 2, 3, ("TAB", (tab-1) % 7)))
            lab = f"{tab+1} {TABS[tab]}"
            put(ch, col, y, 5, lab, WHITE)
            xr = 5 + len(lab) + 2
            put(ch, col, y, xr, "▸", lerp(BRAND, WHITE, .2))
            regions.append((y, xr, xr+1, ("TAB", (tab+1) % 7)))
            for j in range(5, 5+len(lab)): put(ch, col, y+1, j, "▔", GREEN)
            rput(ch, col, y, min(W, TW)-2, f"{tab+1}/7 · ◂▸ tabs", GREY)
            return y + 2
        x = 2
        counts = {1: len(S.get("coins") or []), 2: len(S.get("history") or []),
                  3: len(S.get("pays") or []), 4: len(queue), 5: len(S.get("rules") or []),
                  6: len(SCHEME_ITEMS)}
        for i, name in enumerate(TABS):
            on = (i == tab); cnt = counts.get(i)
            lab = f" {i+1} {name} "
            for j, k in enumerate(lab):
                put(ch, col, y, x+j, k, WHITE if on else GREY)
            xtra = 0
            if cnt:                                   # live badge: items behind each tab
                bs = str(cnt)
                put(ch, col, y, x+len(lab), bs, lerp(GREEN, WHITE, .2) if on else lerp(BRAND, GREY, .25))
                xtra = len(bs) + 1
            if i == 3 and S.get("cj_on"):             # coinjoin running marker on its tab
                put(ch, col, y, x+len(lab)+xtra, "◆", GREEN); xtra += 2
            if on:
                for j in range(len(lab)+xtra): put(ch, col, y+1, x+j, "▔", GREEN)
            regions.append((y, x, x+len(lab)+xtra-1, ("TAB", i)))
            x += len(lab) + xtra + 1
        return y + 2

    def bar(ch, col, y, x, w, fill, colr):
        n = max(0, min(w, round(w*fill)))
        for j in range(w): put(ch, col, y, x+j, "·", lerp(BG, colr, .25))
        for j in range(n): put(ch, col, y, x+j, "█", lerp(colr, WHITE, .15*(j/max(w-1, 1))))

    def draw_dashboard(ch, col, y0):
        wl = S.get("wallets") or []
        loaded = S.get("loaded") or set()
        put(ch, col, y0, 4, "WALLETS ON THIS DAEMON", GREY)   # keys live in GETTING STARTED + hint bar
        if not wl:
            put(ch, col, y0+2, 4, "none found yet " + ("(daemon offline?)" if S.get("err") else "..."), GREY)
        for i, name in enumerate(wl[:H-y0-6]):
            y = y0+2+i; on = (i == sel[0] % max(1, len(wl)))
            cur = (name == S.get("wallet")); ld = (name in loaded)
            dot = "● " if cur else ("○ " if ld else "  ")
            put(ch, col, y, 4, ("▸ " if on else "  ") + dot + name,
                WHITE if on else (lerp(GREEN, WHITE, .2) if ld else lerp(BRAND, WHITE, .15)))
            if cur: put(ch, col, y, 6+len(name)+4, "· active", GREEN)
            elif ld: put(ch, col, y, 6+len(name)+4, "· loaded", lerp(GREEN, GREY, .35))
            regions.append((y, 4, 42, ("ROW", 0, i)))
        put(ch, col, y0, 50, "GETTING STARTED", GREY)
        for i, l in enumerate(["space / l    load one / load ALL wallets",
                               "n / v        create / recover a wallet",
                               "i            find or install the wasabi daemon",
                               "2 wallet     balances & coins",
                               "4 coinjoin   start mixing (the logo breathes green)",
                               "6 auto       rules: hot -> cold via coinjoin",
                               "7 scheme     powerful cross-wallet Scheme scripts",
                               "?            all keys"]):
            put(ch, col, y0+2+i, 50, l, lerp(BRAND, WHITE, .3))

    def draw_wallet(ch, col, y0):
        if not S.get("wallet"):
            put(ch, col, y0+1, 4, "no wallet loaded - go to [1] dashboard and press enter", ORANGE); return
        if S.get("wloading"):
            put(ch, col, y0+1, 4, "⟳ wallet is synchronizing - matching block filters against your keys and", AMBER)
            put(ch, col, y0+2, 4, "  downloading matched blocks from P2P peers.", AMBER)
            h_ = S.get("sync_h"); h0 = S.get("sync_h0")
            try: tip = int((S.get("status") or {}).get("bestBlockchainHeight") or 0)
            except Exception: tip = 0
            if h_ and h0 and tip > h0 and time.monotonic() - S.get("sync_t", 0) < 30:
                pct = max(0.0, min(1.0, (h_-h0)/(tip-h0)))
                bar(ch, col, y0+4, 4, 60, pct, AMBER)
                put(ch, col, y0+4, 66, f"~{100*pct:.0f}%   block {h_:,} of {tip:,}", AMBER)
                put(ch, col, y0+5, 4, f"{S.get('sync_rate', 0):.0f} blocks/min - only blocks that MATCH "
                    "your wallet's filters are fetched", GREY)
            else:
                put(ch, col, y0+4, 4, "old / busy wallets take minutes; coins and history appear when done.", GREY)
            return
        pr, se, np_ = balances(S); tot = max(1, pr+se+np_)
        T = tgt_of(S)
        put(ch, col, y0, 4, f"BALANCE BREAKDOWN  (anon target {T})", GREY)
        rows = [("private", pr, GREEN), ("semi-private", se, AMBER), ("non-private", np_, ORANGE)]
        for i, (lab, v, c_) in enumerate(rows):
            y = y0+1+i
            put(ch, col, y, 4, f"{lab:<13}", lerp(c_, WHITE, .25))
            put(ch, col, y, 18, f"{btc(v):>18}", WHITE if v else GREY)
            bar(ch, col, y, 40, 24, v/tot, c_)
            put(ch, col, y, 66, f"{100*v/tot:3.0f}%", GREY)
            fx = usd(v)
            if fx: put(ch, col, y, 72, fx, GREY)
        coins = coins_view()
        yb = y0 + 4
        pend_v = sum(c.get("amount", 0) for c in coins if not c.get("confirmed", True))
        pend_n = sum(1 for c in coins if not c.get("confirmed", True))
        if pend_v:                                    # unconfirmed funds, called out instead of hidden
            put(ch, col, yb, 4, f"incoming (unconfirmed)   +{btc(pend_v)}  ({pend_n} coin"
                f"{'s' if pend_n != 1 else ''})  {usd(pend_v)}", ORANGE)
            yb += 1
        pct = 100*pr/tot                              # wasabi-style privacy progress
        pcol = GREEN if pct >= 99 else (lerp(AMBER, GREEN, pct/100))
        put(ch, col, yb, 4, "PRIVACY", GREY)
        bar(ch, col, yb, 13, 51, pct/100, pcol)
        put(ch, col, yb, 66, ("fully private ◆" if pct >= 99.5 else f"{pct:.0f}% private"), clamp8(pcol))
        y1 = yb + 2
        skey, srev = S.get("coin_sort") or ("confs", False)
        put(ch, col, y1, 4, f"COINS ({len(coins)})", GREY)
        x = 4 + len(f"COINS ({len(coins)})") + 3
        for lab in ("anon", "amount", "confs"):       # clickable sort headers
            txt = lab + (("▼" if srev else "▲") if lab == skey else "")
            put(ch, col, y1, x, txt, WHITE if lab == skey else GREY)
            regions.append((y1, x, x+len(txt)-1, ("SORT", lab)))
            x += len(txt) + 3
        put(ch, col, y1, x, "· label · address     g receive · k addresses · x exclude · y copy", GREY)
        vis = H - (y1+1) - 2
        n = len(coins)
        if n:
            sel[1] = max(0, min(sel[1], n-1))
            if sel[1] < off[1]: off[1] = sel[1]
            if sel[1] >= off[1]+vis: off[1] = sel[1]-vis+1
            off[1] = max(0, min(off[1], max(0, n-vis)))
        for i, c in enumerate(coins[off[1]:off[1]+vis]):
            gi = off[1]+i; y = y1+1+i; on = (gi == sel[1])
            a = anon_of(c)
            acol = GREEN if a >= T else (AMBER if a > 1 else ORANGE)
            mark = "▸ " if on else "  "
            put(ch, col, y, 4, mark + f"{a:>4}", WHITE if on else acol)
            put(ch, col, y, 11, f"{cbtc(c.get('amount', 0)):>14}", WHITE if on else lerp(BRAND, WHITE, .2))
            put(ch, col, y, 27, f"{c.get('confirmations', 0):>5}c" if c.get("confirmed", True)
                else "  mempool", GREY)
            lab = str(c.get("label", "") or "")       # who this coin came from = its history
            put(ch, col, y, 38, short(lab, 16) if lab else "·", (lerp(GREEN, WHITE, .2) if on else
                lerp(BRAND, WHITE, .1)) if lab else DIM)
            put(ch, col, y, 56, short(c.get("address", ""), 22), lerp(BRAND, GREY, .3))
            if c.get("address"):                      # click the address text = copy it
                regions.append((y, 56, 56+21, ("COPY", str(c["address"]), "address")))
            xx = 80
            if c.get("coinJoinInProgress"):
                sp = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.time()*8) % 10]
                put(ch, col, y, xx, sp + " mixing", GREEN); xx += 10
            if c.get("excludedFromCoinjoin"): put(ch, col, y, xx, "✗ excluded", GREY)
            regions.append((y, 4, W-4, ("ROW", 1, gi)))

    def draw_history(ch, col, y0):
        if not S.get("wallet"):
            put(ch, col, y0+1, 4, "no wallet loaded - go to [1] dashboard and press enter", ORANGE); return
        if S.get("wloading"):
            put(ch, col, y0+1, 4, "⟳ wallet is synchronizing - matching block filters against your keys and", AMBER)
            put(ch, col, y0+2, 4, "  downloading matched blocks from P2P peers.", AMBER)
            h_ = S.get("sync_h"); h0 = S.get("sync_h0")
            try: tip = int((S.get("status") or {}).get("bestBlockchainHeight") or 0)
            except Exception: tip = 0
            if h_ and h0 and tip > h0 and time.monotonic() - S.get("sync_t", 0) < 30:
                pct = max(0.0, min(1.0, (h_-h0)/(tip-h0)))
                bar(ch, col, y0+4, 4, 60, pct, AMBER)
                put(ch, col, y0+4, 66, f"~{100*pct:.0f}%   block {h_:,} of {tip:,}", AMBER)
                put(ch, col, y0+5, 4, f"{S.get('sync_rate', 0):.0f} blocks/min - only blocks that MATCH "
                    "your wallet's filters are fetched", GREY)
            else:
                put(ch, col, y0+4, 4, "old / busy wallets take minutes; coins and history appear when done.", GREY)
            return
        hist = S.get("history") or []
        put(ch, col, y0, 4, f"HISTORY ({len(hist)})   date · amount · label · txid      y copy txid · r raw hex", GREY)
        vis = H - (y0+2) - 2; n = len(hist)
        if n:
            sel[2] = max(0, min(sel[2], n-1))
            if sel[2] < off[2]: off[2] = sel[2]
            if sel[2] >= off[2]+vis: off[2] = sel[2]-vis+1
            off[2] = max(0, min(off[2], max(0, n-vis)))
        for i, h_ in enumerate(hist[off[2]:off[2]+vis]):
            gi = off[2]+i; y = y0+2+i; on = (gi == sel[2])
            amt = h_.get("amount", 0); cj = is_cj_row(h_)
            dt = fmt_dt(h_.get("datetime"))
            put(ch, col, y, 4, ("▸ " if on else "  ") + dt, WHITE if on else GREY)
            ac = GREEN if amt > 0 else (ORANGE if amt < 0 else GREY)
            put(ch, col, y, 24, f"{('+' if amt > 0 else '')+cbtc(amt):>16}", WHITE if on else ac)
            lab = str(h_.get("label", "") or "")
            if cj: put(ch, col, y, 42, "◆ coinjoin", GREEN)
            elif lab: put(ch, col, y, 42, short(lab, 14), lerp(BRAND, GREY, .3))
            put(ch, col, y, 58, short(h_.get("tx", ""), 20), lerp(BRAND, GREY, .4))
            if h_.get("tx"):                          # click the txid text = copy it
                regions.append((y, 58, 58+19, ("COPY", str(h_["tx"]), "txid")))
            hh = h_.get("height")                     # 2.8.0: a string ("902123" / "Mempool")
            try: htxt, hcol = f"block {int(hh):,}", GREY
            except (TypeError, ValueError):
                htxt = str(hh) if hh and str(hh).lower() not in ("mempool", "unknown", "none") else "unconfirmed"
                htxt, hcol = (htxt if htxt != "Mempool" else "unconfirmed"), ORANGE
            rput(ch, col, y, W-4, htxt, hcol)
            regions.append((y, 4, W-4, ("ROW", 2, gi)))

    def draw_coinjoin(ch, col, y0, f):
        pulse = qpulse(f, 0.09)
        on = S.get("cj_on")
        rows = min(13, max(8, H - y0 - 12))
        lcol = lerp(GREEN, GLOW, pulse) if on else lerp(BRAND, GREY, .35)
        cols = draw_logo(ch, col, y0+1, 4, rows, lcol, dimf=0.55 if on else 0.28, depth=2)
        x0 = cols + 10
        if not S.get("wallet"):
            put(ch, col, y0+1, x0, "no wallet loaded - go to [1] dashboard and press enter", ORANGE); return
        if S.get("wloading"):
            put(ch, col, y0+1, x0, "⟳ wallet is synchronizing - coinjoin available once it finishes", AMBER); return
        if S.get("no_coord") or (S.get("coord_uri") == "" and not S.get("cj_status")):
            put(ch, col, y0+1, x0, "⚠ NO COORDINATOR CONFIGURED - coinjoin is disabled", WARN)
            put(ch, col, y0+3, x0, "wasabi ships without a coordinator on purpose: you choose who", lerp(BRAND, WHITE, .3))
            put(ch, col, y0+4, x0, "coordinates your rounds. a coordinator sees coinjoin activity and", lerp(BRAND, WHITE, .3))
            put(ch, col, y0+5, x0, "sets the coordination fee - it can NEVER steal your funds.", lerp(BRAND, WHITE, .3))
            cta = "press c (or click here) to choose one  (coinjoin.nl · kruw.io · your own)"
            put(ch, col, y0+7, x0, cta, clamp8(lerp(GREEN, WHITE, .2)))
            regions.append((y0+7, x0, x0+len(cta)-1, ("ACT", do_coordinator)))
            put(ch, col, y0+9, x0, "sabi edits Config.json for you - the daemon needs a restart after", GREY)
            return
        stat = (S.get("cj_status") or ("In progress" if on else "Idle"))
        stat = (stat.upper() + " ◆") if on else stat.lower()
        put(ch, col, y0+1, x0, "COINJOIN  ·  ", GREY)
        put(ch, col, y0+1, x0+13, stat, clamp8(lerp(GREEN, WHITE, .4*pulse)) if on else GREY)
        if S.get("hw_auth"):
            p2 = qpulse(f, 0.3, step=3)
            left = max(0, 200 - int(time.monotonic() - S["hw_auth"]))
            put(ch, col, y0+2, x0, f"▸▸ CONFIRM ON YOUR TREZOR NOW ◂◂   hold to approve · {left}s left",
                clamp8(lerp(AMBER, WHITE, .5*p2)))
        regions.append((y0+1, x0, x0+13+len(stat)-1, ("ACT", do_cj_toggle)))   # click = start/stop
        pr, se, np_ = balances(S)
        put(ch, col, y0+3, x0, f"daemon says  {S.get('cj_status') or '?'}",
            GREEN if on else GREY)
        put(ch, col, y0+4, x0, f"to mix       {btc(se+np_)}", AMBER if se+np_ else GREY)
        put(ch, col, y0+5, x0, f"private      {btc(pr)}", GREEN if pr else GREY)
        mode = "single round" if S.get("single") else ("auto" if S.get("auto") else "manual")
        put(ch, col, y0+6, x0, f"mode         {mode}", lerp(BRAND, WHITE, .25))
        cu = S.get("coord_uri")
        put(ch, col, y0+7, x0, "coordinator  " + (coord_host(cu) if cu else "?"),
            lerp(BRAND, WHITE, .25) if cu else GREY)
        put(ch, col, y0+8, x0, "space start/stop   o one round   b sweep   c coordinator", lerp(BRAND, WHITE, .35))
        put(ch, col, y0+9, x0, "p pay inside coinjoin   x cancel payment   c coordinator", lerp(BRAND, WHITE, .35))
        pays = S.get("pays") or []
        py = y0 + 11
        put(ch, col, py, x0, f"PAYMENTS INSIDE COINJOIN ({len(pays)})", GREY)
        if not pays: put(ch, col, py+1, x0, "none queued - press p", GREY)
        for i, p in enumerate(pays[:max(1, H-py-4)]):
            y = py+1+i; onr = (i == sel[3] % max(1, len(pays)))
            dest = p.get("address") or p.get("destination") or "?"   # 2.8.0: scriptPubKey hex
            amt = p.get("amount", 0)
            stt = p.get("state") or p.get("status") or ""
            if isinstance(stt, list) and stt:                         # 2.8.0: state history list
                last = stt[-1]
                stt = (last.get("status") or last.get("state") or next(iter(last.values()), "")) \
                      if isinstance(last, dict) else str(last)
            put(ch, col, y, x0, ("▸ " if onr else "  ") + short(str(dest), 22), WHITE if onr else lerp(GREEN, WHITE, .2))
            regions.append((y, x0+2, x0+2+21, ("COPY", str(dest), "destination")))
            put(ch, col, y, x0+26, f"{cbtc(amt) if isinstance(amt, (int, float)) else amt}", lerp(BRAND, WHITE, .2))
            put(ch, col, y, x0+44, str(stt)[:18], GREY)
            regions.append((y, x0, W-4, ("ROW", 3, i)))
        b = S.get("banner")
        if b:
            live = _rem_secs(b.get("InputRegistrationRemaining")) - (time.monotonic()-S.get("banner_t", 0))
            joining = str(b.get("Phase", "")).startswith("Input")
            pc = GREEN if joining else ORANGE
            seg = f"◆ COINJOIN.NL ROUND · {b.get('Phase', '?')} · {b.get('InputCount', 0)} inputs"
            if joining and live > 0: seg += f" · ends in {_fmt_secs(live)}"
            put(ch, col, H-4, 4, seg, clamp8(lerp(pc, WHITE, .25)))

    def draw_send(ch, col, y0):
        if not S.get("wallet"):
            put(ch, col, y0+1, 4, "no wallet loaded - go to [1] dashboard and press enter", ORANGE); return
        if S.get("wloading"):
            put(ch, col, y0+1, 4, "⟳ wallet is synchronizing - sending available once it finishes", AMBER); return
        total = sum(p["amount"] for p in queue)
        put(ch, col, y0, 4, f"PAYMENT QUEUE ({len(queue)})   one on-chain transaction, many recipients", GREY)
        if not queue:
            put(ch, col, y0+2, 4, "empty - press n to queue the first payment", GREY)
        for i, p in enumerate(queue[:H-y0-9]):
            y = y0+2+i; on = (i == sel[4] % max(1, len(queue)))
            put(ch, col, y, 4, ("▸ " if on else "  ") + short(p["sendto"], 34), WHITE if on else lerp(BRAND, WHITE, .2))
            put(ch, col, y, 42, f"{cbtc(p['amount']):>15}", WHITE if on else lerp(GREEN, WHITE, .2))
            put(ch, col, y, 60, short(p.get("label", ""), 18), GREY)
            regions.append((y, 4, W-4, ("ROW", 4, i)))
        yb = y0 + max(3, min(len(queue), H-y0-9)) + 3
        T = tgt_of(S)
        need = total + max(5_000, int(total*0.003)) if total else 0
        coins, have, toxic = pick_coins(S.get("coins") or [], need, T) if total else ([], 0, False)
        put(ch, col, yb, 4, f"total {btc(total)}", WHITE if total else GREY)
        if sub_fee: put(ch, col, yb, 32, "· fee subtracted from payment 1", AMBER)
        locked = bool(sug_lock.get("coins")) and sug_lock.get("total") == total
        if total and locked:                          # applied no-change match
            put(ch, col, yb+1, 4, f"◆ NO CHANGE locked - {len(sug_lock['coins'])} coins consumed exactly, "
                "zero change output (any edit unlocks)", clamp8(lerp(GREEN, WHITE, .2)))
        elif total:
            put(ch, col, yb+1, 4, f"coin selection: {len(coins)} coins, {btc(have)} "
                f"(private first, target {T})", lerp(BRAND, WHITE, .3))
            if toxic:
                put(ch, col, yb+2, 4, "⚠ TOXIC MERGE - this spend would join private + non-private coins "
                    "and undo their mix", WARN)
            elif have < need:
                put(ch, col, yb+2, 4, f"not enough confirmed funds (need ~{cbtc(need)})", WARN)
            sug = get_sug(total)                      # wasabi-style round-up / round-down shields
            ys = yb + 3
            if sug.get("up") or sug.get("dn"):
                put(ch, col, ys, 4, "NO-CHANGE SUGGESTIONS   avoiding change = nothing links back to you", GREY)
                if sug.get("up"):
                    d = sug["up"]["delta"]
                    put(ch, col, ys+1, 4, "◆ ▲", clamp8(lerp(GREEN, WHITE, .2)))
                    put(ch, col, ys+1, 9, f"round UP   +{cbtc(d):>12}  →  send {btc(total+d)}"
                        f"   ({len(sug['up']['coins'])} coins, no change)   press + or click",
                        clamp8(lerp(GREEN, WHITE, .1)))
                    regions.append((ys+1, 4, W-4, ("ACT", lambda: apply_sug("up"))))
                if sug.get("dn"):
                    d = sug["dn"]["delta"]
                    put(ch, col, ys+2, 4, "◆ ▼", AMBER)
                    put(ch, col, ys+2, 9, f"round DOWN {cbtc(d):>13}  →  send {btc(total+d)}"
                        f"   ({len(sug['dn']['coins'])} coins, no change)   press - or click", AMBER)
                    regions.append((ys+2, 4, W-4, ("ACT", lambda: apply_sug("dn"))))
                put(ch, col, ys+3, 4, "only if the receiver accepts a slightly different amount",
                    lerp(BRAND, GREY, .35))

    def draw_auto(ch, col, y0, f):
        if not S.get("wallet"):
            put(ch, col, y0+1, 4, "no wallet loaded - go to [1] dashboard and press enter", ORANGE); return
        armed = S.get("armed"); pulse = qpulse(f, 0.15)
        put(ch, col, y0, 4, f"AUTOMATION RULES · {S['wallet']}", GREY)
        stat = "◆ ARMED - rules are live" if armed else "disarmed - press a to arm (password)"
        put(ch, col, y0, 44, stat, clamp8(lerp(GREEN, WHITE, .4*pulse)) if armed else AMBER)
        rules = S.get("rules") or []
        if not rules:
            for i, l in enumerate([
                "no rules yet - press n. examples:",
                "",
                "  when np ≥ 0.05 BTC → start coinjoin      (mix new deposits automatically)",
                "  when pr ≥ 0.50 BTC → sweep -> ColdVault  (hot wallet -> cold storage, via coinjoin)",
                "  when pr ≥ 0.20 BTC → stop coinjoin       (stop once you have enough private)",
                "  always             → join ONE round      (steady drip, one round per 10 min)"]):
                put(ch, col, y0+2+i, 4, l, lerp(BRAND, WHITE, .3) if l.strip() else GREY)
        for i, rl in enumerate(rules[:H-y0-8]):
            y = y0+2+i; on = (i == sel[5] % max(1, len(rules)))
            onoff = "[ on]" if rl.get("on") else "[off]"
            oc = GREEN if rl.get("on") else GREY
            put(ch, col, y, 4, ("▸ " if on else "  ") + onoff, WHITE if on else oc)
            def _tgl(idx=i):                          # click the [on]/[off] chip = toggle the rule
                S["rules"][idx]["on"] = not S["rules"][idx].get("on")
                save_rules(S["wallet"], S["rules"])
            regions.append((y, 6, 10, ("ACT", _tgl)))
            put(ch, col, y, 12, rule_text(rl), WHITE if on else lerp(BRAND, WHITE, .25))
            last = rl.get("last", 0)
            if last:
                ago = int(time.time() - last)
                rput(ch, col, y, W-4, f"fired {ago//60}m ago" if ago < 3600 else f"fired {ago//3600}h ago", GREY)
            regions.append((y, 4, W-4, ("ROW", 5, i)))
        yb = y0 + max(9, len(rules)+3)
        put(ch, col, yb, 4, "n new rule · e edit · space on/off · x delete · m arm/disarm", GREY)
        put(ch, col, yb+1, 4, "rules check every ~4s while sabi runs · each rule fires at most once per 10 min",
            lerp(BRAND, GREY, .3))
        put(ch, col, yb+2, 4, "sweep target may be a watch-only wallet: hot -> cold via coinjoin, "
            "coins land private", lerp(GREEN, GREY, .35))

    def draw_pager(ch, col):
        p = S["pager"]; lines = p["lines"]
        vw, vh = min(W, TW), min(H, TH)               # fit + center inside the viewport
        w = min(vw-6, max([len(p["title"])] + [len(l) for l in lines]) + 6)
        h = min(vh-4, len(lines) + 6)
        x0 = HOFF + max(0, (vw-w)//2); y0 = VOFF + max(0, (vh-h)//2); vis = h - 6
        p["off"] = max(0, min(p.get("off", 0), max(0, len(lines)-vis)))
        for yy in range(y0, y0+h):
            for xx in range(x0, x0+w):
                edge = yy in (y0, y0+h-1) or xx in (x0, x0+w-1)
                ch[yy][xx] = "█" if edge else " "
                col[yy][xx] = clamp8(lerp(BRAND, WHITE, .25)) if edge else (16, 18, 26)
        put(ch, col, y0+1, x0+(w-len(p["title"]))//2, p["title"], WHITE)
        for i, l in enumerate(lines[p["off"]:p["off"]+vis]):
            put(ch, col, y0+3+i, x0+2, l[:w-4], lerp(BRAND, WHITE, .45))
        foot = f"w/s scroll · q close   [{p['off']+1}-{min(len(lines), p['off']+vis)}/{len(lines)}]"
        put(ch, col, y0+h-2, x0+(w-len(foot))//2, foot, GREY)

    def draw_scheme(ch, col, y0):
        n = len(SCHEME_ITEMS); sel[6] = max(0, min(sel[6], n-1))
        colw = 32
        put(ch, col, y0, 4, "CROSS-WALLET REPORTS & SCRIPTS", GREY)
        put(ch, col, y0, 4+colw+2, "OUTPUT   ·   ◆ native (crash-safe) · λ scheme (experimental)", GREY)
        put(ch, col, H-3, 4, "⚠ λ scheme is experimental daemon code - the curated ones are metadata-only; "
            "custom coin/tx loops can crash the daemon", clamp8(lerp(AMBER, GREY, .25)))
        vis = H - (y0+1) - 3
        if sel[6] < off[6]: off[6] = sel[6]
        elif sel[6] >= off[6]+vis: off[6] = sel[6]-vis+1
        for i, (kd, title, desc, payload) in enumerate(SCHEME_ITEMS[off[6]:off[6]+vis]):
            gi = off[6]+i; y = y0+1+i; on2 = (gi == sel[6])
            tag = "◆" if kd == "rpc" else "λ"
            tc = GREEN if kd == "rpc" else AMBER
            put(ch, col, y, 4, "▸ " if on2 else "  ", WHITE)
            put(ch, col, y, 6, tag, clamp8(lerp(tc, WHITE, .3)) if on2 else tc)
            put(ch, col, y, 8, title, WHITE if on2 else lerp(BRAND, WHITE, .25))
            regions.append((y, 4, 4+colw, ("ROW", 6, gi)))
        kd, title, desc, payload = SCHEME_ITEMS[sel[6]]
        x = 4+colw+2
        put(ch, col, y0+1, x, desc, lerp(BRAND, WHITE, .3))
        cy = y0+3
        if kd == "scm":
            for ln in payload.split("\n")[:8]:        # the selected script
                put(ch, col, cy, x, ln[:W-x-2], lerp(GREEN, WHITE, .25)); cy += 1
        else:
            put(ch, col, cy, x, "◆ native RPC report - exact numbers, cannot crash the daemon",
                lerp(GREEN, GREY, .25)); cy += 1
        cy += 1
        run = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.time()*10) % 10] + " running ..." if S.get("sc_running") else (
              "enter run · e edit/paste scheme · x enable scripting · l load all wallets first")
        put(ch, col, cy, x, run, AMBER if S.get("sc_running") else GREY); cy += 1
        put(ch, col, cy, x, "─"*min(W-x-2, 60), lerp(BG, BRAND, .3)); cy += 1
        for ln in (S.get("sc_out") or ["(no output yet - press enter to run the selection)"]):
            if cy >= H-3: break
            c_ = WARN if ln.startswith("✗") else (AMBER if ln.startswith("⚠") else lerp(BRAND, WHITE, .4))
            put(ch, col, cy, x, ln[:W-x-2], c_); cy += 1

    def draw_modal(ch, col):
        m = modal
        mlines = m.get("lines") or []                 # optional menu block above the fields
        try: mlines = mlines() if callable(mlines) else list(mlines)
        except Exception: mlines = []
        w = max(56, max(len(f["label"]) + 34 for f in m["fields"]) + 8, len(m["title"]) + 8,
                (max(len(l) for l in mlines) + 8) if mlines else 0)
        w = min(w, min(W, TW)-4)
        inf = ""
        if m.get("info"):
            try: inf = m["info"]({f["k"]: f["v"] for f in m["fields"]}) or ""
            except Exception: inf = ""
        h = 4 + 3*len(m["fields"]) + (len(mlines)+1 if mlines else 0) \
            + (2 if m.get("warn") else 0) + (2 if inf else 0) + 2
        vh = min(H, TH)
        x0 = HOFF + max(0, (min(W, TW)-w)//2); y0 = VOFF + max(1, (vh-h)//2)
        for yy in range(y0, min(y0+h, H)):
            for xx in range(x0, min(x0+w, W)):
                ch[yy][xx] = " "; col[yy][xx] = (16, 18, 26)
        draw_box(ch, col, y0, x0, w, h, lerp(BRAND, WHITE, .3))
        put(ch, col, y0+1, x0+(w-len(m["title"]))//2, m["title"], WHITE)
        yy = y0+3
        for l in mlines:
            put(ch, col, yy, x0+3, l[:w-6], lerp(BRAND, WHITE, .4))
            mnum = re.match(r"\s*(\d+)\s", l)         # numbered menu line: click = pick that number
            if mnum: regions.append((yy, x0+3, x0+w-4, ("MPICK", mnum.group(1))))
            yy += 1
        if mlines: yy += 1
        if inf:
            put(ch, col, yy, x0+3, inf[:w-6], lerp(AMBER, WHITE, .25)); yy += 2
        if m.get("warn"):
            put(ch, col, yy, x0+3, m["warn"][:w-6], WARN); yy += 2
        for i, fld in enumerate(m["fields"]):
            onf = (i == m["i"])
            regions.append((yy, x0+3, x0+w-4, ("MFIELD", i)))     # click a field = focus it
            regions.append((yy+1, x0+3, x0+w-4, ("MFIELD", i)))
            put(ch, col, yy, x0+3, fld["label"], WHITE if onf else GREY)
            v = fld["v"]; shown = ("•"*len(v)) if fld.get("mask") else v
            maxw = w - 8
            if len(shown) > maxw: shown = "…" + shown[-(maxw-1):]
            put(ch, col, yy+1, x0+3, shown + ("▌" if onf else " "), lerp(GREEN, WHITE, .3) if onf else GREY)
            if fld.get("hint") and onf:
                put(ch, col, yy+1, x0+3+len(shown)+3, fld["hint"][:w-10-len(shown)], lerp(BRAND, GREY, .3))
            yy += 3
        put(ch, col, y0+h-2, x0+3, "enter ok · tab next field · esc cancel", GREY)

    def row_activate(tt, ii):                         # second click on a selected row = open/run it
        if tt == 0: do_load_wallet()
        elif tt == 4:                                 # send queue: edit the clicked payment
            if queue:
                sug_lock.clear()
                d = dict(queue[ii % len(queue)]); d["_edit"] = ii % len(queue); q_add(d)
        elif tt == 5 and (S.get("rules") or []): rule_edit(ii % len(S["rules"]))
        elif tt == 6:
            kd6, _t6, _d6, pl6 = SCHEME_ITEMS[ii % len(SCHEME_ITEMS)]
            do_native_report(pl6) if kd6 == "rpc" else do_scheme_run(pl6)

    # ---------- main loop --------------------------------------------------------------
    HINTS = ["space load wallet · n create · ? help",  # the rest lives in ? and GETTING STARTED
             "g receive · y copy address · ? help",
             "u speed up · y copy txid · ? help",
             "space start/stop · c coordinator · ? help",
             "n add payment · enter send · ? help",
             "n new rule · m arm · ? help",
             "enter run · e edit · ? help"]
    def draw_block_anim(ch, col, over=False):         # block found: numbered sushi drops on a belt
        a = S.get("blockanim")
        el = time.monotonic() - a["t0"]
        if el > 3.0:
            S["blockanim"] = None; return
        vw = min(W, TW)
        yb = VOFF + (max(9, min(H, TH)//3) if over    # above a running screensaver: higher band
              else min(H, TH)//2 + 3)                 # belt surface
        fade = 1.0 if el < 2.2 else max(0.05, 1.0 - (el - 2.2) / 0.8)
        tint = (lambda c: clamp8(lerp(BG, c, fade)))
        def fput(y, x, s, c): put(ch, col, y, x, s, clamp8(lerp(BG, c, fade)))
        dx = int(el * 7)                              # the belt rolls right
        drop_x = HOFF + max(4, vw//3)
        for x in range(HOFF+1, HOFF+vw-1): fput(yb+1, x, "─", lerp(BG, GREY, .6))
        for x in range(HOFF+1 + (dx % 8), HOFF+vw-1, 8): fput(yb+2, x, "o", lerp(BG, GREY, .45))
        def piece_for(hh): return SUSHI[hh % len(SUSHI)]
        for k in range(1, 5):                         # earlier blocks ride off to the right
            hh = a["h"] - k
            p = piece_for(hh); px = drop_x + k*19 + dx
            if px > HOFF + vw: continue
            _draw_piece(ch, col, px, yb - len(p[2]) + 1, p, tint=tint)
            fput(yb+3, px+1, f"#{hh:,}", GREY)
        p0 = piece_for(a["h"]); land = yb - len(p0[2]) + 1
        if a["y"] is None: a["y"] = float(VOFF + 3)   # enters from just below the header
        if not a["settled"]:                          # gravity + ONE small bounce
            a["vy"] += 0.5; a["y"] += a["vy"]
            if a["y"] >= land:
                a["y"] = land
                if not a["bounced"]: a["vy"] = -abs(a["vy"]) * 0.45; a["bounced"] = True
                elif a["vy"] >= 0: a["settled"] = True; a["vy"] = 0.0
        px0 = drop_x + dx
        _draw_piece(ch, col, px0, int(a["y"]), p0, tint=tint)
        fput(yb+3, px0+1, f"#{a['h']:,}", GREEN)
        fput(yb - 6, px0, f"◆ new block #{a['h']:,}", clamp8(lerp(GREEN, WHITE, .3)))

    def pick_saver():                                 # a coinjoin running -> the galaxy spins
        return (random.choice(("galaxy", "galaxy2", "galaxy3")) if S.get("cj_on")
                else random.choice(("belt", "beltv", "beltr", "platter", "wasabi", "logo", "bounce")))
    def saver_on():                                   # amounts auto-hide while away ('.' mode)
        nonlocal saver, saver_prev_priv
        saver = pick_saver(); saver_prev_priv = DISCREET["on"]; DISCREET["on"] = True; repaint()
        _INFALL.clear()
    try:
        f = 0; last_act = time.monotonic(); saver = None; saver_prev_priv = False
        while frames == 0 or f < frames:
            ev = getkey() if interactive else None
            if ev:
                last_act = time.monotonic()
                if saver:                             # waking up: swallow the key that woke us
                    saver = None; DISCREET["on"] = saver_prev_priv   # restore the user's choice
                    repaint(); ev = None
            elif interactive and saver is None and time.monotonic() - last_act >= AFK_SECS:
                saver_on()
            if ev:
                name, raw = ev
                if S.get("notice"):
                    S["notice"] = None                # any key closes the receive/notice card
                elif S.get("pager"):
                    if name == "UP": S["pager"]["off"] = S["pager"].get("off", 0) - 1
                    elif name == "DOWN": S["pager"]["off"] = S["pager"].get("off", 0) + 1
                    elif name == "WHEELUP": S["pager"]["off"] = S["pager"].get("off", 0) - 3
                    elif name == "WHEELDN": S["pager"]["off"] = S["pager"].get("off", 0) + 3
                    else: S["pager"] = None
                elif modal:
                    if name == "CLICK" and isinstance(raw, tuple):
                        mx, my = raw[0] + HOFF, raw[1] + VOFF   # viewport -> canvas coordinates
                        for (ry, rx0, rx1, tok) in regions:
                            if my == ry and rx0 <= mx <= rx1:
                                if tok[0] == "MFIELD": modal["i"] = tok[1]
                                elif tok[0] == "MPICK" and modal["fields"]:
                                    modal["fields"][modal["i"]]["v"] = tok[1]
                                break
                    else:
                        modal_key(name, raw)
                elif helpon:
                    helpon = False
                elif name == "QUIT":
                    break
                elif name == "HELP": helpon = True
                elif name == "SAVER":                 # Ctrl+E: preview the AFK screensaver now
                    saver_on()
                elif name and name in "1234567": tab = int(name)-1
                elif name == "TAB": tab = (tab+1) % 7
                elif name == "STAB": tab = (tab-1) % 7
                elif name == "RIGHT": tab = (tab+1) % 7
                elif name == "LEFT": tab = (tab-1) % 7
                elif name == "UP":
                    if TH < H and not lists_for_tab(tab): VOFF = max(0, VOFF-2)
                    else: sel[tab] = max(0, sel[tab]-1)
                elif name == "DOWN":
                    if TH < H and not lists_for_tab(tab): VOFF = min(max(0, H-TH), VOFF+2)
                    else: sel[tab] = min(max(0, len(lists_for_tab(tab))-1), sel[tab]+1)
                elif name in ("WHEELUP", "WHEELDN"):
                    d = -3 if name == "WHEELUP" else 3
                    sel[tab] = max(0, min(max(0, len(lists_for_tab(tab))-1), sel[tab]+d))
                elif name == "CLICK":
                    mx, my = raw[0] + HOFF, raw[1] + VOFF   # viewport -> canvas coordinates
                    for (ry, rx0, rx1, tok) in regions:
                        if my == ry and rx0 <= mx <= rx1:
                            if tok[0] == "TAB": tab = tok[1]
                            elif tok[0] == "COPY":    # click the text itself = copy it
                                ok = clip_copy(tok[1])
                                flash(f"{tok[2]} copied ✓" if ok else "clipboard unavailable")
                            elif tok[0] == "ACT":     # click a named action = run it
                                tok[1]()
                            elif tok[0] == "SORT":
                                k2 = tok[1]; sk, sr = S.get("coin_sort") or ("confs", False)
                                # same column toggles direction; fresh column gets its natural one
                                S["coin_sort"] = (k2, not sr if sk == k2 else k2 != "confs")
                            elif tok[0] == "ROW":
                                _, tt, ii = tok
                                already = (tab == tt and sel[tt] == ii)
                                tab = tt; sel[tt] = ii
                                if already: row_activate(tt, ii)   # click selected row = open/run
                            break
                elif name == "ENTER":
                    if tab == 0: do_load_wallet()
                    elif tab == 4: do_send()
                    elif tab == 5 and (S.get("rules") or []): rule_edit(sel[5] % len(S["rules"]))
                    elif tab == 6:
                        kd6, _t6, _d6, pl6 = SCHEME_ITEMS[sel[6] % len(SCHEME_ITEMS)]
                        do_native_report(pl6) if kd6 == "rpc" else do_scheme_run(pl6)
                elif name == "SPACE":
                    if tab == 0: do_load_wallet()     # space opens/loads, same as everywhere else
                    elif tab == 3: do_cj_toggle()
                    elif tab == 6:
                        kd6, _t6, _d6, pl6 = SCHEME_ITEMS[sel[6] % len(SCHEME_ITEMS)]
                        do_native_report(pl6) if kd6 == "rpc" else do_scheme_run(pl6)
                    elif tab == 5 and (S.get("rules") or []):
                        rl = S["rules"][sel[5] % len(S["rules"])]
                        rl["on"] = not rl.get("on"); save_rules(S["wallet"], S["rules"])
                elif raw:
                    r = raw.lower()
                    if TW < W and raw == "]": HOFF = min(W - TW, HOFF + 6)   # thin: pan the viewport
                    elif TW < W and raw == "[": HOFF = max(0, HOFF - 6)
                    elif tab == 2 and r == "r": do_copy_rawtx()
                    elif tab == 4 and r == "r": do_copy_last_send()
                    elif r == "r": S["kick"] = True; flash("refreshing ...", 20)
                    elif r == ".":                                # privacy mode: hide amounts + addresses
                        DISCREET["on"] = not DISCREET["on"]
                        flash("privacy mode ON - amounts & addresses hidden ('.' reveals)" if DISCREET["on"]
                              else "privacy mode off - everything visible", 70)
                    elif r == "g": do_receive()                   # receive works from every tab
                    elif r == "l" and tab in (0, 6): do_load_all()   # load every wallet
                    elif tab == 0 and r == "n": do_create_wallet()
                    elif tab == 0 and r == "v": do_recover_wallet()
                    elif tab == 0 and r == "t": do_import_trezor()
                    elif tab == 3 and r == "e": do_enable_cj_account()
                    elif r == "i" and (tab == 0 or S.get("err")): do_install_wasabi()
                    elif r == "p" and (tab == 0 or S.get("err")): do_rpc_connect()
                    elif tab == 6 and r == "e":
                        it6 = SCHEME_ITEMS[sel[6] % len(SCHEME_ITEMS)]
                        do_scheme_edit(S.get("sc_custom") or (it6[3] if it6[0] == "scm" else ""))
                    elif tab == 6 and r == "x": do_enable_scripting()
                    elif tab == 2 and r == "u": do_speedup()
                    elif tab == 2 and r == "c": do_cancel_tx()
                    elif tab == 5 and r == "n": rule_edit()
                    elif tab == 5 and r == "e" and (S.get("rules") or []): rule_edit(sel[5] % len(S["rules"]))
                    elif tab == 5 and r == "x" and (S.get("rules") or []):
                        S["rules"].pop(sel[5] % len(S["rules"])); sel[5] = max(0, sel[5]-1)
                        save_rules(S["wallet"], S["rules"])
                    elif tab == 5 and r == "m": do_arm()          # 'm' arm/disarm (a is LEFT now)
                    elif tab == 1 and r == "x": do_toggle_exclude()
                    elif tab == 1 and r == "k": do_list_keys()
                    elif tab == 4 and r == "i": do_import()
                    elif tab == 1 and r == "y":
                        coins = coins_view()
                        if coins:
                            ok = clip_copy(coins[sel[1] % len(coins)].get("address", ""))
                            flash("address copied ✓" if ok else "clipboard unavailable")
                    elif tab == 2 and r == "y":
                        hist = S.get("history") or []
                        if hist:
                            ok = clip_copy(hist[sel[2] % len(hist)].get("tx", ""))
                            flash("txid copied ✓" if ok else "clipboard unavailable")
                    elif tab == 3 and r == "c": do_coordinator()
                    elif tab == 3 and r == "o": do_cj_single()
                    elif tab == 3 and r == "b": do_cj_sweep()
                    elif tab == 3 and r == "p": do_pay_in_cj()
                    elif tab == 3 and r == "x": do_cancel_pay()
                    elif tab == 4 and r == "n": sug_lock.clear(); q_add()
                    elif tab == 4 and r == "e":
                        if queue:
                            sug_lock.clear()
                            d = dict(queue[sel[4] % len(queue)]); d["_edit"] = sel[4] % len(queue)
                            q_add(d)
                    elif tab == 4 and r == "x":
                        if queue:
                            sug_lock.clear()
                            queue.pop(sel[4] % len(queue)); sel[4] = max(0, sel[4]-1)
                    elif tab == 4 and r == "u":
                        sug_lock.clear()
                        sub_fee = not sub_fee
                        flash("fee subtracted from payment 1" if sub_fee else "fee paid on top")
                    elif tab == 4 and raw in ("+", "="): apply_sug("up")
                    elif tab == 4 and raw in ("-", "_"): apply_sug("dn")
            if interactive:
                nw, nh = term_canvas(True)
                if (nw, nh) != (TW, TH): apply_canvas(nw, nh); repaint()
            if TW < 44 or TH < 10:                    # comically small terminal: maki protest screen
                VOFF = 0
                ch, col = blank()
                draw_too_small(ch, col, f)
                emit(o, ch, col); time.sleep(FRAME); f += 1
                continue
            regions.clear()
            ch, col = blank()
            y = draw_header(ch, col, f)
            y = draw_tabs(ch, col, y)
            if   tab == 0: draw_dashboard(ch, col, y)
            elif tab == 1: draw_wallet(ch, col, y)
            elif tab == 2: draw_history(ch, col, y)
            elif tab == 3: draw_coinjoin(ch, col, y, f)
            elif tab == 4: draw_send(ch, col, y)
            elif tab == 5: draw_auto(ch, col, y, f)
            elif tab == 6: draw_scheme(ch, col, y)
            if TH < H:                                # thin terminal: viewport follows the selection
                tgt = next((ry for (ry, _x0, _x1, tok) in regions
                            if tok[0] == "ROW" and tok[1] == tab and tok[2] == sel[tab]), None)
                if tgt is not None:
                    if tgt < VOFF + 4: VOFF = max(0, tgt - 4)
                    elif tgt > VOFF + TH - 4: VOFF = tgt - TH + 4
                VOFF = max(0, min(VOFF, H - TH))
            else:
                VOFF = 0
            HOFF = max(0, min(HOFF, W - TW)) if TW < W else 0
            hb = min(H, VOFF + TH)                    # hint/footer pinned to the VISIBLE bottom
            hint = S["flash"] if S.get("flasht", 0) > 0 else HINTS[tab]
            if S.get("werr"):
                hint = ("✗ no coordinator configured - press c on [4] coinjoin to choose one"
                        if "no coordinator" in S["werr"].lower()
                        else "wallet rpc: " + short(S["werr"], 60))
            hcol = (WARN if hint.startswith(("✗", "wallet rpc"))
                    else clamp8(lerp(GREEN, WHITE, .25)) if hint.startswith(("◆", "✓")) or "✓" in hint[:40]
                    else lerp(BRAND, WHITE, .4))
            vw = min(W, TW)
            put(ch, col, hb-2, HOFF + max(0, (vw-len(hint))//2), hint, hcol)
            st_ = S.get("status") or {}                # status footer strip
            okc = GREEN if not S.get("err") else WARN
            put(ch, col, hb-1, HOFF+2, "●", okc)
            put(ch, col, hb-1, HOFF+4, (f"#{st_.get('bestBlockchainHeight', '?')}" if not S.get("err")
                                  else "offline"), GREY)
            if vw >= 100:
                tag = ("sabi · wasabi daemon terminal · coinjoin.nl" if vw >= 150 else "sabi · coinjoin.nl")
                put(ch, col, hb-1, HOFF + max(0, (vw-len(tag))//2), tag, lerp(BRAND, WHITE, .25))
            age = int(time.monotonic() - S.get("t_poll", time.monotonic()))
            eye = "● hidden" if DISCREET["on"] else "○ visible"
            rput(ch, col, hb-1, HOFF + vw-2, f"'.' {eye} · sync {age}s · {time.strftime('%H:%M')}",
                 lerp(AMBER, GREY, .3) if DISCREET["on"] else GREY)
            if S.get("flasht", 0) > 0: S["flasht"] -= 1
            if helpon: draw_overlay(ch, col, "SABI · keys", HELP)
            elif S.get("notice"):
                nt = S["notice"]; draw_overlay(ch, col, nt["title"], nt["lines"], tcol=GREEN)
            elif S.get("pager"): draw_pager(ch, col)
            elif modal: draw_modal(ch, col)
            if S.get("blockanim") and saver in ("galaxy", "galaxy2", "galaxy3"):
                a = S.pop("blockanim")                # immersive: gravity takes the new block
                _INFALL.clear()
                _INFALL.update(h=a["h"], t=1.2, arm=0.0 if random.random() < 0.5 else M.pi)
            elif S.get("blockanim") and saver == "bounce" and _BOUNCE.get("balls"):
                a = S.pop("blockanim")                # immersive: the block joins the physics pit
                p = SUSHI[a["h"] % len(SUSHI)]
                _BOUNCE["balls"].append(dict(p=p, w=_piece_width(p[2]), h=len(p[2]),
                    x=float(max(2, min(W, TW)//2)), y=float(VOFF + 3),
                    vx=random.uniform(-1.0, 1.0), vy=0.3, tag=f"#{a['h']:,}"))
                if len(_BOUNCE["balls"]) > 10: _BOUNCE["balls"].pop(0)
            if saver:                                 # translucent screensaver: UI stays faintly visible
                for r in range(H):
                    rg, rc = ch[r], col[r]
                    for c in range(W):
                        if rg[c] != " ": rc[c] = clamp8(lerp(rc[c], BG, .8))
                {"belt": draw_saver_belt, "beltv": draw_saver_belt_v, "beltr": draw_saver_belt_r,
                 "platter": draw_saver_platter, "wasabi": draw_saver_wasabi,
                 "logo": draw_saver_logo, "bounce": draw_saver_bounce,
                 "galaxy": draw_saver_galaxy,
                 "galaxy2": lambda c1, c2, f_: draw_saver_galaxy(c1, c2, f_, rainbow=True),
                 "galaxy3": draw_saver_galaxy_sushi}[saver](ch, col, f)
            if S.get("blockanim"):
                draw_block_anim(ch, col, over=bool(saver))
            emit(o, ch, col); time.sleep(FRAME); f += 1
    except KeyboardInterrupt:
        raise
    finally:
        stop.set(); restore(); o("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n")

# ---- main ---------------------------------------------------------------------------
def wasabi_config():                                  # auto-detect like wcli.sh: read the daemon's own config
    cands = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata: cands.append(os.path.join(appdata, "WalletWasabi", "Client", "Config.json"))
    home = os.path.expanduser("~")
    cands.append(os.path.join(home, ".walletwasabi", "client", "Config.json"))
    if sys.platform == "darwin":
        cands.append(os.path.join(home, "Library", "Application Support",
                                  "WalletWasabi", "Client", "Config.json"))
    for p in cands:
        try:
            cfg = json.load(open(p, encoding="utf-8-sig"))
        except Exception:
            continue
        pref = (cfg.get("JsonRpcServerPrefixes") or ["http://127.0.0.1:37128/"])[0]
        return dict(path=p, enabled=bool(cfg.get("JsonRpcServerEnabled")),
                    url=str(pref).rstrip("/"), user=cfg.get("JsonRpcUser") or None,
                    password=cfg.get("JsonRpcPassword") or None,
                    coordinator=cfg.get("CoordinatorUri"),
                    btcrpc=dict(url=str(cfg.get("BitcoinRpcEndPoint") or cfg.get("BitcoinRpcUri") or ""),
                                cred=str(cfg.get("BitcoinRpcCredentialString") or "")),
                    log=os.path.join(os.path.dirname(p), "Logs.txt"))
    return None

def find_daemon(extra=None):                          # locate the Wasabi daemon executable
    import shutil as _sh
    cands = [extra] if extra else []
    try:                                              # path remembered from a sabi-verified install
        p = open(os.path.join(os.path.expanduser("~"), ".sabi-daemon"), encoding="utf-8").read().strip()
        if p: cands.append(p)
    except Exception:
        pass
    for name in ("wassabeed", "WalletWasabi.Daemon", "wassabee"):
        w = _sh.which(name)
        if w: cands.append(w)
    if os.name == "nt":
        for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if pf:
                cands += [os.path.join(pf, "WasabiWallet", n)
                          for n in ("wassabeed.exe", "WalletWasabi.Daemon.exe", "wassabee.exe")]
    elif sys.platform == "darwin":
        cands.append("/Applications/Wasabi Wallet.app/Contents/MacOS/wassabeed")
    else:
        cands += ["/usr/bin/wassabeed", "/usr/local/bin/wassabeed"]
    for c in cands:
        if c and os.path.isfile(c): return c
    return None

RPC_WAIT_SECS = 90                                    # post-start grace for the daemon's RPC

DAEMON_LOG = os.path.join(os.path.expanduser("~"), ".sabi-daemon.log")

def launch_daemon(exe, rpc_user=None, rpc_pass=None):  # start it; captures output so failures aren't silent
    import subprocess
    try:
        # a FRESH wasabi writes JsonRpcServerEnabled:false - the env var overrides it. RPC
        # credentials ride along the same way so the daemon never runs an auth-less RPC.
        env = {**os.environ, "WASABI_JSONRPCSERVERENABLED": "true"}
        if rpc_user and rpc_pass:
            env["WASABI_JSONRPCUSER"] = rpc_user; env["WASABI_JSONRPCPASSWORD"] = rpc_pass
        logf = open(DAEMON_LOG, "wb")                 # capture stdout+stderr: the WHY when it dies
        kw = dict(stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT, env=env,
                  cwd=os.path.dirname(exe) or None)
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED | NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        return subprocess.Popen([exe], **kw)          # the handle: caller watches for early exit
    except Exception as e:
        try: open(DAEMON_LOG, "w", encoding="utf-8").write(f"could not exec {exe}: {e}\n")
        except Exception: pass
        print(f"could not start the daemon: {e}", file=sys.stderr)
        return None

def daemon_log_tail(n=12):
    try:
        lines = open(DAEMON_LOG, encoding="utf-8", errors="replace").read().splitlines()
        return [l for l in lines if l.strip()][-n:]
    except Exception:
        return []

def apply_rpc_config(path, user=None, password=None):  # persist enabled + credentials, minimal edit
    try:                                              # user/password only touched when non-None,
        def m(cfg):                                   # so an existing RPC password is never clobbered
            cfg["JsonRpcServerEnabled"] = True
            if user is not None: cfg["JsonRpcUser"] = user
            if password is not None: cfg["JsonRpcPassword"] = password
        _edit_config(path, m)
        return True
    except Exception as e:
        print(f"could not edit {path}: {e}", file=sys.stderr)
        return False

def enable_rpc_in_config(path):                       # flip JsonRpcServerEnabled with minimal edit
    try:
        raw = open(path, encoding="utf-8-sig").read()
        new, n = re.subn(r'("JsonRpcServerEnabled"\s*:\s*)false', r"\1true", raw, count=1)
        if n == 0:
            cfg = json.load(open(path, encoding="utf-8-sig"))
            cfg["JsonRpcServerEnabled"] = True
            new = json.dumps(cfg, indent=2)
        _write_config_atomic(path, new)
        return True
    except Exception as e:
        print(f"could not edit {path}: {e}", file=sys.stderr)
        return False

def main():
    ap = argparse.ArgumentParser(description="sabi - a terminal for the Wasabi Wallet daemon.")
    ap.add_argument("--rpc", default=None, help="daemon JSON-RPC URL (default: auto from Wasabi's Config.json)")
    ap.add_argument("--wallet", default=None, help="wallet name to select at start")
    ap.add_argument("--user", default=os.environ.get("WASABI_RPC_USER"), help="RPC user (if set)")
    ap.add_argument("--pass", dest="password", default=os.environ.get("WASABI_RPC_PASS"),
                    help="RPC password (if set)")
    ap.add_argument("--demo", action="store_true", help="fake daemon with sample data (safe preview)")
    ap.add_argument("--daemon", default=None, metavar="PATH", help="Wasabi daemon executable (for auto-start)")
    ap.add_argument("--frames", type=int, default=0, help="render N frames then exit (testing)")
    a = ap.parse_args()
    a.logpath = None; a.cfgpath = None; a.coord = None; a.btcrpc = None
    if not a.demo:
        cfg = wasabi_config()                          # zero-config: use the daemon's own settings
        if cfg: a.logpath = cfg.get("log"); a.cfgpath = cfg.get("path"); a.btcrpc = cfg.get("btcrpc")
        if cfg:
            a.coord = cfg.get("coordinator")
            if not (a.coord or "").strip():            # wasabi ships with NO coordinator - the user picks one
                print("!  no coinjoin coordinator configured - coinjoin (and most wallet RPC) is disabled.",
                      file=sys.stderr)
                print("   fix inside sabi: [4] coinjoin tab, press c to choose one.", file=sys.stderr)
        if a.rpc is None and cfg:
            a.rpc = cfg["url"]
            if a.user is None: a.user = cfg["user"]
            if a.password is None: a.password = cfg["password"]
            print(f"wasabi config found: {cfg['path']}", file=sys.stderr)
        if a.rpc is None: a.rpc = "http://127.0.0.1:37128"
        def rpc_up(t=2):
            try:
                WasabiRpc(a.rpc, a.user, a.password).call("getstatus", timeout=t); return True
            except RpcError as e:
                return "401" in str(e)                 # answering but wants auth = daemon is up
            except Exception:
                return False
        if not rpc_up():                               # ---- welcome: help the user get running ----
            if cfg and not cfg["enabled"]:
                print("!  JsonRpcServerEnabled is FALSE - sabi can't talk to the daemon.", file=sys.stderr)
                if sys.stdin.isatty():
                    try: ans = input("   enable RPC in that Config.json now? [y/N] ").strip().lower()
                    except (EOFError, KeyboardInterrupt): ans = ""
                    if ans == "y" and enable_rpc_in_config(cfg["path"]):
                        cfg["enabled"] = True
                        print("   enabled ✓  (a daemon that is already running must be restarted)",
                              file=sys.stderr)
                else:
                    print('   fix: set "JsonRpcServerEnabled": true, then restart the daemon.',
                          file=sys.stderr)
            if sys.stdin.isatty() and (not cfg or cfg["enabled"]):
                exe = find_daemon(a.daemon)
                if exe:
                    try: ans = input(f"Wasabi daemon isn't answering. Start it now?\n   ({exe}) [Y/n] ").strip().lower()
                    except (EOFError, KeyboardInterrupt): ans = "n"
                    if ans != "n" and launch_daemon(exe):
                        print("daemon starting - waiting for RPC ", end="", file=sys.stderr, flush=True)
                        for _ in range(30):
                            time.sleep(1); print(".", end="", file=sys.stderr, flush=True)
                            if rpc_up():
                                print(" ✓", file=sys.stderr); break
                        else:
                            print("\nstill warming up (Tor bootstrap takes a bit) - "
                                  "sabi will keep retrying inside.", file=sys.stderr)
                            for l in daemon_log_tail(6):   # surface an early crash, if any
                                print("   | " + l, file=sys.stderr)
                else:
                    print("wasabi daemon executable not found - press i on the dashboard and sabi "
                          "downloads + verifies it for you (or pass --daemon PATH).", file=sys.stderr)
        for attempt in range(3):                       # quick pre-flight probe: say what we found
            try:
                WasabiRpc(a.rpc, a.user, a.password).call("getstatus", timeout=3)
                print(f"daemon detected at {a.rpc} ✓", file=sys.stderr)
                break
            except RpcError as e:
                if "401" in str(e) and sys.stdin.isatty():   # auth needed -> ask, don't hardcode
                    import getpass
                    print(f"the daemon at {a.rpc} requires RPC credentials"
                          + (" (those given were rejected)." if attempt else "."), file=sys.stderr)
                    try:
                        a.user = input("  RPC username: ").strip()
                        a.password = getpass.getpass("  RPC password: ")
                    except (EOFError, KeyboardInterrupt):
                        print("aborted.", file=sys.stderr); return
                    continue
                print(f"no RPC answer from {a.rpc} yet ({e}) - the dashboard will keep retrying.",
                      file=sys.stderr)
                break
            except Exception as e:
                print(f"no RPC answer from {a.rpc} yet ({e}) - the dashboard will keep retrying.",
                      file=sys.stderr)
                break
        else:
            print("still unauthorized - check JsonRpcUser/JsonRpcPassword in the daemon's Config.json.",
                  file=sys.stderr)
    rpc = DemoRpc() if a.demo else WasabiRpc(a.rpc, a.user, a.password)
    if a.demo and not a.wallet: a.wallet = "SavingsWallet"
    if a.demo:                                        # real-looking demo config so the c / x editors work
        a.cfgpath = os.path.join(tempfile.gettempdir(), "sabi-demo-config.json")
        a.coord = ""
        try:
            json.dump({"CoordinatorUri": "", "ExperimentalFeatures": [], "JsonRpcServerEnabled": True},
                      open(a.cfgpath, "w", encoding="utf-8"), indent=2)
        except Exception: pass
    if not sys.stdin.isatty() and a.frames == 0:
        print("sabi needs an interactive terminal (or pass --frames N for a fixed run).", file=sys.stderr)
        return
    tui(rpc, a, a.frames)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        try: sys.stdout.write("\x1b[?2026l\x1b[?25h\x1b[?1049l\x1b[0m\n"); sys.stdout.flush()
        except Exception: pass
        sys.exit(130)
