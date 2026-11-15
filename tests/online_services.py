"""Definitions of a few online services, for tests of the formats and the gateway. Shoggoth bundles none (their prices and terms
change too fast to ship); a person adds one by putting a file like these in <data>/engines.d, and so do these tests."""
import os
import shutil

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "engines.d")


def install(data_dir=None):
    """Copy the definitions into <data>/engines.d (default: $SHOGGOTH_DATA_DIR) and return that folder."""
    dest = os.path.join(data_dir or os.environ["SHOGGOTH_DATA_DIR"], "engines.d")
    os.makedirs(dest, exist_ok=True)
    for n in os.listdir(FOLDER):
        shutil.copy(os.path.join(FOLDER, n), dest)
    return dest
