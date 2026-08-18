# RelayMe - ad builder v2
#
# Captures the real live site in Chromium and renders a vertical ad. Nothing is
# a mockup: every screen is the actual page, photographed in a headless browser.
#
#   python make_ad.py nine-services
#   python make_ad.py one-page
#   python make_ad.py giveaway
#   python make_ad.py --assets      (re-make the mascot and doodles only)
#
# Needs, once:  pip install playwright pillow requests
#               python -m playwright install chromium
#               ffmpeg on PATH, or set $env:FFMPEG to its full path
#
# --------------------------------------------------------------------------
# WHAT CHANGED IN v2
#
# TRANSITIONS. Shots no longer cut. The screen content slides up and cross-fades
# inside the phone while the shell, background and doodles stay put, and the
# caption fades and rises separately. An earlier version dissolved the WHOLE
# frame, which ghosted badly - two near-identical pages blended into each other
# and doubled every caption. Moving only what actually changes fixes that.
#
# MASCOT AND DOODLES. The mascot is rasterised from its own vector source in the
# repo (app/blob.tsx), never re-drawn by hand - so it cannot drift from the one
# the product uses. The doodles are the app's own sticker set, pulled from
# public/stickers. Both are fetched by the --assets step and cached in _assets.
#
# The mascot bobs, its antenna sways and it blinks. Doodles drift and rotate on
# their own slow cycles. Everything is a sine function of the frame index, so
# nothing needs a timeline and nothing can desync.
# --------------------------------------------------------------------------

import math
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(HERE, "Manrope.ttf")
ASSETS = os.path.join(HERE, "_assets")

PAGE = "https://relayme.bio/relayme.bio"
HOME = "https://relayme.bio/"
REDEEM = "https://relayme.bio/redeem"
RAW = "https://raw.githubusercontent.com/clearpathadvisory/relay-app/main"

# Output shapes. Only the canvas and the phone size change - the captures are
# the same either way, so a second format is a re-render, never a re-capture.
#
# SCREEN_W is capped by height, not taste: the phone is 0.461 wide-to-tall, so
# on a square canvas a 560px screen would be 1214px tall and run off the bottom.
FORMATS = {
    "9:16": {"w": 1080, "h": 1920, "screen": 560, "dec": 1.00},   # Reels, TikTok, Shorts
    "4:5":  {"w": 1080, "h": 1350, "screen": 440, "dec": 0.90},   # Instagram feed
    "1:1":  {"w": 1080, "h": 1080, "screen": 340, "dec": 0.78},   # square feed
}
FORMAT = "9:16"     # overridden by the second argument on the command line

W, H = 1080, 1920
FPS = 30
TRANS = 12          # transition length in frames (0.4s)

INK = (27, 13, 68)
VIO = (119, 86, 226)
LIME = (198, 241, 92)
WHITE = (255, 255, 255)
SHELL = (14, 9, 30)
RIM = (86, 74, 128)

# 430x932 is a current iPhone's logical viewport - ratio 0.461. An earlier cut
# used 520x924, which is 9:16 (0.563), and no amount of resizing made that look
# like a phone. It was the wrong shape, not the wrong size.
VIEW_W, VIEW_H = 430, 932
SCREEN_W = 560
BEZEL, SIDE = 11, 8
CORNER, SCREEN_CORNER = 84, 74
PHONE_Y = 268
SAFE = 80
DEC = 1.0           # decoration scale - doodles and mascot


def set_format(name):
    """Everything positional is a fraction of the canvas, so a new shape is a
    table entry rather than a hunt through the file for hard-coded pixels."""
    global W, H, SCREEN_W, BEZEL, SIDE, CORNER, SCREEN_CORNER, PHONE_Y, SAFE, DEC, FORMAT
    f = FORMATS[name]
    FORMAT = name
    W, H = f["w"], f["h"]
    SCREEN_W = f["screen"]
    DEC = f["dec"]
    sc = SCREEN_W / 560.0
    BEZEL = max(6, round(11 * sc))
    SIDE = max(5, round(8 * sc))
    CORNER = round(84 * sc)
    SCREEN_CORNER = round(74 * sc)
    PHONE_Y = round(0.1396 * H)
    SAFE = round(0.074 * W)


def fy(frac):
    return round(frac * H)


def fx(frac):
    return round(frac * W)

# The doodles the app itself ships, so an ad cannot show a shape the product
# does not have.
STICKERS = ["cs-star-1", "cs-flower-2", "cs-misc-1", "cs-wheel-1", "heart", "cs-ellipse-1"]

