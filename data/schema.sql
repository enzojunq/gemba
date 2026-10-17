-- Gemba input schema (PLATFORM_SPEC 8.1). Looks like a WMS export. SQLite.
-- Extra columns beyond 8.1 (documented in PROGRESS_A.md): site.history_days, site.gen_meta, skus.demand_group,
-- order_lines.line_no, order_lines.day.
PRAGMA foreign_keys = OFF;

CREATE TABLE site (
  site_id       TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  opening_hour  INTEGER NOT NULL,
  closing_hour  INTEGER NOT NULL,
  cutoffs       TEXT NOT NULL,          -- JSON list, e.g. [13, 17, 21.5]
  history_days  INTEGER NOT NULL,       -- days 1..history_days are visible history
  gen_meta      TEXT NOT NULL           -- JSON written by the generator (trap ground truth)
);

CREATE TABLE aisles (
  aisle_id    INTEGER PRIMARY KEY,
  x           REAL NOT NULL,
  distance_m  INTEGER NOT NULL,
  bays        INTEGER NOT NULL,         -- 60 = 30 bays x 2 sides (bay 1-30 left, 31-60 right)
  levels      INTEGER NOT NULL
);

CREATE TABLE pm_zones (
  zone_id          INTEGER PRIMARY KEY,
  slots            INTEGER NOT NULL,
  pickers_default  INTEGER NOT NULL,
  seconds_per_line REAL NOT NULL
);

CREATE TABLE locations (
  loc_id         TEXT PRIMARY KEY,
  kind           TEXT NOT NULL CHECK (kind IN ('pm','aisle','staging')),
  aisle_id       INTEGER,
  zone_id        INTEGER,
  bay            INTEGER,
  level          INTEGER,
  slot_type      TEXT NOT NULL CHECK (slot_type IN ('active','reserve')),
  capacity_units INTEGER NOT NULL,
  max_weight_kg  REAL NOT NULL,
  x              REAL NOT NULL,
  y              REAL NOT NULL
);

CREATE TABLE skus (
  sku_id             TEXT PRIMARY KEY,
  family_id          TEXT NOT NULL,
  weight_kg          REAL NOT NULL,
  cube_l             REAL NOT NULL,
  units_per_pallet   INTEGER NOT NULL,
  hazmat             INTEGER NOT NULL,
  fragile            INTEGER NOT NULL,
  customer_tier_hint TEXT NOT NULL CHECK (customer_tier_hint IN ('key','standard')),
  demand_group       TEXT NOT NULL DEFAULT 'normal' CHECK (demand_group IN ('normal','seasonal','fading'))
                     -- scenario alias target: 'seasonal' = 120 SKUs x3 from day 61, 'fading' = 120 SKUs x0.3
);

CREATE TABLE customers (
  customer_id TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  tier        TEXT NOT NULL CHECK (tier IN ('key','standard'))
);

CREATE TABLE slotting_current (
  sku_id                TEXT PRIMARY KEY,
  active_loc            TEXT NOT NULL,
  reserve_loc           TEXT NOT NULL,
  active_capacity_units INTEGER NOT NULL
);

CREATE TABLE slotting_target (
  sku_id                TEXT PRIMARY KEY,
  active_loc            TEXT NOT NULL,
  reserve_loc           TEXT NOT NULL,
  active_capacity_units INTEGER NOT NULL
);

CREATE TABLE orders (
  order_id     TEXT PRIMARY KEY,
  day          INTEGER NOT NULL,
  release_hour INTEGER NOT NULL,
  customer_id  TEXT NOT NULL,
  priority     TEXT NOT NULL CHECK (priority IN ('normal','rush')),
  cutoff_hour  INTEGER NOT NULL         -- 13 | 17 | 21 ; cutoff < release means next day
);

CREATE TABLE order_lines (
  order_id TEXT NOT NULL,
  line_no  INTEGER NOT NULL,
  sku_id   TEXT NOT NULL,
  qty      INTEGER NOT NULL,
  day      INTEGER NOT NULL             -- denormalised from orders for per-day loading
);

CREATE TABLE labor_roster (
  person_id    TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  role         TEXT NOT NULL,           -- contracts.Pool names
  shift        TEXT NOT NULL CHECK (shift IN ('day','swing','night')),
  productivity REAL NOT NULL,
  is_temp      INTEGER NOT NULL
);

CREATE TABLE labor_calendar (
  day       INTEGER NOT NULL,
  shift     TEXT NOT NULL,
  role      TEXT NOT NULL,
  scheduled INTEGER NOT NULL
);
