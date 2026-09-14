import json
from pathlib import Path

try:
    path = Path(r"C:\home\chenkunze\data\m2603codeyun\codepc_mf\fanxiu\data-annotation\asset-tree.json")
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        tree = data.get("tree", []) if isinstance(data, dict) else data
        
        for scene in tree:
            if str(scene.get('id')) == '63':
                print(f"Scene 63 found, shapes count: {len(scene.get('shapes', []))}")
                print(json.dumps([s.get('name') for s in scene.get('shapes', [])], ensure_ascii=False, indent=2))
    else:
        print("File does not exist.")
except Exception as e:
    import traceback
    traceback.print_exc()