# The mascot, copied verbatim from app/blob.tsx. If it changes there, change it
# here - do not redraw it by eye.
MASCOT_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 170" width="640" height="680">
<path d="M80 18 L80 6" stroke="#1B0D44" stroke-width="4" stroke-linecap="round"/>
<circle cx="80" cy="4" r="5" fill="#C6F15C"/>
<path d="M80 18 C118 18 136 46 136 84 C136 122 114 146 80 146 C46 146 24 122 24 84 C24 46 42 18 80 18 Z" fill="#B0A0FF"/>
<circle cx="44" cy="92" r="6" fill="#F0A2FD" opacity="0.8"/>
<circle cx="116" cy="92" r="6" fill="#F0A2FD" opacity="0.8"/>
<circle cx="62" cy="76" r="8" fill="#1B0D44"/><circle cx="64.5" cy="73" r="2.6" fill="#fff"/>
<circle cx="98" cy="76" r="8" fill="#1B0D44"/><circle cx="100.5" cy="73" r="2.6" fill="#fff"/>
<path d="M66 98 Q80 112 94 98" stroke="#1B0D44" stroke-width="5" fill="none" stroke-linecap="round"/>
</svg>"""

# Same mascot with its eyes shut, for the blink.
BLINK_SVG = MASCOT_SVG.replace(
    '<circle cx="62" cy="76" r="8" fill="#1B0D44"/><circle cx="64.5" cy="73" r="2.6" fill="#fff"/>',
    '<path d="M54 76 Q62 82 70 76" stroke="#1B0D44" stroke-width="5" fill="none" stroke-linecap="round"/>'
).replace(
    '<circle cx="98" cy="76" r="8" fill="#1B0D44"/><circle cx="100.5" cy="73" r="2.6" fill="#fff"/>',
    '<path d="M90 76 Q98 82 106 76" stroke="#1B0D44" stroke-width="5" fill="none" stroke-linecap="round"/>'
)


ADS = {
    "nine-services": {
        "end": ["Nine services.", "One page.",
                "They play right on it -", "nobody has to leave.", 105],
        "shots": [
            {"url": PAGE, "caption": "One link in your bio.", "frames": 60},
            {"url": PAGE, "click_poster": 0, "caption": "TIDAL", "frames": 31},
            {"url": PAGE, "click_poster": 1, "caption": "Deezer", "frames": 31},
            {"url": PAGE, "click_poster": 2, "caption": "Spotify", "frames": 31},
            {"url": PAGE, "click_poster": 3, "caption": "YouTube", "frames": 32},
            {"url": PAGE, "click_poster": 5, "caption": "Apple Music", "frames": 32},
            {"url": PAGE, "click_poster": 6, "caption": "SoundCloud", "frames": 32},
            {"url": PAGE, "click_poster": 7, "caption": "Bandcamp", "frames": 32},
            {"url": PAGE, "click_poster": 8, "caption": "Mixcloud", "frames": 32},
            {"url": PAGE, "click_poster": 9, "caption": "Audiomack", "frames": 32},
        ],
    },
    "one-page": {
        "end": ["Everything you make.", "One link.",
                "Free to start -", "Pro when you need it.", 90],
        "shots": [
            {"url": PAGE, "caption": "Your name up top.", "frames": 60},
            {"url": PAGE, "scroll": {"frac": 0.22}, "caption": "Every link you have.", "frames": 60},
            {"url": PAGE, "click_poster": 2, "caption": "Music plays here.", "frames": 60},
            {"url": PAGE, "click_poster": 3, "caption": "So does video.", "frames": 60},
            {"url": PAGE, "scroll": {"frac": 0.86}, "caption": "Collect emails too.", "frames": 60},
            {"url": PAGE, "scroll": "top", "caption": "One address for all of it.", "frames": 60},
        ],
    },
    # A quiet feature tour rather than an advert: real hand, real page, no
    # pitch and no call to action. Light ground, because a photographed hand on
    # a dark gradient looks pasted on rather than held.
    "features": {
        "style": "hand",
        "shots": [
            {"url": PAGE, "caption": "Your face, your name.", "frames": 66},
            {"url": PAGE, "scroll": {"frac": 0.20}, "caption": "Links in your order.", "frames": 66},
            {"url": PAGE, "click_poster": 2, "caption": "Music plays in place.", "frames": 66},
            {"url": PAGE, "click_poster": 3, "caption": "So does video.", "frames": 66},
            {"url": PAGE, "scroll": {"frac": 0.62}, "caption": "Every link keeps its icon.", "frames": 66},
            {"url": PAGE, "scroll": {"frac": 0.86}, "caption": "Collect emails.", "frames": 60},
        ],
    },
    # Three more quiet tours. Same hand, same light ground, no pitch. Captions
    # describe only what is actually on screen - checked against the page's own
    # data, which is why none of these says "thumbnails": no link on the
    # official page has an image set, so the row icons are favicons.
    "features-players": {
        "style": "hand",
        "shots": [
            {"url": PAGE, "click_poster": 2, "caption": "Spotify.", "frames": 44},
            {"url": PAGE, "click_poster": 0, "caption": "TIDAL.", "frames": 44},
            {"url": PAGE, "click_poster": 1, "caption": "Deezer.", "frames": 44},
            {"url": PAGE, "click_poster": 5, "caption": "Apple Music.", "frames": 44},
            {"url": PAGE, "click_poster": 6, "caption": "SoundCloud.", "frames": 44},
            {"url": PAGE, "click_poster": 7, "caption": "Bandcamp.", "frames": 44},
            {"url": PAGE, "click_poster": 8, "caption": "Mixcloud.", "frames": 44},
            {"url": PAGE, "click_poster": 9, "caption": "Audiomack.", "frames": 44},
            {"url": PAGE, "click_poster": 3, "caption": "And video.", "frames": 48},
        ],
    },

    "features-layout": {
        "style": "hand",
        "shots": [
            {"url": PAGE, "caption": "Socials across the top.", "frames": 68},
            {"url": PAGE, "scroll": {"frac": 0.14}, "caption": "Headings to group things.", "frames": 68},
            {"url": PAGE, "scroll": {"frac": 0.34}, "caption": "Dividers to break them up.", "frames": 68},
            {"url": PAGE, "scroll": {"frac": 0.62}, "caption": "Every link keeps its icon.", "frames": 68},
            {"url": PAGE, "scroll": {"frac": 0.86}, "caption": "A sign-up form at the foot.", "frames": 68},
        ],
    },

    # Four posts published, so this one is honest about being small.
    "features-blog": {
        "style": "hand",
        "shots": [
            {"url": "https://relayme.bio/blog", "caption": "There is a blog too.", "frames": 70},
            {"url": "https://relayme.bio/blog/how-to-claim-your-relayme-username",
             "caption": "Written, not generated slop.", "frames": 70},
            {"url": "https://relayme.bio/blog/how-long-until-first-sale",
             "scroll": {"frac": 0.30}, "caption": "Plain answers to real questions.", "frames": 70},
            {"url": "https://relayme.bio/vs-linktree",
             "caption": "Including where we fall short.", "frames": 80},
        ],
    },

    # Runs until 30 September 2026. Twenty judged winners - "best answers win"
    # is what keeps this a Polish konkurs rather than a loteria promocyjna
    # needing a permit. Do not reword it as a prize draw.
    "giveaway": {
        "end": ["A year of Pro,", "on us.",
                "Twenty best answers win -", "enter by 30 September.", 105],
        "shots": [
            {"url": HOME, "caption": "Twenty people get Pro.", "frames": 75},
            {"url": HOME, "scroll": {"selector": "h2.sech"},
             "caption": "Free for a whole year.", "frames": 75},
            {"url": REDEEM, "caption": "Winners get a code.", "frames": 95},
        ],
    },
}


# ---------------------------------------------------------------- assets

def build_assets():
    """Mascot from its own vector source, doodles from the app's sticker set.
    Chromium does the rasterising, so there is no extra dependency to install -
    it is already here for the capture."""
    import urllib.request
    from playwright.sync_api import sync_playwright

    os.makedirs(ASSETS, exist_ok=True)
    for name in STICKERS:
        dest = os.path.join(ASSETS, name + ".webp")
        if not os.path.exists(dest):
            urllib.request.urlretrieve(RAW + "/public/stickers/" + name + ".webp", dest)
            print("  doodle", name)

    build_hand()

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 640, "height": 680}, device_scale_factor=1)
        for svg, out in ((MASCOT_SVG, "mascot.png"), (BLINK_SVG, "mascot-blink.png")):
            dest = os.path.join(ASSETS, out)
            if os.path.exists(dest):
                continue
            pg.set_content(
                '<body style="margin:0;background:transparent">' + svg + "</body>")
            pg.wait_for_timeout(200)
            # omit_background is what keeps the alpha; without it the mascot
            # arrives on a white block and sits on the gradient like a sticker
            # someone forgot to cut out.
            pg.screenshot(path=dest, omit_background=True)
            print("  mascot", out)
        b.close()


HAND_SRC = "hand-mockup.jpg"   # drop the mockup next to this script


def build_hand():
    """Turn the hand mockup into a usable asset.

    The file arrives as a JPEG with the transparency checkerboard baked in as
    real pixels, so it has to be keyed. Two flood fills do it: one from the
    border, which clears the checkerboard around the hand, and one inside the
    bezel, which finds the screen. The screen survives the first fill because it
    is enclosed - that is the whole trick, and it is why this works on a file
    that has no alpha channel at all.

    Cached, so it only runs once."""
    src = os.path.join(HERE, HAND_SRC)
    if not os.path.exists(src):
        print("  ! no", HAND_SRC, "- the hand ads will not build")
        return
    out_png = os.path.join(ASSETS, "hand.png")
    out_box = os.path.join(ASSETS, "hand-screen.txt")
    if os.path.exists(out_png) and os.path.exists(out_box):
        return

    from collections import deque
    im = Image.open(src).convert("RGB")
    w, h = im.size
    px = im.load()

    def isbg(c):
        return min(c) > 232 and (max(c) - min(c)) < 12

    alpha = Image.new("L", (w, h), 255)
    ap = alpha.load()
    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        q.append((x, 0)); q.append((x, h - 1))
    for y in range(h):
        q.append((0, y)); q.append((w - 1, y))
    while q:
        x, y = q.popleft()
        i = y * w + x
        if seen[i]:
            continue
        seen[i] = 1
        if not isbg(px[x, y]):
            continue
        ap[x, y] = 0
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                q.append((nx, ny))

    # the screen: the biggest patch of light pixels the border fill could not
    # reach, found by flooding outward from each unvisited light pixel
    best, bestpx = None, 0
    done = bytearray(w * h)
    for sy in range(0, h, 4):
        for sx in range(0, w, 4):
            i = sy * w + sx
            if done[i] or ap[sx, sy] == 0 or not isbg(px[sx, sy]):
                continue
            comp, qq, n = [], deque([(sx, sy)]), 0
            x0 = y0 = 10 ** 9; x1 = y1 = -1
            while qq:
                x, y = qq.popleft()
                j = y * w + x
                if done[j]:
                    continue
                done[j] = 1
                if ap[x, y] == 0 or not isbg(px[x, y]):
                    continue
                n += 1
                if x < x0: x0 = x
                if y < y0: y0 = y
                if x > x1: x1 = x
                if y > y1: y1 = y
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and not done[ny * w + nx]:
                        qq.append((nx, ny))
            if n > bestpx:
                bestpx, best = n, (x0, y0, x1, y1)

    x0, y0, x1, y1 = best
    print("  hand screen %dx%d ratio %.3f" % (x1 - x0 + 1, y1 - y0 + 1,
                                              (x1 - x0 + 1) / float(y1 - y0 + 1)))
    # knock the screen out so page content shows through from behind
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if isbg(px[x, y]) and ap[x, y] > 0:
                ap[x, y] = 0

    alpha = alpha.filter(ImageFilter.GaussianBlur(0.6))
    hand = im.convert("RGBA")
    hand.putalpha(alpha)
    hand.save(out_png)
    open(out_box, "w").write("%d %d %d %d" % (x0, y0, x1, y1))
    print("  hand.png written")


def load_assets():
    a = {"doodles": [], "mascot": None, "blink": None, "hand": None, "hand_box": None}
    hp = os.path.join(ASSETS, "hand.png")
    hb = os.path.join(ASSETS, "hand-screen.txt")
    if os.path.exists(hp) and os.path.exists(hb):
        a["hand"] = Image.open(hp).convert("RGBA")
        a["hand_box"] = tuple(int(v) for v in open(hb).read().split())
    for name in STICKERS:
        p = os.path.join(ASSETS, name + ".webp")
        if os.path.exists(p):
            a["doodles"].append(Image.open(p).convert("RGBA"))
    for key, f in (("mascot", "mascot.png"), ("blink", "mascot-blink.png")):
        p = os.path.join(ASSETS, f)
        if os.path.exists(p):
            a[key] = Image.open(p).convert("RGBA")
    return a


# ---------------------------------------------------------------- capture

def capture(ad, shots_dir):
    from playwright.sync_api import sync_playwright

    os.makedirs(shots_dir, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(
            viewport={"width": VIEW_W, "height": VIEW_H},
            device_scale_factor=3,
            locale="en-GB",   # without this the player chrome comes back Polish
        )
        for i, shot in enumerate(ad["shots"]):
            # Every shot reloads. Only one embed plays at a time, so leaving the
            # last one open would close it halfway through this capture.
            pg.goto(shot["url"], wait_until="networkidle", timeout=60000)
            pg.wait_for_timeout(2200)

            if "click_poster" in shot:
                btn = pg.locator("button.embedposter").nth(shot["click_poster"])
                btn.scroll_into_view_if_needed()
                pg.wait_for_timeout(350)
                btn.click()
                # Third-party players draw on their own schedule. Under about
                # three seconds the shot catches a blank rectangle instead.
                pg.wait_for_timeout(3400)
                # Centre it deliberately. scroll_into_view_if_needed only
                # promises the element is visible somewhere, which made an
                # earlier cut drift down the page as it went on.
                try:
                    pg.locator(".embedframe").first.evaluate(
                        "el => el.scrollIntoView({block: 'center'})")
                except Exception:
                    pass

            sc = shot.get("scroll")
            if isinstance(sc, dict) and "selector" in sc:
                try:
                    pg.locator(sc["selector"]).first.evaluate(
                        "el => el.scrollIntoView({block: 'center'})")
                except Exception:
                    print("  ! selector missed:", sc["selector"])
            elif isinstance(sc, dict) and "frac" in sc:
                pg.evaluate("f => window.scrollTo(0, (document.body.scrollHeight"
                            " - window.innerHeight) * f)", sc["frac"])
            elif sc == "top":
                pg.evaluate("() => window.scrollTo(0, 0)")

            pg.wait_for_timeout(shot.get("wait", 900))
            pg.screenshot(path=os.path.join(shots_dir, "%02d.png" % i))
            print("  captured %02d - %s" % (i, shot["caption"]))
        b.close()


# ---------------------------------------------------------------- type

def get_font_file():
    if os.path.exists(FONT_PATH):
        return FONT_PATH
    print("fetching Manrope...")
    import requests
    r = requests.get("https://github.com/google/fonts/raw/main/ofl/manrope/"
                     "Manrope%5Bwght%5D.ttf", timeout=60)
    r.raise_for_status()
    with open(FONT_PATH, "wb") as f:
        f.write(r.content)
    return FONT_PATH


def font(size, weight=700):
    f = ImageFont.truetype(get_font_file(), size)
    # Manrope is a variable font; without this every caption renders regular.
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


_probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))


def text_w(text, f):
    b = _probe.textbbox((0, 0), text, font=f)
    return b[2] - b[0]


def fitted(text, size, weight, max_w=W - SAFE * 2):
    """Nothing may touch the edge. Captions are written by hand in ADS, so
    sooner or later one is wider than the canvas - "Everything you make." at
    104px ran off both sides. Shrink until it fits rather than trusting whoever
    writes the next one to count characters."""
    f = font(size, weight)
    while size > 18 and text_w(text, f) > max_w:
        size -= 3
        f = font(size, weight)
    return f


# ---------------------------------------------------------------- the phone

def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0], size[1]], radius, fill=255)
    return m


def prepare_screen(shot_path):
    """The screenshot at screen size, edges faded into the page's own colour."""
    s = Image.open(shot_path).convert("RGB")
    s = s.resize((SCREEN_W, int(s.height * SCREEN_W / s.width)), Image.LANCZOS)
    sw, sh = s.size

    alpha = Image.new("L", (sw, sh), 255)
    d = ImageDraw.Draw(alpha)
    top, bottom = 50, 110
    for i in range(top):
        d.line([(0, i), (sw, i)], fill=int(255 * (i / top)))
    for i in range(bottom):
        d.line([(0, sh - 1 - i), (sw, sh - 1 - i)], fill=int(255 * (i / bottom)))

    # Fade into the page's OWN colour, sampled from its top and bottom rows. An
    # earlier version faded to near-black, which on a light theme did not read
    # as depth - it read as a dark band across the screen.
    px = s.load()

    def row_avg(y):
        r = g = b = 0
        xs = range(0, sw, 7)
        for x in xs:
            c = px[x, y]
            r += c[0]; g += c[1]; b += c[2]
        n = len(list(xs))
        return (r // n, g // n, b // n)

    c_top, c_bot = row_avg(0), row_avg(sh - 1)
    base = Image.new("RGB", (sw, sh))
    bd = ImageDraw.Draw(base)
    for y in range(sh):
        t = y / sh
        bd.line([(0, y), (sw, y)], fill=(
            int(c_top[0] + (c_bot[0] - c_top[0]) * t),
            int(c_top[1] + (c_bot[1] - c_top[1]) * t),
            int(c_top[2] + (c_bot[2] - c_top[2]) * t)))
    base.paste(s, (0, 0), alpha)
    return base


def phone_shell(screen_size):
    """Everything except the screen: shell, rim, island, buttons, shadow.
    Built once and reused for every frame - it never changes."""
    sw, sh = screen_size
    bw, bh = sw + BEZEL * 2, sh + BEZEL * 2
    ow, oh = bw + SIDE * 2, bh
    pad = 60

    shell = Image.new("RGBA", (ow + pad * 2, oh + pad * 2), (0, 0, 0, 0))

    shadow = Image.new("L", (ow + pad * 2, oh + pad * 2), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(
        [pad + SIDE, pad + 26, pad + SIDE + bw, pad + bh + 26], CORNER, fill=140)
    shell.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(30)))

    d = ImageDraw.Draw(shell)
    ox, oy = pad, pad
    b = SCREEN_W / 560.0   # buttons sit at the same place on any size of shell
    d.rounded_rectangle([ox, oy + 300 * b, ox + SIDE + 6, oy + 416 * b], 6, fill=(46, 38, 74, 255))
    d.rounded_rectangle([ox, oy + 448 * b, ox + SIDE + 6, oy + 564 * b], 6, fill=(46, 38, 74, 255))
    d.rounded_rectangle([ox + ow - SIDE - 6, oy + 372 * b, ox + ow, oy + 540 * b], 6,
                        fill=(46, 38, 74, 255))
    d.rounded_rectangle([ox + SIDE, oy, ox + SIDE + bw, oy + bh], CORNER, fill=SHELL + (255,))
    d.rounded_rectangle([ox + SIDE, oy, ox + SIDE + bw, oy + bh], CORNER,
                        outline=RIM + (255,), width=3)
    return shell, (pad + SIDE + BEZEL, pad + BEZEL), (ow + pad * 2, oh + pad * 2)


