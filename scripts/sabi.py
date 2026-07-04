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
#  Keys:   1-5 tabs · Tab/arrows switch · w/s select · enter act · ? help      #
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

def draw_logo(ch, col, y, x, rows, color, dimf=0.35):
    cells, cols = logo_cells(rows)
    for r, c, g, s in cells:
        yy, xx = y+r, x+c
        if 0 <= yy < H and 0 <= xx < W:
            ch[yy][xx] = g; col[yy][xx] = clamp8(lerp(lerp(BG, color, dimf), color, s))
    return cols

# ---- layout / canvas (scales with the terminal; min one row spared) ---------------
W = H = 0
def apply_canvas(w, h):
    global W, H
    W, H = w, h
def term_canvas(interactive=True):
    if not interactive: return 118, 40
    try: c, l = shutil.get_terminal_size((118, 40))
    except Exception: c, l = 118, 40
    return max(100, min(c, 760)), max(29, min(l - 1, 216))
apply_canvas(118, 40)

def blank(): return [[" "]*W for _ in range(H)], [[BG]*W for _ in range(H)]
def put(ch, col, r, c, s, color):
    for i, k in enumerate(str(s)):
        if 0 <= r < H and 0 <= c+i < W: ch[r][c+i] = k; col[r][c+i] = color
def rput(ch, col, r, c_end, s, color): put(ch, col, r, c_end-len(str(s)), s, color)

