import json
from pathlib import Path

try:
    path = Path(r"C:\home\chenkunze\data\m2603codeyun\codepc_mf\fanxiu\data-annotation\entries\30b82d72-8a76-4a74-be4b-4fc1591c6ce2\asset-tree.json")
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        tree = data.get("tree", []) if isinstance(data, dict) else data
        
        results = []
        for scene in tree:
            for shape in scene.get('shapes', []):
                if '镇邪' in shape.get('name', ''):
                    results.append({"scene_id": scene.get("id"), "shape": shape})
        
        print("Found shapes:", json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("File does not exist.")
except Exception as e:
    import traceback
    traceback.print_exc()
