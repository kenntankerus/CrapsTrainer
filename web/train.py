"""
Teach the camera what your dice look like, from the pictures the server has saved:
    python -m web.train          (or the Retrain button in the camera's Tune window)

The same as train_dice_model.py does for the desktop apps, but from the database and data/dice_crops.
The camera starts using the new model straight away.
"""

import os
import sys

from . import config, db

sys.path.insert(0, config.ROOT)


def train():
    """Build data/dice_model.npz from every throw with saved pictures. Returns (made a model, the report)."""
    import dice_vision as dv
    import train_dice_model as trainer

    db.init()
    rows = [dict(r) for r in db.all_rows("SELECT throw_id, die1, die2, camera_read FROM throws "
                                         "WHERE throw_id != '' ORDER BY id")]
    lines = []
    examples = trainer.examples_from(rows, config.CROP_DIR)
    model_file = os.path.join(config.DATA_DIR, "dice_model.npz")
    made = trainer.train_and_save(examples, model_file, print=lambda text="": lines.append(text),
                                  fix_key="Fix last", after="The camera uses it from now on.")
    if made:
        dv.load_model(model_file)
    return made, "\n".join(lines).strip()


if __name__ == "__main__":
    print(train()[1])
