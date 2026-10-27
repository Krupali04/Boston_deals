with snaps as (
  select distinct snapshot_date from {{ source('raw', 'raw_licenses') }}
  order by snapshot_date desc
  limit 2
),
latest as (select max(snapshot_date) as d from snaps),
previous as (select min(snapshot_date) as d from snaps)
select l.license_key, l.snapshot_date as first_seen, l.payload
from {{ source('raw', 'raw_licenses') }} l
where l.snapshot_date = (select d from latest)
  and (select count(*) from snaps) = 2
  and l.license_key not in (
    select license_key from {{ source('raw', 'raw_licenses') }}
    where snapshot_date = (select d from previous)
  )