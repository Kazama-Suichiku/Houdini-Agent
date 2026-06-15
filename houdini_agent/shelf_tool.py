"""
Houdini Agent - Shelf Tool Entry Point
Copy this code into a Houdini Shelf Tool to launch the agent.
"""

import sys
import os

# Add tool path to Python path
tool_path = r"C:\path\to\Houdini-Agent"
if tool_path not in sys.path:
    sys.path.insert(0, tool_path)

try:
    if 'main' in sys.modules:
        import importlib
        import main
        importlib.reload(main)
    else:
        import main
    
    main.show_tool()
    
except Exception as e:
    import hou
    hou.ui.displayMessage(
        f"Failed to launch Houdini Agent:\n\n{str(e)}", 
        severity=hou.severityType.Error,
        title="Houdini Agent Error"
    )
    import traceback
    print("=" * 60)
    print("Houdini Agent Error Traceback:")
    print("=" * 60)
    traceback.print_exc()
    print("=" * 60)
