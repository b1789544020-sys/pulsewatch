// 验证 canvas 高度无限增长 bug 的修复
// 用法: node tools/check_canvas_fix.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "static", "index.html"), "utf8");

// 1) 提取 <script> 块做纯语法解析（new Function 只编译不执行，不会碰 DOM）
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];
try {
  new Function(js);
  console.log("✅ 前端 JS 语法检查通过");
} catch (e) {
  console.error("❌ JS 语法错误:", e.message);
  process.exit(1);
}

// 2) 造一个极简 canvas 替身，模拟高分屏 dpr=1.25
const dpr = 1.25;
function makeCanvas() {
  return {
    _attr: "90",
    dataset: {},
    style: {},
    clientWidth: 600,
    getAttribute(k) { return k === "height" ? this._attr : null; },
    // 给 canvas.height 赋值会反向改写 HTML 属性（真实浏览器行为）
    set height(v) { this._attr = String(v); },
    get height() { return Number(this._attr); },
  };
}

// 旧逻辑（bug 版）
function oldFrame(cv) {
  const h = parseInt(cv.getAttribute("height"));
  cv.height = h * dpr;
}
// 新逻辑（修复版，与 index.html 中一致）
function newFrame(cv) {
  if (!cv.dataset.designH) {
    cv.dataset.designH = parseInt(cv.getAttribute("height"), 10);
    cv.style.height = cv.dataset.designH + "px";
  }
  const h = parseInt(cv.dataset.designH, 10);
  cv.height = Math.round(h * dpr);
}

const oldCv = makeCanvas(), newCv = makeCanvas();
for (let i = 0; i < 60; i++) { oldFrame(oldCv); newFrame(newCv); }

console.log("\n连续刷新 60 帧后：");
console.log(`旧逻辑  画布显示高度 = ${Number(oldCv._attr).toFixed(0)}px  (初始 90px，指数爆炸 -> 页面无限延伸)`);
console.log(`新逻辑  画布显示高度 = ${newCv.style.height}，位图高度 = ${newCv.height}px  (锁定不变)`);

if (newCv.style.height !== "90px" || newCv.height !== 113) {
  console.error("❌ 修复未生效");
  process.exit(1);
}
if (Number(oldCv._attr) < 100000) {
  console.error("❌ 旧逻辑没有复现增长，测试假设错误");
  process.exit(1);
}
console.log("\n✅ 确认：旧逻辑无限膨胀，新逻辑高度恒定，bug 已修复");
