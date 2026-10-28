# Shared constants for Mona Kart. Every module imports from here so tuning
# happens in one place.

WIDTH = 320
HEIGHT = 240

# --- world / projection -----------------------------------------------------
SEGMENT_LENGTH = 200        # world units along the track per segment
ROAD_WIDTH = 1000           # half-width of the road in world units (x = +-1 is the edge)
RUMBLE_LENGTH = 3           # segments per alternating colour band
CAMERA_HEIGHT = 900         # camera height above the road surface
CAMERA_DEPTH = 0.84         # 1 / tan(fov / 2), roughly a 100 degree field of view
PLAYER_Z = 1700             # how far ahead of the camera the player kart sits (world units)
DRAW_DISTANCE = 56          # segments drawn per frame; lower this if the badge drops frames
SPLIT_DRAW_DISTANCE = 32    # segments per half-height view in split screen (two views per frame)
LAPS = 3

# --- driving ----------------------------------------------------------------
MAX_SPEED = 5200            # world units per second at top speed 1.0
OFFROAD_LIMIT = 0.45        # fraction of top speed allowed on the grass
CENTRIFUGAL = 0.32          # how hard curves push karts outward
BOOST_MS = 1200             # one token boost
PAD_BOOST_MS = 700          # driving over a boost pad
BOOST_FACTOR = 1.35         # top speed multiplier while boosting
SPIN_MS = 900               # hitting a bug
MAX_TOKENS = 3
KART_LENGTH = 300           # world units, for collisions and pickups
KART_HALF_WIDTH = 0.16      # in road half-widths (x units)

# --- palette ----------------------------------------------------------------
SKY_TOP = (22, 27, 61)
SKY_BOTTOM = (118, 92, 196)
HILLS = (52, 40, 110)
HILLS_FAR = (78, 60, 150)
GRASS_LIGHT = (24, 140, 72)
GRASS_DARK = (18, 118, 60)
RUMBLE_LIGHT = (240, 240, 245)
RUMBLE_DARK = (200, 40, 70)
ROAD_LIGHT = (92, 96, 108)
ROAD_DARK = (84, 88, 100)
LANE = (235, 235, 240)
PAD = (95, 237, 131)

# --- fonts (built-in bitmap fonts, set as screen.font = ...) ------------------
# Resolved lazily so desktop tools can import this module without the badge.
FONT_TITLE = "ignore"
FONT_BIG = "more"
FONT_BODY = "bacteria"
FONT_HUD = "smart"


def rgb(triple, alpha=255):
    return color.rgb(triple[0], triple[1], triple[2], alpha)


def use_font(name):
    screen.font = getattr(font, name)