def island_layer(size, origin, screen_size):
    """Drawn AFTER the screen, or the screen covers it."""
    lay = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    cx = origin[0] + screen_size[0] // 2
    top = origin[1] + 2
    b = SCREEN_W / 560.0
    d.rounded_rectangle([cx - 78 * b, top + 11 * b, cx + 78 * b, top + 45 * b],
                        max(6, round(17 * b)), fill=(0, 0, 0, 255))
    return lay


# ---------------------------------------------------------------- decoration

LIGHT_A = (251, 250, 249)   # brand base
LIGHT_B = (226, 216, 250)   # a wash of violet, nothing more


def light_bg():
    im = Image.new("RGB", (W, H), LIGHT_A)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(
            int(LIGHT_A[0] + (LIGHT_B[0] - LIGHT_A[0]) * t),
            int(LIGHT_A[1] + (LIGHT_B[1] - LIGHT_A[1]) * t),
            int(LIGHT_A[2] + (LIGHT_B[2] - LIGHT_A[2]) * t)))
    return im


def gradient_bg():
    im = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(
            int(INK[0] + (VIO[0] - INK[0]) * t),
            int(INK[1] + (VIO[1] - INK[1]) * t),
            int(INK[2] + (VIO[2] - INK[2]) * t)))
    return im


