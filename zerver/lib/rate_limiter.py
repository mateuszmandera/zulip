import os

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Type

from django.conf import settings
from django.http import HttpRequest
from zerver.lib.exceptions import RateLimited
from zerver.lib.redis_utils import get_redis_client
from zerver.lib.utils import statsd

from zerver.models import UserProfile

import logging
import redis
import time

# Implement a rate-limiting scheme inspired by the one described here, but heavily modified
# https://www.domaintools.com/resources/blog/rate-limiting-with-redis

client = get_redis_client()
rules = settings.RATE_LIMITING_RULES  # type: Dict[str, List[Tuple[int, int]]]

KEY_PREFIX = ''

logger = logging.getLogger(__name__)

class RateLimiterLockingException(Exception):
    pass

class RateLimitedObject(ABC):
    def __init__(self, backend: Optional['Type[RateLimiterBackend]']=None) -> None:
        if backend is not None:
            self.backend = backend  # type: Type[RateLimiterBackend]
        else:
            self.backend = RedisRateLimiterBackend

    def rate_limit(self) -> Tuple[bool, float]:
        # Returns (ratelimited, secs_to_freedom)
        return self.backend.rate_limit_entity(self.key(), self.rules(),
                                              self.max_api_calls(),
                                              self.max_api_window())

    def rate_limit_request(self, request: HttpRequest) -> None:
        ratelimited, time = self.rate_limit()

        if not hasattr(request, '_ratelimits_applied'):
            request._ratelimits_applied = []
        request._ratelimits_applied.append(RateLimitResult(
            entity=self,
            secs_to_freedom=time,
            remaining=0,
            over_limit=ratelimited
        ))
        # Abort this request if the user is over their rate limits
        if ratelimited:
            # Pass information about what kind of entity got limited in the exception:
            raise RateLimited(str(time))

        calls_remaining, seconds_until_reset = self.api_calls_left()

        request._ratelimits_applied[-1].remaining = calls_remaining
        request._ratelimits_applied[-1].secs_to_freedom = seconds_until_reset

    def block_access(self, seconds: int) -> None:
        "Manually blocks an entity for the desired number of seconds"
        self.backend.block_access(self.key(), seconds)

    def unblock_access(self) -> None:
        self.backend.unblock_access(self.key())

    def clear_history(self) -> None:
        self.backend.clear_history(self.key())

    def max_api_calls(self) -> int:
        "Returns the API rate limit for the highest limit"
        return self.rules()[-1][1]

    def max_api_window(self) -> int:
        "Returns the API time window for the highest limit"
        return self.rules()[-1][0]

    def api_calls_left(self) -> Tuple[int, float]:
        """Returns how many API calls in this range this client has, as well as when
        the rate-limit will be reset to 0"""
        max_window = self.max_api_window()
        max_calls = self.max_api_calls()
        return self.backend.get_api_calls_left(self.key(), max_window, max_calls)

    @abstractmethod
    def key(self) -> str:
        pass

    @abstractmethod
    def rules(self) -> List[Tuple[int, int]]:
        pass

class RateLimitedUser(RateLimitedObject):
    def __init__(self, user: UserProfile, domain: str='api_by_user') -> None:
        self.user = user
        self.domain = domain
        super().__init__()

    def key(self) -> str:
        return "{}:{}:{}".format(type(self).__name__, self.user.id, self.domain)

    def rules(self) -> List[Tuple[int, int]]:
        # user.rate_limits are general limits, applicable to the domain 'api_by_user'
        if self.user.rate_limits != "" and self.domain == 'api_by_user':
            result = []  # type: List[Tuple[int, int]]
            for limit in self.user.rate_limits.split(','):
                (seconds, requests) = limit.split(':', 2)
                result.append((int(seconds), int(requests)))
            return result
        return rules[self.domain]

def bounce_redis_key_prefix_for_testing(test_name: str) -> None:
    global KEY_PREFIX
    KEY_PREFIX = test_name + ':' + str(os.getpid()) + ':'