def emit(o, ch, col):                                 # RLE truecolor frame, synchronized output
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
    x0 = max(0, (W-w)//2); y0 = max(0, (H-h)//2)
    for yy in range(y0, min(y0+h, H)):
        for xx in range(x0, min(x0+w, W)):
            edge = yy in (y0, y0+h-1) or xx in (x0, x0+w-1)
            ch[yy][xx] = "█" if edge else " "
            col[yy][xx] = clamp8(lerp(BRAND, WHITE, .25)) if edge else (16, 18, 26)
    put(ch, col, y0+1, x0+(w-len(title))//2, title, tcol)
    for i, l in enumerate(lines):
        c = l[1] if isinstance(l, tuple) else lerp(BRAND, WHITE, .45)
        put(ch, col, y0+3+i, x0+3, txt(l), c)

# ---- non-blocking key/mouse reader (raw fd; split-escape safe; Ctrl+C = exit) ------
def make_keyreader(mouse=True):
    KEYS = {"w":"UP","s":"DOWN","\t":"TAB"," ":"SPACE","q":"QUIT","Q":"QUIT",
            "\r":"ENTER","\n":"ENTER","\x7f":"BACK","\x08":"BACK","?":"HELP"}
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
                        if b == 64: return (("WHEELUP", (mx-1, my-1)), chars[j:] if j < n else [])
                        if b == 65: return (("WHEELDN", (mx-1, my-1)), chars[j:] if j < n else [])
                        if seq[-1] == "M" and b in (0, 1, 2):
                            return (("CLICK", (mx-1, my-1)), chars[j:] if j < n else [])
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
            txt = "".join(c for c in chars if c.isprintable() or c == "\n")
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
            return dict(txid="f" * 64)
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
        S["ver"] = S.get("ver", 0) + 1; S["t_poll"] = time.monotonic()
        for _ in range(8):                            # 4s in slices; 'r' kicks an early refresh
            if S.pop("kick", False): break
            if stop.wait(0.5): break
            if S.get("wloading"): _tail_log(S)        # live scan progress from the local daemon log

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

def enable_scripting_in_config(path):                 # add "scripting" to ExperimentalFeatures
    try:
        cfg = json.load(open(path, encoding="utf-8-sig"))
        ef = cfg.get("ExperimentalFeatures")
        if not isinstance(ef, list): ef = []
        if not any(str(x).lower() == "scripting" for x in ef): ef.append("scripting")
        cfg["ExperimentalFeatures"] = ef
        json.dump(cfg, open(path, "w", encoding="utf-8"), indent=2)
        return True
    except Exception as e:
        print(f"could not edit {path}: {e}", file=sys.stderr)
        return False

# ---- the TUI ------------------------------------------------------------------------
TABS = ["dashboard", "wallet", "history", "coinjoin", "send", "auto", "scheme"]

HELP = ["1-6 / Tab / ←→   switch tab          w/s or ↑↓   select row",
        "enter            primary action      y           copy (address / txid)",
        "r                refresh now         ?           this help",
        ".                privacy mode: hide amounts + addresses (receive stays visible)",
        "q                quit                Ctrl+C      quit immediately",
        "",
        "any tab    g RECEIVE - label -> fresh address + scannable QR code",
        "dashboard  space/enter load wallet · n create wallet · v recover wallet",
        "wallet     k address book · x exclude coin from coinjoin · y copy address",
        "history    u speed up (fee bump) · c cancel unconfirmed tx · y copy txid",
        "coinjoin   space start/stop · o single round · b sweep to other wallet",
        "           p pay inside coinjoin · x cancel selected payment · a rules",
        "send       n add payment · i import pasted list · e edit · x remove",
        "           + / - apply no-change round-up/down (exact coin match, no change output)",
        "           u subtract-fee · enter send (live fee estimate in the confirm)",
        "auto       programmable rules: when np/pr/tot ≥ threshold -> start/single/",
        "           sweep->cold-wallet/stop · optional night window · bell on fire",
        "",
        "mouse: click tabs & rows, wheel scrolls (Linux/macOS/Windows Terminal)"]

def tui(rpc, args, frames=0):
    import threading
    interactive = sys.stdin.isatty() and frames == 0
    apply_canvas(*term_canvas(interactive or frames > 0))
    S = dict(err=None, werr=None, status=None, wallets=[], wallet=args.wallet, winfo=None,
             coins=[], history=[], pays=[], fees=None, cj_on=False, cj_coins=0, cj_status="",
             armed=False, autopw=None, single=False, single_base=0, banner=None, banner_t=0,
             rules=[], _rules_w=None, notice=None, pager=None, busy=None,
             wloading=False, logpath=getattr(args, "logpath", None),
             cfgpath=getattr(args, "cfgpath", None), loaded=set(),
             sc_out=None, sc_custom=None, sc_running=False, sc_needs_enable=False, sc_hist=[],
             sync_h=None, sync_h0=None, sync_rate=0.0, sync_t=0.0,
             t_poll=0.0, flash="", flasht=0, ver=0)
    if args.wallet: S["loaded"].add(args.wallet)
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

    def open_modal(title, fields, cb, warn=None, info=None):
        nonlocal modal
        modal = dict(title=title, fields=fields, i=0, cb=cb, warn=warn, info=info)

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
        if name == "PASTE": fl[i]["v"] += raw; return
        if raw and raw.isprintable(): fl[i]["v"] += raw

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

    def wo():                                         # watch-only wallets can't sign
        if (S.get("winfo") or {}).get("isWatchOnly"):
            flash("◇ watch-only wallet - it can't sign; open its hot counterpart", 80)
            return True
        return False

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

    def do_toggle_exclude():
        coins = S.get("coins") or []
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
                S["cj_on"] = False; S["single"] = False
            act("coinjoin stopped", fn); return
        def cb(v):
            def fn():
                rpc.call("startcoinjoin", [v.get("password", ""), True, True], wallet=S["wallet"])
                S["cj_on"] = True
            act("coinjoin started ◆", fn)
        open_modal("START COINJOIN", [dict(k="password", label="wallet password", v="", mask=True,
                   hint="mixes until everything is private, then stops")], cb)

    def do_cj_single():
        if wo(): return
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        def cb(v):
            def fn():
                base = sum(1 for h_ in S.get("history") or [] if is_cj_row(h_))
                rpc.call("startcoinjoin", [v.get("password", ""), False, True], wallet=S["wallet"])
                S["cj_on"] = True; S["single"] = True; S["single_base"] = base
                return "watching for the round to confirm"
            act("single round joined ◆", fn)
        open_modal("JOIN ONE COINJOIN ROUND",
                   [dict(k="password", label="wallet password", v="", mask=True,
                         hint="joins now; sabi auto-stops after one completed round")], cb)

    def do_cj_auto():
        nonlocal tab
        tab = 5                                       # automation now lives on the [6] auto tab

    def do_cj_sweep():
        if wo(): return
        if not S.get("wallet"): flash("load a wallet first (tab 1, enter)"); return
        others = [w for w in (S.get("wallets") or []) if w != S.get("wallet")]
        def cb(v):
            outw = v.get("output", "").strip()
            if not outw or outw == S.get("wallet"):
                flash("✗ sweep needs a DIFFERENT output wallet name", 80); return
            act("coinjoin sweep started ◆",
                lambda: rpc.call("startcoinjoinsweep", [v.get("password", ""), outw], wallet=S["wallet"]))
        open_modal("COINJOIN SWEEP  ·  mix everything to another wallet",
                   [dict(k="password", label="wallet password", v="", mask=True, hint=""),
                    dict(k="output", label="output wallet name", v=(others[0] if others else ""),
                         mask=False, hint="must be a different wallet on this daemon")], cb)

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
            return max(1.0, float(f.get(str(k), f.get(k))))
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
                queue.clear(); sug_lock.clear(); clip_copy(txid or "")
                return f"txid {short(txid or '?', 24)} (copied)"
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
            return f"fee ≈ {rate:.0f} sat/vB × ~{vs} vB = {fee:,} sats  {usd(fee)}   ({k}-block rate)"
        fields = [dict(k="feeTarget", label="fee target (blocks)", v="6", mask=False,
                       hint=fees_hint(S)),
                  dict(k="password", label="wallet password", v="", mask=True, hint="")]
        title = f"CONFIRM SEND · {len(queue)} payment(s) · {cbtc(total)} {usd(total)} · {len(coins)} coins"
        if locked: title += " · ◆ NO CHANGE"
        open_modal(title, fields, cb, warn=warn, info=fee_info)

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
                return "current: " + "  ".join(f"{k}blk={v}s/vB" for k, v in items)
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
        pulse = 0.5 + 0.5*M.sin(f*0.09)               # slow breathing (~3.3 s)
        on = S.get("cj_on")
        lcol = lerp(GREEN, GLOW, pulse) if on else lerp(BRAND, GREY, .45)
        rows = 7 if H >= 34 else 5
        cols = draw_logo(ch, col, 1, 2, rows, lcol, dimf=0.5 if on else 0.3)
        x0 = cols + 6
        put(ch, col, 1, x0, "S A B I", WHITE)
        put(ch, col, 1, x0+9, "· wasabi daemon terminal", GREY)
        st = S.get("status") or {}
        if S.get("err"):
            put(ch, col, 3, x0, "daemon unreachable: " + short(S["err"], 46), WARN)
            put(ch, col, 4, x0, "start it:  wassabee daemon   (JsonRpcServerEnabled: true)", GREY)
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
            if (S.get("winfo") or {}).get("isWatchOnly"):
                rput(ch, col, 5, W-2, "◇ WATCH-ONLY", AMBER)
        else:
            put(ch, col, 5, x0, "no wallet loaded - pick one on [1] dashboard", ORANGE)
        if S.get("busy"):                             # action in flight: spinner, never a frozen UI
            sp = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.time()*10) % 10]
            put(ch, col, 2, x0, f"{sp} {S['busy']} ...", AMBER)
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
        put(ch, col, y0, 4, "WALLETS ON THIS DAEMON   space load · l load all · n create", GREY)
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
        coins = S.get("coins") or []
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
        put(ch, col, y1, 4, f"COINS ({len(coins)})   anon · amount · confs · label · address"
            "     g receive · k addresses · x exclude · y copy", GREY)
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
        put(ch, col, y0, 4, f"HISTORY ({len(hist)})   date · amount · label · txid          y copy txid", GREY)
        vis = H - (y0+2) - 2; n = len(hist)
        if n:
            sel[2] = max(0, min(sel[2], n-1))
            if sel[2] < off[2]: off[2] = sel[2]
            if sel[2] >= off[2]+vis: off[2] = sel[2]-vis+1
            off[2] = max(0, min(off[2], max(0, n-vis)))
        for i, h_ in enumerate(hist[off[2]:off[2]+vis]):
            gi = off[2]+i; y = y0+2+i; on = (gi == sel[2])
            amt = h_.get("amount", 0); cj = is_cj_row(h_)
            dt = str(h_.get("datetime", ""))[:16].replace("T", " ")
            put(ch, col, y, 4, ("▸ " if on else "  ") + dt, WHITE if on else GREY)
            ac = GREEN if amt > 0 else (ORANGE if amt < 0 else GREY)
            put(ch, col, y, 24, f"{('+' if amt > 0 else '')+cbtc(amt):>16}", WHITE if on else ac)
            lab = str(h_.get("label", "") or "")
            if cj: put(ch, col, y, 42, "◆ coinjoin", GREEN)
            elif lab: put(ch, col, y, 42, short(lab, 14), lerp(BRAND, GREY, .3))
            put(ch, col, y, 58, short(h_.get("tx", ""), 20), lerp(BRAND, GREY, .4))
            hh = h_.get("height")                     # 2.8.0: a string ("902123" / "Mempool")
            try: htxt, hcol = f"block {int(hh):,}", GREY
            except (TypeError, ValueError):
                htxt = str(hh) if hh and str(hh).lower() not in ("mempool", "unknown", "none") else "unconfirmed"
                htxt, hcol = (htxt if htxt != "Mempool" else "unconfirmed"), ORANGE
            rput(ch, col, y, W-4, htxt, hcol)
            regions.append((y, 4, W-4, ("ROW", 2, gi)))

    def draw_coinjoin(ch, col, y0, f):
        pulse = 0.5 + 0.5*M.sin(f*0.09)
        on = S.get("cj_on")
        rows = min(13, max(8, H - y0 - 12))
        lcol = lerp(GREEN, GLOW, pulse) if on else lerp(BRAND, GREY, .35)
        cols = draw_logo(ch, col, y0+1, 4, rows, lcol, dimf=0.55 if on else 0.28)
        x0 = cols + 10
        if not S.get("wallet"):
            put(ch, col, y0+1, x0, "no wallet loaded - go to [1] dashboard and press enter", ORANGE); return
        if S.get("wloading"):
            put(ch, col, y0+1, x0, "⟳ wallet is synchronizing - coinjoin available once it finishes", AMBER); return
        stat = (S.get("cj_status") or ("In progress" if on else "Idle"))
        stat = (stat.upper() + " ◆") if on else stat.lower()
        put(ch, col, y0+1, x0, "COINJOIN  ·  ", GREY)
        put(ch, col, y0+1, x0+13, stat, clamp8(lerp(GREEN, WHITE, .4*pulse)) if on else GREY)
        pr, se, np_ = balances(S)
        put(ch, col, y0+3, x0, f"daemon says  {S.get('cj_status') or '?'}",
            GREEN if on else GREY)
        put(ch, col, y0+4, x0, f"to mix       {btc(se+np_)}", AMBER if se+np_ else GREY)
        put(ch, col, y0+5, x0, f"private      {btc(pr)}", GREEN if pr else GREY)
        mode = "single round" if S.get("single") else ("auto" if S.get("auto") else "manual")
        put(ch, col, y0+6, x0, f"mode         {mode}", lerp(BRAND, WHITE, .25))
        put(ch, col, y0+8, x0, "space start/stop   o one round   a rules   b sweep", lerp(BRAND, WHITE, .35))
        put(ch, col, y0+9, x0, "p pay inside coinjoin   x cancel payment", lerp(BRAND, WHITE, .35))
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
        put(ch, col, y0, 70, "n add · e edit · x remove · u subtract-fee · enter send", GREY)
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
                        f"   ({len(sug['up']['coins'])} coins, no change)   press +",
                        clamp8(lerp(GREEN, WHITE, .1)))
                if sug.get("dn"):
                    d = sug["dn"]["delta"]
                    put(ch, col, ys+2, 4, "◆ ▼", AMBER)
                    put(ch, col, ys+2, 9, f"round DOWN {cbtc(d):>13}  →  send {btc(total+d)}"
                        f"   ({len(sug['dn']['coins'])} coins, no change)   press -", AMBER)
                put(ch, col, ys+3, 4, "only if the receiver accepts a slightly different amount",
                    lerp(BRAND, GREY, .35))

    def draw_auto(ch, col, y0, f):
        if not S.get("wallet"):
            put(ch, col, y0+1, 4, "no wallet loaded - go to [1] dashboard and press enter", ORANGE); return
        armed = S.get("armed"); pulse = 0.5 + 0.5*M.sin(f*0.15)
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
            put(ch, col, y, 12, rule_text(rl), WHITE if on else lerp(BRAND, WHITE, .25))
            last = rl.get("last", 0)
            if last:
                ago = int(time.time() - last)
                rput(ch, col, y, W-4, f"fired {ago//60}m ago" if ago < 3600 else f"fired {ago//3600}h ago", GREY)
            regions.append((y, 4, W-4, ("ROW", 5, i)))
        yb = y0 + max(9, len(rules)+3)
        put(ch, col, yb, 4, "n new rule · e edit · space on/off · x delete · a arm/disarm", GREY)
        put(ch, col, yb+1, 4, "rules check every ~4s while sabi runs · each rule fires at most once per 10 min",
            lerp(BRAND, GREY, .3))
        put(ch, col, yb+2, 4, "sweep target may be a watch-only wallet: hot -> cold via coinjoin, "
            "coins land private", lerp(GREEN, GREY, .35))

    def draw_pager(ch, col):
        p = S["pager"]; lines = p["lines"]
        w = min(W-6, max([len(p["title"])] + [len(l) for l in lines]) + 6)
        h = min(H-4, len(lines) + 6)
        x0 = (W-w)//2; y0 = (H-h)//2; vis = h - 6
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
        w = max(56, max(len(f["label"]) + 34 for f in m["fields"]) + 8, len(m["title"]) + 8)
        w = min(w, W-4)
        inf = ""
        if m.get("info"):
            try: inf = m["info"]({f["k"]: f["v"] for f in m["fields"]}) or ""
            except Exception: inf = ""
        h = 4 + 3*len(m["fields"]) + (2 if m.get("warn") else 0) + (2 if inf else 0) + 2
        x0 = (W-w)//2; y0 = max(1, (H-h)//2)
        for yy in range(y0, min(y0+h, H)):
            for xx in range(x0, min(x0+w, W)):
                ch[yy][xx] = " "; col[yy][xx] = (16, 18, 26)
        draw_box(ch, col, y0, x0, w, h, lerp(BRAND, WHITE, .3))
        put(ch, col, y0+1, x0+(w-len(m["title"]))//2, m["title"], WHITE)
        yy = y0+3
        if inf:
            put(ch, col, yy, x0+3, inf[:w-6], lerp(AMBER, WHITE, .25)); yy += 2
        if m.get("warn"):
            put(ch, col, yy, x0+3, m["warn"][:w-6], WARN); yy += 2
        for i, fld in enumerate(m["fields"]):
            onf = (i == m["i"])
            put(ch, col, yy, x0+3, fld["label"], WHITE if onf else GREY)
            v = fld["v"]; shown = ("•"*len(v)) if fld.get("mask") else v
            maxw = w - 8
            if len(shown) > maxw: shown = "…" + shown[-(maxw-1):]
            put(ch, col, yy+1, x0+3, shown + ("▌" if onf else " "), lerp(GREEN, WHITE, .3) if onf else GREY)
            if fld.get("hint") and onf:
                put(ch, col, yy+1, x0+3+len(shown)+3, fld["hint"][:w-10-len(shown)], lerp(BRAND, GREY, .3))
            yy += 3
        put(ch, col, y0+h-2, x0+3, "enter ok · tab next field · esc cancel", GREY)

    # ---------- main loop --------------------------------------------------------------
    HINTS = ["space load · l load ALL · n create · v recover · g receive · 1-7 tabs · ? help · q",
             "w/s coins · g receive · k addresses · x exclude · y copy · ? help",
             "w/s scroll · u speed up · c cancel · y copy txid · ? help",
             "space start/stop · o one round · a rules · b sweep · p pay-in-cj · ? help",
             "n add · i import · +/- no-change round · e edit · x remove · enter send · ? help",
             "n new rule · e edit · space on/off · x delete · a arm/disarm · ? help",
             "w/s pick · enter run · e edit/paste · x enable scripting · l load all wallets · ? help"]
    try:
        f = 0
        while frames == 0 or f < frames:
            ev = getkey() if interactive else None
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
                    modal_key(name, raw)
                elif helpon:
                    helpon = False
                elif name == "QUIT":
                    break
                elif name == "HELP": helpon = True
                elif name and name in "1234567": tab = int(name)-1
                elif name == "TAB": tab = (tab+1) % 7
                elif name == "STAB": tab = (tab-1) % 7
                elif name == "RIGHT": tab = (tab+1) % 7
                elif name == "LEFT": tab = (tab-1) % 7
                elif name == "UP": sel[tab] = max(0, sel[tab]-1)
                elif name == "DOWN": sel[tab] = min(max(0, len(lists_for_tab(tab))-1), sel[tab]+1)
                elif name in ("WHEELUP", "WHEELDN"):
                    d = -3 if name == "WHEELUP" else 3
                    sel[tab] = max(0, min(max(0, len(lists_for_tab(tab))-1), sel[tab]+d))
                elif name == "CLICK":
                    mx, my = raw
                    for (ry, rx0, rx1, tok) in regions:
                        if my == ry and rx0 <= mx <= rx1:
                            if tok[0] == "TAB": tab = tok[1]
                            elif tok[0] == "ROW":
                                _, tt, ii = tok
                                tab = tt; sel[tt] = ii
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
                    if r == "r": S["kick"] = True; flash("refreshing ...", 20)
                    elif r == ".":                                # privacy mode: hide amounts + addresses
                        DISCREET["on"] = not DISCREET["on"]
                        flash("privacy mode ON - amounts & addresses hidden ('.' reveals)" if DISCREET["on"]
                              else "privacy mode off - everything visible", 70)
                    elif r == "g": do_receive()                   # receive works from every tab
                    elif r == "l" and tab in (0, 6): do_load_all()   # load every wallet
                    elif tab == 0 and r == "n": do_create_wallet()
                    elif tab == 0 and r == "v": do_recover_wallet()
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
                    elif tab == 5 and r == "a": do_arm()
                    elif tab == 1 and r == "x": do_toggle_exclude()
                    elif tab == 1 and r == "k": do_list_keys()
                    elif tab == 4 and r == "i": do_import()
                    elif tab == 1 and r == "y":
                        coins = S.get("coins") or []
                        if coins:
                            ok = clip_copy(coins[sel[1] % len(coins)].get("address", ""))
                            flash("address copied ✓" if ok else "clipboard unavailable")
                    elif tab == 2 and r == "y":
                        hist = S.get("history") or []
                        if hist:
                            ok = clip_copy(hist[sel[2] % len(hist)].get("tx", ""))
                            flash("txid copied ✓" if ok else "clipboard unavailable")
                    elif tab == 3 and r == "o": do_cj_single()
                    elif tab == 3 and r == "a": do_cj_auto()
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
                if (nw, nh) != (W, H): apply_canvas(nw, nh); o("\x1b[2J")
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
            hint = S["flash"] if S.get("flasht", 0) > 0 else HINTS[tab]
            if S.get("werr"): hint = "wallet rpc: " + short(S["werr"], 60)
            hcol = (WARN if hint.startswith(("✗", "wallet rpc"))
                    else clamp8(lerp(GREEN, WHITE, .25)) if hint.startswith(("◆", "✓")) or "✓" in hint[:40]
                    else lerp(BRAND, WHITE, .4))
            put(ch, col, H-2, max(0, (W-len(hint))//2), hint, hcol)
            st_ = S.get("status") or {}                # status footer strip
            okc = GREEN if not S.get("err") else WARN
            put(ch, col, H-1, 2, "●", okc)
            put(ch, col, H-1, 4, (f"#{st_.get('bestBlockchainHeight', '?')}" if not S.get("err")
                                  else "offline"), GREY)
            tag = ("sabi · wasabi daemon terminal · coinjoin.nl" if W >= 150 else "sabi · coinjoin.nl")
            put(ch, col, H-1, max(0, (W-len(tag))//2), tag, lerp(BRAND, WHITE, .25))
            age = int(time.monotonic() - S.get("t_poll", time.monotonic()))
            eye = "● hidden" if DISCREET["on"] else "○ visible"
            rput(ch, col, H-1, W-2, f"'.' {eye} · sync {age}s · {time.strftime('%H:%M')}",
                 lerp(AMBER, GREY, .3) if DISCREET["on"] else GREY)
            if S.get("flasht", 0) > 0: S["flasht"] -= 1
            if helpon: draw_overlay(ch, col, "SABI · keys", HELP)
            elif S.get("notice"):
                nt = S["notice"]; draw_overlay(ch, col, nt["title"], nt["lines"], tcol=GREEN)
            elif S.get("pager"): draw_pager(ch, col)
            elif modal: draw_modal(ch, col)
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
                    log=os.path.join(os.path.dirname(p), "Logs.txt"))
    return None

def find_daemon(extra=None):                          # locate the Wasabi daemon executable
    import shutil as _sh
    cands = [extra] if extra else []
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

def launch_daemon(exe):                               # start it detached; it logs to its own Logs.txt
    import subprocess
    try:
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED | NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        subprocess.Popen([exe], **kw)
        return True
    except Exception as e:
        print(f"could not start the daemon: {e}", file=sys.stderr)
        return False

def enable_rpc_in_config(path):                       # flip JsonRpcServerEnabled with minimal edit
    try:
        raw = open(path, encoding="utf-8-sig").read()
        new, n = re.subn(r'("JsonRpcServerEnabled"\s*:\s*)false', r"\1true", raw, count=1)
        if n == 0:
            cfg = json.load(open(path, encoding="utf-8-sig"))
            cfg["JsonRpcServerEnabled"] = True
            new = json.dumps(cfg, indent=2)
        open(path, "w", encoding="utf-8").write(new)
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
    a.logpath = None; a.cfgpath = None
    if not a.demo:
        cfg = wasabi_config()                          # zero-config: use the daemon's own settings
        if cfg: a.logpath = cfg.get("log"); a.cfgpath = cfg.get("path")   # log = sync progress; cfg = toggles
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
                else:
                    print("wasabi daemon executable not found - start it yourself, or pass "
                          "--daemon PATH so sabi can.", file=sys.stderr)
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
    if a.demo: a.cfgpath = os.path.join(tempfile.gettempdir(), "sabi-demo-config.json")
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
