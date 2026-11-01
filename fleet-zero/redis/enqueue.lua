-- Fleet ZERO v0.1 Redis atomic coordination, experimental; no persistent Redis on free tier.
-- Keys share one hash tag for Redis Cluster: fz:{queue}:pending, fz:{queue}:leases, fz:{queue}:job:<id>
-- EVALSHA script calls are atomic. Authoritative durable task state MUST live outside Redis.
-- enqueue KEYS: pending, job; ARGV: id, now_ms, max_attempts
local job=redis.call('HGET',KEYS[2],'state')
if job then return {0,job} end
redis.call('HSET',KEYS[2],'id',ARGV[1],'state','pending','attempt',0,'max_attempts',ARGV[3],'updated_ms',ARGV[2],'fence',0)
redis.call('RPUSH',KEYS[1],ARGV[1])
return {1,'pending'}
