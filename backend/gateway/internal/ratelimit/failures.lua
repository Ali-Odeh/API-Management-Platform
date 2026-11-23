-- Counts one failed key attempt from a client IP (V2.2).
--
-- KEYS[1] = authfail:{workspace_id}:{ip}   ARGV[1] = the window, in ms
--
-- A fixed window: the first failure starts it, later ones add to it, and
-- the counter disappears when it ends. limits.lua refuses the IP while the
-- count is at its limit.
--
-- Returns { failures in this window, ms until the window ends }.

local failures = redis.call('INCR', KEYS[1])
if failures == 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return { failures, redis.call('PTTL', KEYS[1]) }
