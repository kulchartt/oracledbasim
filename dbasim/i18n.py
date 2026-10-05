"""English is the default language; Thai via DBASIM_LANG=th or `dbasim lang th`.

Two helpers:
  t(th, en)  pick a string now - for text built at run time (criteria, messages)
  L(th, en)  pick it when printed - for class-level text (titles, stories, hints)
"""
import os

LANGS = ("th", "en")
DEFAULT = "en"


def lang():
    value = os.environ.get("DBASIM_LANG")
    if not value:
        from . import state
        value = state.load().get("lang")
    value = (value or DEFAULT).strip().lower()[:2]
    return value if value in LANGS else DEFAULT


def t(th, en):
    return en if lang() == "en" else th


class L:
    def __init__(self, th, en):
        self.th, self.en = th, en

    def __str__(self):
        return t(self.th, self.en)

    def __format__(self, spec):
        return format(str(self), spec)

    def __add__(self, other):
        return str(self) + other

    def __radd__(self, other):
        return other + str(self)

    def __bool__(self):
        return bool(self.th and self.en)

    def __repr__(self):
        return f"L({self.th!r}, {self.en!r})"
