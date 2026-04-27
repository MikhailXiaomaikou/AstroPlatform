"""run_python 生成的 figures (base64 PNG) 必须出现在 chat 导出文件里.

之前 /api/export/report/from-chat (markdown) + /api/export/notebook/from-chat
(ipynb) 完全没读 action.tool_result.figures, 用户下载的 .md / .ipynb
里看不到任何 CMD / 光变 / phase-fold 图. 这条测试 lock 修复:

- markdown: figures 内嵌成 ![Figure N](data:image/png;base64,...)
- notebook: figures 写进 cell.outputs 当 display_data PNG, 同时把
  stdout 也写成 stream output, 让 .ipynb 在 Jupyter / VSCode / GitHub /
  Colab / nbviewer 里直接显示 (不用重跑 cell — 重跑也跑不出来, 因为
  astro.* helper 在普通 Jupyter kernel 里没有).
"""

from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient


def _client() -> TestClient:
    import app.main as main_mod
    return TestClient(main_mod.app)


# 一个 1x1 透明 PNG 的 base64. 测试只关心字节透传, 不验证图像内容.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _sample_messages_with_figure() -> list[dict]:
    """模拟一条 assistant 消息, 含 run_python action 带 stdout + 1 张图."""
    return [
        {"role": "user", "content": "Plot the CMD."},
        {
            "role": "assistant",
            "content": "Here is the CMD.",
            "actions": [
                {
                    "action": "run_python",
                    "tool_input": {"code": "import matplotlib.pyplot as plt\nplt.plot([1,2,3])\nplt.show()"},
                    "tool_result": {
                        "success": True,
                        "stdout": "Plot saved.\n",
                        "figures": [_TINY_PNG_B64],
                        "variables": {},
                    },
                },
            ],
        },
    ]


