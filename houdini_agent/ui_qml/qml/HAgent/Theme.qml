pragma Singleton
import QtQuick

// ============================================================
// Mono Editorial — design tokens (single source of truth)
// Maps 1:1 to the chosen web design direction. Scale-aware.
// ============================================================
QtObject {
    id: theme

    // ---- palette ----
    readonly property color bg:         "#0d0d0d"
    readonly property color codeBg:     "#080808"
    readonly property color surface:    Qt.rgba(1, 1, 1, 0.04)
    readonly property color surface2:   Qt.rgba(1, 1, 1, 0.07)
    readonly property color border:     Qt.rgba(1, 1, 1, 0.12)
    readonly property color borderSoft: Qt.rgba(1, 1, 1, 0.08)

    readonly property color text:       "#e7e4db"
    readonly property color textBright: "#ffffff"
    readonly property color textDim:    "#bfbbb0"
    readonly property color textMute:   "#969184"

    readonly property color accent:     "#e8e2d4"
    readonly property color accentSoft: Qt.rgba(0.910, 0.886, 0.831, 0.10)
    readonly property color accentLine: Qt.rgba(0.910, 0.886, 0.831, 0.30)

    readonly property color ok:         "#b8c5a0"
    readonly property color okSoft:     Qt.rgba(0.722, 0.773, 0.627, 0.12)
    readonly property color warn:       "#d4a373"
    readonly property color warnSoft:   Qt.rgba(0.831, 0.639, 0.451, 0.10)
    readonly property color err:        "#dd9999"

    readonly property color userFg:     "#fafafa"
    readonly property color userBorder: Qt.rgba(1, 1, 1, 0.20)

    // ---- syntax highlight ----
    readonly property color synCom:  "#5e5b54"
    readonly property color synKw:   "#e8e2d4"
    readonly property color synFn:   "#c9b896"
    readonly property color synAttr: "#d4a373"
    readonly property color synNum:  "#a3b18a"
    readonly property color synStr:  "#bc9b7a"

    // ---- shape (Editorial = square corners, hairline borders) ----
    readonly property int radSm: 2
    readonly property int radMd: 2

    // ---- typography ----
    property real scale: 1.0
    // primary families; graceful fallbacks registered via QFont.insertSubstitutions
    // in host.py / preview.py (register_fonts) so missing fonts degrade to serif/mono.
    readonly property string fontDisplay: "Fraunces"
    readonly property string fontBody:    "Newsreader"
    readonly property string fontMono:    "Space Mono"

    function fs(px) { return Math.round(px * scale) }

    readonly property int fMicro: fs(10)
    readonly property int fXs:    fs(11)
    readonly property int fSm:    fs(12)
    readonly property int fBody:  fs(13)
    readonly property int fMd:    fs(14)
    readonly property int fLg:    fs(16)
    readonly property int fXl:    fs(18)

    // editorial micro-label tracking
    readonly property real trackLabel: 1.6
}
