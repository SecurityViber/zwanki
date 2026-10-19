"""Zwanki's icons, drawn with Pillow so every build renders them fresh.

The app icon: a flash card with a lightning bolt (flash card, and watts) racing forward with
speed lines, on a sunset gradient. The macOS menu bar gets the same shape in black, as a
template image that macOS tints to suit light and dark menu bars.
"""

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SUNSET = [(0.0, (255, 190, 72)), (0.45, (255, 104, 52)), (1.0, (214, 30, 118))]
CARD = (255, 255, 255)

# Bolt outline, in a 100 x 160 box.
BOLT = [(52, 0), (6, 94), (42, 94), (28, 160), (94, 56), (57, 56), (80, 0)]


def _gradient(size, stops, angle=0.0):
    """size x size image of a top-to-bottom gradient through the colour stops, tilted by angle degrees."""
    diag = int(size * math.sqrt(2)) + 2
    strip = Image.new("RGB", (1, diag))
    px = strip.load()
    for y in range(diag):
        t = y / (diag - 1)
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t <= t1:
                f = (t - t0) / (t1 - t0)
                px[0, y] = tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
                break
    square = strip.resize((diag, diag), Image.BILINEAR).rotate(angle, resample=Image.BICUBIC)
    offset = (diag - size) // 2
    return square.crop((offset, offset, offset + size, offset + size))


def _bolt(x, y, height):
    """Bolt polygon with its top-left corner at (x, y)."""
    scale = height / 160
    return [(x + bx * scale, y + by * scale) for bx, by in BOLT]


def _paste_rotated(canvas, layer, center, angle):
    rotated = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    canvas.alpha_composite(rotated, (round(center[0] - rotated.width / 2), round(center[1] - rotated.height / 2)))


def _card(w, h, radius, color, alpha=255, bolt=None):
    """A rounded card; bolt = gradient image to fill a lightning bolt with."""
    layer = Image.new("RGBA", (w, h))
    ImageDraw.Draw(layer).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=color + (alpha,))
    if bolt is not None:
        height = h * 0.64
        width = height * 94 / 160
        mask = Image.new("L", (w, h))
        ImageDraw.Draw(mask).polygon(_bolt((w - width) / 2, (h - height) / 2, height), fill=255)
        layer.paste(bolt.resize((w, h)), (0, 0), mask)
    return layer


def draw_icon(size, margin=0.0):
    """The full-colour app icon, size x size. margin = empty fraction around it (macOS icons use 0.1,
    and then also get a soft shadow underneath). Small sizes get a simpler, bolder layout."""
    compact = size <= 48
    k = 4 if size <= 256 else 2  # draw bigger, then shrink, for smooth edges
    big = size * k
    half = big * (1 - 2 * margin) / 2  # half the width of the tile
    c = big / 2
    unit = half * 2 / 1024  # the design below is laid out on a 1024-unit tile

    def at(x, y):
        return c + (x - 512) * unit, c + (y - 512) * unit

    img = Image.new("RGBA", (big, big))
    tile = Image.new("L", (big, big))
    ImageDraw.Draw(tile).rounded_rectangle([c - half, c - half, c + half, c + half], radius=half * 0.45, fill=255)

    if margin:
        shadow = tile.filter(ImageFilter.GaussianBlur(16 * unit)).point(lambda v: v * 0.45)
        img.paste((0, 0, 0, 255), (0, round(14 * unit)), shadow)

    # Background: sunset gradient, light in the top left, with a soft glow there.
    bg = _gradient(big, SUNSET, angle=28).convert("RGBA")
    glow = Image.new("L", (big, big))
    gx, gy = at(250, 170)
    ImageDraw.Draw(glow).ellipse([gx - 360 * unit, gy - 260 * unit, gx + 360 * unit, gy + 260 * unit], fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(150 * unit)).point(lambda v: v * 0.22)
    bg.paste((255, 255, 255, 255), (0, 0), glow)

    if compact:
        w, h = round(500 * unit), round(650 * unit)
        sx, sy = at(530, 528)
    else:
        # Speed lines that fade in towards the card, which partly hides them.
        lines = Image.new("L", (big, big))
        d = ImageDraw.Draw(lines)
        for x0, x1, y, alpha in ((196, 470, 392, 255), (120, 450, 512, 225), (228, 470, 632, 190)):
            (ax, ay), (bx, _) = at(x0, y), at(x1, y)
            r = 19 * unit
            d.rounded_rectangle([ax, ay - r, bx, ay + r], radius=r, fill=alpha)
        fade = Image.new("L", (big, 1))
        fade.putdata([round(255 * min(1, max(0, ((x - c) / unit + 512 - 110) / 300))) for x in range(big)])
        bg.paste((255, 255, 255, 255), (0, 0), ImageChops.multiply(lines, fade.resize((big, big))))
        w, h = round(372 * unit), round(486 * unit)
        sx, sy = at(600, 548)

    # The deck: a translucent card behind, the bolt card in front with a soft shadow.
    radius = round(w * 0.156)
    if not compact:
        _paste_rotated(bg, _card(w, h, radius, CARD, alpha=105), at(548, 500), 7)
    card_shadow = Image.new("RGBA", (w + round(160 * unit), h + round(160 * unit)))
    pad = round(80 * unit)
    ImageDraw.Draw(card_shadow).rounded_rectangle([pad, pad, pad + w, pad + h], radius=radius, fill=(70, 0, 30, 120))
    card_shadow = card_shadow.filter(ImageFilter.GaussianBlur(26 * unit))
    _paste_rotated(bg, card_shadow, (sx + 10 * unit, sy + 30 * unit), -9)
    bolt_fill = _gradient(512, SUNSET, angle=28)
    _paste_rotated(bg, _card(w, h, radius, CARD, bolt=bolt_fill), (sx, sy), -9)

    img.paste(bg, (0, 0), tile)
    return img.resize((size, size), Image.LANCZOS)


def draw_menu_bar_glyph(size=36):
    """Black card-with-bolt and speed lines on transparent: a macOS menu bar template image.
    36 px = 18 pt at Retina resolution."""
    k = 8
    big = size * k
    unit = big / 100
    mask = Image.new("L", (big, big))
    d = ImageDraw.Draw(mask)
    for x0, x1, y in ((4, 30, 34), (0, 28, 50), (6, 30, 66)):
        r = 3.6 * unit
        d.rounded_rectangle([x0 * unit, y * unit - r, x1 * unit, y * unit + r], radius=r, fill=255)

    w, h = round(54 * unit), round(72 * unit)
    card = Image.new("L", (w, h))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([0, 0, w - 1, h - 1], radius=round(10 * unit), fill=255)
    height = h * 0.66
    cd.polygon(_bolt((w - height * 94 / 160) / 2, (h - height) / 2, height), fill=0)  # cut the bolt out
    rotated = card.rotate(-9, resample=Image.BICUBIC, expand=True)
    # Clear a gap around the card so the speed lines stop short of it.
    gap = rotated.point(lambda v: 255 if v else 0).filter(ImageFilter.MaxFilter(2 * round(3 * unit) + 1))
    x, y = round(64 * unit - rotated.width / 2), round(50 * unit - rotated.height / 2)
    mask.paste(0, (x, y), gap)
    mask.paste(rotated, (x, y), rotated)

    glyph = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    glyph.putalpha(mask)
    return glyph.resize((size, size), Image.LANCZOS)
