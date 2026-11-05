"""board.py's sections: each module turns part of a simulator config into the real board's.

board.py runs them in a fixed order on one config dict; the order matters, since later sections extend
lists and scripts that earlier ones created. A refactor that must not change the firmware: run
`tools/board_snapshot.py save NEW_DIR` before and `compare NEW_DIR` after; every case must stay identical.
"""
