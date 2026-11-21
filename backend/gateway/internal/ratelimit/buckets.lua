-- Token buckets for one request: the workspace's and the consumer's
-- (PLAN.md section 7, decision 6).
--
-- A bucket holds up to `capacity` tokens (requests per minute) and refills
-- continuously: capacity tokens per minute. A request takes one token from
-- every bucket it is checked against, and only if every one of them has a
-- token: a request refused by one bucket costs the other nothing. Redis runs
-- a script as one step that nothing can interrupt, so two gateways can never
-- both take the last token.
--
-- KEYS[1] = rlws:{workspace_id}  ARGV[1] = its capacity   (every request)
-- KEYS[2] = rl:{consumer_id}     ARGV[2] = its capacity   (protected routes only;
--                                all of a consumer's keys share one bucket)
--
-- Returns { allowed (1 or 0), remaining, retry_after_ms, limited_by }:
--   allowed     remaining = whole tokens left in the last bucket (the
--               consumer's, or the workspace's on a public route);
--               retry_after_ms = 0; limited_by = ""
--   refused     the bucket that holds the request back longest:
--               limited_by = "workspace" or "consumer", remaining = its
--               whole tokens (0), retry_after_ms = time until every bucket
--               has a token again
--
-- With Redis Cluster both keys would have to share a hash slot (a later
-- concern, see decision 6).

if #KEYS < 1 or #KEYS > 2 or #ARGV ~= #KEYS then
  return redis.error_reply('want 1 or 2 keys, each with a capacity')
end

local names = { 'workspace', 'consumer' }

-- Redis's own clock, not the gateway's: with several gateways whose clocks
-- differ, time could seem to run backwards. TIME = { seconds, microseconds }.
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

local tokens, waits = {}, {}
local allowed = 1
local decider = #KEYS -- the bucket the answer describes
for i = 1, #KEYS do
  local capacity = tonumber(ARGV[i])
  local refill_per_ms = capacity / 60000

  -- A missing bucket starts full.
  local bucket = redis.call('HMGET', KEYS[i], 'tokens', 'ts')
  local have = tonumber(bucket[1]) or capacity
  local ts = tonumber(bucket[2]) or now
  -- Refill for the time since the last request; never above capacity (that
  -- also applies a lowered limit at once).
  tokens[i] = math.min(capacity, have + math.max(0, now - ts) * refill_per_ms)

  waits[i] = 0
  if tokens[i] < 1 then
    -- How long until one whole token has refilled.
    waits[i] = math.ceil((1 - tokens[i]) / refill_per_ms)
    -- The longest wait decides; on a tie the consumer's own limit is named.
    if allowed == 1 or waits[i] >= waits[decider] then
      decider = i
    end
    allowed = 0
  end
end

for i = 1, #KEYS do
  if allowed == 1 then
    tokens[i] = tokens[i] - 1
  end
  redis.call('HSET', KEYS[i], 'tokens', tokens[i], 'ts', now)
  -- An idle bucket is full again after one minute anyway: drop it after two,
  -- so Redis only keeps buckets of active workspaces and consumers.
  redis.call('PEXPIRE', KEYS[i], 120000)
end

-- Redis turns Lua numbers into integers, so round explicitly.
if allowed == 1 then
  return { 1, math.floor(tokens[decider]), 0, '' }
end
return { 0, math.floor(tokens[decider]), waits[decider], names[decider] }
