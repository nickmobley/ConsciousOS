-- KEYS[1]=job hash KEYS[2]=leases sorted set; ARGV id,worker,fence,now_ms
if redis.call('HGET',KEYS[1],'state')~='leased' then return {0,'not_leased'} end
if redis.call('HGET',KEYS[1],'worker')~=ARGV[2] then return {0,'wrong_worker'} end
if tonumber(redis.call('HGET',KEYS[1],'fence') or '-1')~=tonumber(ARGV[3]) then return {0,'stale_fence'} end
if tonumber(redis.call('HGET',KEYS[1],'expires_ms') or '0')<=tonumber(ARGV[4]) then return {0,'expired'} end
redis.call('HSET',KEYS[1],'state','completed','updated_ms',ARGV[4])
redis.call('ZREM',KEYS[2],ARGV[1])
return {1,'completed'}
