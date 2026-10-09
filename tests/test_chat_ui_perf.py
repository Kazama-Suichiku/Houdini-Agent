# -*- coding: utf-8 -*-
"""长会话 UI 性能相关的回归测试（BlockModel 精确通知 / 回答段落复用 / 图片外置 / 增量保存）。

需要 PySide6；CI 未安装时整体跳过。
"""
import base64
import json

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")

from houdini_agent.ui_qml import controller as C  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    yield app


def _recorder(bm):
    ev = []
    bm.dataChanged.connect(lambda tl, br, roles=None: ev.append(("changed", tl.row(), br.row())))
    bm.rowsInserted.connect(lambda parent, a, b: ev.append(("inserted", a, b)))
    bm.rowsRemoved.connect(lambda parent, a, b: ev.append(("removed", a, b)))
    return ev


def _bare_controller(tmp_path):
    """不跑 __init__ 的 Controller：只测纯 Python 逻辑，不碰 QSettings / QML。"""
    c = C.Controller.__new__(C.Controller)
    c._cache_dir = tmp_path
    c._saved_refs = {}
    c._sessions = []
    c._active = 0
    c._provider = "duojie"
    c._model_name = "claude-opus-5-5"
    c._node_name_map = {}
    return c


# ---------------------------------------------------------------- BlockModel
class TestBlockModelSync:
    def test_unchanged_rows_are_not_renotified(self):
        a, b, c = {"kind": "prose", "html": "a"}, {"kind": "prose", "html": "b"}, {"kind": "prose", "html": "c"}
        bm = C.BlockModel([a, b, c])
        ev = _recorder(bm)
        bm.sync([a, b, c])
        assert ev == []

    def test_only_changed_row_is_notified(self):
        a, b, c = {"kind": "prose", "html": "a"}, {"kind": "thinking", "text": "x"}, {"kind": "prose", "html": "c"}
        bm = C.BlockModel([a, b, c])
        ev = _recorder(bm)
        b["text"] = "xy"
        bm.sync([a, b, c])
        assert ev == [("changed", 1, 1)]

    def test_nested_in_place_mutation_is_detected(self):
        ex = {"kind": "exec", "tools": [{"state": "run", "name": "t"}]}
        bm = C.BlockModel([ex])
        ev = _recorder(bm)
        ex["tools"][0]["state"] = "ok"
        bm.sync([ex])
        assert ev == [("changed", 0, 0)]
        ex["tools"].append({"state": "run", "name": "t2"})
        bm.sync([ex])
        assert ev[-1] == ("changed", 0, 0)

    def test_append_inserts_without_renotifying_prefix(self):
        a = {"kind": "prose", "html": "a"}
        bm = C.BlockModel([a])
        ev = _recorder(bm)
        d = {"kind": "image", "src": "file:///x.jpg"}
        bm.sync([a, d])
        assert ev == [("inserted", 1, 1)]
        assert bm.rowCount() == 2

    def test_identity_break_replaces_tail(self):
        a, b = {"kind": "prose", "html": "a"}, {"kind": "prose", "html": "b"}
        bm = C.BlockModel([a, b])
        ev = _recorder(bm)
        b2 = {"kind": "prose", "html": "b2"}
        bm.sync([a, b2])
        assert ("removed", 1, 1) in ev and ("inserted", 1, 1) in ev
        assert bm.to_list() == [a, b2]

    def test_separate_changed_ranges(self):
        rows = [{"kind": "prose", "html": str(i)} for i in range(5)]
        bm = C.BlockModel(rows)
        ev = _recorder(bm)
        rows[1]["html"] = "x"
        rows[3]["html"] = "y"
        bm.sync(rows)
        assert ev == [("changed", 1, 1), ("changed", 3, 3)]


