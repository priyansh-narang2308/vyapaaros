import re

with open('src/apps_sdk/tools/recommendations.py', 'r') as f:
    content = f.read()

mock_logic = '''async def call_search_agent(
    query: str,
    category: str | None,
    limit: int,
) -> dict[str, Any]:
    """Mocked search agent that bypasses heavy NAT agents to prevent Mac freezing."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{MERCHANT_API_URL}/products")
            if response.status_code != 200:
                return {"results": [], "error": f"Merchant API error: {response.status_code}"}
            
            all_products = response.json()
            
            q = query.lower()
            results = []
            for p in all_products:
                name = p.get("name", "").lower()
                desc = p.get("description", "").lower()
                cat = p.get("category", "").lower()
                
                if category and category.lower() != cat:
                    continue
                
                if q in name or q in desc or q in cat:
                    results.append({"product_id": p["id"], "score": 0.9, "similarity": 0.9})
            
            # Fallback to random products to simulate vector similarity "best effort"
            if not results and all_products:
                import random
                for p in random.sample(all_products, min(3, len(all_products))):
                    results.append({"product_id": p["id"], "score": 0.5, "similarity": 0.5})
                    
            return {"query": query, "results": results[:limit]}
            
    except Exception as e:
        return {"results": [], "error": str(e)}'''

# Replace from "async def call_search_agent(" down to the next function or end of the try/except block
pattern = re.compile(r'async def call_search_agent\(.*?\)\s*->\s*dict\[str, Any\]:.*?(?=async def |\Z)', re.DOTALL)
content = pattern.sub(mock_logic + '\n\n\n', content)

with open('src/apps_sdk/tools/recommendations.py', 'w') as f:
    f.write(content)
