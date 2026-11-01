-- KEYS[1] job hash KEYS[2] leases zset KEYS[3] pending list; ARGV id,now_ms
if redis.call('HGET',KEYS[1],'state')~='leased' then return {0,'not_leased'} end
if tonumber(redis.call('HGET',KEYS[1],'expires_ms') or '0')>tonumber(ARGV[2]) then return {0,'lease_active'} end
local attempts=tonumber(redis.call('HGET',KEYS[1],'attempt') or '0')
local max=tonumber(redis.call('HGET',KEYS[1],'max_attempts') or '3')
redis.call('ZREM',KEYS[2],ARGV[1])
redis.call('HINCRBY',KEYS[1],'fence',1)
redis.call('HDEL',KEYS[1],'worker','expires_ms')
if attempts>=max then redis.call('HSET',KEYS[1],'state','dead','updated_ms',ARGV[2]);return {1,'dead'} end
redis.call('HSET',KEYS[1],'state','pending','updated_ms',ARGV[2])
redis.call('RPUSH',KEYS[3],ARGV[1])
return {1,'requeued'}
