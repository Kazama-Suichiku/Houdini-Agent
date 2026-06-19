# -*- coding: utf-8 -*-
"""Meshy 任务失败信息友好化（P8）。

Meshy 内容审核失败常常只回泛泛的 "The input file or parameters could not be
processed."，模型/用户无从判断原因（S0 里 "Winnie the Pooh" 被拒就是这种）。
"""
from houdini_agent.meshy.client import _friendly_task_error


def test_could_not_be_processed_adds_hint():
    msg = _friendly_task_error(
        "FAILED",
        "The input file or parameters could not be processed. Please check your input and try again.",
        kind="image",
    )
    assert "内容审核" in msg
    assert "版权" in msg or "IP" in msg


def test_image_gen_policy_error_adds_hint():
    msg = _friendly_task_error("FAILED", "content policy violation", kind="text-to-image")
    assert "内容审核" in msg


def test_normal_error_unchanged():
    msg = _friendly_task_error("FAILED", "rigging requires a humanoid mesh", kind="rigging")
    assert msg == "任务FAILED: rigging requires a humanoid mesh"
    assert "内容审核" not in msg


def test_empty_message_falls_back_to_status():
    assert _friendly_task_error("CANCELED", "", kind="text-to-3d") == "任务CANCELED: CANCELED"
