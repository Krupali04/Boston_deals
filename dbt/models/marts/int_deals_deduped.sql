select
  md5(concat_ws('|', restaurant_id, offer_type, item, valid_until)) as deal_key,
  restaurant_id,
  offer_type,
  item,
  valid_until,
  max(valid_from) as valid_from,
  (array_agg(value order by confidence desc))[1] as value,
  (array_agg(conditions order by confidence desc))[1] as conditions,
  (array_agg(code order by confidence desc))[1] as code,
  (array_agg(code_kind order by confidence desc))[1] as code_kind,
  bool_or(expiry_assumed) as expiry_assumed,
  max(confidence) as confidence,
  bool_or(is_chain_deal) as is_chain_deal,
  max(locations) as locations,
  count(distinct source_type) as source_count,
  min(extracted_at) as first_seen
from {{ ref('stg_deals') }}
where restaurant_id is not null
group by restaurant_id, offer_type, item, valid_until