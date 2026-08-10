import json, os

# Intermediate JSON produced by scripts/build_tree.py.
JSON_IN = "build/tag_tree.json"
# Final viewer, written to the repo root (consistent with tag_tree_explorer.html).
HTML_OUT = "tag_tree_explorer.html"

with open(JSON_IN, "r", encoding="utf-8") as f:
    tree = json.load(f)

tree_json = json.dumps(tree, ensure_ascii=False)

html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V2融合世界标签体系 - 树形浏览器</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f5f5f5; color: #333; }
#header { background: #fff; padding: 10px 20px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; position: sticky; top: 0; z-index: 100; }
#title { font-size: 16px; font-weight: 700; color: #1a1a1a; white-space: nowrap; }
#stats { font-size: 12px; color: #888; }
#search { flex: 1; min-width: 150px; padding: 6px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; outline: none; }
#search:focus { border-color: #4e79a7; }
.btn { padding: 5px 12px; border: 1px solid #ddd; border-radius: 6px; background: #fff; cursor: pointer; font-size: 12px; color: #555; white-space: nowrap; }
.btn:hover { background: #f0f0f0; }
#breadcrumb { padding: 5px 20px; background: #fafafa; border-bottom: 1px solid #eee; font-size: 12px; color: #999; cursor: pointer; }
#breadcrumb:hover { color: #4e79a7; }
#tree-container { padding: 8px 20px; overflow-x: auto; }
.node-row { display: flex; align-items: center; padding: 2px 6px; border-radius: 4px; cursor: pointer; white-space: nowrap; line-height: 1.8; }
.node-row:hover { background: #e8f0fe; }
.instance-row { background: #fcfcfd; }
.instance-row:hover { background: #fff8e1; }
.toggle { display: inline-block; width: 16px; text-align: center; font-size: 11px; color: #999; user-select: none; flex-shrink: 0; }
.toggle.collapsed { transform: rotate(-90deg); }
.toggle.leaf { visibility: hidden; }
.node-icon { margin-right: 5px; font-size: 13px; flex-shrink: 0; }
.node-name { font-size: 13px; color: #333; }
.instance-name { font-size: 12px; color: #b06ab3; font-style: italic; }
.node-count { font-size: 10px; color: #aaa; margin-left: 5px; background: #f0f0f0; padding: 0 5px; border-radius: 8px; }
.node-count.inst { background: #f3e8f7; color: #b06ab3; }
.children { margin-left: 18px; border-left: 1px dashed #ddd; padding-left: 4px; }
.highlight { background: #fff3cd !important; }
.search-match { color: #e15759; font-weight: 600; }
#result-count { font-size: 12px; color: #888; margin-left: 8px; }
</style>
</head>
<body>
<div id="header">
  <div id="title">V2融合世界标签体系</div>
  <div id="stats"></div>
  <select id="filter" onchange="applyFilter()" style="padding:5px 8px;border:1px solid #ddd;border-radius:6px;font-size:12px;">
    <option value="all">全部</option>
    <option value="ip" selected>IP 分类标签</option>
    <option value="general">通用分类标签</option>
  </select>
  <input type="text" id="search" placeholder="搜索标签名或实例..." />
  <span id="result-count"></span>
  <button class="btn" onclick="expandAll()">展开可见</button>
  <button class="btn" onclick="collapseAll()">全部折叠</button>
</div>
<div id="breadcrumb" onclick="copyPath()">点击节点查看路径</div>
<div id="tree-container"></div>

<script>
const treeData = ''' + tree_json + ''';

function countNodes(n) {
    let c = 1;
    if (n.children) for (const ch of n.children) c += countNodes(ch);
    return c;
}

document.getElementById("stats").textContent = countNodes(treeData) + " 节点";

let currentRoot = null;
const container = document.getElementById("tree-container");

function getFilteredRoot() {
    const filter = document.getElementById("filter").value;
    if (filter === "all") return treeData;
    if (filter === "ip") {
        const ip = treeData.children.find(c => c.name.includes("IP"));
        return ip || treeData;
    }
    if (filter === "general") {
        const g = treeData.children.find(c => c.name.includes("通用"));
        return g || treeData;
    }
    return treeData;
}

// Lazy render: children DOM created only on expand. Instances rendered as sub-items.
function createNode(node, depth) {
    const isInstance = node._instance === true;
    const hasChildren = node.children && node.children.length > 0;
    const hasInstances = node.instances && node.instances.length > 0;
    const wrap = document.createElement("div");

    const row = document.createElement("div");
    row.className = "node-row" + (isInstance ? " instance-row" : "");

    const toggle = document.createElement("span");
    if (isInstance) {
        toggle.className = "toggle leaf";
        toggle.textContent = "\\u25C6";
    } else {
        toggle.className = "toggle" + ((hasChildren || hasInstances) ? " collapsed" : " leaf");
        toggle.textContent = "\\u25BC";
    }
    row.appendChild(toggle);

    const icon = document.createElement("span");
    icon.className = "node-icon";
    icon.textContent = isInstance ? "\\u25C6" : (hasChildren || hasInstances ? "\\uD83D\\uDCC2" : "\\uD83C\\uDF96");
    row.appendChild(icon);

    const name = document.createElement("span");
    name.className = "node-name" + (isInstance ? " instance-name" : "");
    name.textContent = node.name;
    row.appendChild(name);

    if (!isInstance && (hasChildren || hasInstances)) {
        const cnt = document.createElement("span");
        cnt.className = "node-count" + (hasInstances && !hasChildren ? " inst" : "");
        cnt.textContent = hasChildren ? node.children.length : (node.instances.length + " \\u5B9E\\u4F8B");
        row.appendChild(cnt);
    }

    row.onclick = function(e) {
        e.stopPropagation();
        document.getElementById("breadcrumb").textContent = node._path || node.name;
    };

    if (!isInstance && (hasChildren || hasInstances)) {
        let expanded = false;
        let childrenDiv = null;
        toggle.onclick = function(e) {
            e.stopPropagation();
            if (!expanded) {
                if (!childrenDiv) {
                    childrenDiv = document.createElement("div");
                    childrenDiv.className = "children";
                    if (hasChildren) {
                        for (const child of node.children) {
                            childrenDiv.appendChild(createNode(child, depth + 1));
                        }
                    }
                    if (hasInstances) {
                        for (const inst of node.instances) {
                            const instNode = {
                                name: inst,
                                _instance: true,
                                _path: (node._path || node.name) + " / " + inst,
                                children: []
                            };
                            childrenDiv.appendChild(createNode(instNode, depth + 1));
                        }
                    }
                    wrap.appendChild(childrenDiv);
                }
                childrenDiv.style.display = "";
                toggle.classList.remove("collapsed");
                expanded = true;
            } else {
                childrenDiv.style.display = "none";
                toggle.classList.add("collapsed");
                expanded = false;
            }
        };
    }

    wrap.appendChild(row);
    return wrap;
}

function renderTree() {
    container.innerHTML = "";
    currentRoot = getFilteredRoot();
    container.appendChild(createNode(currentRoot, 0));
    const rootToggle = container.querySelector("#tree-container > div > .node-row > .toggle");
    if (rootToggle) rootToggle.click();
    document.getElementById("stats").textContent = countNodes(currentRoot) + " 节点";
}

function applyFilter() {
    document.getElementById("search").value = "";
    document.getElementById("result-count").textContent = "";
    renderTree();
}

function expandAll() {
    container.querySelectorAll(".toggle.collapsed:not(.leaf)").forEach(t => {
        const p = t.parentElement.parentElement;
        if (p.style.display !== "none") t.click();
    });
}

function collapseAll() {
    container.querySelectorAll(".toggle:not(.collapsed):not(.leaf)").forEach(t => t.click());
}

function copyPath() {
    const text = document.getElementById("breadcrumb").textContent;
    if (!text || text === "点击节点查看路径") return;
    navigator.clipboard.writeText(text).then(() => {
        const orig = document.getElementById("breadcrumb").textContent;
        document.getElementById("breadcrumb").textContent = "\\u2705 \\u5DF2\\u590D\\u5236: " + text;
        setTimeout(() => { document.getElementById("breadcrumb").textContent = orig; }, 1500);
    });
}

let searchTimer;
document.getElementById("search").addEventListener("input", function() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(doSearch, 300);
});

function doSearch() {
    const query = document.getElementById("search").value.trim().toLowerCase();
    const rc = document.getElementById("result-count");

    if (!query) {
        rc.textContent = "";
        renderTree();
        return;
    }

    const matches = [];
    function search(node, parents) {
        const selfMatch = node.name.toLowerCase().includes(query);
        let matchedInst = [];
        if (node.instances) {
            for (const inst of node.instances) {
                if (inst.toLowerCase().includes(query)) matchedInst.push(inst);
            }
        }
        if (selfMatch || matchedInst.length > 0) {
            const synChildren = [];
            if (matchedInst.length > 0) {
                for (const inst of matchedInst) {
                    synChildren.push({
                        name: inst, _instance: true,
                        _path: (node._path || node.name) + " / " + inst, children: []
                    });
                }
            }
            const syn = {
                name: node.name, _path: node._path,
                _instance: node._instance || false,
                instances: null, children: synChildren
            };
            matches.push({ syn: syn, parents: parents.slice() });
        }
        if (node.children) {
            for (const ch of node.children) search(ch, parents.concat([node]));
        }
    }
    search(currentRoot, []);

    rc.textContent = matches.length + " \\u4E2A\\u7ED3\\u679C";
    container.innerHTML = "";

    if (matches.length === 0) {
        container.innerHTML = '<div style="padding:20px;color:#999;">\\u672A\\u627E\\u5230\\u5339\\u914D</div>';
        return;
    }

    const seen = new Set();
    for (const m of matches) {
        const pathKey = (m.parents.concat([m.syn])).map(n => n.name).join(" > ");
        if (seen.has(pathKey)) continue;
        seen.add(pathKey);
        container.appendChild(renderSyntheticMatch(m.syn, m.parents));
    }
}

function renderSyntheticMatch(synNode, parentChain) {
    let rootSyn = { name: currentRoot.name, _path: currentRoot._path, children: [] };
    let cursor = rootSyn;
    for (const p of parentChain) {
        const child = { name: p.name, _path: p._path, children: [] };
        cursor.children.push(child);
        cursor = child;
    }
    cursor.children.push(synNode);

    const wrap = createNode(rootSyn, 0);
    wrap.querySelectorAll(".toggle.collapsed").forEach(t => t.click());
    return wrap;
}

// Initial render
renderTree();
</script>
</body>
</html>'''

with open(HTML_OUT, "w", encoding="utf-8", errors="surrogatepass") as f:
    f.write(html)

print("HTML saved to", HTML_OUT)
print("Size:", len(html), "bytes")
