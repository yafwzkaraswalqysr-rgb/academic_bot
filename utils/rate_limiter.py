import time
from collections import defaultdict,deque
from config import RATE_LIMIT_PER_MINUTE
class RateLimiter:
    def __init__(self,max_requests=RATE_LIMIT_PER_MINUTE,window=60,max_users=10000): self.max_requests=max_requests; self.window=window; self.max_users=max_users; self.requests=defaultdict(deque)
    def is_allowed(self,user_id):
        q=self.requests[user_id]; now=time.time()
        while q and now-q[0]>=self.window:q.popleft()
        if len(q)>=self.max_requests:return False
        if len(self.requests) > self.max_users:
            stale=[uid for uid, items in self.requests.items() if not items or now-items[-1] >= self.window]
            for uid in stale[:max(1, len(stale)//4)]: self.requests.pop(uid, None)
        q.append(now); return True
    def get_wait_time(self,user_id):
        q=self.requests[user_id]
        return max(1,int(self.window-(time.time()-q[0]))) if q else 0
rate_limiter=RateLimiter()
