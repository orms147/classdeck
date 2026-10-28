"""Draw the ClassDeck icon (a slide on a sunny tile) and save it as a multi-size .ico."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512
BLUE = (28, 176, 246)
INK = (30, 27, 75)
SUN = (255, 200, 0)
CARD = (255, 255, 255)
GREEN = (88, 204, 2)
RED = (255, 75, 75)


def draw() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.rounded_rectangle(
        (16, 16, SIZE - 16, SIZE - 16), radius=112, fill=BLUE, outline=INK, width=16
    )
    pen.ellipse((300, 52, 452, 204), fill=SUN, outline=INK, width=12)
    # the slide: a card with a title bar and two answer chips
    pen.rounded_rectangle((70, 170, 402, 410), radius=36, fill=CARD, outline=INK, width=14)
    pen.rounded_rectangle((110, 214, 362, 252), radius=19, fill=INK)
    pen.rounded_rectangle((110, 288, 228, 362), radius=26, fill=GREEN, outline=INK, width=10)
    pen.rounded_rectangle((246, 288, 362, 362), radius=26, fill=RED, outline=INK, width=10)
    # a stand under the slide
    pen.rounded_rectangle((216, 404, 256, 452), radius=10, fill=INK)
    return image


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("classdeck.ico")
    draw().save(
        target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    print(target)


if __name__ == "__main__":
    main()