def add_ratelimit_rule(range_seconds: int, num_requests: int, domain: str='api_by_user') -> None:
    "Add a rate-limiting rule to the ratelimiter"
    global rules

    if domain not in rules:
        # If we don't have any rules for domain yet, the domain key needs to be
        # added to the rules dictionary.
        rules[domain] = []

    rules[domain].append((range_seconds, num_requests))
    rules[domain].sort(key=lambda x: x[0])

def remove_ratelimit_rule(range_seconds: int, num_requests: int, domain: str='api_by_user') -> None:
    global rules
    rules[domain] = [x for x in rules[domain] if x[0] != range_seconds and x[1] != num_requests]

class RateLimiterBackend(ABC):
    @classmethod
    @abstractmethod
    def block_access(cls, entity_key: str, seconds: int) -> None:
        "Manually blocks an entity for the desired number of seconds"

    @classmethod
    @abstractmethod
    def unblock_access(cls, entity_key: str) -> None:
        pass

    @classmethod
    @abstractmethod
    def clear_history(cls, entity_key: str) -> None:
        pass

    @classmethod
    @abstractmethod
    def get_api_calls_left(cls, entity_key: str, range_seconds: int,
                           max_calls: int) -> Tuple[int, float]:
        pass

    @classmethod
    @abstractmethod
    def rate_limit_entity(cls, entity_key: str, rules: List[Tuple[int, int]],
                          max_api_calls: int, max_api_window: int) -> Tuple[bool, float]:
        # Returns (ratelimited, secs_to_freedom)
        pass

