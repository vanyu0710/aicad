"""变速箱运行验收：数据统计 + 装配四视图 + 可视化海报（v0.17）。

用法: .\\.venv\\Scripts\\python.exe scripts/verify_transmission.py <project_id>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import subprocess

AICAD = ROOT
KERNEL = Path(r"G:\lfy design\ai cad\mechcad-kernel")
KP = KERNEL / ".venv/Scripts/python.exe"


def main() -> int:
    pid = sys.argv[1] if len(sys.argv) > 1 else None
    if not pid:
        libs = sorted((AICAD / "work/project_parts").glob("llm-trans-*"),
                      key=lambda p: p.stat().st_mtime)
        if not libs:
            print("no llm-trans-* project found")
            return 1
        lib = libs[-1]
        pid = lib.name
    else:
        lib = AICAD / "work/project_parts" / pid
    print("project:", pid, "->", lib)

    man = json.loads((lib / "parts_manifest.json").read_text(encoding="utf-8"))
    parts = [p for p in man.get("parts", []) if isinstance(p, dict)]
    active = [p for p in parts if p.get("status") == "active"]
    print(f"parts: {len(parts)} (active {len(active)}, superseded {len(parts)-len(active)})")
    for p in parts:
        print(f"  {p.get('status','?'):10} {p.get('name',''):28} v{p.get('version')} "
              f"{p.get('step_file','')} vol={p.get('volume_mm3')}")

    asm = man.get("assembly")
    print("assembly:", json.dumps(asm, ensure_ascii=False)[:300] if asm else None)

    # 用内核渲染 hero + 每件 iso
    helper = KERNEL / "work_verify_trans.py"
    helper.write_text(
        "import sys, json\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, '.')\n"
        "import mech_kernel\n"
        "from mech_kernel.assembly_scene import render_assembly\n"
        "from mech_kernel.renderer import Renderer\n"
        "from build123d.importers import import_step\n"
        f"lib = Path(r'{lib}')\n"
        "man = json.loads((lib/'parts_manifest.json').read_text(encoding='utf-8'))\n"
        "active = [e for e in man['parts'] if e.get('status')=='active']\n"
        "payload = [{'path': str((lib/e['step_file']).resolve()), 'name': e['name'],\n"
        "            'pose': e.get('pose') or {'position':[0,0,0]}} for e in active]\n"
        f"out = Path(r'{KERNEL}')\n"
        "try:\n"
        "    grid = render_assembly(payload, size=520)\n"
        "    (out/'work_trans_hero.png').write_bytes(grid)\n"
        "    print('hero', len(grid)//1024, 'KB')\n"
        "except Exception as ex:\n"
        "    print('hero FAIL', type(ex).__name__, str(ex)[:200])\n"
        "vdir = out/'work_trans_parts'; vdir.mkdir(exist_ok=True)\n"
        "r = Renderer(image_size=(300,300), dpi=100)\n"
        "for e in active:\n"
        "    try:\n"
        "        part = import_step(str(lib/e['step_file']))\n"
        "        v = r.render(part, level='iso_only', views=['iso'], image_size=(300,300),\n"
        "                     show_edges=True, annotate=False, quality='presentation')\n"
        "        png = v.get('iso')\n"
        "        if png: (vdir/f\"{e['name']}.png\").write_bytes(png)\n"
        "    except Exception as ex:\n"
        "        print('part FAIL', e['name'], str(ex)[:80])\n"
        "print('parts rendered')\n",
        encoding="utf-8")
    t0 = time.time()
    subprocess.run([str(KP), str(helper)], cwd=str(KERNEL))
    print(f"render {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
