-- Token bucket, one per consumer (PLAN.md section 7).
--
-- The bucket holds up to `capacity` tokens (the consumer's rate_limit_per_min)
-- and refills continuously: capacity tokens per minute. Each request takes one
-- token. Redis runs a script as one step that nothing can interrupt, so two
-- gateways can never both take the last token.
--
-- KEYS[1] = rl:{consumer_id}   (all of a consumer's keys share one bucket)
-- ARGV[1] = capacity           (requests per minute, at least 1)
--
-- Returns { allowed (1 or 0), remaining (whole tokens), retry_after_ms }

local capacity = tonumber(ARGV[1])
local refill_per_ms = capacity / 60000

-- Redis's own clock, not the gateway's: with several gateways whose clocks
-- differ, time could seem to run backwards. TIME = { seconds, microseconds }.
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

-- A missing bucket starts full.
local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(bucket[1]) or capacity
local ts = tonumber(bucket[2]) or now

-- Refill for the time since the last request; never above capacity (that
-- also applies a lowered limit at once).
tokens = math.min(capacity, tokens + math.max(0, now - ts) * refill_per_ms)

local allowed = 0
local retry_after_ms = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  -- How long until one whole token has refilled.
  retry_after_ms = math.ceil((1 - tokens) / refill_per_ms)
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
-- An idle bucket is full again after one minute anyway: drop it after two,
-- so Redis only keeps buckets of active consumers.
redis.call('PEXPIRE', KEYS[1], 120000)

-- Redis turns Lua numbers into integers, so round explicitly.
return { allowed, math.floor(tokens), retry_after_ms }
