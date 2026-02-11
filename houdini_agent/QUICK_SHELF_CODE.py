"""
Houdini Agent - Quick Shelf Tool Script
Copy this code into a Houdini Shelf Tool for one-click launch.
"""

import sys
import os

# Tool path
tool_path = r"C:\path\to\Houdini-Agent"
if tool_path not in sys.path:
    sys.path.insert(0, tool_path)

# Reload module (support hot-reload)
if 'main' in sys.modules:
    import importlib
    import main
    importlib.reload(main)
else:
    import main

# Launch
main.show_tool()
