create table if not exists raw_licenses (
  id            bigserial primary key,
  snapshot_date date not null default current_date,
  license_key   text not null,
  payload       jsonb not null,
  unique (snapshot_date, license_key)
);

create table if not exists raw_osm_places (
  osm_id     text primary key,
  fetched_at timestamptz default now(),
  payload    jsonb not null
);

create table if not exists dim_restaurants (
  restaurant_id    bigserial primary key,
  license_key      text unique,
  name             text not null,
  address          text,
  zip              text,
  lat              double precision,
  lon              double precision,
  osm_id           text,
  website          text,
  instagram_handle text,
  ig_bio           text,
  ig_website       text,
  found_by         text,                       -- osm / website_scan / manual / ai
  verified         text default 'unchecked',   -- yes / no / unsure / unchecked
  verify_reason    text,
  updated_at       timestamptz default now()
);

create table if not exists raw_ig_posts (
  post_id        text primary key,
  restaurant_id  bigint references dim_restaurants(restaurant_id),
  ig_handle      text,
  media_type     text,          -- IMAGE / VIDEO / CAROUSEL_ALBUM
  caption        text,
  permalink      text,          -- permanent link: use this in the UI
  media_url      text,          -- temporary link: expires, used only for extraction
  thumbnail_url  text,          -- cover image for videos (also temporary)
  children       jsonb,         -- carousel slides
  posted_at      timestamptz,
  like_count     int,
  comments_count int,
  fetched_at     timestamptz default now(),
  processed      boolean default false,
  status         text default 'new'   -- new / done / url_expired
);

create table if not exists raw_web_pages (
  id            bigserial primary key,
  restaurant_id bigint references dim_restaurants(restaurant_id),
  url           text not null,
  content_hash  text not null,
  snippets      text,          -- only the deal-keyword text blocks, not the full page
  fetched_at    timestamptz default now(),
  processed     boolean default false,
  unique (url, content_hash)
);

create table if not exists rss_sources (
  feed_url text primary key,
  name     text,
  active   boolean default true
);

create table if not exists raw_rss_items (
  link         text primary key,
  feed_url     text,
  title        text,
  summary      text,
  published_at timestamptz,
  fetched_at   timestamptz default now(),
  processed    boolean default false
);

create table if not exists deals_extracted (
  id                  bigserial primary key,
  source_type         text,      -- instagram / website / rss
  source_id           text,
  restaurant_id       bigint,
  restaurant_name_raw text,
  offer_type          text,
  item                text,
  value               text,
  conditions          text,
  code                text,
  code_kind           text,      -- general / personal
  valid_from          date,
  valid_until         date,
  confidence          real,
  extracted_at        timestamptz default now()
);

-- Everything the AI has already read, so collectors never save it again.
-- source_id is the post_id (instagram), url|content_hash (website), or link (rss).
create table if not exists seen_posts (
  source_type text not null,
  source_id   text not null,
  seen_at     timestamptz default now(),
  primary key (source_type, source_id)
);

-- Lock the tables so they can't be read through Supabase's public web API.
-- Your scripts connect directly to Postgres, so they are not affected.
alter table raw_licenses    enable row level security;
alter table raw_osm_places  enable row level security;
alter table dim_restaurants enable row level security;
alter table raw_ig_posts    enable row level security;
alter table raw_web_pages   enable row level security;
alter table rss_sources     enable row level security;
alter table raw_rss_items   enable row level security;
alter table deals_extracted enable row level security;
alter table seen_posts      enable row level security;