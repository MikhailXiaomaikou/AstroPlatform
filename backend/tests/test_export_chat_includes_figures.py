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
    assert "![Figure 1](data:image/png;base64," in body, (
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
    # 两张图都要出现, 都应该带正确的 data URI 前缀
    assert body.count("![Figure 1](data:image/png;base64,") == 1
    assert body.count("![Figure 2](data:image/png;base64,") == 1


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
