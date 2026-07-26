#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import tkinter as tk
from tkinter import ttk, messagebox
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any


# =========================
# 健保規則常數
# =========================
NHI_EARLY_DAYS = 10               # 一般：累積用藥末日前10日內（最早=末日-9）
# 春節提前：末日回推 14 天（含）→ 最早=末日-14（例如 2/14 可提前到 1/31）
NHI_CNY_ADVANCE_DAYS = 14

# =========================
# 設定檔（記憶輸入）
# =========================
SETTINGS_PATH = Path(__file__).resolve().with_name("mcdc_settings.json")


def load_settings() -> Dict[str, Any]:
    try:
        if SETTINGS_PATH.exists():
            with SETTINGS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def save_settings(data: Dict[str, Any]) -> None:
    try:
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(SETTINGS_PATH)
    except Exception:
        # 記憶功能失敗不影響主功能
        pass


# =========================
# 日期處理（民國/西元）
# =========================
def parse_date(s: str) -> Optional[date]:
    """
    支援：
      - 2026-02-19 / 2026/2/19
      - 115/2/19 / 115-2-19（民國）
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None

    m = re.match(r"^(\d{2,4})[\/\-](\d{1,2})[\/\-](\d{1,2})$", s)
    if not m:
        return None

    y = int(m.group(1))
    mo = int(m.group(2))
    da = int(m.group(3))

    if y < 1911:  # 民國
        y += 1911

    try:
        return date(y, mo, da)
    except ValueError:
        return None


def fmt_ymd(d: date) -> str:
    return d.isoformat()


def fmt_roc(d: date) -> str:
    return f"{d.year - 1911}/{d.month}/{d.day}"


def today_local_date() -> date:
    now = datetime.now()
    return date(now.year, now.month, now.day)


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def in_range(d: date, start: date, end: date) -> bool:
    return start <= d <= end


# =========================
# 春節假期設定
# =========================
@dataclass
class NHIHolidayRule:
    enabled: bool
    start: Optional[date]
    end: Optional[date]


# =========================
# 「堆疊用藥末日」核心
# =========================
def compute_coverage_end(prev_coverage_end: Optional[date], fill_date: date, days: int) -> date:
    """
    回傳這次領藥後的「累積用藥末日 coverage_end」：
    - 若提前領（fill_date <= prev_coverage_end）：藥會堆疊 → coverage_end = prev_coverage_end + days
    - 若太晚領（fill_date > prev_coverage_end）：中斷 → coverage_end = fill_date + days - 1
    """
    if prev_coverage_end is None:
        return add_days(fill_date, days - 1)

    if fill_date <= prev_coverage_end:
        return add_days(prev_coverage_end, days)   # 堆疊
    return add_days(fill_date, days - 1)           # 中斷


@dataclass
class RuleOutcome:
    earliest: date
    notes: List[str]
    cny_applied: bool
    cny_window_start: Optional[date]
    cny_window_end: Optional[date]
    coverage_end: date


def nhi_earliest_by_rule_from_coverage_end(
    last_coverage_end: date,
    issue: date,
    base_not_before: date,
    holiday: NHIHolidayRule,
) -> RuleOutcome:
    """
    用「累積用藥末日 coverage_end」推下一次最早可領日：
    1) 一般：coverage_end - 9
    2) 春節（末日回推14天）：若 coverage_end 落在春節 → coverage_end - 14；窗口= [末日-14, 末日]
    3) 下限：不早於 issue、不早於 base_not_before
    """
    notes: List[str] = []
    coverage_end = last_coverage_end

    # 一般健保提前（10天內）
    earliest_normal = add_days(coverage_end, -(NHI_EARLY_DAYS - 1))  # 末日-9
    earliest = earliest_normal
    notes.append(f"一般健保：累積用藥末日 {fmt_ymd(coverage_end)} → 最早 {fmt_ymd(earliest_normal)}（10天內）")

    cny_applied = False
    cny_start = None
    cny_end = None

    # 春節提前（末日回推14天）
    if holiday.enabled and holiday.start and holiday.end:
        if in_range(coverage_end, holiday.start, holiday.end):
            cny_applied = True
            # 末日回推 14 天（含）
            cny_start = add_days(coverage_end, -NHI_CNY_ADVANCE_DAYS)  # 末日-14
            cny_end = coverage_end
            notes.append(f"春節啟動：累積用藥末日 {fmt_ymd(coverage_end)} 落在春節 {fmt_ymd(holiday.start)}~{fmt_ymd(holiday.end)}")
            notes.append(f"春節提前（末日回推14天）：可提前領藥期間 {fmt_ymd(cny_start)} ~ {fmt_ymd(cny_end)}")
            earliest = min(earliest, cny_start)
        else:
            notes.append(f"春節未啟動：累積用藥末日 {fmt_ymd(coverage_end)} 不在春節 {fmt_ymd(holiday.start)}~{fmt_ymd(holiday.end)}")

    # 下限限制
    earliest_clamped = max(earliest, issue, base_not_before)
    if earliest_clamped != earliest:
        notes.append(f"下限限制：max(開立日 {fmt_ymd(issue)}, 基礎限制 {fmt_ymd(base_not_before)}) → {fmt_ymd(earliest_clamped)}")

    return RuleOutcome(
        earliest=earliest_clamped,
        notes=notes,
        cny_applied=cny_applied,
        cny_window_start=cny_start,
        cny_window_end=cny_end,
        coverage_end=coverage_end,
    )


# =========================
# 計算（第1~第3次）
# =========================
@dataclass
class CalcInput:
    issue: date
    fill1: date
    fill2: Optional[date]
    days: int
    valid_days: int
    today: date
    holiday: NHIHolidayRule


@dataclass
class RowResult:
    k: str
    earliest: date
    latest: date
    can_today: bool
    note: str
    extra_lines: List[str]
    cny_applied: bool
    cny_window_start: Optional[date]
    cny_window_end: Optional[date]


@dataclass
class CalcResult:
    issue: date
    expiry: date
    days: int
    valid_days: int
    today: date
    impossible: bool
    warnings: List[str]
    rows: List[RowResult]


def calc_plan(inp: CalcInput) -> CalcResult:
    if inp.fill1 < inp.issue:
        raise ValueError("第 1 次實領藥日不可早於開立日")
    if inp.days <= 0:
        raise ValueError("每次處方天數必須 > 0")
    if inp.valid_days <= 0:
        raise ValueError("總有效天數必須 > 0")

    expiry = add_days(inp.issue, inp.valid_days - 1)
    warnings: List[str] = []

    # 第1次：累積末日
    cov1_end = compute_coverage_end(None, inp.fill1, inp.days)

    # 第2次：先用「第1次累積末日」推第2次最早
    out2 = nhi_earliest_by_rule_from_coverage_end(
        last_coverage_end=cov1_end,
        issue=inp.issue,
        base_not_before=inp.fill1,
        holiday=inp.holiday,
    )
    second_earliest = out2.earliest
    second_latest = add_days(expiry, -(inp.days - NHI_EARLY_DAYS))  # 保守估

    # 第2次未填：假設在最早日領（用來推第3次）
    actual_fill2 = inp.fill2 if inp.fill2 else second_earliest

    # 第2次領完，累積末日會「堆疊延後」
    cov2_end = compute_coverage_end(cov1_end, actual_fill2, inp.days)

    # 第3次：用「第2次後累積末日」推第3次最早
    out3 = nhi_earliest_by_rule_from_coverage_end(
        last_coverage_end=cov2_end,
        issue=inp.issue,
        base_not_before=actual_fill2,
        holiday=inp.holiday,
    )
    third_earliest = out3.earliest
    third_latest = expiry

    # 若使用者有填第2次，檢核是否超出第2次可領區間（以 second_earliest~second_latest）
    if inp.fill2:
        if inp.fill2 < second_earliest:
            warnings.append("你輸入的第 2 次實領日早於規則允許之最早日（或春節提前起算日）。")
        if inp.fill2 > second_latest:
            warnings.append("你輸入的第 2 次實領日太晚，可能導致第 3 次最早可領日超過效期。")

    can_second_today = (inp.today >= second_earliest and inp.today <= second_latest)
    can_third_today = (inp.today >= third_earliest and inp.today <= third_latest)

    impossible = (second_earliest > second_latest) or (third_earliest > third_latest)

    rows: List[RowResult] = []

    rows.append(
        RowResult(
            k="第 1 次",
            earliest=inp.fill1,
            latest=expiry,
            can_today=(inp.today >= inp.fill1),
            note="第1次以實領日為起算；後續若提前領，採『堆疊延後末日』避免末日算太早。",
            extra_lines=[
                f"第1次領藥日：{fmt_ymd(inp.fill1)}",
                f"第1次後累積用藥末日：{fmt_ymd(cov1_end)}",
            ],
            cny_applied=False,
            cny_window_start=None,
            cny_window_end=None,
        )
    )

    rows.append(
        RowResult(
            k="第 2 次",
            earliest=second_earliest,
            latest=second_latest,
            can_today=can_second_today,
            note="以『第1次累積末日』推第2次：一般10天內；春節：末日回推14天。",
            extra_lines=[
                f"今天（{fmt_ymd(inp.today)}）=> {'可領 ✅' if can_second_today else '不可領 ❌'}",
                *out2.notes,
                f"（第2次實領日：{fmt_ymd(actual_fill2)}）第2次後累積末日：{fmt_ymd(cov2_end)}",
            ],
            cny_applied=out2.cny_applied,
            cny_window_start=out2.cny_window_start,
            cny_window_end=out2.cny_window_end,
        )
    )

    rows.append(
        RowResult(
            k="第 3 次",
            earliest=third_earliest,
            latest=third_latest,
            can_today=can_third_today,
            note="以『第2次後累積末日』推第3次；第2次若提前領，末日會往後延，進而推遲第3次。",
            extra_lines=[
                f"今天（{fmt_ymd(inp.today)}）=> {'可領 ✅' if can_third_today else '不可領 ❌'}",
                f"第2次實領日：{fmt_ymd(actual_fill2)}（未填則假設=第2次最早日）",
                f"第2次後累積末日：{fmt_ymd(cov2_end)}",
                *out3.notes,
            ],
            cny_applied=out3.cny_applied,
            cny_window_start=out3.cny_window_start,
            cny_window_end=out3.cny_window_end,
        )
    )

    return CalcResult(
        issue=inp.issue,
        expiry=expiry,
        days=inp.days,
        valid_days=inp.valid_days,
        today=inp.today,
        impossible=impossible,
        warnings=warnings,
        rows=rows,
    )


# =========================
# GUI
# =========================
class ScrollableFrame(ttk.Frame):
    """簡易可捲動容器（垂直）。

    用途：在 24 吋 / 1080p 或 Windows 125% 縮放時，避免下方按鈕/欄位被遮住。
    """

    def __init__(self, master: tk.Misc, *, padding: int = 0) -> None:
        super().__init__(master)

        # 重要：我們要做到「需要才捲，不需要就展開」。
        # 方式：永遠用 Canvas 承載內容，但根據內容高度自動隱藏/顯示 Scrollbar。
        # 這樣 24 吋可捲、32 吋不會像被關在小窗裡（Scrollbar 會消失）。
        self._canvas = tk.Canvas(self, highlightthickness=0)
        self._vbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vbar.set)

        self._vbar_visible = True

        # 用 grid 讓顯示/隱藏 scrollbar 時版面不抖動
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._vbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.inner = ttk.Frame(self._canvas, padding=padding)
        self._win = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        # 滑鼠滾輪支援（進入可捲動區才綁定，避免影響右側結果區）
        self.inner.bind("<Enter>", self._bind_mousewheel)
        self.inner.bind("<Leave>", self._unbind_mousewheel)

    def _on_inner_configure(self, _evt: tk.Event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._update_scrollbar_visibility()

    def _on_canvas_configure(self, evt: tk.Event) -> None:
        # 讓 inner 寬度跟著 canvas
        self._canvas.itemconfigure(self._win, width=evt.width)
        self._update_scrollbar_visibility()

    def _update_scrollbar_visibility(self) -> None:
        """內容高度超過可視高度才顯示 scrollbar；否則隱藏，達到 24~32 吋自適應。"""
        try:
            self.update_idletasks()
            content_h = self.inner.winfo_reqheight()
            view_h = self._canvas.winfo_height()
            # 邊界：避免來回跳動
            need = content_h > (view_h + 2)
        except Exception:
            return

        if need and not self._vbar_visible:
            self._vbar.grid(row=0, column=1, sticky="ns")
            self._vbar_visible = True
        elif (not need) and self._vbar_visible:
            # 隱藏時把捲動回到頂端，避免空白感
            self._canvas.yview_moveto(0.0)
            self._vbar.grid_remove()
            self._vbar_visible = False

    def _on_mousewheel(self, evt: tk.Event) -> None:
        # 若不需要捲動（大螢幕展開狀態），就不吃滾輪，避免影響使用者對「右側結果框」的操作感
        if not self._vbar_visible:
            return
        # Windows / macOS / Linux（Tk 事件不同）
        if getattr(evt, "delta", 0):
            # delta: Windows=120倍數；macOS 也會有 delta
            self._canvas.yview_scroll(int(-evt.delta / 120), "units")
        else:
            # Linux 常用 Button-4/5
            if evt.num == 4:
                self._canvas.yview_scroll(-1, "units")
            elif evt.num == 5:
                self._canvas.yview_scroll(1, "units")

    def _bind_mousewheel(self, _evt: tk.Event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind_all("<Button-4>", self._on_mousewheel)
        self._canvas.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_mousewheel(self, _evt: tk.Event) -> None:
        self._canvas.unbind_all("<MouseWheel>")
        self._canvas.unbind_all("<Button-4>")
        self._canvas.unbind_all("<Button-5>")


class RxPickCalcGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("慢箋領藥日計算（健保春節版｜末日回推14天）")
        # 24~32 吋自適應：小螢幕預設較小；大螢幕（27/32吋）預設放大，避免看起來被關小窗
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        # 留邊距，避免貼邊或被工作列吃掉
        w = min(1400, max(1100, sw - 120))
        h = min(900, max(680, sh - 140))
        # 若螢幕本身偏小（例如 1366x768 / 1920x1080 且縮放較大），就回到保守尺寸
        if sh <= 820:
            w, h = 1100, 680
        self.geometry(f"{w}x{h}")
        self.minsize(920, 560)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(
            outer,
            text="慢箋領藥日計算（一般10天內 + 春節提前：末日回推14天）",
            font=("Microsoft JhengHei UI", 16, "bold"),
        )
        title.pack(anchor="w")

        # 規則說明（預設收合，省高度）
        hint = ttk.Label(outer, text="規則說明可展開查看（為避免 24 吋視窗被遮住，預設收合）", foreground="#6b7280")
        hint.pack(anchor="w", pady=(6, 4))

        sub_box = ttk.Frame(outer)
        sub_box.pack(fill="x", pady=(0, 10))
        self._sub_open = tk.BooleanVar(value=False)

        sub_text = (
            "一般：以上次『累積用藥末日』往前10日內可領（末日-9）。\n"
            "春節提前（你指定）：若『累積用藥末日』落春節 → 最早可領 = 末日回推14天（末日-14，例如 2/14 可提前到 1/31）。\n"
            "※ 已修正：提前領會「堆疊延後末日」，不會把末日算太早。"
        )

        sub_label = ttk.Label(sub_box, text=sub_text, foreground="#4b5563")

        def toggle_sub() -> None:
            if self._sub_open.get():
                sub_label.pack_forget()
                btn.configure(text="顯示規則說明")
                self._sub_open.set(False)
            else:
                sub_label.pack(anchor="w", pady=(6, 0))
                btn.configure(text="收合規則說明")
                self._sub_open.set(True)

        btn = ttk.Button(sub_box, text="顯示規則說明", command=toggle_sub)
        btn.pack(anchor="w")

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)

        # 左右切分：左側輸入可捲動、右側結果固定可見
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        left_scroll = ScrollableFrame(left, padding=0)
        left_scroll.pack(fill="both", expand=True)

        # ===== 左：輸入 =====
        card = ttk.LabelFrame(left_scroll.inner, text="輸入", padding=12)
        card.pack(fill="both", expand=True)

        self.var_issue = tk.StringVar()
        self.var_fill1 = tk.StringVar()
        self.var_fill2 = tk.StringVar()
        self.var_days = tk.IntVar(value=30)
        self.var_valid_days = tk.IntVar(value=90)

        self.var_h_enable = tk.BooleanVar(value=False)
        self.var_h_start = tk.StringVar()
        self.var_h_end = tk.StringVar()

        self.var_mode = tk.StringVar(value="pharmacist")  # pharmacist / patient

        # 載入上次輸入（記憶功能）
        st = load_settings()
        if st:
            if isinstance(st.get("issue"), str):
                self.var_issue.set(st["issue"])
            if isinstance(st.get("fill1"), str):
                self.var_fill1.set(st["fill1"])
            if isinstance(st.get("fill2"), str):
                self.var_fill2.set(st["fill2"])
            if isinstance(st.get("days"), int):
                self.var_days.set(st["days"])
            if isinstance(st.get("valid_days"), int):
                self.var_valid_days.set(st["valid_days"])
            if isinstance(st.get("mode"), str) and st["mode"] in ("pharmacist", "patient"):
                self.var_mode.set(st["mode"])

            # 春節假期區間（你問的『領藥區間記憶』主要就是這三個）
            if isinstance(st.get("h_enable"), bool):
                self.var_h_enable.set(st["h_enable"])
            if isinstance(st.get("h_start"), str):
                self.var_h_start.set(st["h_start"])
            if isinstance(st.get("h_end"), str):
                self.var_h_end.set(st["h_end"])

        # 關閉視窗時也存一次
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        def row(r: int, label: str, widget: tk.Widget) -> None:
            ttk.Label(card, text=label).grid(row=r, column=0, sticky="w", pady=6)
            widget.grid(row=r, column=1, sticky="ew", pady=6)
            card.grid_columnconfigure(1, weight=1)

        row(0, "處方開立日（民國/西元）", ttk.Entry(card, textvariable=self.var_issue))
        row(1, "第 1 次實領藥日（未填＝開立日）", ttk.Entry(card, textvariable=self.var_fill1))
        row(2, "第 2 次實領藥日（可選填，用於推第3次）", ttk.Entry(card, textvariable=self.var_fill2))
        row(3, "每次處方天數", ttk.Spinbox(card, from_=1, to=365, textvariable=self.var_days, width=10))
        row(4, "總有效天數（慢箋）", ttk.Spinbox(card, from_=1, to=365, textvariable=self.var_valid_days, width=10))

        mode_box = ttk.LabelFrame(card, text="顯示模式", padding=8)
        mode_box.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        ttk.Radiobutton(mode_box, text="藥師模式（紅框 + 完整計算細節）", variable=self.var_mode, value="pharmacist").pack(anchor="w")
        ttk.Radiobutton(mode_box, text="病患模式（紅框 + 精簡重點）", variable=self.var_mode, value="patient").pack(anchor="w")

        hbox = ttk.LabelFrame(card, text="春節假期（需自行輸入公告區間）", padding=10)
        hbox.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        hbox.grid_columnconfigure(1, weight=1)

        ttk.Checkbutton(
            hbox,
            text="啟用春節提前（規則：累積用藥末日落春節 → 最早可領=末日回推14天）",
            variable=self.var_h_enable,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        ttk.Label(hbox, text="春節假期開始日（含）").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(hbox, textvariable=self.var_h_start).grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(hbox, text="春節假期結束日（含）").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(hbox, textvariable=self.var_h_end).grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(
            hbox,
            text="例：2026 春節 2026-02-14 ~ 2026-02-22（依公告自行填）",
            foreground="#6b7280",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        btns = ttk.Frame(card)
        btns.grid(row=7, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Button(btns, text="計算", command=self.on_calc).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="帶入範例（2026春節）", command=self.on_example).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="清除", command=self.on_clear).pack(side="left")

        # ===== 右：結果 =====
        out_card = ttk.LabelFrame(right, text="結果", padding=12)
        out_card.pack(fill="both", expand=True)

        # 右欄字體放大（保留）
        self.out = tk.Text(out_card, wrap="word", height=20, font=("Microsoft JhengHei UI", 13))
        self.out.pack(fill="both", expand=True)

        self.out.tag_configure("h", font=("Microsoft JhengHei UI", 14, "bold"))
        self.out.tag_configure("mono", font=("Consolas", 10))
        self.out.tag_configure("warn_big", foreground="#b91c1c", font=("Microsoft JhengHei UI", 16, "bold"))

        # 一般紅框（在春節提前期間內才顯示）
        self.out.tag_configure(
            "big_box",
            foreground="#991b1b",
            background="#fee2e2",
            font=("Consolas", 20, "bold"),
            justify="center",
            spacing1=8,
            spacing3=10,
            lmargin1=18,
            lmargin2=18,
            rmargin=18,
        )

        # ✅ 春節提前「第一天」特別高亮（深紅底白字）
        self.out.tag_configure(
            "big_box_day1",
            foreground="#ffffff",
            background="#b91c1c",
            font=("Consolas", 22, "bold"),
            justify="center",
            spacing1=10,
            spacing3=12,
            lmargin1=18,
            lmargin2=18,
            rmargin=18,
        )

        # ✅ 春節提前期間卡片（大字紅框底色）
        self.out.tag_configure(
            "cny_period_card",
            foreground="#991b1b",
            background="#fee2e2",
            font=("Microsoft JhengHei UI", 20, "bold"),
            justify="left",
            spacing1=8,
            spacing3=10,
            lmargin1=18,
            lmargin2=18,
            rmargin=18,
        )

        self._set_output("請輸入資料後按「計算」。")

    def _set_output(self, s: str) -> None:
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("end", s)
        self.out.configure(state="disabled")

    def _persist_settings(self) -> None:
        data: Dict[str, Any] = {
            "issue": self.var_issue.get().strip(),
            "fill1": self.var_fill1.get().strip(),
            "fill2": self.var_fill2.get().strip(),
            "days": int(self.var_days.get()),
            "valid_days": int(self.var_valid_days.get()),
            "mode": self.var_mode.get().strip(),
            "h_enable": bool(self.var_h_enable.get()),
            "h_start": self.var_h_start.get().strip(),
            "h_end": self.var_h_end.get().strip(),
        }
        save_settings(data)

    def _append(self, s: str, tag: Optional[str] = None) -> str:
        """附加文字到結果區並回傳插入起點 index。

        用途：像「春節紅框」這種需要自動捲動到該位置時，可以用回傳的 index 搭配 Text.see()。
        """
        self.out.configure(state="normal")
        start_idx = self.out.index("end-1c")
        if tag:
            self.out.insert("end", s, tag)
        else:
            self.out.insert("end", s)
        self.out.configure(state="disabled")
        return start_idx

    def on_close(self) -> None:
        # 關閉視窗前也記憶一次（避免只填春節區間沒按計算）
        try:
            self._persist_settings()
        finally:
            self.destroy()

    def on_clear(self) -> None:
        self.var_issue.set("")
        self.var_fill1.set("")
        self.var_fill2.set("")
        self.var_days.set(30)
        self.var_valid_days.set(90)
        self.var_h_enable.set(False)
        self.var_h_start.set("")
        self.var_h_end.set("")
        self.var_mode.set("pharmacist")
        self._set_output("請輸入資料後按「計算」。")

    def on_example(self) -> None:
        # 範例：驗證「提前領堆疊」＋「春節末日回推14天」
        self.var_issue.set("2026-01-01")
        self.var_fill1.set("")            # 未填=開立日
        self.var_fill2.set("2026-01-21")  # 提前領第2次（堆疊後末日會往後）
        self.var_days.set(30)
        self.var_valid_days.set(90)

        self.var_h_enable.set(True)
        self.var_h_start.set("2026-02-14")
        self.var_h_end.set("2026-02-22")
        self.var_mode.set("pharmacist")
        self.on_calc()

    def _banner_box(self, start_date: date, reason: str) -> str:
        s = fmt_ymd(start_date)
        lines = [
            "╔══════════════════════════════╗",
            "║      ⭐ 春節提前可領藥 ⭐      ║",
            "║                              ║",
            f"║        {s} 起         ║",
            "║          可 領 藥 ✅          ║",
            "║                              ║",
            f"║  {reason:<28}║",
            "╚══════════════════════════════╝",
        ]
        return "\n".join(lines) + "\n\n"

    def _cny_period_box(self, start_date: date, end_date: date) -> str:
        """春節提前期間卡片（大字底色）。

        需求：把「春節提前（末日回推14天）：可提前領藥期間 A ~ B」做成醒目卡片。
        """
        s = fmt_ymd(start_date)
        e = fmt_ymd(end_date)
        s_roc = fmt_roc(start_date)
        e_roc = fmt_roc(end_date)
        return (
            "【春節提前（末日回推14天）】\n"
            f"可提前領藥期間：{s} ～ {e}\n"
            f"（民國：{s_roc} ～ {e_roc}）\n\n"
        )

    def on_calc(self) -> None:
        mode = self.var_mode.get().strip()

        issue = parse_date(self.var_issue.get().strip())
        if not issue:
            messagebox.showerror("格式錯誤", "處方開立日格式錯誤（例：114/12/20 或 2026-12-20）")
            return

        fill1_raw = self.var_fill1.get().strip()
        fill1 = parse_date(fill1_raw) if fill1_raw else issue
        if fill1_raw and not fill1:
            messagebox.showerror("格式錯誤", "第 1 次實領藥日格式錯誤")
            return

        fill2_raw = self.var_fill2.get().strip()
        fill2 = parse_date(fill2_raw) if fill2_raw else None
        if fill2_raw and not fill2:
            messagebox.showerror("格式錯誤", "第 2 次實領藥日格式錯誤")
            return

        try:
            days = int(self.var_days.get())
            valid_days = int(self.var_valid_days.get())
        except Exception:
            messagebox.showerror("格式錯誤", "處方天數/有效天數 必須是整數")
            return

        if days <= 0 or valid_days <= 0:
            messagebox.showerror("參數錯誤", "處方天數/有效天數必須 > 0")
            return

        # 春節假期
        h_enabled = bool(self.var_h_enable.get())
        h_start = parse_date(self.var_h_start.get().strip()) if self.var_h_start.get().strip() else None
        h_end = parse_date(self.var_h_end.get().strip()) if self.var_h_end.get().strip() else None
        if h_enabled:
            if not h_start or not h_end:
                messagebox.showerror("春節提前", "啟用春節提前時，請填春節假期開始日與結束日。")
                return
            if h_start > h_end:
                messagebox.showerror("春節提前", "春節假期開始日不可晚於結束日。")
                return


        # 記憶本次輸入
        self._persist_settings()

        res = calc_plan(
            CalcInput(
                issue=issue,
                fill1=fill1,
                fill2=fill2,
                days=days,
                valid_days=valid_days,
                today=today_local_date(),
                holiday=NHIHolidayRule(enabled=h_enabled, start=h_start, end=h_end),
            )
        )

        self._set_output("")
        self._append(
            f"開立日：{fmt_ymd(res.issue)}（民國 {fmt_roc(res.issue)}）\n"
            f"到期最後可領日：{fmt_ymd(res.expiry)}（民國 {fmt_roc(res.expiry)}）\n"
            f"每次 {res.days} 天、總有效 {res.valid_days} 天；一般提前：{NHI_EARLY_DAYS} 天；春節提前：末日回推 {NHI_CNY_ADVANCE_DAYS} 天\n",
            "h",
        )

        self._append("\n領藥區間（最早 ~ 最晚）：\n", "h")

        if res.warnings:
            self._append("\n警示：\n", "warn_big")
            for w in res.warnings:
                self._append(f" - {w}\n", "warn_big")
            self._append("\n")

        for r in res.rows:
            self._append(f"\n{r.k}\n", "h")
            self._append(f"  {fmt_ymd(r.earliest)} ~ {fmt_ymd(r.latest)}\n", "mono")
            self._append(f"  民國：{fmt_roc(r.earliest)} ~ {fmt_roc(r.latest)}\n")

            # ✅ 春節提前期間（末日回推14天）做成大字卡片
            if r.cny_applied and r.cny_window_start and r.cny_window_end:
                self._append(self._cny_period_box(r.cny_window_start, r.cny_window_end), "cny_period_card")

            # ✅ 只有「今天真的在春節提前領藥期間」才顯示紅框
            if (
                r.cny_applied
                and r.cny_window_start
                and r.cny_window_end
                and (res.today >= r.cny_window_start and res.today <= r.cny_window_end)
            ):
                # ✅ 春節提前第一天自動高亮
                box_tag = "big_box_day1" if (res.today == r.cny_window_start) else "big_box"
                idx = self._append(self._banner_box(r.cny_window_start, "（末日回推14天）"), box_tag)
                # ✅ 紅框出現時自動捲到該位置（讓櫃檯一眼看到）
                try:
                    self.out.see(idx)
                    self.out.update_idletasks()
                except Exception:
                    pass

            # 病患模式：精簡
            if mode == "patient":
                self._append(
                    f"  今天（{fmt_ymd(res.today)}）=> {'可領 ✅' if r.can_today else '不可領 ❌'}\n",
                    "warn_big" if not r.can_today else None,
                )
                self._append(f"  說明：{r.note}\n")
                continue

            # 藥師模式：完整細節
            for ex in r.extra_lines:
                if "不可領 ❌" in ex:
                    self._append(f"  {ex}\n", "warn_big")
                else:
                    self._append(f"  {ex}\n")
            self._append(f"  說明：{r.note}\n")


def main():
    app = RxPickCalcGUI()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    app.mainloop()


if __name__ == "__main__":
    main()
