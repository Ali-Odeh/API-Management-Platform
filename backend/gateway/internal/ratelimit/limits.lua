-- Every limit of one request, in one atomic step (PLAN.md section 7, V2.2).
--
-- Each key comes with a kind and a limit in ARGV, in the same order:
--   ARGV[2i-1] = kind, ARGV[2i] = limit, for KEYS[i]
-- Kinds:
--   'workspace', 'ip', 'consumer'  a token bucket of `limit` requests per
--                                  minute, refilled continuously
--   'quota'                        a counter of the consumer's requests this
--                                  calendar month, at most `limit`; ARGV
--                                  after the pairs: ms until the month ends
--   'auth_fail'                    the client IP's failed key attempts in the
--                                  current window: at `limit` the IP is refused
--                                  until the window ends (nothing is counted here)
--
-- A request is allowed only if every check passes, and only then is
-- anything spent: one token from each bucket, one from the quota. A refused
-- request costs nothing. Redis runs a script as one step, so two gateways
-- can never both take the last token or the last request of a quota.
--
-- Returns { allowed (1/0), remaining, retry_after_ms, limited_by, quota_remaining }:
--   allowed   the bucket the answer describes: the consumer's if there is
--             one, else the one with the fewest tokens left (the workspace's
--             or the client IP's); remaining = its whole tokens left;
--             retry_after_ms = 0; limited_by = its kind
--   refused   the check that holds the request back longest: limited_by =
--             its kind, remaining = what it has left (0), retry_after_ms =
--             time until every check would pass again
--   quota_remaining = requests left this month after this request (-1: no quota)
--
-- With Redis Cluster all keys would have to share a hash slot (a later
-- concern, see decision 6).

local pairs_count = #KEYS
if pairs_count < 1 or #ARGV < 2 * pairs_count then
  return redis.error_reply('want a kind and a limit for every key')
end

-- Redis's own clock, not the gateway's: with several gateways whose clocks
-- differ, time could seem to run backwards. TIME = { seconds, microseconds }.
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local month_left_ms = tonumber(ARGV[2 * pairs_count + 1]) or 0

local allowed = 1
local decider, decider_wait = nil, -1
local tokens, quota_used = {}, {}
local described = nil -- the bucket an allowed answer describes

local function refuse(i, wait)
  -- The longest wait decides; on a tie the later check (the more specific
  -- one) is named.
  if wait >= decider_wait then
    decider, decider_wait = i, wait
  end
  allowed = 0
end

for i = 1, pairs_count do
  local kind = ARGV[2 * i - 1]
  local limit = tonumber(ARGV[2 * i])
  if kind == 'workspace' or kind == 'ip' or kind == 'consumer' then
    local refill_per_ms = limit / 60000
    -- A missing bucket starts full.
    local bucket = redis.call('HMGET', KEYS[i], 'tokens', 'ts')
    local have = tonumber(bucket[1]) or limit
    local ts = tonumber(bucket[2]) or now
    -- Refill for the time since the last request; never above the limit
    -- (that also applies a lowered limit at once).
    tokens[i] = math.min(limit, have + math.max(0, now - ts) * refill_per_ms)
    if kind == 'consumer' or described == nil
       or (ARGV[2 * described - 1] ~= 'consumer' and tokens[i] < tokens[described]) then
      described = i
    end
    if tokens[i] < 1 then
      refuse(i, math.ceil((1 - tokens[i]) / refill_per_ms))
    end
  elseif kind == 'quota' then
    quota_used[i] = tonumber(redis.call('GET', KEYS[i])) or 0
    if quota_used[i] >= limit then
      refuse(i, month_left_ms)
    end
  elseif kind == 'auth_fail' then
    local failures = tonumber(redis.call('GET', KEYS[i])) or 0
    if failures >= limit then
      refuse(i, math.max(redis.call('PTTL', KEYS[i]), 1))
    end
  else
    return redis.error_reply('unknown kind ' .. tostring(kind))
  end
end

local quota_remaining = -1
for i = 1, pairs_count do
  local kind = ARGV[2 * i - 1]
  local limit = tonumber(ARGV[2 * i])
  if tokens[i] ~= nil then
    if allowed == 1 then
      tokens[i] = tokens[i] - 1
    end
    redis.call('HSET', KEYS[i], 'tokens', tokens[i], 'ts', now)
    -- An idle bucket is full again after one minute anyway: drop it after
    -- two, so Redis only keeps buckets that are in use.
    redis.call('PEXPIRE', KEYS[i], 120000)
  elseif kind == 'quota' then
    if allowed == 1 then
      quota_used[i] = redis.call('INCR', KEYS[i])
      -- The month's counter outlives the month a little, then goes.
      if quota_used[i] == 1 then
        redis.call('PEXPIRE', KEYS[i], 35 * 24 * 3600 * 1000)
      end
    end
    quota_remaining = math.max(0, limit - quota_used[i])
  end
end

-- Redis turns Lua numbers into integers, so round explicitly.
if allowed == 1 then
  return { 1, math.floor(tokens[described]), 0, ARGV[2 * described - 1], quota_remaining }
end
local left = 0
if tokens[decider] ~= nil then
  left = math.floor(tokens[decider])
end
return { 0, left, decider_wait, ARGV[2 * decider - 1], quota_remaining }
