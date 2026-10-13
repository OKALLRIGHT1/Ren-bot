"""Pure geometry helpers for ball ↔ panel switching."""

from __future__ import annotations

from typing import Tuple

# x, y, w, h
Rect = Tuple[int, int, int, int]
# left, top, right, bottom (exclusive-ish screen box, same as QRect right/bottom)
Box = Tuple[int, int, int, int]


def clamp_pos(
    x: int, y: int, width: int, height: int, avail: Box
) -> Tuple[int, int]:
    left, top, right, bottom = avail
    width = max(1, int(width))
    height = max(1, int(height))
    max_x = max(left, right - width)
    max_y = max(top, bottom - height)
    return min(max(int(x), left), max_x), min(max(int(y), top), max_y)


def panel_pos_from_ball(
    ball: Rect, panel_size: Tuple[int, int], avail: Box
) -> Tuple[int, int]:
    """Place the panel using the ball as an anchor.

    If the ball sits on the right half of the screen, grow left so the right
    edges stay aligned. Otherwise grow right from the ball's left edge.
    """
    bx, by, bw, bh = (int(v) for v in ball)
    pw, ph = (max(1, int(v)) for v in panel_size)
    left, top, right, bottom = avail
    mid = (left + right) / 2.0
    ball_cx = bx + bw / 2.0
    if ball_cx >= mid:
        x = bx + bw - pw
    else:
        x = bx
    return clamp_pos(x, by, pw, ph, avail)


def clamp_rect(rect: Rect, avail: Box) -> Rect:
    x, y, w, h = (int(v) for v in rect)
    nx, ny = clamp_pos(x, y, w, h, avail)
    return nx, ny, max(1, w), max(1, h)