def test_markdown_export_embeds_run_python_figures_as_data_uri() -> None:
    """Markdown export 必须把 figures 内嵌成 data:image/png;base64 URI,
    这样 .md 用 VS Code / Typora / Obsidian / GitHub 看都能直接显示图,
    不需要 zip 一堆 png 出来."""
    client = _client()
    resp = client.post(
        "/api/export/report/from-chat",
        json={"messages": _sample_messages_with_figure(), "title": "Pleiades"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    # 图必须以 markdown image 形式 + data URI 出现
    # PART AI: caption 是 "Figure {py_cell_idx}.{fig_idx}" 多 cell 友好.
    assert "![Figure 1.1](data:image/png;base64," in body, (
        "Markdown export missing inline figure; figures field on tool_result "
        "was dropped. Body sample: " + body[:500]
    )
    # 真实 base64 字节必须透传 (不被截断 / 不被替换成占位符)
    assert _TINY_PNG_B64 in body
    # stdout 也应保留, 让用户能看到 print 出来的数值
    assert "Plot saved." in body


def test_markdown_export_handles_full_data_uri_and_bare_base64() -> None:
    """Frontend 有时把 figures 存成 'data:image/png;base64,XXX', 有时只
    存裸 base64. 两种都要兼容."""
    client = _client()
    msgs = [
        {
            "role": "assistant",
            "content": "two figs",
            "actions": [{
                "action": "run_python",
                "tool_input": {"code": "pass"},
                "tool_result": {
                    "success": True,
                    "figures": [
                        f"data:image/png;base64,{_TINY_PNG_B64}",  # 完整 URI
                        _TINY_PNG_B64,                              # 裸 base64
                    ],
                },
            }],
        }
    ]
    resp = client.post("/api/export/report/from-chat", json={"messages": msgs, "title": "t"})
    assert resp.status_code == 200
    body = resp.text
    # 两张图都要出现, 都应该带正确的 data URI 前缀.
    # 两张图来自同一个 run_python (cell #1), 编号是 1.1 和 1.2.
    assert body.count("![Figure 1.1](data:image/png;base64,") == 1
    assert body.count("![Figure 1.2](data:image/png;base64,") == 1


def test_notebook_export_writes_figures_to_cell_outputs() -> None:
    """Notebook export 必须把 figures 写进 cell.outputs 当 display_data PNG,
    这样 .ipynb 在 Jupyter / VSCode / GitHub / Colab 打开就直接渲染图,
    不需要 'Run All' (重跑也跑不出来, astro.* helper 不存在于普通 kernel)."""
    client = _client()
    resp = client.post(
        "/api/export/notebook/from-chat",
        json={"messages": _sample_messages_with_figure(), "title": "Pleiades"},
    )
    assert resp.status_code == 200, resp.text
    nb = json.loads(resp.text)
    assert nb["nbformat"] == 4

    # 找到 run_python 那条 code cell
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    # 第一条 code cell 是 helper imports (D6.1), 跳过, 取后续含 outputs 的
    code_with_outputs = [c for c in code_cells if c.get("outputs")]
    assert len(code_with_outputs) >= 1, (
        "No code cell carries outputs; figures got dropped during export. "
        f"Got {len(code_cells)} code cells, none with outputs."
    )

    target = code_with_outputs[0]
    outputs = target["outputs"]

    # outputs 至少一条 image/png display_data
    image_outputs = [
        o for o in outputs
        if o.get("output_type") == "display_data"
        and isinstance(o.get("data"), dict)
        and "image/png" in o["data"]
    ]
    assert len(image_outputs) == 1, (
        f"Expected exactly 1 image/png display_data output; got "
        f"{len(image_outputs)}. outputs: {outputs}"
    )
    assert image_outputs[0]["data"]["image/png"] == _TINY_PNG_B64

    # stdout 也应有, 当 stream output
    stdout_outputs = [
        o for o in outputs
        if o.get("output_type") == "stream" and o.get("name") == "stdout"
    ]
    assert len(stdout_outputs) == 1
    assert "Plot saved." in stdout_outputs[0]["text"]

    # execution_count 应被设非 None (代表 cell 显得运行过)
    assert target.get("execution_count") is not None


def test_notebook_export_strips_data_uri_prefix_for_image_png() -> None:
    """Jupyter 标准要求 display_data['image/png'] 是裸 base64, 不带
    'data:image/png;base64,' 前缀. 测试两种输入形式都被规范化."""
    client = _client()
    msgs = [
        {
            "role": "assistant",
            "content": "fig",
            "actions": [{
                "action": "run_python",
                "tool_input": {"code": "pass"},
                "tool_result": {
                    "success": True,
                    "figures": [f"data:image/png;base64,{_TINY_PNG_B64}"],
                },
            }],
        }
    ]
    resp = client.post("/api/export/notebook/from-chat", json={"messages": msgs, "title": "t"})
    assert resp.status_code == 200
    nb = json.loads(resp.text)
    code_with_outputs = [c for c in nb["cells"] if c["cell_type"] == "code" and c.get("outputs")]
    img = code_with_outputs[0]["outputs"][0]["data"]["image/png"]
    # 前缀必须被剥掉
    assert not img.startswith("data:")
    assert img == _TINY_PNG_B64
    # 真要能 base64-decode (sanity, 防止存了乱码)
    base64.b64decode(img)


def test_markdown_export_skips_empty_figures_gracefully() -> None:
    """没图的 run_python action 不应报错, 也不应留下空 ![Figure 0] 占位."""
    client = _client()
    msgs = [
        {
            "role": "assistant",
            "content": "no plot",
            "actions": [{
                "action": "run_python",
                "tool_input": {"code": "print('hi')"},
                "tool_result": {"success": True, "stdout": "hi\n", "figures": []},
            }],
        }
    ]
    resp = client.post("/api/export/report/from-chat", json={"messages": msgs, "title": "t"})
    assert resp.status_code == 200
    body = resp.text
    assert "data:image/png" not in body
    assert "hi" in body  # stdout 仍保留


def test_markdown_export_drops_action_dump_for_non_python_tools() -> None:
    """PART AI: 之前每条 action 都吐 '### Action: xxx' + 全 params dict,
    长会话会到 100+ 页. 改成一行摘要后, run_adql 200 行结果不该出现在 md 里."""
    client = _client()

    # 模拟一条 run_adql action, result 含 200 行真实数据 + columns
    fake_rows = [{"ra": float(i), "dec": float(i)} for i in range(200)]
    msgs = [
        {
            "role": "assistant",
            "content": "Queried Gaia.",
            "actions": [
                {
                    "action": "run_adql",
                    "tool_input": {"query": "SELECT TOP 200 ra, dec FROM gaia"},
                    "tool_result": {
                        "row_count": 200,
                        "columns": ["ra", "dec"],
                        "data": fake_rows,
                    },
                }
            ],
        }
    ]
    resp = client.post("/api/export/report/from-chat", json={"messages": msgs, "title": "t"})
    assert resp.status_code == 200
    body = resp.text

    # 必须有一行摘要 (含 row_count)
    assert "200 rows" in body
    assert "run_adql" in body
    # 但不允许把 200 行原始数据 dump 进去
    assert '{"ra": 199.0' not in body
    assert "ra: 199" not in body
    # 也不允许出现旧 "### Action:" header
    assert "### Action:" not in body
    # 整体长度应该 < 1000 字符 (单 action + 短消息)
    assert len(body) < 2000, f"Markdown body too verbose: {len(body)} bytes"


def test_markdown_export_truncates_huge_stdout() -> None:
    """单条 run_python 打 5000 行 print 不该让 export 直接炸. 截到 head/tail."""
    client = _client()
    huge_stdout = "\n".join(f"line {i}" for i in range(5000))
    msgs = [
        {
            "role": "assistant",
            "content": "x",
            "actions": [{
                "action": "run_python",
                "tool_input": {"code": "for i in range(5000): print(...)"},
                "tool_result": {"success": True, "stdout": huge_stdout, "figures": []},
            }],
        }
    ]
    resp = client.post("/api/export/report/from-chat", json={"messages": msgs, "title": "t"})
    assert resp.status_code == 200
    body = resp.text

    # 头几行应保留
    assert "line 0" in body
    assert "line 1" in body
    # 尾几行应保留
    assert "line 4999" in body
    # 中间一定不能全部 dump (不能含 "line 2500")
    assert "line 2500" not in body
    # 截断标记应在
    assert "lines truncated" in body
    # 整体不能比原 5000 行还长
    assert len(body) < 5000  # 30 head + 5 tail + frame ≪ 5000 lines raw


def test_html_export_is_self_contained_and_includes_figures() -> None:
    """新 HTML 导出: 单文件 .html 双击在浏览器看, 所有图 base64 内嵌,
    Ctrl+P 打 PDF. 不依赖外部 CSS / JS / 图片资源."""
    client = _client()
    resp = client.post(
        "/api/export/report/html-from-chat",
        json={"messages": _sample_messages_with_figure(), "title": "Pleiades"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text

    # HTML 框架
    assert "<!DOCTYPE html>" in body
    assert '<html lang="en">' in body
    assert "<title>Pleiades</title>" in body

    # 内嵌 CSS, 不引外部
    assert "<style>" in body
    assert 'href="http' not in body  # 不外链
    assert '<link rel="stylesheet"' not in body  # 不外链 css
    assert "<script src=" not in body  # 不引外部 js

    # 图必须以 <img src="data:image/png;base64,..."> 内嵌
    assert 'src="data:image/png;base64,' in body
    assert _TINY_PNG_B64 in body

    # User / Assistant 标签
    assert "🧑 You" in body or "You" in body
    assert "🤖 AI Assistant" in body or "AI Assistant" in body


def test_html_export_escapes_user_content_to_prevent_xss() -> None:
    """User 输入里的 <script> 必须被转义, 不能落地成可执行 JS."""
    client = _client()
    msgs = [{"role": "user", "content": "<script>alert(1)</script>"}]
    resp = client.post("/api/export/report/html-from-chat", json={"messages": msgs, "title": "t"})
    assert resp.status_code == 200
    body = resp.text
    # raw 不能出现
    assert "<script>alert(1)</script>" not in body
    # 转义后的形式应该出现
    assert "&lt;script&gt;" in body


def test_html_export_includes_table_of_contents() -> None:
    """长会话的 HTML 导出应该有目录 (按 user message 分章)."""
    client = _client()
    msgs = [
        {"role": "user", "content": "First question about Gaia"},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user", "content": "Second question about TESS"},
        {"role": "assistant", "content": "answer 2"},
    ]
    resp = client.post("/api/export/report/html-from-chat", json={"messages": msgs, "title": "t"})
    assert resp.status_code == 200
    body = resp.text
    assert '<nav class="toc">' in body
    assert "First question about Gaia" in body
    assert "Second question about TESS" in body
    # 每个 user 章节都有 anchor id
    assert 'id="turn-0"' in body
    assert 'id="turn-2"' in body


def test_notebook_export_no_figures_still_emits_clean_cell() -> None:
    """没 figures / stdout 的 run_python 不应制造空 outputs 数组的运行态错觉."""
    client = _client()
    msgs = [
        {
            "role": "assistant",
            "content": "x",
            "actions": [{
                "action": "run_python",
                "tool_input": {"code": "x = 1"},
                "tool_result": {"success": True, "figures": [], "stdout": ""},
            }],
        }
    ]
    resp = client.post("/api/export/notebook/from-chat", json={"messages": msgs, "title": "t"})
    assert resp.status_code == 200
    nb = json.loads(resp.text)
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code" and c["source"] == ["x = 1"]]
    assert len(code_cells) == 1
    cell = code_cells[0]
    assert cell["outputs"] == []
    # 没 outputs 时 execution_count 维持 None (不假装运行过)
    assert cell["execution_count"] is None