# Where the doodles live, how big, how fast they drift and spin. Kept out at the
# margins on purpose - the phone is the subject, these are the confetti.
DOODLE_PLAN = [
    {"i": 0, "x": 0.109, "y": 0.245, "size": 0.111, "bob": 16, "spin": 10, "phase": 0.0},
    {"i": 1, "x": 0.826, "y": 0.333, "size": 0.122, "bob": 20, "spin": -8, "phase": 1.7},
    {"i": 2, "x": 0.098, "y": 0.615, "size": 0.104, "bob": 14, "spin": 12, "phase": 3.1},
    {"i": 3, "x": 0.839, "y": 0.682, "size": 0.115, "bob": 18, "spin": -11, "phase": 4.4},
    {"i": 4, "x": 0.156, "y": 0.432, "size": 0.089, "bob": 22, "spin": 7, "phase": 2.3},
    {"i": 5, "x": 0.793, "y": 0.516, "size": 0.096, "bob": 15, "spin": -9, "phase": 5.2},
]


def draw_doodles(im, assets, f):
    """Everything is a sine of the frame index, so there is no timeline to keep
    in sync and no state to get wrong. Different phases stop them pulsing in
    unison, which reads as a glitch rather than as motion."""
    for plan in DOODLE_PLAN:
        if plan["i"] >= len(assets["doodles"]):
            continue
        d = assets["doodles"][plan["i"]]
        t = f / FPS
        size = plan["size"] * W * DEC
        dy = int(math.sin(t * 0.9 + plan["phase"]) * plan["bob"] * DEC)
        dx = int(math.cos(t * 0.6 + plan["phase"]) * plan["bob"] * 0.5 * DEC)
        ang = math.sin(t * 0.4 + plan["phase"]) * plan["spin"]
        sc = 1.0 + 0.06 * math.sin(t * 1.1 + plan["phase"])
        w = max(8, int(size * sc))
        d2 = d.resize((w, max(8, int(d.height * w / d.width))), Image.LANCZOS)
        d2 = d2.rotate(ang, resample=Image.BICUBIC, expand=True)
        im.paste(d2, (fx(plan["x"]) - d2.width // 2 + dx,
                      fy(plan["y"]) - d2.height // 2 + dy), d2)


def draw_mascot(im, assets, f, x, y, size=190):
    """Bobs, sways and blinks. The blink is a second rasterisation of the same
    vector rather than eyelids drawn over the top, so it stays on-model."""
    if not assets["mascot"]:
        return
    t = f / FPS
    # a blink roughly every 3.4s, lasting 4 frames
    phase = (f % int(FPS * 3.4))
    src = assets["blink"] if (assets["blink"] and phase < 4) else assets["mascot"]
    m = src.resize((size, int(src.height * size / src.width)), Image.LANCZOS)
    m = m.rotate(math.sin(t * 1.2) * 5, resample=Image.BICUBIC, expand=True)
    dy = int(math.sin(t * 1.6) * 12 * DEC)
    im.paste(m, (x - m.width // 2, y - m.height // 2 + dy), m)


def ease(t):
    """Smootherstep. Linear movement between shots looks mechanical; this
    starts and ends at rest, which is what makes it read as smooth."""
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


# ---------------------------------------------------------------- render

def end_card(lines, assets, f):
    im = Image.new("RGB", (W, H), LIME)
    draw_mascot(im, assets, f, W // 2, fy(0.235), round(0.213 * W * DEC))
    d = ImageDraw.Draw(im)
    # Both headline lines share one size, taken from the longer, so they stay a
    # matched pair instead of one shrinking away from the other.
    hs = 104
    for ln in lines[:2]:
        f2 = font(hs, 800)
        while hs > 40 and text_w(ln, f2) > W - SAFE * 2:
            hs -= 3
            f2 = font(hs, 800)
    # On a square canvas the headline needs to come down a size, or two 104px
    # lines eat the room the subtitle needs.
    hs = min(hs, round(0.096 * H))
    hf = font(hs, 800)
    sub_f = fitted(lines[2], round(0.041 * H), 500)
    sub_h = hf.size  # line step for the subtitle pair

    def ctr(text, y, fnt, fill=INK):
        d.text(((W - text_w(text, fnt)) // 2, y), text, font=fnt, fill=fill)

    # Stacked from the headline rather than from fixed fractions, so the block
    # can never overlap itself when the type resizes for a shorter canvas.
    y = fy(0.396)
    ctr(lines[0], y, hf)
    y += int(hs * 1.21)
    ctr(lines[1], y, hf)
    y += int(hs * 1.55)
    ctr(lines[2], y, sub_f)
    y += int(sub_f.size * 1.32)
    ctr(lines[3], y, fitted(lines[3], round(0.041 * H), 500))
    y += int(sub_f.size * 2.4)
    ctr("relayme.bio", y, fitted("relayme.bio", round(0.059 * H), 800))
    return im


def render_hand(name, ad, shots_dir, frames_dir):
    """The mockup version. No drawn shell, no end card, no pitch - the hand
    holds the real page and the captions name what is on screen."""
    os.makedirs(frames_dir, exist_ok=True)
    assets = load_assets()
    if not assets["hand"]:
        raise SystemExit("no hand asset - put %s next to this script and run "
                         "--assets" % HAND_SRC)

    x0, y0, x1, y1 = assets["hand_box"]
    sw0, sh0 = x1 - x0 + 1, y1 - y0 + 1

    # Size the mockup by its SCREEN, not by its own bounds - the screen is the
    # part that has to look right, and the wrist can run off the bottom.
    target_h = round(H * 0.648)
    sc = target_h / float(sh0)
    hand = assets["hand"].resize((round(assets["hand"].width * sc),
                                  round(assets["hand"].height * sc)), Image.LANCZOS)
    sx, sy = round(x0 * sc), round(y0 * sc)
    sw, sh = round(sw0 * sc), round(sh0 * sc)
    hx = (W - sw) // 2 - sx           # centre the SCREEN, not the hand
    # 0.175 put the bezel about 20px under the caption's descenders. The
    # caption also moved up, so the gap comes from both ends.
    hy = round(H * 0.222) - sy

    screens = [prepare_screen(os.path.join(shots_dir, "%02d.png" % i))
               for i in range(len(ad["shots"]))]
    screens = [im.resize((sw, sh), Image.LANCZOS) for im in screens]

    bg = light_bg()
    caps = [s2["caption"] for s2 in ad["shots"]]
    lengths = [s2["frames"] for s2 in ad["shots"]]
    total = sum(lengths)
    print("total %d frames = %.1fs" % (total, total / FPS))

    starts, acc = [], 0
    for n in lengths:
        starts.append(acc); acc += n

    i = 0
    for si in range(len(screens)):
        for k in range(lengths[si]):
            frame = bg.copy()
            draw_doodles(frame, assets, i)

            cur = screens[si]
            if si + 1 < len(screens) and k >= lengths[si] - TRANS:
                t = ease((k - (lengths[si] - TRANS)) / float(TRANS))
                up = Image.new("RGB", (sw, sh)); up.paste(cur, (0, -int(sh * 0.10 * t)))
                dn = Image.new("RGB", (sw, sh)); dn.paste(screens[si + 1], (0, int(sh * 0.10 * (1 - t))))
                layer = Image.blend(up, dn, t)
            else:
                layer = cur

            # page first, hand over the top - its screen is punched out, so the
            # fingers keep overlapping the phone the way they do in the photo
            frame.paste(layer, (hx + sx, hy + sy))
            frame.paste(hand, (hx, hy), hand)

            cap = caps[si]
            local = i - starts[si]
            a = ease(min(1.0, local / 9.0))
            if si + 1 < len(screens) and k >= lengths[si] - TRANS:
                a *= 1.0 - ease((k - (lengths[si] - TRANS)) / float(TRANS))
            if a > 0.01:
                cf = fitted(cap, round(0.058 * H), 800)
                rise = int((1 - ease(min(1.0, local / 9.0))) * 16)
                tmp = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
                ImageDraw.Draw(tmp).text(((W - text_w(cap, cf)) // 2, 0), cap,
                                         font=cf, fill=INK + (int(255 * a),))
                frame.paste(tmp, (0, fy(0.072) + rise), tmp)

            # Top-left, not centred at the foot: the wrist runs off the bottom
            # of the frame and swallowed it there.
            d = ImageDraw.Draw(frame)
            wf = font(round(0.024 * H), 700)
            d.text((fx(0.055), fy(0.028)), "relayme.bio", font=wf,
                   fill=(133, 118, 178))

            frame.save(os.path.join(frames_dir, "%04d.png" % i))
            i += 1
        print("  shot", si + 1, "of", len(screens))
    return i


def render(name, ad, shots_dir, frames_dir):
    os.makedirs(frames_dir, exist_ok=True)
    assets = load_assets()

    screens = [prepare_screen(os.path.join(shots_dir, "%02d.png" % i))
               for i in range(len(ad["shots"]))]
    sw, sh = screens[0].size
    shell, origin, shell_size = phone_shell((sw, sh))
    island = island_layer(shell_size, origin, (sw, sh))
    smask = rounded_mask((sw, sh), SCREEN_CORNER)
    bg = gradient_bg()
    px = (W - shell_size[0]) // 2

    caps = [s["caption"] for s in ad["shots"]]
    lengths = [s["frames"] for s in ad["shots"]]
    total = sum(lengths) + ad["end"][4]
    print("total %d frames = %.1fs" % (total, total / FPS))

    # starts of each shot
    starts, acc = [], 0
    for n in lengths:
        starts.append(acc)
        acc += n
    end_start = acc

    i = 0
    for si in range(len(screens)):
        for k in range(lengths[si]):
            frame = bg.copy()
            draw_doodles(frame, assets, i)

            # the screen, sliding and cross-fading into the next one
            cur = screens[si]
            layer = Image.new("RGB", (sw, sh), (0, 0, 0))
            shift = 0
            if si + 1 < len(screens) and k >= lengths[si] - TRANS:
                t = ease((k - (lengths[si] - TRANS)) / float(TRANS))
                nxt = screens[si + 1]
                up = Image.new("RGB", (sw, sh))
                up.paste(cur, (0, -int(sh * 0.10 * t)))
                dn = Image.new("RGB", (sw, sh))
                dn.paste(nxt, (0, int(sh * 0.10 * (1 - t))))
                layer = Image.blend(up, dn, t)
            else:
                layer.paste(cur, (0, shift))

            # Order matters: the shell body is opaque, so the screen goes on
            # AFTER it, and the island after that or the screen covers it.
            frame.paste(shell, (px, PHONE_Y), shell)
            frame.paste(layer, (px + origin[0], PHONE_Y + origin[1]), smask)
            frame.paste(island, (px, PHONE_Y), island)

            draw_mascot(frame, assets, i, fx(0.870), fy(0.844),
                        round(0.157 * W * DEC))

            # caption: fades and rises rather than cutting
            d = ImageDraw.Draw(frame)
            cap = caps[si]
            local = i - starts[si]
            fade_in = min(1.0, local / 9.0)
            fade_out = 1.0
            if si + 1 < len(screens) and k >= lengths[si] - TRANS:
                fade_out = 1.0 - ease((k - (lengths[si] - TRANS)) / float(TRANS))
            a = ease(fade_in) * fade_out
            if a > 0.01:
                cf = fitted(cap, 76, 800)
                rise = int((1 - ease(fade_in)) * 18)
                tw = text_w(cap, cf)
                tmp = Image.new("RGBA", (W, 160), (0, 0, 0, 0))
                ImageDraw.Draw(tmp).text(((W - tw) // 2, 0), cap, font=cf,
                                         fill=WHITE + (int(255 * a),))
                frame.paste(tmp, (0, fy(0.0875) + rise), tmp)
            d.text(((W - text_w("relayme.bio", font(38, 600))) // 2, H - fy(0.0615)),
                   "relayme.bio", font=font(38, 600), fill=WHITE)

            frame.save(os.path.join(frames_dir, "%04d.png" % i))
            i += 1
        print("  shot", si + 1, "of", len(screens))

    # end card, with the mascot still going
    for k in range(ad["end"][4]):
        end_card(ad["end"][:4], assets, i).save(
            os.path.join(frames_dir, "%04d.png" % i))
        i += 1
    print("  end card")
    return i


def encode(frames_dir, out):
    exe = os.environ.get("FFMPEG", "ffmpeg")
    subprocess.run([exe, "-y", "-framerate", str(FPS),
                    "-i", os.path.join(frames_dir, "%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "19",
                    "-movflags", "+faststart", out], check=True)
    print("\ndone ->", out)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--assets":
        build_assets()
        sys.exit(0)
    if len(sys.argv) < 2 or sys.argv[1] not in ADS:
        print("usage: python make_ad.py <name> [format]")
        print("names:  ", ", ".join(ADS), "| --assets")
        print("formats:", ", ".join(FORMATS), "(default 9:16)")
        sys.exit(1)

    name = sys.argv[1]
    fmt = sys.argv[2] if len(sys.argv) > 2 else "9:16"
    if fmt not in FORMATS:
        print("unknown format:", fmt, "- try", ", ".join(FORMATS))
        sys.exit(1)
    set_format(fmt)
    print("format %s - %dx%d" % (fmt, W, H))

    ad = ADS[name]
    # Shots are shape-independent, so every format reuses the same capture.
    shots_dir = os.path.join(HERE, "_shots_" + name)
    tag = fmt.replace(":", "x")
    frames_dir = os.path.join(HERE, "_frames_%s_%s" % (name, tag))
    out = os.path.join(HERE, "relayme-ad-%s-%s.mp4" % (name, tag))

    if not os.path.isdir(ASSETS) or len(os.listdir(ASSETS)) < len(STICKERS) + 2:
        print("building assets")
        build_assets()

    # Reuses shots if they are there. Delete _shots_<name> to force a fresh
    # capture - needed after changing a url, a scroll or a click, since those
    # live in the capture rather than the render.
    if os.path.isdir(shots_dir) and len(os.listdir(shots_dir)) >= len(ad["shots"]):
        print("reusing", shots_dir)
    else:
        print("capturing", name)
        capture(ad, shots_dir)

    print("rendering", name)
    if ad.get("style") == "hand":
        render_hand(name, ad, shots_dir, frames_dir)
    else:
        render(name, ad, shots_dir, frames_dir)
    encode(frames_dir, out)
