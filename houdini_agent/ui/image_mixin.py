# -*- coding: utf-8 -*-
"""
Image Mixin — 多模态图片处理

从 ai_tab.py 拆分：
  - _current_model_supports_vision
  - _on_attach_image / _add_image_from_path / _resize_image_if_needed
  - _on_image_dropped / _add_pending_image / _remove_pending_image
  - _rebuild_image_preview / _clear_pending_images / _build_multimodal_content
  - _MAX_IMAGE_DIMENSION / _MAX_IMAGE_BYTES
"""

import os

from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui

from .cursor_widgets import ClickableImageLabel


class ImageMixin:
    """多模态图片附件与预览管理"""

    def _current_model_supports_vision(self) -> bool:
        """检查当前选中的模型是否支持图片输入"""
        model = self.model_combo.currentText()
        features = self._model_features.get(model, {})
        return features.get('supports_vision', False)

    def _on_attach_image(self):
        """打开文件对话框选择图片"""
        if not self._current_model_supports_vision():
            model = self.model_combo.currentText()
            QtWidgets.QMessageBox.information(
                self, "不支持图片",
                f"当前模型 {model} 不支持图片输入。\n请切换到支持视觉的模型（如 Claude、GPT-5.2 等）。"
            )
            return

        file_paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "选择图片", "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;All Files (*)"
        )
        for fp in file_paths:
            self._add_image_from_path(fp)

    def _add_image_from_path(self, file_path: str):
        """从文件路径加载图片并添加到待发送列表（自动缩放过大图片）"""
        import base64
        try:
            # ★ 通过 QImage 加载，统一走缩放逻辑
            qimg = QtGui.QImage(file_path)
            if qimg.isNull():
                print(f"[AI Tab] 无法加载图片: {file_path}")
                return
            qimg = self._resize_image_if_needed(qimg, self._MAX_IMAGE_DIMENSION)

            ext = os.path.splitext(file_path)[1].lower()
            # 优先保持原始格式；BMP/GIF 等不适合直接发 API，统一转 PNG
            if ext in ('.jpg', '.jpeg'):
                fmt, media_type = 'JPEG', 'image/jpeg'
            elif ext == '.webp':
                fmt, media_type = 'WEBP', 'image/webp'
            else:
                fmt, media_type = 'PNG', 'image/png'

            buf = QtCore.QBuffer()
            buf.open(QtCore.QIODevice.WriteOnly)
            quality = 90 if fmt == 'JPEG' else -1
            qimg.save(buf, fmt, quality)
            raw_bytes = buf.data().data()
            buf.close()

            # ★ 过大时降级为 JPEG 压缩
            if len(raw_bytes) > self._MAX_IMAGE_BYTES and fmt != 'JPEG':
                buf2 = QtCore.QBuffer()
                buf2.open(QtCore.QIODevice.WriteOnly)
                qimg.save(buf2, 'JPEG', 85)
                raw_bytes = buf2.data().data()
                buf2.close()
                media_type = 'image/jpeg'
                print(f"[AI Tab] 图片过大，已转为 JPEG ({len(raw_bytes)//1024}KB)")

            b64 = base64.b64encode(raw_bytes).decode('utf-8')
            self._add_pending_image(b64, media_type)
        except Exception as e:
            print(f"[AI Tab] 加载图片失败: {e}")

    # ★ 图片尺寸限制：超过此分辨率的图片自动缩放（防止 base64 过大导致 API 400 错误）
    _MAX_IMAGE_DIMENSION = 2048  # 最长边不超过 2048px
    _MAX_IMAGE_BYTES = 5 * 1024 * 1024  # base64 前的原始字节数上限 ~5MB（编码后约 6.7MB）

    @staticmethod
    def _resize_image_if_needed(image: 'QtGui.QImage', max_dim: int = 2048) -> 'QtGui.QImage':
        """如果图片超过 max_dim，等比缩放。返回缩放后的 QImage。"""
        w, h = image.width(), image.height()
        if w <= max_dim and h <= max_dim:
            return image
        if w > h:
            new_w = max_dim
            new_h = int(h * max_dim / w)
        else:
            new_h = max_dim
            new_w = int(w * max_dim / h)
        print(f"[AI Tab] 图片过大 ({w}x{h})，自动缩放至 {new_w}x{new_h}")
        return image.scaled(new_w, new_h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

    def _on_image_dropped(self, image: 'QtGui.QImage'):
        """ChatInput 拖拽或粘贴图片的回调"""
        if not self._current_model_supports_vision():
            return
        import base64
        # ★ 自动缩放过大图片
        image = self._resize_image_if_needed(image, self._MAX_IMAGE_DIMENSION)
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.WriteOnly)
        image.save(buf, "PNG")
        raw_bytes = buf.data().data()
        buf.close()
        # ★ 如果 PNG 仍然过大，改用 JPEG 压缩
        if len(raw_bytes) > self._MAX_IMAGE_BYTES:
            buf2 = QtCore.QBuffer()
            buf2.open(QtCore.QIODevice.WriteOnly)
            image.save(buf2, "JPEG", 85)
            raw_bytes = buf2.data().data()
            buf2.close()
            media_type = 'image/jpeg'
            print(f"[AI Tab] PNG 过大，已转为 JPEG (quality=85, {len(raw_bytes)//1024}KB)")
        else:
            media_type = 'image/png'
        b64 = base64.b64encode(raw_bytes).decode('utf-8')
        self._add_pending_image(b64, media_type)

    def _add_pending_image(self, b64_data: str, media_type: str):
        """添加图片到待发送列表并在预览区显示缩略图（点击可放大）"""
        # 创建缩略图和完整 pixmap
        img_bytes = __import__('base64').b64decode(b64_data)
        full_pixmap = QtGui.QPixmap()
        full_pixmap.loadFromData(img_bytes)
        thumb = full_pixmap.scaled(60, 60, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

        # 存储
        idx = len(self._pending_images)
        self._pending_images.append((b64_data, media_type, thumb))

        # 创建预览 widget
        img_widget = QtWidgets.QWidget()
        img_layout = QtWidgets.QVBoxLayout(img_widget)
        img_layout.setContentsMargins(2, 2, 2, 2)
        img_layout.setSpacing(1)

        lbl = ClickableImageLabel(thumb, full_pixmap)
        lbl.setObjectName("imgThumb")
        img_layout.addWidget(lbl)

        # 删除按钮
        rm_btn = QtWidgets.QPushButton("x")
        rm_btn.setFixedSize(16, 16)
        rm_btn.setObjectName("imgRemoveBtn")
        rm_btn.clicked.connect(lambda checked=False, i=idx: self._remove_pending_image(i))
        img_layout.addWidget(rm_btn, alignment=QtCore.Qt.AlignCenter)

        # 插入到 stretch 之前
        count = self.image_preview_layout.count()
        self.image_preview_layout.insertWidget(count - 1, img_widget)
        self.image_preview_container.setVisible(True)

    def _remove_pending_image(self, index: int):
        """移除待发送图片"""
        if 0 <= index < len(self._pending_images):
            self._pending_images[index] = None  # 标记为已删除
            self._rebuild_image_preview()  # 过滤 None 后重建整个预览区

    def _rebuild_image_preview(self):
        """重新构建图片预览区"""
        # 清除所有 widget（保留 stretch）
        while self.image_preview_layout.count() > 1:
            item = self.image_preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 重新过滤并添加
        new_images = [(b64, mt, th) for entry in self._pending_images
                      if entry is not None for b64, mt, th in [entry]]
        self._pending_images = list(new_images)

        if not self._pending_images:
            self.image_preview_container.setVisible(False)
            return

        for i, (b64, mt, thumb) in enumerate(self._pending_images):
            img_widget = QtWidgets.QWidget()
            img_layout = QtWidgets.QVBoxLayout(img_widget)
            img_layout.setContentsMargins(2, 2, 2, 2)
            img_layout.setSpacing(1)

            # 从 base64 还原完整 pixmap 用于放大预览
            full_pixmap = QtGui.QPixmap()
            full_pixmap.loadFromData(__import__('base64').b64decode(b64))
            lbl = ClickableImageLabel(thumb, full_pixmap)
            lbl.setObjectName("imgThumb")
            img_layout.addWidget(lbl)

            rm_btn = QtWidgets.QPushButton("x")
            rm_btn.setFixedSize(16, 16)
            rm_btn.setObjectName("imgRemoveBtn")
            rm_btn.clicked.connect(lambda checked=False, idx=i: self._remove_pending_image(idx))
            img_layout.addWidget(rm_btn, alignment=QtCore.Qt.AlignCenter)

            count = self.image_preview_layout.count()
            self.image_preview_layout.insertWidget(count - 1, img_widget)

    def _clear_pending_images(self):
        """清空所有待发送图片"""
        self._pending_images.clear()
        while self.image_preview_layout.count() > 1:
            item = self.image_preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.image_preview_container.setVisible(False)

    def _build_multimodal_content(self, text: str, images: list) -> list:
        """构建包含文字和图片的多模态消息内容（OpenAI Vision API 格式）

        Args:
            text: 用户文字消息
            images: List of (base64_data, media_type, thumbnail) tuples

        Returns:
            list: content 数组，包含 text 和 image_url 项
        """
        # ★ API 支持的 media type 白名单（BMP 等需要先转换）
        _SUPPORTED_MEDIA = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}

        content_parts = []
        # ★ 始终添加 text 部分（即使为空也提供占位符，某些 API 要求至少一个 text block）
        content_parts.append({"type": "text", "text": text or " "})
        # 添加图片
        for b64_data, media_type, _thumb in images:
            if not b64_data:
                continue  # 跳过空数据
            # ★ 不支持的 media type 降级为 image/png
            if media_type not in _SUPPORTED_MEDIA:
                media_type = 'image/png'
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{b64_data}"
                }
            })
        return content_parts
