with deals as (
  select
    id as deal_id, source_type, source_id, restaurant_id, restaurant_name_raw,
    upper(offer_type) as offer_type,
    lower(trim(item)) as item,
    value, conditions, code, code_kind, valid_from,
    coalesce(valid_until, extracted_at::date + 14) as valid_until,
    valid_until is null as expiry_assumed,
    confidence, extracted_at
  from {{ source('raw', 'deals_extracted') }}
  where confidence >= 0.5
),

-- How many active locations share each Instagram account
account_sizes as (
  select lower(instagram_handle) as handle, count(*) as locations
  from {{ source('raw', 'dim_restaurants') }}
  where instagram_handle is not null and verified <> 'no'
  group by 1
),

-- An Instagram deal belongs to every location that shares the post's account
instagram_locations as (
  select d.deal_id, r.restaurant_id, a.locations
  from deals d
  join {{ source('raw', 'raw_ig_posts') }} p
    on d.source_type = 'instagram' and p.post_id = d.source_id
  join {{ source('raw', 'dim_restaurants') }} r
    on lower(r.instagram_handle) = lower(p.ig_handle) and r.verified <> 'no'
  join account_sizes a on a.handle = lower(p.ig_handle)
),

-- Website deals stay with their own restaurant
other_locations as (
  select deal_id, restaurant_id, 1 as locations
  from deals
  where source_type <> 'instagram'
),

deal_locations as (
  select * from instagram_locations
  union all
  select * from other_locations
)

select
  dl.restaurant_id,
  dl.locations,
  dl.locations > 1 as is_chain_deal,
  d.deal_id, d.source_type, d.source_id, d.restaurant_name_raw,
  d.offer_type, d.item, d.value, d.conditions, d.code, d.code_kind,
  d.valid_from, d.valid_until, d.expiry_assumed, d.confidence, d.extracted_at
from deal_locations dl
join deals d using (deal_id)