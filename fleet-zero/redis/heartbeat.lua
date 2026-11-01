-- Fleet ZERO heartbeat: atomic lease extension. KEYS[1]=job hash, KEYS[2]=lease zset
-- ARGV id,worker,fence,now_ms,extension_ms
if redis.call('HGET',KEYS[1],'state')~='leased' then return {0,'not_leased'} end
if redis.call('HGET',KEYS[1],'worker')~=ARGV[2] then return {0,'wrong_worker'} end
if tonumber(redis.call('HGET',KEYS[1],'fence') or '-1')~=tonumber(ARGV[3]) then return {0,'stale_fence'} end
local expiry=tonumber(redis.call('HGET',KEYS[1],'expires_ms') or '0')
if expiry<=tonumber(ARGV[4]) then return {0,'expired'} end
local nextExpiry=math.max(expiry,tonumber(ARGV[4])+tonumber(ARGV[5]))
redis.call('HSET',KEYS[1],'expires_ms',nextExpiry,'updated_ms',ARGV[4])
redis.call('ZADD',KEYS[2],nextExpiry,ARGV[1])
return {1,nextExpiry}
