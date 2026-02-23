#!/usr/bin/env python3
"""
Sakere — Personal Media Center
Run with: python run.py
"""
import sys
import os

# Ensure we run from the correct directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  🎬  Sakere Media Center")
    print("="*50)
    print("  URL: http://localhost:7575")
    print("  Press Ctrl+C to stop")
    print("="*50 + "\n")
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=7575,
        reload=False,
        log_level="warning",
    )
