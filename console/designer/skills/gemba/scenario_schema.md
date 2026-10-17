# Gemba scenario schema (generated from contracts.EVENT_ARGS)

A scenario is YAML with: `id` (snake_case slug, not one of reslot_default/train_1..3/test_1..3), `seed` (int),
`days: [start, end]` with 61 <= start <= end <= 90 (prefer 10-day windows like [61, 70]),
`events: [{kind, day, hour (default 6), args}]`, optional `perturbation: {family_scale_pct, seasonal_group_seed, surge_shift_days}`,
optional `overrides: {headcounts: {day|swing|night: {pool: int}}, budgets: {legs_per_night: int}}`.

Pools: pm, forklift, pallet, replen, consolidation, loading, night_move, temp. Aisles 1-28. Pick Module zones 1-6. Customers C001-C200 (C001-C003 are key).
Families F001-F300, or the alias `seasonal` / `fading`. SKUs S0001-S2000. pct 0-100; days/hours/count/n positive ints.

## Event kinds you may use (exogenous only) and their args

- `labor_shortage`: args ['pool', 'pct', 'days']  (announced 0 day(s) ahead)
- `demand_surge`: args ['skus', 'family', 'customer', 'factor', 'days']  (announced 2 day(s) ahead)
- `rack_damage`: args ['aisle', 'days']  (announced 0 day(s) ahead)
- `pm_zone_down`: args ['zone', 'hours']  (announced 0 day(s) ahead)
- `leadership_visit`: args ['no_moves_in_aisles']  (announced 5 day(s) ahead)
- `key_customer_week`: args ['customer', 'days']  (announced 3 day(s) ahead)
- `new_sku_launch`: args ['n']  (announced 3 day(s) ahead)
- `returns_spike`: args ['pct', 'days']  (announced 1 day(s) ahead)
- `wms_sync_delay`: args ['days']  (announced 0 day(s) ahead)
- `forklift_breakdown`: args ['count', 'days']  (announced 0 day(s) ahead)

`demand_surge` takes exactly one of skus | family | customer, plus factor and days.
Consequence kinds (customer_escalation, absenteeism, staging_congestion, safety_review, aisle_closure, director_escalation, replen_alert) are produced by the simulation; never write them.

## Example

```yaml
# Held-out test scenario 1 (spec 13.1): unseen seed, timing and mix; includes wms_sync_delay.
id: test_1
seed: 21
days: [61, 70]
events:
  - {kind: demand_surge, day: 63, args: {family: seasonal, factor: 3, days: 3}}
  - {kind: wms_sync_delay, day: 66, args: {days: 2}}
  - {kind: key_customer_week, day: 68, args: {customer: C001, days: 3}}
perturbation: {family_scale_pct: -9, seasonal_group_seed: 21, surge_shift_days: 1}
overrides: {}
```
