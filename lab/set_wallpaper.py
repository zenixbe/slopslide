"""Give the lab's root window a real background *pixmap* (survives exposes), so glass/alignment can be judged."""
import random

import cairo
from Xlib import X, display

W, H = 5120, 1440


def make():
    random.seed(7)
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surf)
    g = cairo.LinearGradient(0, 0, W, H)
    g.add_color_stop_rgb(0, 0.05, 0.28, 0.32)
    g.add_color_stop_rgb(1, 0.10, 0.45, 0.42)
    cr.set_source(g)
    cr.paint()
    for _ in range(70):
        x, y, r = random.randint(0, W), random.randint(900, H), random.randint(60, 260)
        c = (random.random() * .6, .4 + random.random() * .5, .4 + random.random() * .5)
        rg = cairo.RadialGradient(x, y, 0, x, y, r)
        rg.add_color_stop_rgba(0, *c, .6)
        rg.add_color_stop_rgba(1, *c, 0)
        cr.set_source(rg)
        cr.arc(x, y, r, 0, 6.3)
        cr.fill()
    surf.flush()
    return surf


def apply():
    surf = make()
    data = bytes(surf.get_data())
    d = display.Display()
    root = d.screen().root
    pm = root.create_pixmap(W, H, d.screen().root_depth)
    gc = pm.create_gc()
    stride = surf.get_stride()
    rows = 12
    for y in range(0, H, rows):
        n = min(rows, H - y)
        pm.put_image(gc, 0, y, W, n, X.ZPixmap, d.screen().root_depth, 0, data[y * stride:(y + n) * stride])
    root.change_attributes(background_pixmap=pm)
    root.clear_area(0, 0, W, H)
    d.sync()
    print("wallpaper set")


if __name__ == "__main__":
    apply()
