# Hello Badge v1 (older): Air Merge fixture app, seeded onto badge B as hello_badge.
VERSION = 1


def update():
    screen.pen = color.rgb(0, 0, 0)
    screen.clear()
    screen.pen = color.rgb(255, 255, 255)
    screen.text("hi", 150, 110)


run(update)
