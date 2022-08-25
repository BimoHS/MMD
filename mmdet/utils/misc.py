# Copyright (c) OpenMMLab. All rights reserved.
import glob
import os
import os.path as osp
import warnings

<<<<<<< HEAD
<<<<<<< HEAD
from mmengine.config import Config, ConfigDict
from mmengine.logging import print_log
=======
import mmcv
import mmengine
<<<<<<< HEAD
from mmcv.utils import print_log
>>>>>>> update
=======
=======
from mmengine import Config, ConfigDict
>>>>>>> update
from mmengine.logging import print_log
>>>>>>> update misc.py


def find_latest_checkpoint(path, suffix='pth'):
    """Find the latest checkpoint from the working directory.

    Args:
        path(str): The path to find checkpoints.
        suffix(str): File extension.
            Defaults to pth.

    Returns:
        latest_path(str | None): File path of the latest checkpoint.
    References:
        .. [1] https://github.com/microsoft/SoftTeacher
                  /blob/main/ssod/utils/patch.py
    """
    if not osp.exists(path):
        warnings.warn('The path of checkpoints does not exist.')
        return None
    if osp.exists(osp.join(path, f'latest.{suffix}')):
        return osp.join(path, f'latest.{suffix}')

    checkpoints = glob.glob(osp.join(path, f'*.{suffix}'))
    if len(checkpoints) == 0:
        warnings.warn('There are no checkpoints in the path.')
        return None
    latest = -1
    latest_path = None
    for checkpoint in checkpoints:
        count = int(osp.basename(checkpoint).split('_')[-1].split('.')[0])
        if count > latest:
            latest = count
            latest_path = checkpoint
    return latest_path


def update_data_root(cfg, logger=None):
    """Update data root according to env MMDET_DATASETS.

    If set env MMDET_DATASETS, update cfg.data_root according to
    MMDET_DATASETS. Otherwise, using cfg.data_root as default.

    Args:
        cfg (:obj:`Config`): The model config need to modify
        logger (logging.Logger | str | None): the way to print msg
    """
<<<<<<< HEAD
<<<<<<< HEAD
    assert isinstance(cfg, Config), \
=======
    assert isinstance(cfg, mmengine.Config), \
>>>>>>> update
=======
    assert isinstance(cfg, Config), \
>>>>>>> update
        f'cfg got wrong type: {type(cfg)}, expected mmengine.Config'

    if 'MMDET_DATASETS' in os.environ:
        dst_root = os.environ['MMDET_DATASETS']
        print_log(f'MMDET_DATASETS has been set to be {dst_root}.'
                  f'Using {dst_root} as data root.')
    else:
        return

<<<<<<< HEAD
<<<<<<< HEAD
    assert isinstance(cfg, Config), \
=======
    assert isinstance(cfg, mmengine.Config), \
>>>>>>> update
=======
    assert isinstance(cfg, Config), \
>>>>>>> update
        f'cfg got wrong type: {type(cfg)}, expected mmengine.Config'

    def update(cfg, src_str, dst_str):
        for k, v in cfg.items():
            if isinstance(v, ConfigDict):
                update(cfg[k], src_str, dst_str)
            if isinstance(v, str) and src_str in v:
                cfg[k] = v.replace(src_str, dst_str)

    update(cfg.data, cfg.data_root, dst_root)
    cfg.data_root = dst_root
