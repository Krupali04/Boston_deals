select
  d.*,
  r.name as restaurant_name,
  r.address,
  r.lat,
  r.lon,
  r.website,
  r.instagram_handle
from {{ ref('int_deals_deduped') }} d
join {{ source('raw', 'dim_restaurants') }} r using (restaurant_id)
where current_date between coalesce(d.valid_from, d.first_seen::date) and d.valid_until