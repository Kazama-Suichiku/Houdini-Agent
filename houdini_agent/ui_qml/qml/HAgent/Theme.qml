pragma Singleton
import QtQuick

// ============================================================
// Mono Editorial — design tokens (single source of truth)
// Scale-aware + theme-aware. The whole app re-skins by changing
// the four state keys below (driven by Controller via Main.qml).
// All historical token NAMES are preserved so existing QML keeps working.
// ============================================================
QtObject {
    id: theme

    // ---- user-driven appearance state (set from Main <- controller) ----
    property string mode: "noir"          // noir | graphite | midnight | day
    property string accentKey: "warm"     // warm | steel | celadon | clay | neutral
    property string typeKey: "editorial"  // editorial | modern | mono
    property string densityKey: "normal"  // compact | normal | roomy
    property real scale: 1.0

    readonly property bool light: mode === "day"

    // ---- palette lookup (per mode) ----
    function _palette(m) {
        if (m === "day") return {
            bg:"#e6e1d6", codeBg:"#dcd6c7", panel:"#ddd8cc", panelDeep:"#d3cdbe",
            surface:Qt.rgba(0.16,0.125,0.07,0.045), surface2:Qt.rgba(0.16,0.125,0.07,0.085),
            border:Qt.rgba(0.16,0.125,0.07,0.18), borderSoft:Qt.rgba(0.16,0.125,0.07,0.10),
            text:"#2a2620", textBright:"#14110a", textDim:"#524c40", textMute:"#857d6c",
            ok:"#5d7a3f", okA:Qt.rgba(0.36,0.48,0.25,0.14), warn:"#9a5a2a", warnA:Qt.rgba(0.6,0.35,0.16,0.12), err:"#a8484f",
            userFg:"#1c1910", userBorder:Qt.rgba(0,0,0,0.24),
            synCom:"#9c9483", synKw:"#5b4f37", synFn:"#7a6a3f", synAttr:"#8a583a", synNum:"#5f7a45", synStr:"#8a583a"
        }
        if (m === "graphite") return {
            bg:"#161616", codeBg:"#0e0e0e", panel:"#1c1c1c", panelDeep:"#121212",
            surface:Qt.rgba(1,1,1,0.05), surface2:Qt.rgba(1,1,1,0.085),
            border:Qt.rgba(1,1,1,0.13), borderSoft:Qt.rgba(1,1,1,0.085),
            text:"#e7e4db", textBright:"#ffffff", textDim:"#bfbbb0", textMute:"#969184",
            ok:"#b8c5a0", okA:Qt.rgba(0.72,0.77,0.63,0.12), warn:"#d4a373", warnA:Qt.rgba(0.83,0.64,0.45,0.10), err:"#dd9999",
            userFg:"#fafafa", userBorder:Qt.rgba(1,1,1,0.20),
            synCom:"#5e5b54", synKw:"#e8e2d4", synFn:"#c9b896", synAttr:"#d4a373", synNum:"#a3b18a", synStr:"#bc9b7a"
        }
        if (m === "midnight") return {
            bg:"#0c0e13", codeBg:"#07090d", panel:"#10131a", panelDeep:"#0a0c11",
            surface:Qt.rgba(1,1,1,0.045), surface2:Qt.rgba(1,1,1,0.075),
            border:Qt.rgba(1,1,1,0.12), borderSoft:Qt.rgba(1,1,1,0.08),
            text:"#e3e4e2", textBright:"#ffffff", textDim:"#b8bcc4", textMute:"#888d98",
            ok:"#b8c5a0", okA:Qt.rgba(0.72,0.77,0.63,0.12), warn:"#d4a373", warnA:Qt.rgba(0.83,0.64,0.45,0.10), err:"#dd9999",
            userFg:"#fafafa", userBorder:Qt.rgba(1,1,1,0.20),
            synCom:"#5e5b54", synKw:"#e8e2d4", synFn:"#c9b896", synAttr:"#d4a373", synNum:"#a3b18a", synStr:"#bc9b7a"
        }
        // noir (default)
        return {
            bg:"#0d0d0d", codeBg:"#080808", panel:"#101010", panelDeep:"#0c0c0c",
            surface:Qt.rgba(1,1,1,0.04), surface2:Qt.rgba(1,1,1,0.07),
            border:Qt.rgba(1,1,1,0.12), borderSoft:Qt.rgba(1,1,1,0.08),
            text:"#e7e4db", textBright:"#ffffff", textDim:"#bfbbb0", textMute:"#969184",
            ok:"#b8c5a0", okA:Qt.rgba(0.72,0.77,0.63,0.12), warn:"#d4a373", warnA:Qt.rgba(0.83,0.64,0.45,0.10), err:"#dd9999",
            userFg:"#fafafa", userBorder:Qt.rgba(1,1,1,0.20),
            synCom:"#5e5b54", synKw:"#e8e2d4", synFn:"#c9b896", synAttr:"#d4a373", synNum:"#a3b18a", synStr:"#bc9b7a"
        }
    }
    readonly property var _pal: _palette(mode)

    // ---- palette ----
    readonly property color bg:         _pal.bg
    readonly property color codeBg:     _pal.codeBg
    readonly property color panel:      _pal.panel
    readonly property color panelDeep:  _pal.panelDeep
    readonly property color surface:    _pal.surface
    readonly property color surface2:   _pal.surface2
    readonly property color border:     _pal.border
    readonly property color borderSoft: _pal.borderSoft

    readonly property color text:       _pal.text
    readonly property color textBright: _pal.textBright
    readonly property color textDim:    _pal.textDim
    readonly property color textMute:   _pal.textMute

    // ---- accent (hue picked by accentKey; darkened for foreground on light) ----
    function _hue(k) {
        if (k === "steel")   return "#aec4d6"
        if (k === "celadon") return "#aecdb8"
        if (k === "clay")    return "#e0b48c"
        if (k === "neutral") return "#cfccc4"
        return "#e8e2d4" // warm (default)
    }
    readonly property color accentHue: _hue(accentKey)
    readonly property color accent:     light ? Qt.darker(accentHue, 1.72) : accentHue
    readonly property color accentSoft: Qt.rgba(accent.r, accent.g, accent.b, light ? 0.13 : 0.10)
    readonly property color accentLine: Qt.rgba(accent.r, accent.g, accent.b, light ? 0.42 : 0.30)

    // ---- Meshy brand (green) — for Meshy-related UI only ----
    readonly property color meshy:      "#c5f955"
    readonly property color meshyHover: "#a8e328"
    readonly property color meshyInk:   "#0e0e0e"
    readonly property color meshySoft:  Qt.rgba(0.773, 0.976, 0.333, 0.12)
    readonly property color meshyLine:  Qt.rgba(0.773, 0.976, 0.333, 0.38)

    readonly property color ok:         _pal.ok
    readonly property color okSoft:     _pal.okA
    readonly property color warn:       _pal.warn
    readonly property color warnSoft:   _pal.warnA
    readonly property color err:        _pal.err

    readonly property color userFg:     _pal.userFg
    readonly property color userBorder: _pal.userBorder

    // ---- syntax highlight ----
    readonly property color synCom:  _pal.synCom
    readonly property color synKw:   _pal.synKw
    readonly property color synFn:   _pal.synFn
    readonly property color synAttr: _pal.synAttr
    readonly property color synNum:  _pal.synNum
    readonly property color synStr:  _pal.synStr

    // ---- shape (Editorial = square corners, hairline borders) ----
    readonly property int radSm: 2
    readonly property int radMd: 2

    // ---- typography ----
    function _fam(role) {
        if (typeKey === "mono") return "Space Mono"
        if (typeKey === "modern") return role === "mono" ? "Space Mono" : "Inter"
        // editorial (default)
        return role === "display" ? "Fraunces" : role === "mono" ? "Space Mono" : "Newsreader"
    }
    readonly property string fontDisplay: _fam("display")
    readonly property string fontBody:    _fam("body")
    readonly property string fontMono:    _fam("mono")

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

    // ---- density (spacing only; does not touch font size) ----
    readonly property real density: densityKey === "compact" ? 0.82
                                   : densityKey === "roomy"   ? 1.22 : 1.0
    function sp(px) { return Math.round(px * scale * density) }
    readonly property int gap:   sp(14)   // between chat messages
    readonly property int gapLg: sp(20)
    readonly property int pad:   sp(14)   // pane padding
}