class RedisRateLimiterBackend(RateLimiterBackend):
    @classmethod
    def get_keys(cls, entity_key: str) -> List[str]:
        return ["{}ratelimit:{}:{}".format(KEY_PREFIX, entity_key, keytype)
                for keytype in ['list', 'zset', 'block']]

    @classmethod
    def block_access(cls, entity_key: str, seconds: int) -> None:
        "Manually blocks an entity for the desired number of seconds"
        _, _, blocking_key = cls.get_keys(entity_key)
        with client.pipeline() as pipe:
            pipe.set(blocking_key, 1)
            pipe.expire(blocking_key, seconds)
            pipe.execute()

    @classmethod
    def unblock_access(cls, entity_key: str) -> None:
        _, _, blocking_key = cls.get_keys(entity_key)
        client.delete(blocking_key)

    @classmethod
    def clear_history(cls, entity_key: str) -> None:
        for key in cls.get_keys(entity_key):
            client.delete(key)

    @classmethod
    def get_api_calls_left(cls, entity_key: str, range_seconds: int,
                           max_calls: int) -> Tuple[int, float]:
        list_key, set_key, _ = cls.get_keys(entity_key)
        # Count the number of values in our sorted set
        # that are between now and the cutoff
        now = time.time()
        boundary = now - range_seconds

        with client.pipeline() as pipe:
            # Count how many API calls in our range have already been made
            pipe.zcount(set_key, boundary, now)
            # Get the newest call so we can calculate when the ratelimit
            # will reset to 0
            pipe.lindex(list_key, 0)

            results = pipe.execute()

        count = results[0]  # type: int
        newest_call = results[1]  # type: Optional[bytes]

        calls_left = max_calls - count
        if newest_call is not None:
            time_reset = now + (range_seconds - (now - float(newest_call)))
        else:
            time_reset = now

        return calls_left, time_reset - now

    @classmethod
    def is_ratelimited(cls, entity_key: str, rules: List[Tuple[int, int]]) -> Tuple[bool, float]:
        "Returns a tuple of (rate_limited, time_till_free)"
        list_key, set_key, blocking_key = cls.get_keys(entity_key)

        # Go through the rules from shortest to longest,
        # seeing if this user has violated any of them. First
        # get the timestamps for each nth items
        with client.pipeline() as pipe:
            for _, request_count in rules:
                pipe.lindex(list_key, request_count - 1)  # 0-indexed list

            # Get blocking info
            pipe.get(blocking_key)
            pipe.ttl(blocking_key)

            rule_timestamps = pipe.execute()  # type: List[Optional[bytes]]

        # Check if there is a manual block on this API key
        blocking_ttl_b = rule_timestamps.pop()
        key_blocked = rule_timestamps.pop()

        if key_blocked is not None:
            # We are manually blocked. Report for how much longer we will be
            if blocking_ttl_b is None:
                blocking_ttl = 0.5
            else:
                blocking_ttl = int(blocking_ttl_b)
            return True, blocking_ttl

        if len(rules) == 0:
            return False, 0.0

        now = time.time()
        for timestamp, (range_seconds, num_requests) in zip(rule_timestamps, rules):
            # Check if the nth timestamp is newer than the associated rule. If so,
            # it means we've hit our limit for this rule
            if timestamp is None:
                continue

            boundary = float(timestamp) + range_seconds
            if boundary >= now:
                free = boundary - now
                return True, free

        return False, 0.0

    @classmethod
    def incr_ratelimit(cls, entity_key: str, rules: List[Tuple[int, int]],
                       max_api_calls: int, max_api_window: int) -> None:
        """Increases the rate-limit for the specified entity"""
        list_key, set_key, _ = cls.get_keys(entity_key)
        now = time.time()

        # If we have no rules, we don't store anything
        if len(rules) == 0:
            return

        # Start redis transaction
        with client.pipeline() as pipe:
            count = 0
            while True:
                try:
                    # To avoid a race condition between getting the element we might trim from our list
                    # and removing it from our associated set, we abort this whole transaction if
                    # another agent manages to change our list out from under us
                    # When watching a value, the pipeline is set to Immediate mode
                    pipe.watch(list_key)

                    # Get the last elem that we'll trim (so we can remove it from our sorted set)
                    last_val = pipe.lindex(list_key, max_api_calls - 1)

                    # Restart buffered execution
                    pipe.multi()

                    # Add this timestamp to our list
                    pipe.lpush(list_key, now)

                    # Trim our list to the oldest rule we have
                    pipe.ltrim(list_key, 0, max_api_calls - 1)

                    # Add our new value to the sorted set that we keep
                    # We need to put the score and val both as timestamp,
                    # as we sort by score but remove by value
                    pipe.zadd(set_key, {str(now): now})

                    # Remove the trimmed value from our sorted set, if there was one
                    if last_val is not None:
                        pipe.zrem(set_key, last_val)

                    # Set the TTL for our keys as well
                    api_window = max_api_window
                    pipe.expire(list_key, api_window)
                    pipe.expire(set_key, api_window)

                    pipe.execute()

                    # If no exception was raised in the execution, there were no transaction conflicts
                    break
                except redis.WatchError:
                    if count > 10:
                        raise RateLimiterLockingException()
                    count += 1

                    continue

    @classmethod
    def rate_limit_entity(cls, entity_key: str, rules: List[Tuple[int, int]],
                          max_api_calls: int, max_api_window: int) -> Tuple[bool, float]:
        ratelimited, time = cls.is_ratelimited(entity_key, rules)

        if ratelimited:
            statsd.incr("ratelimiter.limited.%s" % (entity_key,))

        else:
            try:
                cls.incr_ratelimit(entity_key, rules, max_api_calls, max_api_window)
            except RateLimiterLockingException:
                logger.warning("Deadlock trying to incr_ratelimit for %s" % (entity_key,))
                # rate-limit users who are hitting the API so hard we can't update our stats.
                ratelimited = True

        return ratelimited, time

class RateLimitResult:
    def __init__(self, entity: RateLimitedObject, secs_to_freedom: float, over_limit: bool,
                 remaining: int) -> None:
        if over_limit:
            assert not remaining

        self.entity = entity
        self.secs_to_freedom = secs_to_freedom
        self.over_limit = over_limit
        self.remaining = remaining
