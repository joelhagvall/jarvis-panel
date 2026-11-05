"""The moon for Jarvis and the dashboard (Joel, 2026-10-28): its phase, how much of it is lit, and the next full and
new moon, computed here without any service.

New and full moons follow Jean Meeus, Astronomical Algorithms (2nd ed.), chapter 49: the mean phase and its periodic
terms, within a minute or two of the published times (ΔT of 69 s taken off). The phase in between is where `now`
lies between two new moons, and the lit fraction (1 - cos 2πp) / 2 of that, within a few percent. Standard library
only, so the tests run without Home Assistant.
"""
from datetime import datetime, timedelta, timezone
import math

SYNODIC_DAYS = 29.530588861
DELTA_T = timedelta(seconds=69)        # TT - UTC around 2026
_EPOCH = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)   # JDE 2451545.0

# (name, from, to) as fractions of the month from one new moon to the next
PHASES = (("Nymåne", 0.0, 0.0339), ("Tilltagande månskära", 0.0339, 0.2161), ("Halvmåne, tilltagande", 0.2161, 0.2839),
          ("Tilltagande måne", 0.2839, 0.4661), ("Fullmåne", 0.4661, 0.5339), ("Avtagande måne", 0.5339, 0.7161),
          ("Halvmåne, avtagande", 0.7161, 0.7839), ("Avtagande månskära", 0.7839, 0.9661), ("Nymåne", 0.9661, 1.0001))

# Periodic terms (coefficient, multiplier of E, M, M', F, Ω) for new (0) and full (0.5) moons, Meeus table 49.A
_NEW = ((-0.40720, 0, 0, 1, 0, 0), (0.17241, 1, 1, 0, 0, 0), (0.01608, 0, 0, 2, 0, 0), (0.01039, 0, 0, 0, 2, 0),
        (0.00739, 1, -1, 1, 0, 0), (-0.00514, 1, 1, 1, 0, 0), (0.00208, 2, 2, 0, 0, 0), (-0.00111, 0, 0, 1, -2, 0),
        (-0.00057, 0, 0, 1, 2, 0), (0.00056, 1, 1, 2, 0, 0), (-0.00042, 0, 0, 3, 0, 0), (0.00042, 1, 1, 0, 2, 0),
        (0.00038, 1, 1, 0, -2, 0), (-0.00024, 1, -1, 2, 0, 0), (-0.00017, 0, 0, 0, 0, 1), (-0.00007, 0, 2, 1, 0, 0),
        (0.00004, 0, 0, 2, -2, 0), (0.00004, 0, 3, 0, 0, 0), (0.00003, 0, 1, 1, -2, 0), (0.00003, 0, 0, 2, 2, 0),
        (-0.00003, 0, 1, 1, 2, 0), (0.00003, 0, -1, 1, 2, 0), (-0.00002, 0, -1, 1, -2, 0), (-0.00002, 0, 1, 3, 0, 0),
        (0.00002, 0, 0, 4, 0, 0))
_FULL = ((-0.40614, 0, 0, 1, 0, 0), (0.17302, 1, 1, 0, 0, 0), (0.01614, 0, 0, 2, 0, 0), (0.01043, 0, 0, 0, 2, 0),
         (0.00734, 1, -1, 1, 0, 0), (-0.00515, 1, 1, 1, 0, 0), (0.00209, 2, 2, 0, 0, 0), (-0.00111, 0, 0, 1, -2, 0),
         (-0.00057, 0, 0, 1, 2, 0), (0.00056, 1, 1, 2, 0, 0), (-0.00042, 0, 0, 3, 0, 0), (0.00042, 1, 1, 0, 2, 0),
         (0.00038, 1, 1, 0, -2, 0), (-0.00024, 1, -1, 2, 0, 0), (-0.00017, 0, 0, 0, 0, 1), (-0.00007, 0, 2, 1, 0, 0),
         (0.00004, 0, 0, 2, -2, 0), (0.00004, 0, 3, 0, 0, 0), (0.00003, 0, 1, 1, -2, 0), (0.00003, 0, 0, 2, 2, 0),
         (-0.00003, 0, 1, 1, 2, 0), (0.00003, 0, -1, 1, 2, 0), (-0.00002, 0, -1, 1, -2, 0), (-0.00002, 0, 1, 3, 0, 0),
         (0.00002, 0, 0, 4, 0, 0))


def _event(k):
    """The UTC time of lunation k: an integer k is a new moon, k + 0.5 the full moon after it."""
    t = k / 1236.85
    jde = (2451550.09766 + 29.530588861 * k + 0.00015437 * t ** 2 - 0.000000150 * t ** 3 + 0.00000000073 * t ** 4)
    e = 1 - 0.002516 * t - 0.0000074 * t ** 2
    rad = math.radians
    m = rad(2.5534 + 29.10535670 * k - 0.0000014 * t ** 2 - 0.00000011 * t ** 3)
    mp = rad(201.5643 + 385.81693528 * k + 0.0107582 * t ** 2 + 0.00001238 * t ** 3 - 0.000000058 * t ** 4)
    f = rad(160.7108 + 390.67050284 * k - 0.0016118 * t ** 2 - 0.00000227 * t ** 3 + 0.000000011 * t ** 4)
    om = rad(124.7746 - 1.56375588 * k + 0.0020672 * t ** 2 + 0.00000215 * t ** 3)
    terms = _NEW if k == math.floor(k) else _FULL
    jde += sum(c * e ** pe * math.sin(pm * m + pmp * mp + pf * f + po * om) for c, pe, pm, pmp, pf, po in terms)
    return _EPOCH + timedelta(days=jde - 2451545.0) - DELTA_T


def _lunation(when):
    """k of the new moon at or before `when`."""
    k = math.floor((when - datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)) / timedelta(days=SYNODIC_DAYS))
    while _event(k) > when:
        k -= 1
    while _event(k + 1) <= when:
        k += 1
    return k


def next_full(when):
    k = _lunation(when)
    return _event(k + 0.5) if _event(k + 0.5) > when else _event(k + 1.5)


def next_new(when):
    return _event(_lunation(when) + 1)


def now(when=None):
    """{phase, illumination (0-100 %), age_days, next_full, next_new} for `when` (UTC now by default)."""
    when = when or datetime.now(timezone.utc)
    k = _lunation(when)
    start, end = _event(k), _event(k + 1)
    p = (when - start) / (end - start)
    name = next(n for n, low, high in PHASES if low <= p < high)
    return {"phase": name, "illumination": round(100 * (1 - math.cos(2 * math.pi * p)) / 2),
            "age_days": round((when - start) / timedelta(days=1), 1), "fraction": round(p, 4),
            "next_full": next_full(when), "next_new": next_new(when)}
