#!/usr/bin/env python3
import requests
import sys

try:
    print("Testing gateway on http://localhost:8000...")
    r = requests.get('http://localhost:8000', timeout=5)
    print(f"✅ Status: {r.status_code}")
    print(f"✅ Content-Type: {r.headers.get('content-type', 'N/A')}")
    print(f"✅ HTML Content Length: {len(r.text)} bytes")
    
    if "Gateway is running" in r.text or "Context-Aware" in r.text:
        print("\n✅ Gateway is serving HTML successfully!")
        print("\n🌐 Access the UI at: http://localhost:8000")
    else:
        print("\n⚠️  Gateway responded but content is unexpected")
        print(f"First 200 chars: {r.text[:200]}")
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
