import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.middleware.security import RateLimitMiddleware

class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_outage_is_bounded_and_recovers(self):
        app = FastAPI()
        @app.post('/auth/clinical/hospital-code')
        async def lookup():
            return {'hospital_code': 'TEST'}
        middleware = RateLimitMiddleware(app)
        middleware._redis_timeout = 0.02
        redis = AsyncMock()
        async def hang(*args):
            await asyncio.sleep(60)
        redis.eval.side_effect = hang
        middleware._redis = redis
        with patch('app.middleware.security.settings.RATE_LIMIT_ENABLED', True):
            async with AsyncClient(transport=ASGITransport(app=middleware), base_url='http://test') as client:
                start = time.monotonic()
                self.assertEqual((await client.post('/auth/clinical/hospital-code')).status_code, 200)
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual((await client.post('/auth/clinical/hospital-code')).status_code, 200)
                self.assertEqual(redis.eval.await_count, 1)
                middleware._retry_after = 0
                redis.eval.side_effect = None
                redis.eval.return_value = 11
                response = await client.post('/auth/clinical/hospital-code')
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers['retry-after'], '60')
                redis.eval.return_value = 1
                self.assertEqual((await client.post('/auth/clinical/hospital-code')).status_code, 200)

if __name__ == '__main__':
    unittest.main()
