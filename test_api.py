#!/usr/bin/env python3
"""
Quick test script to verify the /prune API endpoint is working correctly.
Run this after starting the FastAPI gateway.

Usage:
  python test_api.py
"""

import requests
import json
import sys
from typing import Any

BASE_URL = "http://localhost:8000"
TIMEOUT = 10

def test_skeleton():
    """Test /skeleton endpoint"""
    print("=" * 60)
    print("Testing /skeleton endpoint...")
    print("=" * 60)
    try:
        resp = requests.get(f"{BASE_URL}/skeleton", timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        print(f"✅ Status: {resp.status_code}")
        print(f"✅ Found {data.get('file_count', 0)} files")
        print(f"✅ Found {data.get('total_symbols', 0)} symbols")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_prune():
    """Test /prune endpoint"""
    print("\n" + "=" * 60)
    print("Testing /prune endpoint...")
    print("=" * 60)
    
    payload = {
        "user_query": "Show me the React component structure",
        "goal_hint": "Focus on Dashboard",
        "file_paths": [],
        "max_tokens": 80000,
        "compression_target": 0.5,
        "provider": "anthropic",
        "system_instructions": "You are a helpful assistant"
    }
    
    print(f"Sending query: '{payload['user_query']}'")
    
    try:
        resp = requests.post(
            f"{BASE_URL}/prune",
            json=payload,
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        
        print(f"✅ Status: {resp.status_code}")
        print(f"✅ Response received ({len(json.dumps(data))} bytes)")
        
        # Validate response structure
        required_keys = ["pruned_files", "stats", "cache_info"]
        for key in required_keys:
            if key in data:
                print(f"✅ Field '{key}' present")
            else:
                print(f"❌ Field '{key}' MISSING")
                return False
        
        # Check stats
        stats = data.get("stats", {})
        print(f"\n  📊 Stats:")
        print(f"     Token Savings: {stats.get('token_savings_pct', 0):.1f}%")
        print(f"     Compression:   {stats.get('compression_ratio', 0):.2f}x")
        print(f"     Raw Tokens:    {stats.get('total_raw_tokens', 0)}")
        print(f"     Pruned Tokens: {stats.get('total_pruned_tokens', 0)}")
        print(f"     Files:         {stats.get('files_processed', 0)}")
        
        # Check cache info
        cache = data.get("cache_info", {})
        print(f"\n  ⚡ Cache Info:")
        print(f"     Cache Hit:     {cache.get('cache_hit_likely', False)}")
        print(f"     Code Hash:     {cache.get('code_hash', 'N/A')[:16]}...")
        print(f"     System Tokens: {cache.get('system_tokens', 0)}")
        print(f"     Code Tokens:   {cache.get('code_tokens', 0)}")
        print(f"     Query Tokens:  {cache.get('query_tokens', 0)}")
        
        # Check files
        files = data.get("pruned_files", [])
        print(f"\n  📄 Pruned Files: {len(files)}")
        for f in files[:3]:  # Show first 3
            print(f"     - {f.get('file_path', 'unknown')}")
            print(f"       {f.get('raw_tokens', 0)} → {f.get('pruned_tokens', 0)} tokens")
        
        print(f"\n  ⏱️  Elapsed: {data.get('elapsed_ms', 0):.1f}ms")
        
        return True
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Cannot connect to {BASE_URL}")
        print("   Make sure the FastAPI gateway is running:")
        print("   python -m server.gateway")
        return False
    except requests.exceptions.Timeout:
        print(f"❌ Request timed out after {TIMEOUT}s")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_index():
    """Test /index endpoint"""
    print("\n" + "=" * 60)
    print("Testing /index endpoint...")
    print("=" * 60)
    try:
        resp = requests.post(f"{BASE_URL}/index", json={}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        print(f"✅ Status: {resp.status_code}")
        print(f"✅ Indexed {data.get('total_symbols', 0)} symbols")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def main():
    print("\n🧪 Context-Aware Pruning Gateway API Test\n")
    
    results = {
        "skeleton": test_skeleton(),
        "prune": test_prune(),
        "index": test_index(),
    }
    
    print("\n" + "=" * 60)
    print("📋 Test Summary")
    print("=" * 60)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(results.values())
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ All tests passed! System is ready.")
        print("\nNext steps:")
        print("1. Open http://localhost:5173 in your browser")
        print("2. Enter a query about your codebase")
        print("3. Click 'Prune & Assemble'")
        print("4. Watch the TokenGauge animate to show savings %")
    else:
        print("❌ Some tests failed. Debug items above.")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  Tests interrupted")
        sys.exit(0)
