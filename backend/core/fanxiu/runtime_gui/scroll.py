"""List observation defaults shared by scrolling and business end detection.

Input completion does not mean the game animation has settled. Observe only
following the post-release delay and consecutive stable samples. Similarity is
expressed as a percentage, using the same scale as image_signature_similarity.
"""

DEFAULT_SCROLL_SETTLE_SECONDS = 1.5
DEFAULT_SCROLL_UNCHANGED_THRESHOLD = 95.0
