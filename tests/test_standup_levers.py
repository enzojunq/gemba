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


def test_standing_plan_carried_into_additive_options():
    from org.standup import with_standing_plan
    out = with_standing_plan([c.ResizeSlots(skus=["S0412"], days_cover=4)], 200, "value", paused=False)
    assert [lv.kind for lv in out] == ["resize_slots", "reslot_tonight"] and out[1].n_moves == 200
    # a pause, a reslot or staging clearance already says what tonight does
    assert [lv.kind for lv in with_standing_plan([c.PauseReslot(days=1)], 200, "value", paused=False)] == ["pause_reslot"]
    assert len(with_standing_plan([c.StagingClearance(n_movers=2)], 200, "value", paused=False)) == 1
    # while paused nothing is appended
    assert [lv.kind for lv in with_standing_plan([c.ResizeSlots(skus=["S0412"], days_cover=4)], 200, "value", paused=True)] == ["resize_slots"]


def test_labor_allocate_headcount_bounds():
    from types import SimpleNamespace
    me = SimpleNamespace(profile=SimpleNamespace(budgets={}))
    err = OrgPolicy._lever_error(me, c.LaborAllocate(shift="night", pool="night_move", headcount=0))
    assert err and "empty" in err
    assert OrgPolicy._lever_error(me, c.LaborAllocate(shift="night", pool="night_move", headcount=13))
    assert OrgPolicy._lever_error(me, c.LaborAllocate(shift="night", pool="night_move", headcount=6)) is None


def test_normalize_keeps_last_of_a_kind_and_unrelated_levers():
    out = OrgPolicy._normalize([_reslot(200), c.Protect(target="customer", ids=["C001"], days=3), _reslot(120),
                                c.StagingClearance(n_movers=2), c.PauseReslot(days=2)])
    assert [lv.kind for lv in out] == ["protect", "pause_reslot"]
