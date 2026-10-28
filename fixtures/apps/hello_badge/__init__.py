# Hello Badge v2: Air Merge fixture app (valid Python, not run by the simulator).
VERSION = 2

with open("/system/apps/hello_badge/greeting.txt") as fh:
    LINES = fh.read().splitlines()


def update():
    screen.pen = color.rgb(20, 24, 40)
    screen.clear()
    screen.pen = color.rgb(255, 204, 51)
    screen.font = font.ignore
    screen.text("hello", 120, 100)
    screen.font = font.smart
    screen.text(LINES[0] if LINES else "", 10, 220)
    if badge.pressed(BUTTON_BACK):
        return False


run(update)
