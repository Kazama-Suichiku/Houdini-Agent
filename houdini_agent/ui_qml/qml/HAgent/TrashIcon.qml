import QtQuick

// 垃圾桶图标（Canvas 描边，跟随主题色，无 emoji、任意缩放都清晰）。
Canvas {
    id: ico
    property color color: "#969184"
    property int size: 14
    width: size; height: size
    onColorChanged: requestPaint()
    onPaint: {
        var ctx = getContext("2d");
        ctx.reset();
        ctx.strokeStyle = ico.color;
        ctx.lineWidth = Math.max(1, size / 11);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        var w = width, h = height;
        var lidY = h * 0.26;                 // 桶盖横线
        ctx.beginPath();
        ctx.moveTo(w * 0.16, lidY);
        ctx.lineTo(w * 0.84, lidY);
        ctx.stroke();
        // 提手
        ctx.beginPath();
        ctx.moveTo(w * 0.38, lidY);
        ctx.lineTo(w * 0.40, h * 0.13);
        ctx.lineTo(w * 0.60, h * 0.13);
        ctx.lineTo(w * 0.62, lidY);
        ctx.stroke();
        // 桶身（上宽下窄的梯形）
        ctx.beginPath();
        ctx.moveTo(w * 0.24, lidY);
        ctx.lineTo(w * 0.29, h * 0.86);
        ctx.lineTo(w * 0.71, h * 0.86);
        ctx.lineTo(w * 0.76, lidY);
        ctx.stroke();
        // 两道竖纹
        ctx.beginPath();
        ctx.moveTo(w * 0.42, h * 0.40); ctx.lineTo(w * 0.43, h * 0.74);
        ctx.moveTo(w * 0.58, h * 0.40); ctx.lineTo(w * 0.57, h * 0.74);
        ctx.stroke();
    }
}
