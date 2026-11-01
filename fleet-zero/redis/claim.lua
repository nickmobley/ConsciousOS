-- Fleet ZERO atomic claim, KEYS: pending, leases, job-prefix key is passed as ARGV[4] (same Redis hash slot)
-- ARGV worker, now_ms, lease_ms, job_prefix. Returns id, fence, expiry or nil.
-- NOTE Redis Lua KEYS should be declared explicitly in production cluster deployment.
local id=redis.call('LPOP',KEYS[1])
if not id then return nil end
local key=ARGV[4]..id
if redis.call('HGET',key,'state')~='pending' then return {false,'stale_queue_entry'} end
local attempts=tonumber(redis.call('HINCRBY',key,'attempt',1))
local max=tonumber(redis.call('HGET',key,'max_attempts') or '3')
if attempts>max then redis.call('HSET',key,'state','dead'); return {false,'exhausted'} end
local fence=redis.call('HINCRBY',key,'fence',1)
local expires=tonumber(ARGV[2])+tonumber(ARGV[3])
redis.call('HSET',key,'state','leased','worker',ARGV[1],'expires_ms',expires,'updated_ms',ARGV[2])
redis.call('ZADD',KEYS[2],expires,id)
return {id,fence,expires}
