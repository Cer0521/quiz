import httpx
import asyncio

async def run_tests():
    print("Running API Tests...")
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
        # Test 1: Health Check
        print("1. Testing GET /api/health")
        try:
            response = await client.get("/api/health")
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.json()}")
        except Exception as e:
            print(f"   Error: {e}")

        # Test 2: Forgot Password (Unauthenticated endpoint)
        print("\n2. Testing POST /api/auth/forgot-password")
        try:
            response = await client.post("/api/auth/forgot-password", json={"email": "test@example.com"})
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.json()}")
        except Exception as e:
            print(f"   Error: {e}")

if __name__ == "__main__":
    asyncio.run(run_tests())
