"""British spelling for the pages of a Global Success book.

The books are written in British English, but a vision model reading a scanned page sometimes
returns the American form (``specialty`` for the book's ``speciality``), and a drafting model
writes it from habit (``color``). Two small helpers fix that deterministically: ``britishise``
rewrites the American forms of a known list in the text read from the page, and ``variants``
lists the spellings a word may also be written with, so that a word of a model's reply can be
corrected to the spelling the page really has.
"""

from __future__ import annotations

import re

AMERICAN_TO_BRITISH: dict[str, str] = {
    "specialty": "speciality",
    "specialties": "specialities",
    "color": "colour",
    "colors": "colours",
    "colorful": "colourful",
    "favorite": "favourite",
    "favorites": "favourites",
    "neighbor": "neighbour",
    "neighbors": "neighbours",
    "neighborhood": "neighbourhood",
    "neighborhoods": "neighbourhoods",
    "center": "centre",
    "centers": "centres",
    "theater": "theatre",
    "theaters": "theatres",
    "flavor": "flavour",
    "flavors": "flavours",
    "behavior": "behaviour",
    "honor": "honour",
    "humor": "humour",
    "labor": "labour",
    "harbor": "harbour",
    "traveler": "traveller",
    "travelers": "travellers",
    "traveling": "travelling",
    "jewelry": "jewellery",
    "gray": "grey",
    "organize": "organise",
    "organized": "organised",
    "organizing": "organising",
    "realize": "realise",
    "realized": "realised",
    "recognize": "recognise",
    "recognized": "recognised",
}
"""American spellings that are never right in a Global Success page, with the British form."""

_WORD = re.compile(r"[A-Za-z]+")

_SWAPS: tuple[tuple[str, str], ...] = (
    ("or", "our"),
    ("our", "or"),
    ("er", "re"),
    ("re", "er"),
    ("ize", "ise"),
    ("ise", "ize"),
    ("ty", "tity"),
    ("tity", "ty"),
    ("ities", "ties"),
    ("ties", "ities"),
    ("ler", "lller"),
    ("ller", "ler"),
    ("ay", "ey"),
    ("ey", "ay"),
)


def _match_case(original: str, replacement: str) -> str:
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def britishise(text: str) -> str:
    """``text`` with the American forms of :data:`AMERICAN_TO_BRITISH` rewritten, case kept."""

    def replace(match: re.Match[str]) -> str:
        word = match.group()
        lowered = word.lower()
        if lowered not in AMERICAN_TO_BRITISH:
            return word
        return _match_case(word, AMERICAN_TO_BRITISH[lowered])

    return _WORD.sub(replace, text)


def variants(word: str) -> list[str]:
    """Other spellings of one word: the listed British form and the usual endings swapped.

    ``specialty`` gives ``speciality``; ``colour`` gives ``color``. The list is a set of
    candidates: a caller keeps one only when the page really has it.
    """
    lowered = word.lower()
    found: list[str] = []
    listed = AMERICAN_TO_BRITISH.get(lowered)
    if listed:
        found.append(listed)
    for old, new in _SWAPS:
        for pattern in (rf"{old}(?=s?$)", rf"{old}(?=(?:ed|ing|ly)$)"):
            candidate = re.sub(pattern, new, lowered, count=1)
            if candidate != lowered and candidate not in found:
                found.append(candidate)
    inverse = [us for us, uk in AMERICAN_TO_BRITISH.items() if uk == lowered]
    found += [candidate for candidate in inverse if candidate not in found]
    return [_match_case(word, candidate) for candidate in found]