# ---------------------------------------------------------------- answer segments
class TestAnswerSegmentsReuse:
    def _run(self, c, text):
        c._prose_text = text
        c._recompute_answer()
        return list(c._answer_blocks)

    def test_segments_keep_identity_while_streaming(self, tmp_path):
        c = _bare_controller(tmp_path)
        c._blocks, c._answer_blocks = [], []
        first = self._run(c, "第一段\n\n```vex\n@P.y += 1;\n```\n\n第二段")
        snap = [dict(b) for b in first]
        second = self._run(c, "第一段\n\n```vex\n@P.y += 1;\n```\n\n第二段继续写")
        assert len(first) == len(second)
        assert all(x is y for x, y in zip(first, second))
        # 只有最后一段内容变了
        assert second[0] == snap[0] and second[1] == snap[1]
        assert second[-1] != snap[-1]
        assert c._blocks[-len(second):] == second

    def test_model_only_notifies_last_segment(self, tmp_path):
        c = _bare_controller(tmp_path)
        c._blocks, c._answer_blocks = [], []
        self._run(c, "甲\n\n```vex\nint a;\n```\n\n乙")
        bm = C.BlockModel(list(c._blocks))
        ev = _recorder(bm)
        self._run(c, "甲\n\n```vex\nint a;\n```\n\n乙丙")
        bm.sync(c._blocks)
        assert ev == [("changed", len(c._blocks) - 1, len(c._blocks) - 1)]


# ---------------------------------------------------------------- images out of rows
PNG_1PX = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class TestImageExternalize:
    def test_data_uri_written_to_cache_and_returns_file_url(self, tmp_path):
        c = _bare_controller(tmp_path)
        uri = "data:image/png;base64," + PNG_1PX
        out = c._externalize_image(uri)
        assert out.startswith("file:")
        files = list((tmp_path / "images").iterdir())
        assert len(files) == 1 and files[0].suffix == ".png"
        assert files[0].read_bytes() == base64.b64decode(PNG_1PX)
        # 同内容二次外置复用同一文件
        assert c._externalize_image(uri) == out
        assert len(list((tmp_path / "images").iterdir())) == 1

    def test_non_data_uri_returned_unchanged(self, tmp_path):
        c = _bare_controller(tmp_path)
        src = "file:///C:/x.jpg"
        assert c._externalize_image(src) is src
        assert c._externalize_image(None) is None

    def test_no_cache_dir_keeps_data_uri(self, tmp_path):
        c = _bare_controller(tmp_path)
        c._cache_dir = None
        uri = "data:image/png;base64," + PNG_1PX
        assert c._externalize_image(uri) is uri

    def test_rows_migration(self, tmp_path):
        c = _bare_controller(tmp_path)
        uri = "data:image/png;base64," + PNG_1PX
        rows = [{"type": "user", "payload": {"text": "hi", "images": [uri]}},
                {"type": "ai", "payload": {"blocks": [{"kind": "image", "src": uri},
                                                      {"kind": "prose", "html": "ok"}]}}]
        assert c._externalize_rows(rows) is True
        assert rows[0]["payload"]["images"][0].startswith("file:")
        assert rows[1]["payload"]["blocks"][0]["src"].startswith("file:")
        assert "base64" not in json.dumps(rows)
        assert c._externalize_rows(rows) is False


# ---------------------------------------------------------------- incremental save
class TestIncrementalSave:
    def test_only_changed_sessions_are_rewritten(self, tmp_path):
        c = _bare_controller(tmp_path)
        c._sessions = [{"id": "aaa", "title": "A", "rows": [], "history": []},
                       {"id": "bbb", "title": "B", "rows": [], "history": []}]
        c._save_all()
        pa, pb = tmp_path / "session_aaa.json", tmp_path / "session_bbb.json"
        assert pa.exists() and pb.exists()
        pa.write_text("SENTINEL", encoding="utf-8")
        pb.write_text("SENTINEL", encoding="utf-8")
        # 无变化：都不重写
        c._save_all()
        assert pa.read_text(encoding="utf-8") == "SENTINEL"
        assert pb.read_text(encoding="utf-8") == "SENTINEL"
        # 新快照（rows 换成新对象）只重写该会话
        c._sessions[1]["rows"] = [{"type": "user", "payload": {"text": "x"}}]
        c._save_all()
        assert pa.read_text(encoding="utf-8") == "SENTINEL"
        assert json.loads(pb.read_text(encoding="utf-8"))["rows"][0]["payload"]["text"] == "x"
        # 改标题也会重写
        c._sessions[0]["title"] = "A2"
        c._save_all()
        assert json.loads(pa.read_text(encoding="utf-8"))["title"] == "A2"

    def test_deleted_session_ref_dropped(self, tmp_path):
        c = _bare_controller(tmp_path)
        c._sessions = [{"id": "aaa", "title": "A", "rows": [], "history": []}]
        c._save_all()
        c._sessions = []
        c._save_all()
        assert c._saved_refs == {}
