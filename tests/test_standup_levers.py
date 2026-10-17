"""Lever normalisation inside the standup: contradictory combinations never reach an option."""
import contracts as c
from org.standup import OrgPolicy


def _reslot(n=200):
    return c.ReslotTonight(n_moves=n, order="value")


def test_pause_amendment_replaces_reslot():
    merged = OrgPolicy._merge([_reslot()], [c.PauseReslot(days=1)])
    assert [lv.kind for lv in merged] == ["pause_reslot"]


def test_reslot_amendment_replaces_pause():
    merged = OrgPolicy._merge([c.PauseReslot(days=1)], [_reslot(150)])
    assert [(lv.kind, getattr(lv, "n_moves", None)) for lv in merged] == [("reslot_tonight", 150)]


def test_normalize_keeps_last_of_a_kind_and_unrelated_levers():
    out = OrgPolicy._normalize([_reslot(200), c.Protect(target="customer", ids=["C001"], days=3), _reslot(120),
                                c.StagingClearance(n_movers=2), c.PauseReslot(days=2)])
    assert [lv.kind for lv in out] == ["protect", "pause_reslot"]
