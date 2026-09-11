/**
 * The MechKernel subprocess emits Chinese `narrative` strings (they are baked
 * into the kernel, which is a separate repo). The agent loop forwards them as the
 * tool-card summary, so an English UI showed raw Chinese. Translate the known
 * narrative shapes here, at the presentation boundary.
 *
 * Anything unrecognised is returned unchanged: a Chinese label is better than a
 * dropped one, and the regexes are anchored on the kernel's own formats
 * (see mech_kernel/kernel.py `narrative=f"..."`).
 */
const RULES: Array<[RegExp, (m: RegExpMatchArray) => string]> = [
  [/^创建工作平面\s+(\S+)(?:\s+\((\w+)\))?$/, (m) => `create workplane ${m[1]}${m[2] ? ` (${m[2]})` : ""}`],
  [/^创建草图\s+(\S+)$/, (m) => `start sketch ${m[1]}`],
  [/^关闭草图\s+(\S+)$/, (m) => `close sketch ${m[1]}`],
  [/^画矩形\s+(.+)$/, (m) => `add rectangle ${m[1]}`],
  [/^画圆\s+r=(.+)$/, (m) => `add circle r=${m[1]}`],
  [/^画线$/, () => "add line"],
  [/^拉伸\s+(\S+)\s+深度\s+(\S+)$/, (m) => `extrude ${m[1]} depth ${m[2]}`],
  [/^旋转\s+(\S+)\s+角度\s+(\S+)$/, (m) => `revolve ${m[1]} ${m[2]}`],
  [/^扫掠\s+(\S+)\s+沿\s+(\S+)$/, (m) => `sweep ${m[1]} along ${m[2]}`],
  [/^抽壳\s+t=(\S+)\s+面\s+(\S+)$/, (m) => `shell t=${m[1]} faces ${m[2]}`],
  [/^镜像\s+(\S+)\s+沿\s+(\S+)$/, (m) => `mirror ${m[1]} about ${m[2]}`],
  [/^添加约束\s+(\S+)$/, (m) => `add constraint ${m[1]}`],
  [/^求解草图\s+(\S+)$/, (m) => `solve sketch ${m[1]}`],
  [/^生成齿轮\s+m=(\S+)\s+z=(\S+)\s+b=(\S+)（(.+?)）$/, (m) => `gear m=${m[1]} z=${m[2]} b=${m[3]} (${m[4]})`],
  [/^撤销\s+(\d+)\s+步$/, (m) => `undo ${m[1]} step(s)`],
  [/^导出\s+(\S+)\s+→\s+(.+?)\s+\(\d+\s+bytes\)$/, (m) => `export ${m[1]} → ${m[2]}`],
  [/^加载项目\s+←\s+(.+)$/, (m) => `load project ← ${m[1]}`],
  [/^导入\s+STEP\s+←\s+(.+)$/, (m) => `import STEP ← ${m[1]}`],
  [/^重建：重放\s+(\d+)\s+个\s+op$/, (m) => `rebuild: replay ${m[1]} ops`],
  [/^删除\s+(\S+)（重放\s+(\d+)\s+个\s+op）$/, (m) => `delete ${m[1]} (replay ${m[2]} ops)`],
  [/^更新\s+(\S+)（重放\s+(\d+)\s+个\s+op）$/, (m) => `update ${m[1]} (replay ${m[2]} ops)`],
  [/^circular_pattern\s+(\d+)\s+个副本$/, (m) => `circular_pattern ×${m[1]}`],
  [/^run_script\s+(\S+)\s+完成:\s+(\d+)\s+个\s+op,\s+solids=(\d+)$/, (m) => `run_script ${m[1]}: ${m[2]} ops, ${m[3]} solids`],
  [/^查询参考系\s+(\S+)$/, (m) => `query reference ${m[1]}`],
  [/^查询装配实例\s+(\S+)$/, (m) => `query instance ${m[1]}`],
  [/^创建参考系\s+(\S+)$/, (m) => `create reference ${m[1]}`],
  [/^零件已归档:\s+(.+)$/, (m) => `part archived: ${m[1]}`],
  [/^几何已更新，3D\s+网格已导出$/, () => "geometry updated, mesh exported"],
  [/^几何快照已更新$/, () => "geometry snapshot updated"],
  [/^计划已批准$/, () => "plan approved"],
  [/^计划进度更新$/, () => "plan progress updated"],
];

const CJK = /[\u4e00-\u9fff]/;

/** Translate a kernel narrative for display; leave English/unknown text alone. */
export function translateNarrative(text: string): string {
  if (!text) return text;
  const s = text.trim();
  if (!CJK.test(s)) return text;
  for (const [re, fn] of RULES) {
    const m = s.match(re);
    if (m) return fn(m);
  }
  return text;
}
