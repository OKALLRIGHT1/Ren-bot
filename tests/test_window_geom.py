from modules.gui.window_geom import clamp_rect, panel_pos_from_ball


def test_panel_opens_left_when_ball_on_right():
    avail = (0, 0, 1920, 1080)
    ball = (1860, 200, 70, 70)
    x, y = panel_pos_from_ball(ball, (600, 280), avail)
    assert x + 600 <= 1920
    assert x < ball[0]
    assert y == 200


def test_panel_opens_right_when_ball_on_left():
    avail = (0, 0, 1920, 1080)
    ball = (20, 80, 70, 70)
    x, y = panel_pos_from_ball(ball, (600, 280), avail)
    assert x == 20
    assert y == 80


def test_restore_ball_clamp_does_not_walk_left():
    avail = (0, 0, 1920, 1080)
    stored = (1850, 400, 70, 70)
    first = clamp_rect(stored, avail)
    second = clamp_rect(first, avail)
    assert first == second
    assert first[0] >= 1850 - 1
